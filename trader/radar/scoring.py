"""Fixed watchlist scoring shared by radar and paper autopilot."""

import math


MAJORS = frozenset(('XBTUSDTM', 'ETHUSDTM', 'SOLUSDTM'))
DIRECTIONS = frozenset(('LONG', 'SHORT', 'TURNING-UP', 'TURNING-DOWN', 'NEUTRAL'))
FIELDS = ('price', 'expected_grids_per_hour', 'turnover_24h_usdt', 'spread_pct',
          'range_low', 'range_high', 'atr_1h_pct', 'atr_4h_pct', 'position_7d',
          'funding_pct', 'change_24h_pct', 'listing_age_days', 'snapshot_age_min')


def _clamp(value, upper):
    return min(upper, max(0, value))


def score_row(row):
    """Return a bounded score and numeric measurements without changing the row.

    LONG/SHORT radar labels already encode agreement of the daily and 4h reads.
    ATR percentages have a common price denominator, so their ratio equals the
    absolute ATR ratio. Funding favour takes precedence over the tiny-rate rule.
    """
    try:
        values = {key: row[key] for key in FIELDS}
        if any(type(value) not in (int, float) or not math.isfinite(value)
               for value in values.values()):
            raise ValueError
        if row['direction'] not in DIRECTIONS or not isinstance(row['symbol'], str):
            raise ValueError
        if values['price'] <= 0 or values['atr_4h_pct'] <= 0:
            raise ValueError
        if values['range_low'] >= values['range_high']:
            raise ValueError
        if any(values[key] < 0 for key in ('expected_grids_per_hour', 'turnover_24h_usdt',
                'spread_pct', 'atr_1h_pct', 'listing_age_days', 'snapshot_age_min')):
            raise ValueError
    except (KeyError, TypeError, ValueError, OverflowError):
        raise ValueError('invalid radar scoring input') from None
    direction = row['direction']
    position = values['position_7d']
    clarity = (20 if direction in ('LONG', 'SHORT') else
               14 if direction.startswith('TURNING-') else
               16 if .25 <= position <= .75 else 6)
    nearest = min(values['price'] - values['range_low'],
                  values['range_high'] - values['price'])
    room = nearest / values['price'] * 100 / values['atr_4h_pct']
    stability = 2 * values['atr_1h_pct'] / values['atr_4h_pct']
    rate = values['funding_pct']
    favoured = ((direction in ('LONG', 'TURNING-UP') and rate < 0) or
                (direction in ('SHORT', 'TURNING-DOWN') and rate > 0))
    parts = []

    def add(code, value, points):
        if not math.isfinite(value) or not math.isfinite(points):
            raise ValueError('invalid radar scoring result')
        parts.append({'code': code, 'value': value, 'points': points})

    add('OSCILLATION', values['expected_grids_per_hour'],
        _clamp(values['expected_grids_per_hour'] * 1.5, 30))
    add('TREND_CLARITY', position, clarity)
    add('LIQUIDITY_TURNOVER', values['turnover_24h_usdt'],
        _clamp((values['turnover_24h_usdt'] - 3_000_000) / 27_000_000 * 10, 10))
    spread = values['spread_pct']
    add('LIQUIDITY_SPREAD', spread,
        5 if spread <= .05 else 0 if spread >= .15 else (.15 - spread) / .10 * 5)
    add('ROOM', room, _clamp(room * 7.5, 15))
    add('FUNDING', rate, 10 if favoured else 5 if abs(rate) < .01 else 0)
    add('STABILITY', stability, 10 if .6 <= stability <= 1.4 else 4)
    for code, value, applies, penalty in (
            ('MOVER_RISK', values['change_24h_pct'], abs(values['change_24h_pct']) > 30, -15),
            ('YOUNG_LISTING', values['listing_age_days'], values['listing_age_days'] < 14, -10),
            ('STALE_DATA', values['snapshot_age_min'], values['snapshot_age_min'] > 60, -20),
            ('MAJOR_LOW_YIELD', 1, row['symbol'] in MAJORS, -10)):
        if applies:
            add(code, value, penalty)
    return {'score': _clamp(sum(part['points'] for part in parts), 100),
            'score_parts': parts}
