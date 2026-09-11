"""Bounded preparation of closed minute candles without invented observations.

``asof_ms`` is exclusive: its current minute is never included. Candle timestamps
are minute-open times in milliseconds (timestamp_ms or time_ms). Coverage counts
unique actual candles only; exactly 95% qualifies. Missing minutes get flat OHLC
at the last earlier actual close with zero volume and turnover. Observed volume
and turnover keep their supplied units; preparation performs no currency conversion. An unseeded leading gap remains
None, making indicators invalid, even when the coverage threshold qualifies.

Synthetic rows AND the first actual row after a gap have crossing_eligible=False.
Crossing consumers must reset their anchor on these rows, never bridge a gap.
This is conservative: it also omits the first observed minute after the gap from
crossing estimates. Flags never turn a supplied synthetic row into an observation.
"""
import math
from bisect import bisect_left
from copy import deepcopy
from itertools import accumulate


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
    for key in ('volume','turnover'):
        if key in bar:
            value = float(bar[key])
            if isinstance(bar[key],bool) or not math.isfinite(value) or value < 0:
                raise ValueError(f'{key} must be finite and nonnegative')
            result[key] = value
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
                         'low':previous_close,'close':previous_close,'volume':0.,'turnover':0.,
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


# These immutable values are created only by the validating factories below.
# Plain caller-provided dictionaries never qualify for the indicator fast path.
_VALIDATED_TOKEN = object()


def _immutable(*args, **kwargs):
    raise TypeError('Validated candle values are immutable')


def _immutable_copy(self, memo=None):
    return self


class _FrozenDict(dict):
    __slots__ = ('_sealed',)

    def __init__(self, value=()):
        if hasattr(self,'_sealed'):
            _immutable()
        dict.__init__(self,value)
        object.__setattr__(self,'_sealed',True)

    __copy__ = __deepcopy__ = _immutable_copy
    __setattr__ = _immutable
    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _immutable


class _FrozenList(list):
    __slots__ = ('_sealed',)

    def __init__(self, value=()):
        if hasattr(self,'_sealed'):
            _immutable()
        list.__init__(self,value)
        object.__setattr__(self,'_sealed',True)

    __copy__ = __deepcopy__ = _immutable_copy
    __setattr__ = _immutable
    __setitem__ = __delitem__ = append = clear = extend = insert = pop = remove = reverse = sort = __iadd__ = __imul__ = _immutable


def _freeze(value):
    if isinstance(value,(_FrozenDict,_FrozenList)):
        return value
    if isinstance(value,dict):
        return _FrozenDict({key:_freeze(item) for key,item in value.items()})
    if isinstance(value,list):
        return _FrozenList(_freeze(item) for item in value)
    return value


def _snapshot(value):
    # Error fallbacks can themselves be combined; copy frozen containers by value.
    if isinstance(value,dict):
        return {key:_snapshot(item) for key,item in value.items()}
    if isinstance(value,list):
        return [_snapshot(item) for item in value]
    if isinstance(value,tuple):
        return tuple(_snapshot(item) for item in value)
    return deepcopy(value)


class PreparedHistory(_FrozenDict):
    """Immutable, JSON-compatible output authenticated by prepare_validated."""
    __slots__ = ('_identity','_observed_rows','_membership','_factory_marker')

    def __init__(self, value, identity=None, observed_rows=(), membership=None, *, _token=None):
        if _token is not _VALIDATED_TOKEN:
            raise TypeError('Use prepare_validated to construct PreparedHistory')
        dict.__init__(self,_freeze(value))
        object.__setattr__(self,'_identity',identity)
        object.__setattr__(self,'_factory_marker',_VALIDATED_TOKEN)
        object.__setattr__(self,'_observed_rows',tuple(observed_rows))
        object.__setattr__(self,'_membership',tuple(membership) if membership is not None else None)
        object.__setattr__(self,'_sealed',True)

    @property
    def observed_bars(self):
        return self._observed_rows

    __setattr__ = _immutable


def is_prepared_history(value):
    """Cheap factory-provenance check, including bypassed __init__ rejection.

    This guards optimization contracts, not hostile in-process Python: explicit
    base-dict mutation of an already issued object can bypass Python overrides.
    """
    return type(value) is PreparedHistory and getattr(value,'_factory_marker',None) is _VALIDATED_TOKEN


class ValidatedHistory:
    """Immutable canonical rows; window() shares validation and excludes older rows.

    Invalid histories retain an isolated raw copy for exact reference preparation,
    including error ordering and the rule that malformed future OHLC is ignored.
    """
    __slots__ = ('_records','_times','_prefix','_begin','_end','_identity','_raw','_original')

    def __init__(self, records=(), counts=(), raw=None, original=None, *, _token=None):
        if _token is not _VALIDATED_TOKEN:
            raise TypeError('Use validate_history or combine_histories')
        object.__setattr__(self,'_records',tuple(records))
        object.__setattr__(self,'_times',tuple(row['timestamp_ms'] for row in records))
        object.__setattr__(self,'_prefix',(0,*accumulate(counts)))
        object.__setattr__(self,'_begin',0)
        object.__setattr__(self,'_end',len(records))
        object.__setattr__(self,'_identity',object())
        object.__setattr__(self,'_raw',raw)
        object.__setattr__(self,'_original',tuple(records) if original is None else tuple(original))

    __copy__ = __deepcopy__ = _immutable_copy
    __setattr__ = _immutable

    @property
    def validated(self):
        return self._raw is None

    @property
    def rows(self):
        return self._records[self._begin:self._end] if self.validated else self._raw

    def __iter__(self):
        return iter(self.rows)

    def __len__(self):
        return len(self.rows)

    def window(self, start_ms, end_ms):
        if type(start_ms) is not int or type(end_ms) is not int or start_ms > end_ms:
            raise ValueError('Window bounds must be ordered integer milliseconds')
        if not self.validated:
            selected = []
            for row in self._raw:
                try:
                    timestamp = _timestamp(row)
                except (KeyError,TypeError,ValueError,OverflowError):
                    selected.append(row)
                    continue
                if start_ms <= timestamp < end_ms:
                    selected.append(row)
            return validate_history(selected)
        view = object.__new__(ValidatedHistory)
        for key in self.__slots__:
            object.__setattr__(view,key,getattr(self,key))
        object.__setattr__(view,'_begin',bisect_left(self._times,start_ms,self._begin,self._end))
        object.__setattr__(view,'_end',bisect_left(self._times,end_ms,view._begin,self._end))
        return view

    def _expanded(self):
        if not self.validated:
            return self._raw
        if self._begin == self._end:
            return ()
        low,high = self._times[self._begin],self._times[self._end-1]
        return tuple(row for row in self._original if low <= row['timestamp_ms'] <= high)


def validate_history(bars):
    """Validate/canonicalize one immutable block once, retaining duplicate counts."""
    raw = tuple(bars)
    observed, duplicates, original = {}, {}, []
    try:
        for row in raw:
            timestamp = _timestamp(row)
            if type(row.get('synthetic',False)) is not bool:
                raise ValueError('synthetic flag must be boolean')
            if row.get('synthetic',False):
                continue
            canonical = _FrozenDict(_actual_bar(row,timestamp))
            if timestamp in observed:
                if observed[timestamp] != canonical:
                    raise ValueError('Conflicting duplicate')
                duplicates[timestamp] = duplicates.get(timestamp,0)+1
            observed[timestamp] = canonical
            original.append(canonical)
    except (KeyError,TypeError,ValueError,OverflowError):
        return ValidatedHistory(raw=tuple(_freeze(_snapshot(row)) for row in raw),_token=_VALIDATED_TOKEN)
    times = sorted(observed)
    return ValidatedHistory(tuple(observed[time] for time in times),
                            tuple(duplicates.get(time,0) for time in times),original=original,_token=_VALIDATED_TOKEN)


def combine_histories(histories):
    """Combine validated day blocks without repeating numeric/schema validation."""
    blocks = tuple(histories)
    if any(type(block) is not ValidatedHistory for block in blocks):
        raise TypeError('combine_histories requires validated history objects')
    if any(not block.validated for block in blocks):
        return validate_history(row for block in blocks for row in block._expanded())
    rows, counts = {}, {}
    for block in blocks:
        for index in range(block._begin,block._end):
            row = block._records[index]
            time = row['timestamp_ms']
            count = block._prefix[index+1]-block._prefix[index]
            if time in rows:
                if rows[time] != row:
                    return validate_history(value for part in blocks for value in part._expanded())
                count += counts[time]+1
            rows[time], counts[time] = row, count
    times = sorted(rows)
    return ValidatedHistory(tuple(rows[time] for time in times),tuple(counts[time] for time in times),
                            original=tuple(row for block in blocks for row in block._expanded()),
                            _token=_VALIDATED_TOKEN)


def prepare_validated(history, asof_ms, window_minutes=MAX_WINDOW_MINUTES, prior_seed=None, previous=None):
    """Exact prepare output with cached validation and safe forward-window reuse.

    Reuse requires the same immutable history and actual-candle membership in
    the overlapping time span. Leading candles are
    rebuilt until an actual observation establishes the current window's seed;
    an older prepared window can never backfill a newly unseeded leading gap.
    """
    if type(history) is not ValidatedHistory:
        raise TypeError('prepare_validated requires ValidatedHistory')
    def reference():
        result = prepare(history._expanded(),asof_ms,window_minutes,prior_seed)
        observed = tuple(_freeze(row) for row in result['bars'] if not row.get('synthetic'))
        return PreparedHistory(result,observed_rows=observed,_token=_VALIDATED_TOKEN)
    if (not history.validated or type(window_minutes) is not int or not 1 <= window_minutes <= MAX_WINDOW_MINUTES
            or type(asof_ms) is not int or asof_ms < 0):
        return reference()
    end = asof_ms//MINUTE_MS*MINUTE_MS
    start = end-window_minutes*MINUTE_MS
    records, times = history._records, history._times
    first = bisect_left(times,start,history._begin,history._end)
    stop = bisect_left(times,end,first,history._end)
    seed = records[first-1] if first > history._begin else None
    try:
        if prior_seed is not None:
            seed_time = _timestamp(prior_seed)
            if seed_time >= start or prior_seed.get('synthetic',False):
                return reference()
            if seed is None or seed_time >= seed['timestamp_ms']:
                candidate = _actual_bar(prior_seed,seed_time)
                if seed is not None and seed_time == seed['timestamp_ms'] and candidate != seed:
                    return reference()
                seed = candidate
    except (KeyError,TypeError,ValueError,OverflowError):
        return reference()
    previous_close = seed['close'] if seed is not None else None
    previous_actual = seed['timestamp_ms'] if seed is not None else None
    reuse = (is_prepared_history(previous) and getattr(previous,'_identity',None) is history._identity
             and getattr(previous,'_membership',None) is not None
             and previous['coverage']['window_start_ms'] <= start
             and previous['coverage']['window_end_ms'] <= end)
    prior_start = previous['coverage']['window_start_ms'] if reuse else 0
    prior_end = previous['coverage']['window_end_ms'] if reuse else 0
    if reuse:
        # Shared storage does not imply shared membership: a view may expose or
        # hide actual candles inside a previously prepared timestamp window.
        overlap_first = bisect_left(times,max(start,prior_start))
        overlap_stop = bisect_left(times,min(end,prior_end))
        old_first,old_stop = previous._membership
        old_span = (max(old_first,overlap_first),min(old_stop,overlap_stop))
        new_span = (max(first,overlap_first),min(stop,overlap_stop))
        old_span = old_span if old_span[0] < old_span[1] else None
        new_span = new_span if new_span[0] < new_span[1] else None
        reuse = old_span == new_span
    dense, missing_seed, established, cursor = [], False, False, first
    for timestamp in range(start,end,MINUTE_MS):
        observed = cursor < stop and times[cursor] == timestamp
        cached = (reuse and established and prior_start <= timestamp < prior_end)
        if observed:
            record = records[cursor]
            value = (previous['bars'][(timestamp-prior_start)//MINUTE_MS] if cached else
                     _FrozenDict(dict(record,synthetic=False,crossing_eligible=previous_actual == timestamp-MINUTE_MS)))
            previous_close, previous_actual = record['close'], timestamp
            cursor += 1
            established = True
        else:
            if cached:
                value = previous['bars'][(timestamp-prior_start)//MINUTE_MS]
            else:
                value = dict(timestamp_ms=timestamp,open=previous_close,high=previous_close,
                             low=previous_close,close=previous_close,volume=0.,turnover=0.,
                             synthetic=True,crossing_eligible=False)
                if previous_close is None:
                    value['unfillable'] = True
                value = _FrozenDict(value)
            missing_seed |= previous_close is None
            previous_actual = None
        dense.append(value)
    actual = stop-first
    eligible = actual*100 >= window_minutes*95
    coverage = dict(actual=actual,expected=window_minutes,fraction=actual/window_minutes,
                    eligible=eligible,indicators_valid=not missing_seed,
                    duplicates=history._prefix[stop]-history._prefix[first],
                    window_start_ms=start,window_end_ms=end,threshold=.95,
                    synthetic=window_minutes-actual,seed_timestamp_ms=seed['timestamp_ms'] if seed else None)
    reason = ('Leading gap has no earlier actual seed; indicators cannot use future backfill' if missing_seed else
              f'Observed candle coverage {actual}/{window_minutes} is below 95%' if not eligible else '')
    return PreparedHistory(dict(bars=dense,coverage=coverage,valid=eligible and not missing_seed,reason=reason),
                           history._identity,records[first:stop],membership=(first,stop),_token=_VALIDATED_TOKEN)
