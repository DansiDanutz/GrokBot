"""Public KuCoin futures snapshots for the isolated paper experiment.

Only GETs to three fixed public paths; no keys, account calls or orders.
Range % is (24h high - low) / low, NOT the app's undocumented volatility.
Entry score is a testable heuristic, not a probability or validated edge.
"""
from __future__ import annotations

import copy
from datetime import datetime
import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

BASE = 'https://api-futures.kucoin.com'
PATHS = frozenset(('/api/v1/contracts/active', '/api/v1/allTickers', '/api/v1/kline/query'))
MAX_QUOTE_AGE = 90
MIN_TURNOVER = 1_000_000
HOUR = 3600
SYMBOL = re.compile(r'^[A-Z0-9]{1,30}USDTM$')


class MarketError(ValueError):
    """Sanitized public-data failure."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise MarketError('redirect refused')


def public_get(path: str, params: dict | None = None):
    if path not in PATHS:
        raise MarketError('endpoint not allowed')
    params = params or {}
    if path != '/api/v1/kline/query' and params:
        raise MarketError('unexpected query')
    if path.endswith('/kline/query'):
        if set(params) != {'symbol', 'granularity', 'from', 'to'} or not SYMBOL.fullmatch(str(params['symbol'])):
            raise MarketError('invalid candle query')
        if params['granularity'] != 60 or not 0 < int(params['to']) - int(params['from']) <= 49 * HOUR * 1000:
            raise MarketError('unbounded candle query')
    url = BASE + path + ('?' + urllib.parse.urlencode(params) if params else '')
    request = urllib.request.Request(url, headers={'Accept': 'application/json', 'User-Agent': 'ZmartyChat-paper-grid/1'}, method='GET')
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=8) as response:
            payload = response.read(4_000_001)
        if len(payload) > 4_000_000:
            raise MarketError('response too large')
        result = json.loads(payload)
        if not isinstance(result, dict) or result.get('code') != '200000' or not isinstance(result.get('data'), list):
            raise MarketError('invalid API response')
        return result['data']
    except MarketError:
        raise
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, TypeError):
        raise MarketError('public request failed') from None


def number(value) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise MarketError('non-finite number')
    return result


def contract_lot_size(value) -> int:
    """Futures lots count whole contracts; never truncate invalid fractions."""
    lot = number(value)
    if isinstance(value, bool) or lot <= 0 or not lot.is_integer():
        raise MarketError('invalid whole-contract lot size')
    return int(lot)


def epoch_seconds(value) -> float:
    """Decode current-era provider seconds/milliseconds/microseconds/nanoseconds."""
    stamp = number(value)
    if stamp >= 1e17:
        stamp /= 1e9
    elif stamp >= 1e14:
        stamp /= 1e6
    elif stamp >= 1e11:
        stamp /= 1e3
    if not 1e9 <= stamp < 1e10:
        raise MarketError('invalid timestamp')
    return stamp


def completed_candles(rows: list, now: float) -> list[dict]:
    """Futures row order: timestamp, open, high, low, close, volume, turnover."""
    candles = {}
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            raise MarketError('malformed candle')
        stamp = epoch_seconds(row[0])
        if stamp % HOUR:
            raise MarketError('unaligned candle')
        if stamp + HOUR > now:
            continue
        o, h, l, c, volume = (number(x) for x in row[1:6])
        if min(o, h, l, c) <= 0 or volume < 0 or h < max(o, c, l) or l > min(o, c, h):
            raise MarketError('invalid candle prices')
        item = dict(time=stamp, open=o, high=h, low=l, close=c, volume=volume)
        if stamp in candles and candles[stamp] != item:
            raise MarketError('conflicting duplicate candle')
        candles[stamp] = item
    ordered = sorted(candles.values(), key=lambda x: x['time'])[-48:]
    if len(ordered) < 24:
        raise MarketError('fewer than 24 completed candles')
    expected_latest = math.floor(now / HOUR) * HOUR - HOUR
    if ordered[-1]['time'] != expected_latest:
        raise MarketError('stale candle history')
    # Every bar used for this heuristic must be contiguous; missing bars are not filled.
    if any(b['time'] - a['time'] != HOUR for a, b in zip(ordered, ordered[1:])):
        raise MarketError('gapped candle history')
    return ordered


def contract_valid(contract: dict) -> bool:
    try:
        return bool(SYMBOL.fullmatch(contract['symbol']) and contract['quoteCurrency'] == 'USDT'
                    and contract['settleCurrency'] == 'USDT' and contract['status'] == 'Open'
                    and contract['isInverse'] is False and contract.get('expireDate') in (None, 0)
                    and contract.get('marketStage', 'NORMAL') == 'NORMAL'
                    and contract.get('marketType', 'CRYPTO') == 'CRYPTO'
                    and contract_lot_size(contract['lotSize']) > 0
                    and all(number(contract[k]) > 0 for k in ('lotSize', 'tickSize', 'multiplier')))
    except (KeyError, ValueError, TypeError):
        return False


def range_pct(contract: dict) -> float:
    high, low = number(contract['highPrice']), number(contract['lowPrice'])
    if not 0 < low <= high:
        raise MarketError('invalid 24h range')
    return (high / low - 1) * 100


def rank_contracts(contracts: list[dict]) -> list[dict]:
    ranked = []
    for contract in contracts:
        if not contract_valid(contract):
            continue
        try:
            turnover = number(contract['turnoverOf24h'])
            volatility = range_pct(contract)
            if turnover >= MIN_TURNOVER:
                ranked.append({'symbol': contract['symbol'], 'range_pct': volatility, 'turnover24h': turnover})
        except (KeyError, ValueError, TypeError):
            continue
    return sorted(ranked, key=lambda c: (-c['range_pct'], -c['turnover24h'], c['symbol']))


def analyze(contract: dict, ticker: dict, candles: list[dict], now: float) -> dict:
    symbol = contract['symbol']
    bid, ask, mark = (number(x) for x in (ticker['bestBidPrice'], ticker['bestAskPrice'], contract['markPrice']))
    quote_time = epoch_seconds(ticker['ts'])
    if min(bid, ask, mark) <= 0 or bid > ask:
        raise MarketError('invalid quote')
    funding = number(contract['fundingFeeRate'])
    interval = number(contract.get('currentFundingRateGranularity') or contract['fundingRateGranularity']) / 3_600_000
    if not 0 < interval <= 24:
        raise MarketError('invalid funding interval')
    lot, multiplier = contract_lot_size(contract['lotSize']), number(contract['multiplier'])
    spread = (ask / bid - 1) * 100
    bars = candles[-24:]
    low, high = min(c['low'] for c in bars), max(c['high'] for c in bars)
    midpoint = (low + high) / 2
    location = (ask - low) / (high - low) if high > low else 1
    atr = sum(max(b['high'] - b['low'], abs(b['high'] - a['close']), abs(b['low'] - a['close']))
              for a, b in zip(candles[-15:-1], candles[-14:])) / 14
    atr_pct = atr / ask * 100
    edge = max(0.0, (midpoint / ask - 1) * 100)
    six_hour_change = (bars[-1]['close'] / bars[-7]['close'] - 1) * 100
    day_change = (bars[-1]['close'] / bars[0]['open'] - 1) * 100
    reasons = []
    if not contract_valid(contract): reasons.append('invalid_contract')
    if not 0 <= now - quote_time <= MAX_QUOTE_AGE: reasons.append('stale_quote')
    if number(contract['turnoverOf24h']) < MIN_TURNOVER: reasons.append('low_turnover')
    if spread > 0.20: reasons.append('wide_spread')
    if not 0.25 <= atr_pct <= 8: reasons.append('atr_outside_bounds')
    if not 0 <= location <= 0.45: reasons.append('not_lower_range')
    if bars[-1]['close'] <= bars[-1]['open']: reasons.append('no_completed_rebound')
    if six_hour_change < -5 or day_change < -15: reasons.append('strong_downtrend')
    if edge < 0.8: reasons.append('insufficient_rebound_room')
    if abs(funding) > 0.003: reasons.append('funding_outside_bounds')
    bid_size, ask_size = number(ticker.get('bestBidSize', 0)), number(ticker.get('bestAskSize', 0))
    if min(bid_size, ask_size) <= 0: reasons.append('missing_top_depth')
    score = round(max(0, min(100, 40 + min(edge, 5) * 8 + (0.45 - location) * 20 - spread * 40)), 4)
    return dict(symbol=symbol, bid=bid, ask=ask, mark=mark, quote_time=quote_time,
                lot_size=lot, multiplier=multiplier, tick_size=number(contract['tickSize']),
                funding_rate=funding, funding_interval_hours=interval, score=score,
                eligible=not reasons, add_eligible=not reasons, atr_pct=atr_pct,
                entry_edge_pct=edge, turnover24h=number(contract['turnoverOf24h']),
                bid_size=bid_size, ask_size=ask_size,
                bid_depth_usdt=bid_size * multiplier * bid, ask_depth_usdt=ask_size * multiplier * ask,
                max_paper_order_notional=200.0, spread_pct=spread, reasons=reasons,
                candle_close_time=bars[-1]['time'] + HOUR, range_position=location,
                signal='completed_1h_rebound_in_lower_24h_range',
                mark_time_source='contract retrieval; provider has no mark timestamp')


def collect(previous_scan: dict | None, held_symbols: list[str], now: float, force_scan: bool = False) -> tuple[dict, dict]:
    started = time.monotonic()
    day = datetime.fromtimestamp(now, ZoneInfo('Europe/Bucharest')).date().isoformat()
    previous = previous_scan if isinstance(previous_scan, dict) else {}
    scan = copy.deepcopy(previous)
    scan.update(checked_at=now, errors={}, range_metric='100 * (24h high / low - 1); not KuCoin app volatility',
                limitations=['Top-of-book only; no queue/fill guarantee.', '200 USDT maximum paper order assumption.',
                             'Heuristic not yet validated on unseen data.'])
    try:
        contracts = public_get('/api/v1/contracts/active')
        tickers = public_get('/api/v1/allTickers')
    except MarketError as error:
        scan['errors']['global'] = str(error)
        return {}, scan
    specs = {c['symbol']: c for c in contracts if isinstance(c, dict) and isinstance(c.get('symbol'), str)}
    quotes = {t['symbol']: t for t in tickers if isinstance(t, dict) and isinstance(t.get('symbol'), str)}
    cache = {}

    def candles_for(symbol):
        if symbol not in cache:
            rows = public_get('/api/v1/kline/query', {'symbol': symbol, 'granularity': 60,
                                                    'from': int((now - 49 * HOUR) * 1000), 'to': int(now * 1000)})
            cache[symbol] = completed_candles(rows, now)
        return cache[symbol]

    if force_scan or previous.get('day') != day or not isinstance(previous.get('top5'), list):
        # Usually every contract has provider high/low. At most 20 missing-range
        # contracts receive a candle fallback, selected by turnover to bound IO.
        missing = []
        for contract in specs.values():
            if not contract_valid(contract):
                continue
            try:
                if number(contract['turnoverOf24h']) < MIN_TURNOVER:
                    continue
            except (KeyError, ValueError, TypeError):
                continue
            try:
                range_pct(contract)
            except (KeyError, ValueError, TypeError):
                missing.append(contract)
        missing.sort(key=lambda c: -float(c.get('turnoverOf24h', 0)))
        for contract in missing[:20]:
            try:
                bars = candles_for(contract['symbol'])[-24:]
                contract['highPrice'], contract['lowPrice'] = max(b['high'] for b in bars), min(b['low'] for b in bars)
            except (MarketError, ValueError, KeyError, TypeError) as error:
                scan['errors'][contract['symbol']] = str(error) if isinstance(error, MarketError) else 'invalid candle data'
        ranking = rank_contracts(list(specs.values()))
        scan.update(day=day, scanned_at=now, top5=[r['symbol'] for r in ranking[:5]], ranking=ranking[:5],
                    liquid_contracts=len(ranking), range_fallback_count=min(len(missing), 20),
                    range_unavailable_skipped=max(0, len(missing) - 20))
    # Two comparison accounts may each hold two different symbols.
    targets = list(dict.fromkeys(scan.get('top5', [])[:5] + held_symbols[:4]))
    market = {}
    for symbol in targets:
        if not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol):
            continue
        try:
            # Candle failure must not prevent fresh quotes from reaching held-position
            # exits: emit a quote-only record with all entry signals disabled.
            bars = candles_for(symbol)
            # now is cycle start. Include elapsed retrieval time for freshness
            # checks without replacing the provider's actual quote timestamp.
            receipt_time = now + time.monotonic() - started
            market[symbol] = analyze(specs[symbol], quotes[symbol], bars, receipt_time)
        except (MarketError, KeyError, ValueError, TypeError) as error:
            reason = str(error) if isinstance(error, MarketError) else 'missing or invalid symbol data'
            scan['errors'][symbol] = reason
            try:
                c, t = specs[symbol], quotes[symbol]
                if not contract_valid(c):
                    continue
                bid, ask, mark = number(t['bestBidPrice']), number(t['bestAskPrice']), number(c['markPrice'])
                stamp = epoch_seconds(t['ts'])
                if min(bid, ask, mark) <= 0 or bid > ask:
                    continue
                market[symbol] = dict(symbol=symbol, bid=bid, ask=ask, mark=mark, quote_time=stamp,
                                      bid_size=number(t.get('bestBidSize', 0)),
                                      ask_size=number(t.get('bestAskSize', 0)),
                                      lot_size=contract_lot_size(c['lotSize']), multiplier=number(c['multiplier']),
                                      tick_size=number(c['tickSize']), funding_rate=number(c['fundingFeeRate']),
                                      funding_interval_hours=number(c.get('currentFundingRateGranularity') or c['fundingRateGranularity']) / 3_600_000,
                                      score=0, eligible=False, add_eligible=False, atr_pct=0, entry_edge_pct=0,
                                      turnover24h=number(c['turnoverOf24h']), reasons=[reason])
            except (MarketError, KeyError, TypeError, ValueError):
                pass
    return market, scan
