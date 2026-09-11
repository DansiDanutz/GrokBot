"""Bounded preparation of closed minute candles without invented observations.

``asof_ms`` is exclusive: its current minute is never included. Candle timestamps
are minute-open times in milliseconds (timestamp_ms or time_ms). Coverage counts
unique actual candles only; exactly 95% qualifies. Missing minutes get flat OHLC
at the last earlier actual close and zero volume. An unseeded leading gap remains
None, making indicators invalid, even when the coverage threshold qualifies.

Synthetic rows AND the first actual row after a gap have crossing_eligible=False.
Crossing consumers must reset their anchor on these rows, never bridge a gap.
This is conservative: it also omits the first observed minute after the gap from
crossing estimates. Flags never turn a supplied synthetic row into an observation.
"""
import math


MINUTE_MS = 60000
MAX_WINDOW_MINUTES = 10080


def _timestamp(bar):
    if 'timestamp_ms' not in bar and 'time_ms' not in bar:
        raise ValueError('Candle timestamp_ms or time_ms is required')
    key = 'timestamp_ms' if 'timestamp_ms' in bar else 'time_ms'
    value = bar[key]
    if isinstance(value,bool):
        raise ValueError('Candle timestamp must be an integer millisecond minute boundary')
    value = float(value)
    if not math.isfinite(value) or not value.is_integer() or value < 0 or value % MINUTE_MS:
        raise ValueError('Candle timestamp must be an integer millisecond minute boundary')
    return int(value)


def _actual_bar(bar, timestamp):
    result = {'timestamp_ms':timestamp}
    for key in ('open','high','low','close'):
        if isinstance(bar[key],bool):
            raise ValueError('OHLC must be finite and positive')
        value = float(bar[key])
        if not math.isfinite(value) or value <= 0:
            raise ValueError('OHLC must be finite and positive')
        result[key] = value
    if not result['low'] <= min(result['open'],result['close']) <= max(result['open'],result['close']) <= result['high']:
        raise ValueError('Invalid OHLC ordering')
    if 'volume' in bar:
        volume = float(bar['volume'])
        if isinstance(bar['volume'],bool) or not math.isfinite(volume) or volume < 0:
            raise ValueError('Volume must be finite and nonnegative')
        result['volume'] = volume
    return result


def prepare(bars, asof_ms, window_minutes=MAX_WINDOW_MINUTES, prior_seed=None):
    """Return {bars, coverage, valid, reason}; malformed input fails explicitly.

    An optional actual prior_seed must precede the window. A supplied older actual
    bar can also seed the leading gap; neither contributes to window coverage.
    Identical duplicate observations count once; conflicting duplicates reject.
    Output has at most 10080 rows and does not mutate the input.
    """
    result = {'bars':[],'coverage':{'actual':0,'expected':window_minutes,'fraction':0.,
                                  'eligible':False,'indicators_valid':False,'duplicates':0},
              'valid':False,'reason':''}
    observed = {}
    try:
        if type(window_minutes) is not int or not 1 <= window_minutes <= MAX_WINDOW_MINUTES:
            raise ValueError('window_minutes must be an integer from 1 to 10080')
        if type(asof_ms) is not int or asof_ms < 0:
            raise ValueError('asof_ms must be a nonnegative integer in milliseconds')
        end = asof_ms//MINUTE_MS*MINUTE_MS
        start = end-window_minutes*MINUTE_MS
        result['coverage'].update(window_start_ms=start,window_end_ms=end,threshold=.95)
        seed_candidates = []
        latest_seed_timestamp = None
        if prior_seed is not None:
            seed_time = _timestamp(prior_seed)
            if seed_time >= start or prior_seed.get('synthetic',False):
                raise ValueError('prior_seed must be an actual candle strictly before the window')
            seed_candidates = [prior_seed]
            latest_seed_timestamp = seed_time
        for raw in bars:
            timestamp = _timestamp(raw)
            if timestamp >= end:
                continue
            if type(raw.get('synthetic',False)) is not bool:
                raise ValueError('synthetic flag must be boolean')
            if raw.get('synthetic',False):
                continue
            if timestamp < start:
                if latest_seed_timestamp is None or timestamp > latest_seed_timestamp:
                    latest_seed_timestamp = timestamp
                    seed_candidates = [raw]
                elif timestamp == latest_seed_timestamp:
                    seed_candidates.append(raw)
                continue
            value = _actual_bar(raw,timestamp)
            if timestamp in observed:
                if observed[timestamp] != value:
                    raise ValueError(f'Conflicting duplicate candle at {timestamp}')
                result['coverage']['duplicates'] += 1
            observed[timestamp] = value
        seed = None
        for raw in seed_candidates:
            candidate = _actual_bar(raw,latest_seed_timestamp)
            if seed is not None and seed != candidate:
                raise ValueError(f'Conflicting duplicate seed candle at {latest_seed_timestamp}')
            seed = candidate
        previous_close = seed['close'] if seed is not None else None
        previous_actual_time = seed['timestamp_ms'] if seed is not None else None
        missing_seed = False
        dense = []
        for index in range(window_minutes):
            timestamp = start+index*MINUTE_MS
            if timestamp in observed:
                value = dict(observed[timestamp])
                value.update(synthetic=False,crossing_eligible=previous_actual_time == timestamp-MINUTE_MS)
                previous_close = value['close']
                previous_actual_time = timestamp
            else:
                value = {'timestamp_ms':timestamp,'open':previous_close,'high':previous_close,
                         'low':previous_close,'close':previous_close,'volume':0.,
                         'synthetic':True,'crossing_eligible':False}
                if previous_close is None:
                    missing_seed = True
                    value['unfillable'] = True
                previous_actual_time = None
            dense.append(value)
        actual = len(observed)
        coverage_eligible = actual*100 >= window_minutes*95
        result['bars'] = dense
        result['coverage'].update(actual=actual,fraction=actual/window_minutes,
                                  eligible=coverage_eligible,indicators_valid=not missing_seed,
                                  synthetic=window_minutes-actual,seed_timestamp_ms=seed['timestamp_ms'] if seed else None)
        result['valid'] = coverage_eligible and not missing_seed
        if missing_seed:
            result['reason'] = 'Leading gap has no earlier actual seed; indicators cannot use future backfill'
        elif not coverage_eligible:
            result['reason'] = f'Observed candle coverage {actual}/{window_minutes} is below 95%'
    except (KeyError,TypeError,ValueError,OverflowError) as exc:
        result['coverage'].update(actual=len(observed),eligible=False,indicators_valid=False)
        if type(window_minutes) is int and window_minutes > 0:
            result['coverage']['fraction'] = len(observed)/window_minutes
        result['reason'] = str(exc)
    return result
