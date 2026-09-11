"""Resumable public-only OHLCV collection. Never synthesizes missing candles."""
import argparse
import json
import sys
import time

from trader.data.kucoin_public import INTERVALS, MAX_CANDLES, PublicClient, integer, select_contracts

DAY_MS = 86400000


def run(store, client, start_ms, end_ms, symbols=None, now_ms=None,
        max_pages=None, progress=None):
    """Collect both intervals; complete means queried, gap_free means populated."""
    integer(start_ms); integer(end_ms)
    if end_ms <= start_ms:
        raise ValueError('invalid backfill range')
    if max_pages is not None and (isinstance(max_pages, bool) or max_pages < 1):
        raise ValueError('max_pages must be positive')
    now_ms = int(time.time() * 1000) if now_ms is None else integer(now_ms)
    effective_end = min(end_ms, now_ms)
    if effective_end <= start_ms:
        raise ValueError("no available backfill window")
    begun = time.monotonic()
    contracts = select_contracts(client.contracts(), symbols)
    if not contracts:
        raise ValueError("empty active contract universe")
    report = {'source': 'kucoin_public_classic', 'start_ms': start_ms,
              'end_ms': end_ms, 'pages': 0, 'written_rows': 0, 'series': []}
    for contract in contracts:
        for interval in INTERVALS:
            series = _collect_series(store, client, contract, interval, start_ms,
                                     effective_end, max_pages, report, progress)
            report['series'].append(series)
    report['complete'] = all(row['queried_complete'] for row in report['series'])
    report['gap_free'] = report['complete'] and all(row['gap_free'] for row in report['series'])
    report['elapsed_seconds'] = round(time.monotonic() - begun, 3)
    report['universe_count'] = len(contracts)
    report['limitations'] = ['Current active universe has survivor bias.',
                             'Missing no-tick bars are reported, never synthesized.',
                             'No historical order book or open-interest backfill.']
    return report


def _collect_series(store, client, contract, interval, start, end, max_pages, report, progress):
    step = INTERVALS[interval]
    listed = contract['firstOpenDate']
    begin = ((max(start, listed) + step - 1) // step) * step
    finish = end // step * step
    symbol = contract['symbol']
    if begin >= finish:
        return _inapplicable(symbol, interval, end, max(0, listed - start))
    source = f'kucoin:klines:{start}:{end}'
    checkpoint = store.query('SELECT * FROM checkpoints WHERE source=? AND symbol=? AND interval=?',
                             (source, symbol, interval))
    cursor = _cursor(checkpoint, begin, finish, step)
    while cursor < finish and (max_pages is None or report['pages'] < max_pages):
        until = min(finish, cursor + MAX_CANDLES * step)
        rows = client.klines(symbol, interval, cursor, until)
        _save_page(store, source, symbol, interval, cursor, until, rows)
        report['pages'] += 1
        report['written_rows'] += len(rows)
        cursor = until
        if progress:
            progress({'symbol': symbol, 'interval': interval, 'next_time_ms': cursor,
                      'pages': report['pages'], 'written_rows': report['written_rows']})
    coverage = _coverage(store, symbol, interval, begin, finish, step)
    return dict(symbol=symbol, interval=interval, start_ms=begin, end_ms=finish,
                listing_clip_ms=max(0, listed - start), next_time_ms=cursor,
                queried_complete=cursor >= finish, status="queried" if cursor >= finish else "partial",
                gap_free=cursor >= finish and coverage["missing_count"] == 0, **coverage)


def _inapplicable(symbol, interval, end, listing_clip):
    return dict(symbol=symbol, interval=interval, start_ms=end, end_ms=end,
                listing_clip_ms=listing_clip, next_time_ms=None, queried_complete=True,
                status='no_completed_bars', gap_free=False, expected_count=0,
                stored_count=0, missing_count=0, gaps=[])


def _cursor(rows, begin, finish, step):
    if not rows:
        return begin
    value = integer(rows[0]['next_time_ms'])
    if len(rows) != 1 or not begin <= value <= finish or value % step:
        raise ValueError('invalid backfill checkpoint')
    return value


def _save_page(store, source, symbol, interval, start, end, rows):
    with store.transaction():
        old = store.query('SELECT * FROM klines WHERE symbol=? AND interval=? AND time_ms>=? AND time_ms<?',
                          (symbol, interval, start, end))
        indexed = {row['time_ms']: row for row in old}
        for row in rows:
            previous = indexed.get(row['time_ms'])
            if previous is not None and any(previous[key] != value for key, value in row.items()):
                raise ValueError('conflicting stored candle')
        store.upsert('klines', rows)
        store.upsert('checkpoints', [dict(source=source, symbol=symbol, interval=interval,
                     next_time_ms=end, updated_at_ms=int(time.time() * 1000))])


def _coverage(store, symbol, interval, start, end, step):
    rows = store.query('SELECT time_ms FROM klines WHERE symbol=? AND interval=? AND time_ms>=? AND time_ms<?',
                      (symbol, interval, start, end))
    present = {row['time_ms'] for row in rows}
    gaps = []
    missing = 0
    gap_start = None
    for at in range(start, end, step):
        if at not in present:
            missing += 1
            if gap_start is None:
                gap_start = at
        elif gap_start is not None:
            gaps.append({'start_ms': gap_start, 'end_ms': at, 'count': (at - gap_start) // step})
            gap_start = None
    if gap_start is not None:
        gaps.append({'start_ms': gap_start, 'end_ms': end, 'count': (end - gap_start) // step})
    return dict(expected_count=(end - start) // step, stored_count=len(present),
                missing_count=missing, gaps=gaps)


def _arguments(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    parser.add_argument('--days', type=int, default=90)
    parser.add_argument('--symbols', nargs='+', help='Default: all active crypto USDT perpetuals')
    parser.add_argument('--end-ms', type=int, help='Fixed UTC millisecond end; retain for resume')
    parser.add_argument('--max-pages', type=int, help='Stop after this many pages across all series')
    args = parser.parse_args(argv)
    if not 1 <= args.days <= 3650:
        parser.error('--days must be between 1 and 3650')
    if args.max_pages is not None and args.max_pages < 1:
        parser.error('--max-pages must be positive')
    return args


def main(argv=None):
    args = _arguments(argv)
    from trader.data.store import Store
    now = int(time.time() * 1000)
    end = now // 3600000 * 3600000 if args.end_ms is None else args.end_ms
    if end > now or end < args.days * DAY_MS:
        raise ValueError('invalid backfill end time')
    with Store(args.database) as store:
        report = run(store, PublicClient(), end - args.days * DAY_MS, end,
                     args.symbols, now, args.max_pages,
                     progress=lambda row: print(json.dumps(row), file=sys.stderr, flush=True))
    print(json.dumps(report, sort_keys=True))
    return 0 if report['complete'] else 2


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError):
        print('Public backfill failed; inspect configuration and offline diagnostics.', file=sys.stderr)
        raise SystemExit(1)
