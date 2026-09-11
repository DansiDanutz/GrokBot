"""Closed UTC OHLCV buckets from actual one-minute candles, without execution data.

Timestamps are bucket-open milliseconds. ``asof_ms`` is exclusive; only buckets
whose end is reached are emitted. Explicit ``start_ms`` selects the first UTC
bucket beginning at or after that instant; an implicit start includes the bucket
containing the earliest supplied observation. No OHLC is invented for incomplete
buckets by default. Optional past-close filling is exclusively for indicators:
every affected bucket remains non-observed and ineligible for crossings.

Volume is summed in its original contract units using decimal summation. Neither
quantity multipliers nor turnover conversion are applied. Synthetic input rows
are discarded and cannot seed fills. A comparison with stored hourly candles
uses exact numeric equality unless the caller explicitly supplies tolerances.
"""
from decimal import Decimal
import math

from trader.strategies.candle_coverage import MINUTE_MS, _actual_bar, _timestamp


TIMEFRAME_MINUTES = {'5m': 5, '15m': 15, '1h': 60, '4h': 240, '1d': 1440}
_OHLCV = ('open', 'high', 'low', 'close', 'volume')


def _options(asof_ms, start_ms, indicator_fill):
    if type(asof_ms) is not int or asof_ms < 0:
        raise ValueError('asof_ms must be a nonnegative integer in milliseconds')
    if start_ms is not None:
        if type(start_ms) is not int or not 0 <= start_ms <= asof_ms or start_ms % MINUTE_MS:
            raise ValueError('start_ms must be a minute boundary at or before asof_ms')
    if type(indicator_fill) is not bool:
        raise ValueError('indicator_fill must be boolean')


def _observation(raw, duration, asof_ms):
    try:
        timestamp = _timestamp(raw)
        if 'time_ms' in raw and _timestamp({'time_ms': raw['time_ms']}) != timestamp:
            raise ValueError('Conflicting timestamp_ms and time_ms')
        if timestamp % duration:
            raise ValueError('Candle timestamp is not aligned to its UTC timeframe')
        if timestamp + duration > asof_ms:
            return None
        for key in ('synthetic', 'indicator_only', 'observed'):
            if key in raw and type(raw[key]) is not bool:
                raise ValueError(f'{key} flag must be boolean')
        if raw.get('synthetic', False) or raw.get('indicator_only', False):
            return None
        if raw.get('observed') is False:
            raise ValueError('Non-observed input must be explicitly synthetic or indicator_only')
        if 'volume' not in raw:
            raise ValueError('Contract volume is required')
        return _actual_bar(raw, timestamp)
    except (KeyError, TypeError, OverflowError) as exc:
        raise ValueError(f'Malformed candle: {exc}') from exc


def _observations(bars, asof_ms, duration=MINUTE_MS):
    observations = {}
    for raw in bars:
        value = _observation(raw, duration, asof_ms)
        if value is None:
            continue
        timestamp = value['timestamp_ms']
        if timestamp in observations and observations[timestamp] != value:
            raise ValueError(f'Conflicting duplicate candle at {timestamp}')
        observations[timestamp] = value
    return observations


def _window(observations, duration, asof_ms, start_ms):
    if start_ms is None:
        if not observations:
            return asof_ms, asof_ms
        start = min(observations) // duration * duration
    else:
        start = (start_ms + duration - 1) // duration * duration
    return start, asof_ms // duration * duration


def _seed(observations, prior_seed, start, asof_ms):
    candidates = [value for time, value in observations.items() if time < start]
    if prior_seed is not None:
        value = _observation(prior_seed, MINUTE_MS, asof_ms)
        if value is None or value['timestamp_ms'] >= start:
            raise ValueError('prior_seed must be an actual candle strictly before the window')
        duplicate = observations.get(value['timestamp_ms'])
        if duplicate is not None and duplicate != value:
            raise ValueError('Conflicting duplicate prior_seed candle')
        candidates.append(value)
    return max(candidates, key=lambda bar: bar['timestamp_ms']) if candidates else None


def _sum_volume(bars):
    return float(sum((Decimal(str(bar['volume'])) for bar in bars), Decimal(0)))


def _bucket(observations, timestamp, minutes, previous_close, indicator_fill):
    values, actual = [], []
    for time in range(timestamp, timestamp + minutes * MINUTE_MS, MINUTE_MS):
        value = observations.get(time)
        if value is not None:
            actual.append(value)
            values.append(value)
            previous_close = value['close']
        elif indicator_fill and previous_close is not None:
            values.append(dict.fromkeys(('open', 'high', 'low', 'close'), previous_close)
                          | {'volume': 0.})
    missing = minutes - len(actual)
    fillable = len(values) == minutes
    result = {'timestamp_ms': timestamp, 'end_ms': timestamp + minutes * MINUTE_MS,
              'closed': True, 'observed': missing == 0, 'synthetic': missing > 0,
              'indicator_only': missing > 0, 'observed_minutes': len(actual),
              'expected_minutes': minutes, 'missing_minutes': missing,
              'coverage_fraction': len(actual) / minutes,
              'observed_volume': _sum_volume(actual), 'crossing_eligible': False,
              'unfillable': not fillable, 'source': 'aggregated_observed_1m'}
    result.update(dict.fromkeys(_OHLCV))
    if fillable:
        result.update(open=values[0]['open'], high=max(bar['high'] for bar in values),
                      low=min(bar['low'] for bar in values), close=values[-1]['close'],
                      volume=_sum_volume(values))
    return result, previous_close


def _aggregate(observations, timeframe, asof_ms, start_ms, indicator_fill, prior_seed):
    if timeframe not in TIMEFRAME_MINUTES:
        raise ValueError(f'Unsupported timeframe: {timeframe}')
    minutes = TIMEFRAME_MINUTES[timeframe]
    duration = minutes * MINUTE_MS
    start, end = _window(observations, duration, asof_ms, start_ms)
    seed = _seed(observations, prior_seed, start, asof_ms)
    previous_close = seed['close'] if seed else None
    previous_observed = False
    rows = []
    for timestamp in range(start, end, duration):
        row, previous_close = _bucket(observations, timestamp, minutes,
                                      previous_close, indicator_fill)
        row['crossing_eligible'] = row['observed'] and previous_observed
        previous_observed = row['observed']
        rows.append(row)
    return rows


def aggregate_candles(minute_bars, timeframe, asof_ms, *, start_ms=None,
                      indicator_fill=False, prior_seed=None):
    """Return one timeframe's closed buckets; invalid observations raise ValueError."""
    _options(asof_ms, start_ms, indicator_fill)
    observed = _observations(minute_bars, asof_ms)
    return _aggregate(observed, timeframe, asof_ms, start_ms, indicator_fill, prior_seed)


def build_timeframes(minute_bars, asof_ms, *, start_ms=None,
                     indicator_fill=False, prior_seed=None):
    """Return 5m/15m/1h/4h/1d lists after validating the supplied history once."""
    _options(asof_ms, start_ms, indicator_fill)
    observed = _observations(minute_bars, asof_ms)
    return {name: _aggregate(observed, name, asof_ms, start_ms, indicator_fill, prior_seed)
            for name in TIMEFRAME_MINUTES}


def _tolerance(value, name):
    if isinstance(value, bool):
        raise ValueError(f'{name} must be finite and nonnegative')
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f'{name} must be finite and nonnegative')
    return number


def _comparison_row(timestamp, derived, stored, price_tolerance, volume_tolerance):
    reasons = []
    if derived is None or not derived['observed']:
        reasons.append('missing_source_minutes')
    if stored is None:
        reasons.append('missing_stored_candle')
    result = {'timestamp_ms': timestamp, 'status': 'gap' if reasons else 'match',
              'reasons': reasons, 'differences': {}, 'derived': derived, 'stored': stored}
    if reasons:
        return result
    for key in _OHLCV:
        delta = Decimal(str(derived[key])) - Decimal(str(stored[key]))
        tolerance = volume_tolerance if key == 'volume' else price_tolerance
        if abs(delta) > Decimal(str(tolerance)):
            result['differences'][key] = {'derived': derived[key], 'stored': stored[key],
                                          'delta': float(delta)}
    if result['differences']:
        result['status'] = 'mismatch'
    return result


def compare_kucoin_1h(minute_bars, stored_1h, asof_ms, *, start_ms=None,
                      price_tolerance=0, volume_tolerance=0):
    """Report hourly matches, differences, and source/stored gaps without filling.

    Tolerances are absolute and echoed in the result. ``all_match`` requires at
    least one complete comparison and zero missing or mismatching buckets.
    """
    _options(asof_ms, start_ms, False)
    price_tolerance = _tolerance(price_tolerance, 'price_tolerance')
    volume_tolerance = _tolerance(volume_tolerance, 'volume_tolerance')
    observed = _observations(minute_bars, asof_ms)
    stored = _observations(stored_1h, asof_ms, 60 * MINUTE_MS)
    if start_ms is None and (observed or stored):
        start_ms = min(set(observed) | set(stored)) // (60 * MINUTE_MS) * 60 * MINUTE_MS
    derived = {bar['timestamp_ms']: bar for bar in
               _aggregate(observed, '1h', asof_ms, start_ms, False, None)}
    timestamps = sorted(set(derived) | {time for time in stored
                                        if start_ms is None or time >= start_ms})
    rows = [_comparison_row(time, derived.get(time), stored.get(time),
                            price_tolerance, volume_tolerance) for time in timestamps]
    counts = {name: sum(row['status'] == status for row in rows)
              for name, status in (('matches', 'match'), ('mismatches', 'mismatch'), ('gaps', 'gap'))}
    return dict(counts, rows=rows, all_match=bool(rows) and counts['matches'] == len(rows),
                asof_ms=asof_ms, price_tolerance=price_tolerance,
                volume_tolerance=volume_tolerance, volume_units='supplied_contract_units')
