"""Incremental public market data, never authentication or exchange orders.

Run an explicit database with --once or --duration-hours 24. Failed cycles
retain successful observations with atomic status and resumable frontiers.
A queried candle frontier is not a claim of gap-free exchange history.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import math
import sys

from trader.data.kucoin_public import PublicClient, select_contracts
from trader.data.ratelimit import TokenBucket
from trader.data.store import Store
from trader.data.updater_records import (
    DAY_MS, HOUR_MS, INITIAL_MINUTES, MAX_CANDLE_SLOTS, MAX_PAGES, MINUTE_MS,
    SOURCE_CANDLES, SOURCE_FUNDING, TABLES, UPDATER_WEIGHT_RATE, WORKER_COUNT,
    checkpoint, empty_batch, open_interest, quality, snapshot, top_book,
)
from trader.data.updater_runtime import (
    AlreadyRunning, CADENCE_SECONDS, Clock, CollectorLock, scheduled_run,
)


class CycleDeadline(TimeoutError):
    """The configured cycle or run deadline has been reached."""


# A candle interval is "complete enough" at or above this share of its expected
# slots. KuCoin omits candles for untraded minutes, so thin listings always have
# some gaps; the floor mirrors the daily review's >=95% kline gate. Without it
# every cycle of hundreds of symbols warns and 'pass' is unreachable.
COVERAGE_FLOOR = 0.95


def _require_time(clock, deadline):
    if clock.monotonic() >= deadline:
        raise CycleDeadline('collection deadline reached')


def _frontier(points, source, symbol, interval, initial):
    return points.get((source, symbol, interval), initial)


def _plans(contract, points, now):
    symbol = contract['symbol']
    listed = contract['firstOpenDate']
    plans = []
    for interval, step in [('1m', MINUTE_MS), ('1h', HOUR_MS)]:
        end = now // step * step
        initial = end - (INITIAL_MINUTES if interval == '1m' else 1) * step
        start = _frontier(points, SOURCE_CANDLES, symbol, interval, initial)
        listing_start = min(end, ((listed + step - 1) // step) * step)
        start = max(start, listing_start)
        plans.append((interval, start, end, step))
    start = _frontier(points, SOURCE_FUNDING, symbol, '1m', now - DAY_MS)
    plans.append(('funding', max(start, listed), now, DAY_MS))
    return plans


def _history(client, plan, symbol, now, clock, deadline):
    interval, start, target, step = plan
    is_funding = interval == 'funding'
    source = SOURCE_FUNDING if is_funding else SOURCE_CANDLES
    store_interval = '1m' if is_funding else interval
    batch = empty_batch()
    rows, gaps, errors, frontier = [], 0, [], start
    expected = 0
    if start > target:
        errors.append('checkpoint_ahead_of_cutoff')
    for _ in range(MAX_PAGES):
        if frontier >= target:
            break
        width = DAY_MS if is_funding else MAX_CANDLE_SLOTS * step
        end = min(target, frontier + width)
        try:
            _require_time(clock, deadline)
            fetched = (client.funding(symbol, frontier, end) if is_funding
                       else client.klines(symbol, interval, frontier, end))
        except Exception as error:
            errors.append(type(error).__name__)
            break
        if is_funding:
            rows.extend(dict(row, period_ms=None) for row in fetched)
        else:
            completed = [r for r in fetched if r['time_ms'] + step <= now]
            rows.extend(completed)
            slots = (end - frontier) // step
            expected += slots
            gaps += slots - len(completed)
        frontier = end
    batch['funding' if is_funding else 'klines'] = rows
    batch['checkpoints'] = [checkpoint(source, symbol, store_interval,
                                       frontier, now)]
    behind = frontier < target
    details = dict(rows=len(rows), gaps=gaps, errors=errors, behind=behind,
                   queried_through_ms=frontier, requested_through_ms=target)
    if not is_funding:
        details['expected'] = expected
    if is_funding:
        details.update(schedule_verified=False,
                       completeness='queried_window_only')
    status = 'fail' if errors else 'warn' if gaps or behind else 'pass'
    record = quality('updater_' + interval, symbol, store_interval,
                     start, max(start, target), now, status, details)
    batch['data_quality'] = [record]
    return batch, details


def _merge(target, source):
    for table in TABLES:
        target[table].extend(source[table])


def _symbol_job(client, contract, plans, now, clock, deadline):
    symbol, batch, failures, gaps = contract['symbol'], empty_batch(), [], 0
    expected = rows_seen = 0
    for plan in plans:
        history, details = _history(client, plan, symbol, now, clock, deadline)
        _merge(batch, history)
        failures.extend(details['errors'])
        if details['behind']:
            failures.append('catchup_incomplete')
        gaps += details['gaps']
        if 'expected' in details:  # candle intervals only; funding has no slots
            expected += details['expected']
            rows_seen += details['rows']
    try:
        _require_time(clock, deadline)
        raw = client.book(symbol)
        batch['top_of_book'].append(top_book(raw, int(clock.wall() * 1000)))
    except Exception as error:
        failures.append(type(error).__name__)
    return batch, dict(symbol=symbol, errors=failures, gaps=gaps,
                       expected=expected, rows=rows_seen,
                       deadline_reached=clock.monotonic() >= deadline)


class Updater:
    def __init__(self, store, client, clock=None):
        self.store, self.client, self.clock = store, client, clock or Clock()

    def _points(self):
        rows = self.store.query('SELECT * FROM checkpoints')
        return {(r['source'], r['symbol'], r['interval']): r['next_time_ms']
                for r in rows}

    def _ordered(self, contracts):
        prior = self.store.query(
            "SELECT details_json FROM data_quality "
            "WHERE check_name='collection_cycle' "
            'ORDER BY checked_at_ms DESC LIMIT 1')
        resume = json.loads(prior[0]['details_json']).get('resume_symbol') \
            if prior else None
        names = [row['symbol'] for row in contracts]
        index = names.index(resume) if resume in names else 0
        return contracts[index:] + contracts[:index]

    def _fetch(self, contracts, points, now, deadline, batch):
        jobs, results = [], []
        with ThreadPoolExecutor(max_workers=WORKER_COUNT) as pool:
            for contract in self._ordered(contracts):
                jobs.append(pool.submit(_symbol_job, self.client, contract,
                            _plans(contract, points, now), now,
                            self.clock, deadline))
            for job in jobs:
                rows, result = job.result()
                _merge(batch, rows)
                results.append(result)
        return results

    def _collect(self, now, deadline, batch):
        _require_time(self.clock, deadline)
        contracts = select_contracts(self.client.contracts())
        observed = int(self.clock.wall() * 1000)
        if not contracts:
            raise ValueError('no active crypto USDT perpetual contracts')
        snapshot_errors = []
        for contract in contracts:
            try:
                ticker = snapshot(contract, observed)
                interest = open_interest(contract, observed)
                batch['ticker_snapshots'].append(ticker)
                batch['open_interest'].append(interest)
            except (KeyError, ValueError, TypeError) as error:
                snapshot_errors.append(dict(symbol=contract['symbol'],
                                            errors=[type(error).__name__]))
        results = self._fetch(contracts, self._points(), now, deadline, batch)
        return results, snapshot_errors

    def cycle(self, run_deadline):
        started = self.clock.monotonic()
        now = int(self.clock.wall() * 1000)
        deadline = min(run_deadline, started + CADENCE_SECONDS)
        self.client.deadline = deadline
        batch, results, errors = empty_batch(), [], []
        try:
            results, errors = self._collect(now, deadline, batch)
        except Exception as error:
            errors = [dict(errors=[type(error).__name__])]
        failed = [result for result in results if result['errors']]
        deferred = [r for r in failed if r['deadline_reached']]
        gaps = sum(result['gaps'] for result in results)
        elapsed = self.clock.monotonic() - started
        # KuCoin omits candles for untraded minutes, so across the full symbol
        # set some gaps are guaranteed every cycle. Judge by coverage against
        # COVERAGE_FLOOR, not by any-gap-exists.
        low_coverage = sorted(
            result['symbol'] for result in results
            if result.get('expected')
            and result.get('rows', 0) / result['expected'] < COVERAGE_FLOOR
        )
        status = ('fail' if errors or failed
                  else 'warn' if low_coverage else 'pass')
        incomplete = {r['symbol'] for r in failed + errors if 'symbol' in r}
        incomplete.update(low_coverage)
        missed = max(0, math.ceil(elapsed / CADENCE_SECONDS) - 1)
        resume = deferred[0]['symbol'] if deferred else None
        details = dict(status=status, contracts=len(results),
                       complete_symbols=max(0, len(results) - len(incomplete)),
                       gaps=gaps, low_coverage_symbols=low_coverage,
                       failures=failed + errors, elapsed_seconds=elapsed,
                       deadline_reached=self.clock.monotonic() >= deadline,
                       missed_schedule_slots=missed, resume_symbol=resume,
                       source='kucoin_classic_public', paper_only=True)
        batch['data_quality'].append(quality(
            'collection_cycle', '*', '5m', now, now + CADENCE_SECONDS * 1000,
            int(self.clock.wall() * 1000), status, details))
        with self.store.transaction():
            for table in TABLES:
                self.store.upsert(table, batch[table])
        return details


def run_session(updater, duration_hours, clock):
    outcomes = {'pass': 0, 'warn': 0, 'fail': 0}

    def collect(deadline):
        outcome = updater.cycle(deadline)
        outcomes[outcome['status']] += 1

    count = scheduled_run(collect, duration_hours, clock)
    return dict(cycles_attempted=count, cycles_complete=outcomes['pass'],
                cycles_warned=outcomes['warn'], cycles_failed=outcomes['fail'],
                paper_only=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--once', action='store_true')
    mode.add_argument('--duration-hours', type=float, choices=[24])
    args = parser.parse_args(argv)
    clock = Clock()
    try:
        with CollectorLock(args.database), Store(args.database) as store:
            client = PublicClient(limiter=TokenBucket(
                rate=UPDATER_WEIGHT_RATE, capacity=UPDATER_WEIGHT_RATE))
            updater = Updater(store, client, clock)
            if args.once:
                status = updater.cycle(clock.monotonic() + CADENCE_SECONDS)
                print(json.dumps(status, sort_keys=True))
                return 0 if status['status'] == 'pass' else 1
            result = run_session(updater, args.duration_hours, clock)
            print(json.dumps(result, sort_keys=True))
            complete = result['cycles_complete'] == result['cycles_attempted']
            return 0 if complete else 1
    except AlreadyRunning as error:
        print(str(error), file=sys.stderr)
        return 2
    except Exception as error:
        print('updater failed: ' + type(error).__name__, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
