"""Source-aware quality reports; missing candles are never manufactured."""
import argparse
from collections import Counter
import json
import time

from trader.data.constants import INTERVAL_MS, MAX_TIMESTAMP_MS
from trader.data.store import Store
from trader.data.validation import validate

ZERO_RUN_THRESHOLD = 3
MAX_GAP_RANGES = 100
CHECKS = ('duplicates', 'input_order', 'candle_gaps', 'zero_volume_runs', 'funding_gaps')
DEFINITIONS = [
    'Candle windows are completed opening timestamps in [start, end), clipped to documented listing time.',
    'Missing candles can reflect no trades or provider outages; no synthetic bars are inserted.',
    'Stored primary keys prevent duplicate rows. Source arrival order is not retained after normalization.',
    'Funding checks use recorded periods only; absent historical schedules remain unknown.',
    'Zero-volume runs require at least three adjacent stored candles; gaps break a run.',
]


def _time(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_TIMESTAMP_MS:
        raise ValueError('invalid quality timestamp')
    return value


def _window(interval, start, end):
    if interval not in INTERVAL_MS:
        raise ValueError('unsupported quality interval')
    step = INTERVAL_MS[interval]
    if _time(end) <= _time(start) or start % step or end % step:
        raise ValueError('quality window must contain aligned completed candles')
    return step


def _gaps(times, start, end, step):
    cursor, missing, ranges, total_ranges = start, 0, [], 0
    for at in [*times, end]:
        if at > cursor:
            missing += (at - cursor) // step
            total_ranges += 1
            if len(ranges) < MAX_GAP_RANGES:
                ranges.append([cursor, at])
        cursor = at + step
    return dict(status='unknown' if not times else 'warn' if missing else 'pass',
                observed=len(times), expected=(end-start)//step, missing=missing,
                ranges=ranges, total_ranges=total_ranges)


def _zero_runs(rows, step):
    run, longest, count, previous = 0, 0, 0, None
    for row in rows:
        at = row['time_ms']
        if row['volume'] == 0:
            run = run + 1 if previous is not None and at-previous == step else 1
            count += run == ZERO_RUN_THRESHOLD
            longest = max(longest, run)
        else:
            run = 0
        previous = at
    return dict(status='warn' if count else 'pass' if rows else 'unknown',
                count=count, longest=longest, threshold=ZERO_RUN_THRESHOLD)


def assess_candles(rows, interval, start, end):
    """Inspect one raw series before sorting; callers retain responsibility for provenance."""
    step = _window(interval, start, end)
    seen, duplicate, conflicting, backwards, previous, symbols = {}, 0, 0, 0, None, set()
    for row in rows:
        validate('klines', row)
        at = row['time_ms']
        symbols.add(row['symbol'])
        if row['interval'] != interval or not start <= at < end or len(symbols) > 1:
            raise ValueError('candle is outside the requested series')
        backwards += previous is not None and at < previous
        if at in seen:
            duplicate += 1
            conflicting += row != seen[at]
        else:
            seen[at] = dict(row)
        previous = at
    ordered = [seen[at] for at in sorted(seen)]
    return dict(duplicates=dict(status='fail' if duplicate else 'pass', count=duplicate,
                                conflicting=conflicting),
                input_order=dict(status='fail' if backwards else 'pass', count=backwards),
                candle_gaps=_gaps(sorted(seen), start, end, step),
                zero_volume_runs=_zero_runs(ordered, step))


def assess_funding(rows):
    ordered = sorted(rows, key=lambda row: _time(row['time_ms']))
    missing, unknown, irregular, deltas = 0, 0, 0, Counter()
    for left, right in zip(ordered, ordered[1:]):
        delta = right['time_ms'] - left['time_ms']
        if delta <= 0:
            raise ValueError('duplicate funding event timestamp')
        deltas[delta] += 1
        period = right.get('period_ms')
        if period is None:
            unknown += 1
        elif isinstance(period, bool) or not isinstance(period, int) or period <= 0:
            raise ValueError('invalid funding period')
        else:
            missing += max(0, delta // period - 1)
            irregular += delta % period != 0
    status = 'warn' if missing or irregular else 'unknown' if unknown or len(ordered) < 2 else 'pass'
    return dict(status=status, events=len(ordered), missing_minimum=missing,
                irregular_intervals=irregular, unknown_periods=unknown,
                observed_deltas_ms={str(k): v for k, v in sorted(deltas.items())},
                boundary_coverage='unknown; settlement schedule outside observed events is not inferred')


def _series(store, symbol, interval, start, end):
    step = INTERVAL_MS[interval]
    metadata = store.query('SELECT listed_at_ms FROM universe WHERE symbol=?', (symbol,))
    listed = metadata[0]['listed_at_ms'] if metadata else None
    effective = max(start, ((listed+step-1)//step)*step) if listed is not None else start
    if effective >= end:
        return {name: dict(status='unknown', reason='no_completed_bars',
                           effective_start_ms=effective) for name in CHECKS}
    rows = store.query('SELECT * FROM klines WHERE symbol=? AND interval=? '
                       'AND time_ms>=? AND time_ms<? ORDER BY time_ms',
                       (symbol, interval, effective, end))
    result = assess_candles(rows, interval, effective, end)
    result['input_order'] = dict(status='unknown', reason='source arrival order not retained')
    result['duplicates']['basis'] = 'stored primary-key uniqueness; raw import may differ'
    result['candle_gaps']['effective_start_ms'] = effective
    funding = store.query('SELECT * FROM funding WHERE symbol=? AND time_ms>=? '
                          'AND time_ms<? ORDER BY time_ms', (symbol, effective, end))
    result['funding_gaps'] = assess_funding(funding)
    return result


def _record(name, symbol, interval, start, end, checked, details):
    return dict(check_name=name, symbol=symbol, interval=interval, start_ms=start,
                end_ms=end, checked_at_ms=checked, status=details['status'],
                details_json=json.dumps(details, allow_nan=False, sort_keys=True))


def _report(records):
    records = sorted(records, key=lambda row: (row['interval'], row['symbol'],
                     CHECKS.index(row['check_name'])))
    checks = [dict({k: v for k, v in row.items() if k != 'details_json'},
                   details=json.loads(row['details_json'])) for row in records]
    return dict(schema=1, mode='public-data-quality', definitions=DEFINITIONS,
                status_counts=dict(Counter(row['status'] for row in records)), checks=checks)


def generate(store, start_ms, end_ms, symbols, intervals=('1m', '1h'), now_ms=None):
    checked = int(time.time() * 1000) if now_ms is None else _time(now_ms)
    if checked < end_ms or not symbols or not intervals:
        raise ValueError('quality requires symbols and a completed window')
    records = []
    for interval in dict.fromkeys(intervals):
        _window(interval, start_ms, end_ms)
        for symbol in dict.fromkeys(symbols):
            if not isinstance(symbol, str) or not symbol:
                raise ValueError('invalid quality symbol')
            checks = _series(store, symbol, interval, start_ms, end_ms)
            records.extend(_record(name, symbol, interval, start_ms, end_ms, checked, details)
                           for name, details in checks.items())
    with store.transaction():
        store.upsert('data_quality', records)
    return _report(records)


def latest(store):
    placeholders = ','.join('?' for _ in CHECKS)
    rows = store.query(
        f'SELECT * FROM data_quality WHERE check_name IN ({placeholders}) '
        'AND checked_at_ms=(SELECT MAX(checked_at_ms) FROM data_quality '
        f'WHERE check_name IN ({placeholders})) ORDER BY interval, symbol, check_name',
        (*CHECKS, *CHECKS))
    return _report(rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    parser.add_argument('--start-ms', type=int, required=True)
    parser.add_argument('--end-ms', type=int, required=True)
    parser.add_argument('--symbols', nargs='+', required=True)
    parser.add_argument('--intervals', nargs='+', choices=tuple(INTERVAL_MS), default=list(INTERVAL_MS))
    args = parser.parse_args(argv)
    try:
        with Store(args.database) as store:
            report = generate(store, args.start_ms, args.end_ms, args.symbols, args.intervals)
        print(json.dumps(report, allow_nan=False))
        return 0
    except (ValueError, OSError) as error:
        print(json.dumps(dict(status='failed', category=type(error).__name__)))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
