"""Offline end-to-end persistence and failure tests for the collector."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from trader.data.store import Store
from trader.data.updater import Updater, run_session
from trader.data.updater_runtime import scheduled_run

MINUTE = 60000
HOUR = 3600000
START = 1789084800000


class SyntheticClock:
    def __init__(self):
        self.elapsed = 0

    def wall(self):
        return (START + self.elapsed * 1000) / 1000

    def monotonic(self):
        return self.elapsed

    def sleep(self, seconds):
        self.elapsed += seconds


class SyntheticClient:
    def __init__(self, clock):
        self.clock = clock
        self.fail = None
        self.calls = []
        self.gap = False
        self.gap_intervals = None  # None = gap every interval; else gap only these
        self.deadline = None

    def contracts(self):
        return [dict(symbol='XBTUSDTM', status='Open',
                     quoteCurrency='USDT', settleCurrency='USDT',
                     isInverse=False, type='FFWCSX', expireDate=None,
                     settleDate=None, assetClass='CRYPTO',
                     firstOpenDate=START - 100 * HOUR, multiplier=0.001,
                     lotSize=1, lastTradePrice=100, markPrice=101,
                     indexPrice=102, volumeOf24h=40, turnoverOf24h=4000,
                     openInterest='300', fundingFeeRate=0.0001)]

    def klines(self, symbol, interval, start, end):
        self.calls.append((interval, start, end))
        if self.fail == interval:
            raise ValueError('secret-like data must not be copied to status')
        step = MINUTE if interval == '1m' else HOUR
        rows = [dict(symbol=symbol, interval=interval, time_ms=t,
                     open=100, high=102, low=99, close=101,
                     volume=10, turnover=1000)
                for t in range(start, end, step)]
        gapped = self.gap and (self.gap_intervals is None
                               or interval in self.gap_intervals)
        return rows[1:] if gapped else rows

    def funding(self, symbol, start, end):
        self.calls.append(('funding', start, end))
        return [dict(symbol=symbol, time_ms=START-HOUR, rate=0.0001)] \
            if start <= START-HOUR < end else []

    def book(self, symbol):
        if self.fail == 'book':
            raise ValueError('untrusted private-looking exception')
        return dict(symbol=symbol, ts=int(self.clock.wall()*1000)*1000000,
                    bids=[[100, 3]], asks=[[101, 4]])


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name).resolve() / 'market.sqlite3'
        self.store = Store(self.database)
        self.clock = SyntheticClock()
        self.client = SyntheticClient(self.clock)
        self.updater = Updater(self.store, self.client, self.clock)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def rows(self, table):
        return self.store.query('SELECT * FROM ' + table)

    def test_complete_cycle_stores_closed_candles_and_raw_snapshots(self):
        status = self.updater.cycle(300)
        self.assertEqual(status['status'], 'pass')
        self.assertEqual(len(self.rows('klines')), 6)
        self.assertTrue(all(row['time_ms'] < START
                            for row in self.rows('klines')))
        snapshot = self.rows('ticker_snapshots')[0]
        self.assertEqual(json.loads(snapshot['raw_json']),
                         self.client.contracts()[0])
        self.assertIsNone(snapshot['source_time_ms'])
        self.assertEqual(snapshot['observed_at_ms'], START)
        self.assertIsNone(self.rows('funding')[0]['period_ms'])
        self.assertEqual(self.rows('top_of_book')[0]['time_ms'], START)

    def test_restart_catches_up_using_persisted_frontier(self):
        self.updater.cycle(300)
        self.clock.elapsed += 1200
        restarted = Updater(self.store, self.client, self.clock)
        status = restarted.cycle(1500)
        self.assertEqual(status['status'], 'pass')
        minute = [r for r in self.rows('klines') if r['interval'] == '1m']
        self.assertEqual(len(minute), 25)
        self.assertEqual(max(r['time_ms'] for r in minute),
                         START + 19 * MINUTE)
        self.assertEqual(len([c for c in self.client.calls if c[0]=='1h']), 1)

    def test_partial_failure_preserves_success_and_retries_failed_stream(self):
        self.client.fail = '1m'
        status = self.updater.cycle(300)
        self.assertEqual(status['status'], 'fail')
        self.assertNotIn('secret-like', json.dumps(self.rows('data_quality')))
        points = self.rows('checkpoints')
        self.assertEqual(next(p['next_time_ms'] for p in points
                              if p['source'] == 'updater-klines'
                              and p['interval'] == '1m'), START - 5 * MINUTE)
        self.assertEqual(len(self.rows('top_of_book')), 1)
        self.client.fail = None
        self.clock.elapsed = 300
        self.updater.cycle(600)
        self.assertEqual(len([r for r in self.rows('klines')
                              if r['interval']=='1m']), 10)

    def test_gapped_response_is_not_complete_or_synthetic(self):
        self.client.gap = True
        status = self.updater.cycle(300)
        self.assertEqual(status['status'], 'warn')
        self.assertEqual(len(self.rows('klines')), 4)
        self.assertGreater(status['gaps'], 0)

    def test_small_gap_within_coverage_floor_passes(self):
        # One missing 1m candle in a ~30-minute catch-up (≈97%) is inside the
        # COVERAGE_FLOOR — thin listings legitimately have untraded minutes.
        self.updater.cycle(300)
        self.clock.elapsed += 1800
        restarted = Updater(self.store, self.client, self.clock)
        self.client.gap = True
        self.client.gap_intervals = {'1m'}
        status = restarted.cycle(2400)
        self.assertEqual(status['status'], 'pass')
        self.assertGreater(status['gaps'], 0)
        self.assertEqual(status['low_coverage_symbols'], [])

    def test_288_updates_with_real_store_and_synthetic_clock(self):
        count = scheduled_run(self.updater.cycle, 24, self.clock)
        self.assertEqual(count, 288)
        cycles = [r for r in self.rows('data_quality')
                  if r['check_name']=='collection_cycle']
        self.assertEqual(len(cycles), 288)
        self.assertTrue(all(row['status']=='pass' for row in cycles))
        self.assertEqual(len(self.rows('ticker_snapshots')), 288)
        self.assertEqual(len(self.rows('top_of_book')), 288)

    def test_snapshot_validation_is_a_partial_failure(self):
        contracts = self.client.contracts()
        contracts[0]['openInterest'] = -1
        with patch.object(self.client, 'contracts', return_value=contracts):
            status = self.updater.cycle(300)
        self.assertEqual(status['status'], 'fail')
        self.assertFalse(self.rows('ticker_snapshots'))
        self.assertEqual(len(self.rows('top_of_book')), 1)

    def test_cycle_transaction_rolls_back_data_and_checkpoints(self):
        original = self.store.upsert

        def reject_status(table, rows):
            if table == 'data_quality':
                raise RuntimeError('synthetic status-write failure')
            return original(table, rows)

        with patch.object(self.store, 'upsert', side_effect=reject_status):
            with self.assertRaises(RuntimeError):
                self.updater.cycle(300)
        self.assertFalse(self.rows('klines'))
        self.assertFalse(self.rows('checkpoints'))
        self.assertFalse(self.rows('ticker_snapshots'))

    def test_session_reports_failed_cycles_instead_of_claiming_success(self):
        self.client.fail = 'book'
        result = run_session(self.updater, 1, self.clock)
        self.assertEqual(result['cycles_attempted'], 12)
        self.assertEqual(result['cycles_failed'], 12)
        self.assertEqual(result['cycles_complete'], 0)

    def test_deadline_prevents_network_calls_and_records_failure(self):
        status = self.updater.cycle(0)
        self.assertEqual(status['status'], 'fail')
        self.assertFalse(self.client.calls)
        self.assertEqual(len(self.rows('data_quality')), 1)


if __name__ == '__main__':
    unittest.main()
