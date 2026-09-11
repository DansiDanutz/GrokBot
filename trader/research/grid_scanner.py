"""Historical, evidence-gated ranking of grid crossing rates on offline copies."""

import math
from dataclasses import asdict

MINUTE = 60000
HOUR = 60 * MINUTE
DAY = 24 * HOUR
MAX_AGE_MS = 10 * MINUTE


def size_grid(**kwargs):
    from trader.strategies.grid_sizing import size_grid as implementation
    return implementation(**kwargs)


class Excluded(ValueError):
    def __init__(self, reason, status='unknown'):
        super().__init__(reason)
        self.status = status


def number(value, name, positive=False):
    if isinstance(value, bool):
        raise Excluded('invalid ' + name)
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise Excluded('missing ' + name) from error
    if not math.isfinite(result) or (positive and result <= 0):
        raise Excluded('invalid ' + name)
    return result


def crossings(prices, step):
    """Count step-sized moves with a persistent anchor, not round-trip fills.

    Sub-step movement carries forward. Each whole step moves the anchor, so
    minute segmentation cannot discard residual motion. Closes alone cannot
    reveal intraminute paths or guarantee maker order fills.
    """
    step = number(step, 'step', positive=True)
    if not prices:
        return 0
    anchor = number(prices[0], 'price', positive=True)
    count = 0
    for value in prices[1:]:
        price = number(value, 'price', positive=True)
        delta = price - anchor
        moves = math.floor(abs(delta) / step + 1e-10)
        if moves:
            anchor += math.copysign(moves * step, delta)
            count += moves
    return count


def _fresh(market, at_ms):
    keys = ['ticker_observed_at_ms', 'book_observed_at_ms', 'book_time_ms']
    if market.get('source_time_ms') is not None:
        keys.append('source_time_ms')
    for key in keys:
        timestamp = number(market.get(key), key)
        if timestamp > at_ms or at_ms - timestamp > MAX_AGE_MS:
            raise Excluded('stale or future ' + key)


def _contract(market, at_ms):
    raw = market.get('raw', {})
    if raw.get('status') != 'Open':
        raise Excluded('contract is not active', 'filter')
    if raw.get('quoteCurrency') != 'USDT' or raw.get('isInverse') is not False:
        raise Excluded('not a linear USDT contract', 'filter')
    if raw.get('expireDate') not in (None, 0):
        raise Excluded('not a perpetual contract', 'filter')
    if raw.get('marketType') in ('STOCK', 'INDEX', 'COMMODITY', 'FOREX'):
        raise Excluded('not a crypto contract', 'filter')
    listed = number(raw.get('firstOpenDate'), 'listing time')
    if listed <= 0 or listed > at_ms:
        raise Excluded('invalid listing time')
    if at_ms - listed < 7 * DAY:
        raise Excluded('listing age below seven days', 'filter')
    return raw


def _market_filters(market, raw):
    bid = number(market.get('bid'), 'bid', positive=True)
    ask = number(market.get('ask'), 'ask', positive=True)
    if ask < bid:
        raise Excluded('crossed book')
    spread = (ask - bid) / ((ask + bid) / 2)
    if spread > 0.0005 + 1e-12:
        raise Excluded('spread above 0.05 percent', 'filter')
    turnover = number(market.get('turnover24'), '24-hour turnover')
    if turnover < 5000000:
        raise Excluded('24-hour turnover below 5 million USDT', 'filter')
    period = number(raw.get('fundingRateGranularity'), 'funding period', positive=True)
    funding = number(market.get('funding_rate'), 'funding rate') * (8 * HOUR / period)
    if abs(funding) > 0.0005:
        raise Excluded('funding outside 0.05 percent per eight hours', 'filter')
    return bid, ask, spread, funding


def _sizing(market, raw, centre, investment, grids):
    multiplier = number(raw.get('multiplier'), 'multiplier', positive=True)
    tick_size = number(raw.get('tickSize'), 'tick size', positive=True)
    lot_size = number(raw.get('lotSize'), 'lot size', positive=True)
    sizing = size_grid(investment=investment, leverage=5, centre=centre,
                       target_profit=1, grids=grids, tick_size=tick_size,
                       multiplier=multiplier, lot_size=lot_size)
    bid_depth = number(market.get('bid_size'), 'bid size') * multiplier
    ask_depth = number(market.get('ask_size'), 'ask size') * multiplier
    if min(bid_depth, ask_depth) < sizing.quantity:
        raise Excluded('top-of-book depth below one grid', 'filter')
    return sizing, bid_depth, ask_depth


def _rates(snapshot, pair, at_ms, step):
    end = at_ms // MINUTE * MINUTE
    start = end - DAY
    rows = snapshot.candles(pair, start, end)
    expected = range(start, end, MINUTE)
    if len(rows) != 1440 or any(row['time_ms'] != timestamp
                                for row, timestamp in zip(rows, expected)):
        raise Excluded('24-hour minute history is incomplete or conflicting')
    prices = [number(row.get('close'), 'candle close', positive=True)
              for row in rows]
    count_24h = crossings(prices, step)
    count_4h = crossings(prices[-240:], step)
    return count_4h, count_24h


def _candidate(snapshot, pair, at_ms, investment, grids):
    market = snapshot.market(pair, at_ms)
    if market is None:
        raise Excluded('no as-of market evidence')
    _fresh(market, at_ms)
    raw = _contract(market, at_ms)
    bid, ask, spread, funding = _market_filters(market, raw)
    try:
        sizing, bid_depth, ask_depth = _sizing(
            market, raw, (bid + ask) / 2, investment, grids)
    except ValueError as error:
        if isinstance(error, Excluded):
            raise
        raise Excluded('grid sizing is unavailable') from error
    count_4h, count_24h = _rates(snapshot, pair, at_ms, sizing.step)
    return {
        'pair': pair, 'expected_grids_per_hour': min(count_4h / 4, count_24h / 24),
        'crossings_4h': count_4h, 'crossings_24h': count_24h,
        'rate_4h': count_4h / 4, 'rate_24h': count_24h / 24,
        'form': sizing.form_values(pair), 'sizing': asdict(sizing),
        'bid': bid, 'ask': ask, 'spread': spread,
        'bid_depth_base': bid_depth, 'ask_depth_base': ask_depth,
        'funding_rate': funding, 'funding_period_ms': 8 * HOUR,
        'funding_raw_rate': float(market['funding_rate']),
        'funding_source_period_ms': float(raw['fundingRateGranularity']),
        'multiplier': float(raw['multiplier']), 'lot_size': float(raw['lotSize']),
        'tick_size': float(raw['tickSize']),
        'observed_at_ms': market['ticker_observed_at_ms'],
    }


def scan(snapshot, at_ms, investment=1000, grids=20, limit=10):
    """Every offline tick ranks only complete as-of evidence; no network use.

    Rate denominators are fixed 4/24 hours. Each window contains the completed
    closes in [floor(t)-window, floor(t)); the first close is its anchor. The
    minimum of the two rates is the deliberately conservative ranking score.
    """
    if not isinstance(limit, int) or isinstance(limit, bool) or not 5 <= limit <= 10:
        raise ValueError('limit must be an integer from five through ten')
    candidates, excluded = [], []
    symbols = snapshot.symbols(at_ms)
    for pair in symbols:
        try:
            candidates.append(_candidate(snapshot, pair, at_ms, investment, grids))
        except Excluded as error:
            excluded.append({'pair': pair, 'status': error.status, 'reason': str(error)})
    candidates.sort(key=lambda item: (-item['expected_grids_per_hour'], item['pair']))
    return {
        'at_ms': at_ms, 'candidates': candidates[:limit], 'excluded': excluded,
        'eligible_candidates': candidates,
        'coverage': {'status': 'known' if symbols else 'unknown',
                     'reason': None if symbols else 'no as-of market symbols',
                     'asof_symbols': len(symbols), 'eligible': len(candidates),
                     'unknown': sum(item['status'] == 'unknown' for item in excluded),
                     'filtered': sum(item['status'] == 'filter' for item in excluded)},
        'metric_definition': 'Step-crossing proxy per hour; not observed completed '
                             'grid pairs or guaranteed fills. Score=min(4h,24h).',
        'freshness_limit_ms': MAX_AGE_MS,
    }
