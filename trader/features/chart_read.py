"""Pure closed-candle chart heuristics; scores are not calibrated probabilities.

Defaults are explicit research rules, not claims of optimal tuning. Timestamped
bars are bucket openings. EMA starts with its period SMA; slopes compare five
bars, ATR uses fourteen true ranges, and pivots require two closed bars per side.
Finite causally filled aggregate buckets may inform indicators, with a coverage
penalty; unfilled or unrepresented intervals restart the contiguous indicator run.
Filled values can establish explicitly estimated pivots and entry patterns, never
observed extrema or executions. Entry callers must supply an actual observed quote.
"""
import math
from statistics import mean

DURATIONS = {'1d': 86400000, '4h': 14400000, '1h': 3600000, '15m': 900000, '5m': 300000}
CONFIDENCE_BASIS = 'heuristic indicator/structure agreement, not calibrated probability'


def _estimated(row):
    return bool(row.get('synthetic') or row.get('indicator_only') or row.get('observed') is False)


def _closed_bars(candles, timeframe, asof_ms):
    if timeframe not in DURATIONS or type(asof_ms) is not int or asof_ms < 0:
        raise ValueError('known timeframe and nonnegative integer asof_ms required')
    duration, rows, trailing, previous, unavailable = DURATIONS[timeframe], [], [], None, 0
    for source in candles:
        timestamp = source.get('timestamp_ms', source.get('time_ms'))
        if type(timestamp) is not int or timestamp < 0 or timestamp % duration:
            raise ValueError('candle opening must align to its UTC timeframe')
        if timestamp+duration > asof_ms:
            continue
        if previous is not None and timestamp <= previous:
            raise ValueError('closed candles must be ordered without duplicates')
        previous = timestamp
        if source.get('end_ms', timestamp+duration) != timestamp+duration:
            raise ValueError('candle end must match its timeframe')
        values = [source.get(key) for key in ('open', 'high', 'low', 'close', 'volume')]
        if source.get('closed') is False or (_estimated(source) and any(x is None for x in values)):
            unavailable += 1
            trailing = []
            continue
        if any(type(x) not in (int, float) or not math.isfinite(x) for x in values):
            raise ValueError('finite OHLCV fields required')
        opening, high, low, close, volume = values
        if min(values[:4]) <= 0 or volume < 0 or low > min(opening, close) or high < max(opening, close):
            raise ValueError('invalid OHLCV prices or volume')
        row = dict(source, timestamp_ms=timestamp)
        rows.append(row)
        if trailing and row['timestamp_ms'] != trailing[-1]['timestamp_ms']+duration:
            trailing = []
        trailing.append(row)
    fresh = bool(trailing) and trailing[-1]['timestamp_ms']+duration == asof_ms//duration*duration
    return trailing, len(rows), fresh, unavailable


def _ema(closes, period):
    if len(closes) < period:
        return None, None
    current = mean(closes[:period])
    history = [current]
    for close in closes[period:]:
        current += 2/(period+1)*(close-current)
        history.append(current)
    return current, (current-history[-6])/5 if len(history) >= 6 else None


def _atr(bars):
    if len(bars) < 15:
        return None
    return mean(max(row['high']-row['low'], abs(row['high']-prior['close']),
                    abs(row['low']-prior['close'])) for prior, row in zip(bars[-15:-1], bars[-14:]))


def _pivots(bars, duration, swing_count):
    result = {'highs': [], 'lows': [], 'confirmation_bars': 2, 'swing_count': swing_count,
              'estimated_windows': 0,
              'confirmation_basis': 'two closed bars per side; causal filled windows are estimates'}
    for index in range(2, len(bars)-2):
        estimated = any(_estimated(row) for row in bars[index-2:index+3])
        result['estimated_windows'] += int(estimated)
        neighbors = bars[index-2:index]+bars[index+1:index+3]
        for label, field, sign in [('highs', 'high', 1), ('lows', 'low', -1)]:
            value = bars[index][field]
            if all(sign*value > sign*row[field] for row in neighbors):
                result[label].append(dict(price=value, timestamp_ms=bars[index]['timestamp_ms'],
                    confirmed_at_ms=bars[index+2]['timestamp_ms']+duration, estimated=estimated,
                    observed=not estimated))
    for label in ('highs', 'lows'):
        result[label] = result[label][-swing_count:]
    highs, lows = result['highs'], result['lows']
    enough = len(highs) >= 2 and len(lows) >= 2
    changes = {label: [0 if math.isclose(a['price'], b['price'], rel_tol=1e-12, abs_tol=0)
        else b['price']-a['price'] for a, b in zip(result[label], result[label][1:])]
        for label in ('highs', 'lows')}
    for label in ('highs', 'lows'):
        result['higher_'+label] = all(x > 0 for x in changes[label]) if len(result[label]) >= 2 else None
        result['lower_'+label] = all(x < 0 for x in changes[label]) if len(result[label]) >= 2 else None
    result['direction'] = ('unknown' if not enough else 'up' if result['higher_highs'] and result['higher_lows']
                           else 'down' if result['lower_highs'] and result['lower_lows'] else 'range')
    result['available'] = enough
    pivots = highs+lows
    result['estimated_pivot_count'] = sum(p['estimated'] for p in pivots)
    result['observed_pivot_count'] = sum(p['observed'] for p in pivots)
    return result


def _levels(bars, structure):
    if len(bars) < 20:
        return dict(support=None, resistance=None, basis='insufficient twenty-bar range')
    price = bars[-1]['close']
    supports = [p['price'] for p in structure['lows'] if p['price'] < price]
    resistances = [p['price'] for p in structure['highs'] if p['price'] > price]
    return dict(estimated=bool(structure['estimated_pivot_count'] or
                ((not supports or not resistances) and any(_estimated(b) for b in bars[-20:]))),
                support=max(supports) if supports else min(b['low'] for b in bars[-20:]),
                resistance=min(resistances) if resistances else max(b['high'] for b in bars[-20:]),
                support_basis='confirmed swing' if supports else 'trailing twenty-bar low fallback',
                resistance_basis='confirmed swing' if resistances else 'trailing twenty-bar high fallback')


def _regime(ema20, ema50, slope20, slope50, atr, structure, available):
    if not available:
        return dict(regime='unknown', direction='neutral', confidence=None, strength=None,
                    evidence_votes=None)
    normalized = [slope/atr if atr else 0 for slope in (slope20, slope50)]
    votes = [('up' if slope > .03 else 'down' if slope < -.03 else 'range') for slope in normalized]
    votes.append(structure['direction'])
    if votes[:2] == ['up', 'up'] and ema20 > ema50 and votes[2] != 'down':
        regime = 'up'
    elif votes[:2] == ['down', 'down'] and ema20 < ema50 and votes[2] != 'up':
        regime = 'down'
    else:
        regime = 'range'
    confidence = sum(vote == regime for vote in votes)/3
    strength = min(1., abs(ema20-ema50)/atr) if atr else 0.
    return dict(regime=regime, direction=regime if regime != 'range' else 'neutral',
                confidence=confidence, strength=strength, evidence_votes=votes)


def _entry_context(bars, ema20, duration):
    if len(bars) < 21:
        return None
    coverage = _indicator_coverage(bars[-21:], duration)
    latest = bars[-1].get('observed_minutes', 0 if _estimated(bars[-1]) else duration//60000)
    return dict(estimated=bool(coverage['estimated_buckets']),
                lookback_observed_fraction=coverage['indicator_observed_fraction'],
                latest_observed_minutes=latest, coverage_basis=coverage['coverage_basis'],
                close=bars[-1]['close'], previous_close=bars[-2]['close'],
                last_high=bars[-1]['high'], last_low=bars[-1]['low'], ema20=ema20,
                prior_high=max(b['high'] for b in bars[-21:-1]),
                prior_low=min(b['low'] for b in bars[-21:-1]))


def _indicator_coverage(bars, duration):
    observed = expected = 0
    estimated = sum(_estimated(row) for row in bars)
    for row in bars:
        total = row.get('expected_minutes', duration//60000)
        actual = row.get('observed_minutes', 0 if _estimated(row) else total)
        if type(total) is not int or type(actual) is not int or not 0 <= actual <= total or total <= 0:
            raise ValueError('indicator coverage requires integer observed/expected minute counts')
        expected += total
        observed += actual
    fraction = observed/expected if expected else None
    return dict(estimated_buckets=estimated, observed_closed_bars=len(bars)-estimated,
                missing_indicator_minutes=expected-observed, indicator_observed_fraction=fraction,
                confidence_coverage_penalty=fraction,
                coverage_basis='minute counts when supplied; otherwise unflagged buckets assumed observed and flagged buckets fully estimated',
                warnings=['Causally filled buckets inform indicator estimates; minute coverage penalizes confidence; estimated pivots are not observed extrema.'] if estimated else [])


def read_timeframe(candles, timeframe, asof_ms, swing_count=4):
    """Read one timeframe as of its latest completed UTC bucket, without IO."""
    if type(swing_count) is not int or swing_count < 2:
        raise ValueError('swing_count must be an integer of at least two')
    bars, supplied, fresh, unavailable = _closed_bars(candles, timeframe, asof_ms)
    closes = [bar['close'] for bar in bars]
    ema20, slope20 = _ema(closes, 20)
    ema50, slope50 = _ema(closes, 50)
    atr = _atr(bars)
    structure = _pivots(bars, DURATIONS[timeframe], swing_count)
    available = fresh and slope50 is not None and atr is not None
    low = min((b['low'] for b in bars[-20:]), default=None)
    high = max((b['high'] for b in bars[-20:]), default=None)
    position = (closes[-1]-low)/(high-low) if len(bars) >= 20 and high > low else None
    coverage = _indicator_coverage(bars, DURATIONS[timeframe])
    regime = _regime(ema20, ema50, slope20, slope50, atr, structure, available)
    if regime['confidence'] is not None:
        regime['confidence'] *= coverage['confidence_coverage_penalty']
    return dict(timeframe=timeframe, asof_ms=asof_ms,
        last_closed_ms=bars[-1]['timestamp_ms']+DURATIONS[timeframe] if bars else None,
        supplied_closed_bars=supplied, contiguous_bars=len(bars), fresh=fresh, available=available,
        unavailable_buckets=unavailable,
        reason=None if available else 'stale latest closed bucket' if not fresh else 'fifty-five contiguous closed bars required for EMA50 slope',
        availability=dict(ema20=ema20 is not None, ema50=ema50 is not None,
            ema20_slope=slope20 is not None, ema50_slope=slope50 is not None, atr14=atr is not None),
        ema20=ema20, ema50=ema50, ema20_slope=slope20, ema50_slope=slope50,
        ema20_slope_atr=slope20/atr if slope20 is not None and atr else None,
        ema50_slope_atr=slope50/atr if slope50 is not None and atr else None,
        atr14=atr, range_low=low, range_high=high, position_in_range=position,
        structure=structure, support_resistance=_levels(bars, structure),
        entry_context=_entry_context(bars, ema20, DURATIONS[timeframe]), confidence_basis=CONFIDENCE_BASIS,
        slope_deadband_atr=.03, strength_basis='min(1, abs(EMA20-EMA50)/ATR14)',
        **coverage, **regime)


def _bias(cells, mode, minimum_confidence):
    names = ('1d', '4h') if mode == '1d+4h' else ('4h',)
    reads = [cells[name] for name in names]
    if any(not row['available'] for row in reads):
        return 'neutral', 'higher-timeframe indicator evidence unavailable'
    if any(row['confidence'] < minimum_confidence for row in reads):
        return 'neutral', 'higher-timeframe confidence below declared threshold'
    directions = {row['direction'] for row in reads}
    if len(directions) != 1:
        return 'neutral', 'higher-timeframe disagreement'
    return {'up': 'long', 'down': 'short', 'neutral': 'neutral'}[directions.pop()], 'higher-timeframe agreement'


def _wait(reason, **details):
    return dict(eligible=False, reason=reason, trigger=None, **details)


def _entry_ready(cells, minimum_coverage):
    if not all(name in cells and cells[name].get('available') is True for name in ('1h', '15m', '5m')):
        return False
    keys = ('close', 'previous_close', 'last_high', 'last_low', 'prior_high', 'prior_low', 'ema20')
    contexts = [cells[name].get('entry_context') for name in ('15m', '5m')]
    if not all(isinstance(row, dict) for row in contexts):
        return False
    values = [row.get(key) for row in contexts for key in keys]
    levels = cells['1h'].get('support_resistance', {})
    coverage = [cells['1h'].get('indicator_observed_fraction')] + [row.get('lookback_observed_fraction') for row in contexts]
    if any(type(value) not in (int, float) or not minimum_coverage <= value <= 1 for value in coverage):
        return False
    if any(type(row.get('latest_observed_minutes')) is not int or row['latest_observed_minutes'] <= 0 for row in contexts):
        return False
    values.extend(levels.get(name) for name in ('support', 'resistance'))
    return all(type(value) in (int, float) and math.isfinite(value) and value > 0 for value in values)


def _entry_evidence(cells, signals, minimum_coverage):
    fractions = [cells['1h']['indicator_observed_fraction']]+[row['lookback_observed_fraction'] for row in signals]
    return dict(estimated=bool(cells['1h']['support_resistance'].get('estimated') or any(row.get('estimated') for row in signals)),
                entry_observed_fraction=min(fractions), minimum_entry_coverage=minimum_coverage,
                requires_actual_observed_quote=True,
                entry_data_basis='causal indicator pattern estimate; latest signal buckets contain real observations; caller verifies actual entry quote')


def interpret_entry(cells, direction, neutral_band=(.2, .8), minimum_entry_coverage=.95):
    """Research entry condition; trigger is a price, never an executable order."""
    if direction not in ('long', 'short', 'neutral'):
        raise ValueError('direction must be long, short or neutral')
    if (not isinstance(neutral_band, (list, tuple)) or len(neutral_band) != 2 or
            any(type(value) not in (int, float) or not math.isfinite(value) for value in neutral_band) or
            not 0 < neutral_band[0] < neutral_band[1] < 1):
        raise ValueError('neutral band must lie strictly inside the range')
    if type(minimum_entry_coverage) not in (int, float) or not 0 < minimum_entry_coverage <= 1:
        raise ValueError('minimum_entry_coverage must be a finite fraction in (0, 1]')
    if not _entry_ready(cells, minimum_entry_coverage):
        return _wait('one-hour range or lower-timeframe entry evidence unavailable')
    low, high = (cells['1h']['support_resistance'].get(name) for name in ('support', 'resistance'))
    signals = [cells[name]['entry_context'] for name in ('15m', '5m')]
    price = signals[-1]['close']
    evidence = _entry_evidence(cells, signals, minimum_entry_coverage)
    if low is None or high is None or not low < price < high:
        return _wait('price must be strictly inside one-hour support/resistance')
    fresh_high = any(row['last_high'] >= row['prior_high'] for row in signals)
    fresh_low = any(row['last_low'] <= row['prior_low'] for row in signals)
    if (direction != 'short' and fresh_high) or (direction != 'long' and fresh_low):
        return _wait('fresh lower-timeframe extreme; do not chase a breakout', low=low, high=high)
    if direction == 'neutral':
        if not neutral_band[0] <= (price-low)/(high-low) <= neutral_band[1]:
            return _wait('neutral price is outside the declared central band', low=low, high=high)
        return dict(eligible=True, trigger=price, low=low, high=high, reason=None,
                    entry_basis='current price inside neutral central band; no 15m/5m breakout', **evidence)
    long = direction == 'long'
    pullbacks = [row['last_low'] <= row['ema20'] and row['close'] > row['previous_close'] if long else
                 row['last_high'] >= row['ema20'] and row['close'] < row['previous_close'] for row in signals]
    if not all(pullbacks):
        return _wait('both entry timeframes require an EMA20 pullback and close reversal', low=low, high=high)
    trigger = max(row['last_high'] for row in signals) if long else min(row['last_low'] for row in signals)
    if not low < trigger < high:
        return _wait('pullback trigger is not strictly inside one-hour range', low=low, high=high)
    return dict(eligible=True, reason=None, trigger=trigger, low=low, high=high,
                entry_basis='15m and 5m EMA20 pullback with close reversal', **evidence)


def read_chart(frames, asof_ms, bias_mode='1d+4h', minimum_confidence=.75, neutral_band=(.2, .8),
               minimum_entry_coverage=.95):
    """Return five timeframe cells, higher-timeframe bias and a pure entry check."""
    if bias_mode not in ('1d+4h', '4h-only'):
        raise ValueError('bias_mode must be 1d+4h or 4h-only')
    if type(minimum_confidence) not in (int, float) or not 0 <= minimum_confidence <= 1:
        raise ValueError('minimum_confidence must be a finite fraction')
    cells = {name: read_timeframe(frames.get(name, []), name, asof_ms) for name in DURATIONS}
    direction, reason = _bias(cells, bias_mode, minimum_confidence)
    return dict(asof_ms=asof_ms, five_cells=cells, direction=direction, bias_reason=reason,
                bias_mode=bias_mode, minimum_confidence=minimum_confidence,
                neutral_band=list(neutral_band), support_resistance=cells['1h']['support_resistance'],
                entry=interpret_entry(cells, direction, neutral_band, minimum_entry_coverage),
                minimum_entry_coverage=minimum_entry_coverage,
                methodology='declared heuristic defaults; not optimized or calibrated trading advice')
