"""Point-in-time candidate filters and a crossing proxy, never claimed as fills."""
import math
from trader.strategies.grid_features import features
from trader.strategies.grid_setup import build_setup

HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS
MINUTE_MS = 60_000


def _number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def normalize_market(raw, asof_ms):
    """KuCoin sizes are contracts; turnoverOf24h is quote value, volumeOf24h is not."""
    result = dict(raw)
    aliases = {'quote_currency': 'quoteCurrency', 'quote_turnover_24h': 'turnoverOf24h',
               'bid': 'bestBidPrice', 'ask': 'bestAskPrice', 'listed_at_ms': 'firstOpenDate',
               'tick_size': 'tickSize', 'lot_size': 'lotSize', 'funding_rate': 'fundingFeeRate'}
    for key, alias in aliases.items():
        result[key] = raw.get(key, raw.get(alias))
    if 'active' not in result:
        result['active'] = raw.get('status') == 'Open' if 'status' in raw else None
    if 'perpetual' not in result:
        result['perpetual'] = raw.get('isPerpetual')
    asset = raw.get('asset_class', raw.get('assetClass'))
    result['asset_class'] = asset.lower() if isinstance(asset, str) else None
    interval = _number(raw.get('funding_interval_hours'))
    if interval is None and raw.get('fundingRateGranularity') is not None:
        granularity = _number(raw['fundingRateGranularity'])
        interval = granularity / HOUR_MS if granularity is not None else None
    rate = _number(result['funding_rate'])
    result['funding_rate_8h'] = rate * 8 / interval if rate is not None and interval and interval > 0 else None
    for side in ('bid', 'ask'):
        depth = _number(raw.get(side + '_depth_usdt'))
        size = _number(raw.get('best' + side.title() + 'Size'))
        price, multiplier = _number(result[side]), _number(raw.get('multiplier'))
        if depth is None and size is not None and price is not None and multiplier is not None:
            depth = size * price * multiplier
        result[side + '_depth_usdt'] = depth
    result['asof_ms'] = asof_ms
    return result


def completed_bars(bars, asof_ms, hours=168):
    """Canonical timestamps identify minute opens; exclude still-open/future bars."""
    start = asof_ms - hours * HOUR_MS
    result = []
    for row in bars:
        timestamp = row.get('timestamp_ms', row.get('time_ms'))
        if timestamp is None:
            raise ValueError('candle timestamp_ms or time_ms required')
        if start <= timestamp and timestamp + MINUTE_MS <= asof_ms:
            result.append(dict(row, timestamp_ms=timestamp))
    result.sort(key=lambda row: row['timestamp_ms'])
    if len({row['timestamp_ms'] for row in result}) != len(result):
        raise ValueError('duplicate minute candles')
    return result


def _coverage(bars, asof_ms):
    expected = 7 * 24 * 60
    if len(bars) != expected:
        return 'requires seven days of completed one-minute candles'
    if bars[0]['timestamp_ms'] != asof_ms - 7 * DAY_MS:
        return 'seven-day candle window is not aligned to asof'
    if any(b['timestamp_ms'] - a['timestamp_ms'] != MINUTE_MS for a, b in zip(bars, bars[1:])):
        return 'missing one-minute candles'
    return None


def _market_reason(market, asof_ms, options):
    observed = market.get('observed_at_ms')
    age = options.get('market_max_age_ms', HOUR_MS)
    if observed is None or not 0 <= asof_ms - observed <= age:
        return 'unknown, stale or future market observation'
    for field in ('active', 'perpetual'):
        if market.get(field) is not True:
            return field + ' must be known and true'
    if market.get('quote_currency') != 'USDT':
        return 'requires USDT quote currency'
    if market.get('asset_class') != 'crypto':
        return 'requires known crypto asset class; stocks and forex are excluded'
    turnover = _number(market.get('quote_turnover_24h'))
    if turnover is None or turnover < 500_000:
        return 'quote turnover unknown or below 500000 USDT; base volume is not turnover'
    bid, ask = _number(market.get('bid')), _number(market.get('ask'))
    if bid is None or ask is None or bid <= 0 or ask < bid or (ask-bid)/((bid+ask)/2) > .001:
        return 'spread unknown, invalid or above 0.1 percent'
    listed = _number(market.get('listed_at_ms'))
    if listed is None or asof_ms - listed < 7 * DAY_MS:
        return 'listing age unknown or below seven days'
    funding = market.get('funding_rate_8h')
    if funding is None or abs(funding) > .001:
        return 'normalized eight-hour funding unknown or outside 0.1 percent'
    return None


def _crossings(bars, step, low, high):
    if not bars:
        return 0
    anchor, crossings = min(high, max(low, float(bars[0]['close']))), 0
    for row in bars[1:]:
        price = min(high, max(low, float(row['close'])))
        count = int((abs(price - anchor) + step * 1e-10) / step)
        if count:
            anchor += math.copysign(count * step, price - anchor)
            crossings += count
    return crossings


def crossing_score(bars, setup, asof_ms):
    """Close-only hysteresis ignores sub-step jitter; monotonic moves are not cycles."""
    step = setup.get('interval', (setup['high'] - setup['low']) / setup['grids'])
    if not math.isfinite(step) or step <= 0:
        raise ValueError('positive finite grid step required')
    rates = {hours: _crossings(completed_bars(bars, asof_ms, hours), step,
                              setup['low'], setup['high']) / hours
             for hours in (4, 24)}
    return dict(score=min(rates.values()), rate_4h=rates[4], rate_24h=rates[24],
                step=step, kind='hysteretic_step_crossing_proxy',
                limitation='close-only step crossings are not executed fills or completed grids')


def _evaluate(record, asof_ms, options):
    market = normalize_market(record['market'], asof_ms)
    reason = _market_reason(market, asof_ms, options)
    if reason:
        return None, reason
    bars = completed_bars(record['bars'], asof_ms)
    reason = _coverage(bars, asof_ms)
    if reason:
        return None, reason
    market['price'] = bars[-1]['close']
    analysis = features(bars, market['funding_rate_8h'])
    form = build_setup(record['pair'], analysis, market, options.get('setup'))
    if not form.get('eligible'):
        return None, form.get('reason', 'setup rejected')
    notional = form.get('per_grid_notional', form['quantity'] * form['entry'])
    depths = [market.get(side + '_depth_usdt') for side in ('bid', 'ask')]
    if any(depth is None or depth < notional for depth in depths):
        return None, 'top-of-book depth unknown or below one grid notional on either side'
    score = crossing_score(bars, form, asof_ms)
    return dict(pair=record['pair'], asof_ms=asof_ms, **score, setup=form,
                direction=form['direction'], reason=form['reason'], coverage='complete'), None


def _rejection_coverage(record, asof_ms, reason):
    """Known filter failures do not mean data is missing for the strategy."""
    market = normalize_market(record['market'], asof_ms)
    if reason.startswith('quote turnover'):
        return _number(market.get('quote_turnover_24h')) is None
    if reason.startswith('spread'):
        bid, ask = _number(market.get('bid')), _number(market.get('ask'))
        return bid is None or ask is None or bid <= 0 or ask < bid
    if reason.startswith('listing age'):
        return _number(market.get('listed_at_ms')) is None
    if reason.startswith('normalized eight-hour'):
        return market.get('funding_rate_8h') is None
    if reason.startswith('top-of-book depth'):
        return any(market.get(side+'_depth_usdt') is None for side in ('bid', 'ask'))
    if reason.startswith(('active', 'perpetual')):
        return market.get(reason.split()[0]) is None
    if reason.startswith('requires known crypto'):
        return market.get('asset_class') is None
    if reason.startswith('requires USDT'):
        return market.get('quote_currency') is None
    return reason.startswith(('unknown, stale', 'requires seven days',
                              'seven-day candle', 'missing one-minute'))


def radar(records, asof_ms, running_pairs=(), parameters=None):
    """Return the best five eligible non-running pairs and explicit rejections."""
    options, selected, rejected = parameters or {}, [], []
    running, seen = set(running_pairs), set()
    for record in records:
        pair = record['pair']
        if pair in seen:
            raise ValueError('duplicate market pair')
        seen.add(pair)
        if pair in running:
            rejected.append(dict(pair=pair, reason='already running', coverage_issue=False))
            continue
        candidate, reason = _evaluate(record, asof_ms, options)
        if reason:
            rejected.append(dict(pair=pair, reason=reason,
                                 coverage_issue=_rejection_coverage(record, asof_ms, reason)))
        else:
            selected.append(candidate)
    selected.sort(key=lambda row: (-row['score'], row['pair']))
    return dict(asof_ms=asof_ms, radar=selected[:5], rejected=rejected,
                coverage=dict(observed=len(seen), eligible=len(selected), rejected=len(rejected)))


def volatility_proxy(high_24h, low_24h):
    """Candidate definition only: 100*(high24-low24)/low24; KuCoin unverified."""
    high, low = _number(high_24h), _number(low_24h)
    if high is None or low is None or low <= 0 or high < low:
        raise ValueError('valid positive time-aligned daily high and low required')
    return (high-low) / low * 100


def compare_volatility(fixture_rows, aligned_daily_ranges, tolerance_pp=1):
    """Ranges must be independently observed at the fixture capture time."""
    rows = []
    for expected in fixture_rows:
        data = aligned_daily_ranges.get(expected['symbol'])
        actual = None
        if data and data.get('capture_aligned') is True:
            actual = volatility_proxy(data['high_24h'], data['low_24h'])
        difference = None if actual is None else abs(actual-expected['volatility_pct'])
        rows.append(dict(pair=expected['symbol'], expected=expected['volatility_pct'],
                         measured=actual, difference_pp=difference,
                         status='unknown' if actual is None else
                         ('within_tolerance' if difference <= tolerance_pp else 'mismatch')))
    verified = sum(row['status'] == 'within_tolerance' for row in rows)
    return dict(formula='100 * (high24 - low24) / low24 (unverified candidate)',
                rows=rows, verified=verified,
                unknown=sum(row['status'] == 'unknown' for row in rows),
                validated=bool(rows) and verified == len(rows), tolerance_pp=tolerance_pp)
