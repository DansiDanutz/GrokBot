"""Offline fixed-window pagination, interrupted resume, gaps and atomicity."""
import copy
import json
import unittest
from contextlib import contextmanager
from pathlib import Path

from trader.data.kucoin_backfill import run

FIXTURES = Path(__file__).resolve().parents[2] / 'tests/fixtures/kucoin'


class FakeStore:
    def __init__(self):
        self.tables = {'klines': [], 'checkpoints': []}
        self.fail_checkpoint = False

    @contextmanager
    def transaction(self):
        old = copy.deepcopy(self.tables)
        try:
            yield self
        except Exception:
            self.tables = old
            raise

    def query(self, sql, params=()):
        if 'checkpoints' in sql:
            source, symbol, interval = params
            return [x for x in self.tables['checkpoints'] if
                    (x['source'], x['symbol'], x['interval']) == params]
        symbol, interval, start, end = params
        return [x for x in self.tables['klines'] if x['symbol'] == symbol
                and x['interval'] == interval and start <= x['time_ms'] < end]

    def upsert(self, table, rows):
        if table == 'checkpoints' and self.fail_checkpoint:
            raise RuntimeError('injected checkpoint failure')
        keys = ('symbol', 'interval', 'time_ms') if table == 'klines' else ('source', 'symbol', 'interval')
        for row in rows:
            self.tables[table] = [x for x in self.tables[table]
                                  if any(x[k] != row[k] for k in keys)] + [row]
        return len(rows)


class Client:
    def __init__(self, missing=()):
        self.calls = []
        self.missing = missing
        self.metadata = json.loads((FIXTURES / 'contracts.json').read_text())['data'][:1]
        self.metadata[0]['firstOpenDate'] = 0

    def contracts(self):
        return self.metadata

    def klines(self, symbol, interval, start, end):
        self.calls.append((symbol, interval, start, end))
        step = 60000 if interval == '1m' else 3600000
        return [dict(symbol=symbol, interval=interval, time_ms=at, open=10.,
                     high=11., low=9., close=10., volume=1., turnover=10.)
                for at in range(start, end, step) if at not in self.missing]


class BackfillTests(unittest.TestCase):
    def test_fixed_pages_complete_candles_and_resume(self):
        store, client = FakeStore(), Client()
        first = run(store, client, 0, 60000 * 502 + 12345, now_ms=60000 * 502 + 12345,
                    max_pages=1)
        self.assertFalse(first['complete'])
        self.assertEqual(len(store.tables['klines']), 200)
        second = run(store, client, 0, 60000 * 502 + 12345,
                     now_ms=60000 * 502 + 12345)
        self.assertTrue(second['complete'])
        self.assertEqual(second['series'][0]['missing_count'], 0)
        self.assertEqual(client.calls[1][2], 60000 * 200)
        self.assertTrue(all(row['time_ms'] < 60000 * 502 for row in store.tables['klines']))

    def test_effective_200_row_provider_limit_does_not_skip_requested_slots(self):
        class CappedClient(Client):
            def klines(self, symbol, interval, start, end):
                return super().klines(symbol, interval, start, end)[:200]
        store, client = FakeStore(), CappedClient()
        report = run(store, client, 0, 60000*600, now_ms=60000*600)
        self.assertTrue(report['gap_free'])
        self.assertTrue(all(end-start <= 200*(60000 if interval == '1m' else 3600000)
                            for _, interval, start, end in client.calls))

    def test_different_requested_start_does_not_reuse_later_checkpoint(self):
        store, client = FakeStore(), Client()
        run(store, client, 60000 * 500, 60000 * 502, now_ms=60000 * 502)
        client.calls.clear()
        run(store, client, 0, 60000 * 502, now_ms=60000 * 502)
        self.assertEqual(client.calls[0][2], 0)

    def test_gap_report_and_zero_fabrication(self):
        store, client = FakeStore(), Client(missing=(60000,))
        report = run(store, client, 0, 180000, now_ms=180000)
        series = report['series'][0]
        self.assertEqual(series['missing_count'], 1)
        self.assertEqual(series['gaps'], [{'start_ms': 60000, 'end_ms': 120000, 'count': 1}])
        self.assertEqual(len(store.tables['klines']), 2)
        self.assertTrue(report['complete'])
        self.assertFalse(report['gap_free'])

    def test_listing_clip_and_no_available_completed_hour(self):
        store, client = FakeStore(), Client()
        client.metadata[0]['firstOpenDate'] = 60001
        report = run(store, client, 0, 180000, now_ms=180000)
        self.assertEqual(client.calls[0][2], 120000)
        self.assertEqual(report['series'][0]['listing_clip_ms'], 60001)
        self.assertEqual(report['series'][1]['expected_count'], 0)

    def test_checkpoint_write_failure_rolls_back_candles(self):
        store, client = FakeStore(), Client()
        store.fail_checkpoint = True
        with self.assertRaises(RuntimeError):
            run(store, client, 0, 180000, now_ms=180000)
        self.assertEqual(store.tables, {'klines': [], 'checkpoints': []})

    def test_conflict_with_existing_record_fails_without_checkpoint(self):
        store, client = FakeStore(), Client()
        store.tables['klines'] = client.klines('XBTUSDTM', '1m', 0, 60000)
        store.tables['klines'][0]['close'] = 10.5
        with self.assertRaises(ValueError):
            run(store, client, 0, 180000, now_ms=180000)
        self.assertEqual(store.tables['checkpoints'], [])

class SQLiteIntegrationTests(unittest.TestCase):
    def setUp(self):
        # This case exercises resume and rollback, not the free-space reserve.
        # Stub the machine reading so the offline suite stays deterministic on a
        # host whose real free space is below MIN_FREE_BYTES.
        from types import SimpleNamespace
        from unittest.mock import patch
        reserve = patch('shutil.disk_usage',
                        return_value=SimpleNamespace(free=10 * 1024 ** 3))
        reserve.start()
        self.addCleanup(reserve.stop)

    def test_real_sqlite_resume_and_nested_rollback(self):
        import tempfile
        from trader.data.store import Store
        with tempfile.TemporaryDirectory() as directory:
            with Store(Path(directory).resolve() / 'market.sqlite') as store:
                client = Client()
                report = run(store, client, 0, 180000, now_ms=180000)
                self.assertFalse(report['gap_free'])
                self.assertTrue(report['series'][0]['gap_free'])
                self.assertEqual(store.query('SELECT count(*) AS n FROM klines')[0]['n'], 3)
                client.calls.clear()
                run(store, client, 0, 180000, now_ms=180000)
                self.assertEqual(client.calls, [])
                with self.assertRaises(RuntimeError), store.transaction():
                    store.upsert('klines', client.klines('XBTUSDTM', '1m', 180000, 240000))
                    raise RuntimeError('injected transaction failure')
                self.assertEqual(store.query('SELECT count(*) AS n FROM klines')[0]['n'], 3)

class EmptyUniverseTests(unittest.TestCase):
    def test_empty_discovery_is_not_reported_as_complete_backfill(self):
        client = Client(); client.metadata = []
        with self.assertRaisesRegex(ValueError, 'universe'):
            run(FakeStore(), client, 0, 180000, now_ms=180000)

class AvailableWindowTests(unittest.TestCase):
    def test_future_or_empty_effective_window_rejected(self):
        for start, end, now in ((120000, 180000, 60000), (60000, 120000, 60000)):
            with self.subTest(start=start), self.assertRaisesRegex(ValueError, 'window'):
                run(FakeStore(), Client(), start, end, now_ms=now)

    def test_prelisting_only_has_no_coverage_evidence(self):
        client = Client(); client.metadata[0]['firstOpenDate'] = 240000
        report = run(FakeStore(), client, 0, 180000, now_ms=180000)
        self.assertFalse(report['gap_free'])
        self.assertEqual(client.calls, [])
        self.assertTrue(all(row['status'] == 'no_completed_bars' for row in report['series']))
        self.assertTrue(all(row['end_ms'] <= report['end_ms'] for row in report['series']))
        self.assertTrue(all(row['expected_count'] == 0 for row in report['series']))

    def test_short_window_without_completed_hour_is_not_gap_free_hour(self):
        report = run(FakeStore(), Client(), 0, 180000, now_ms=180000)
        self.assertEqual(report['series'][1]['status'], 'no_completed_bars')
        self.assertFalse(report['series'][1]['gap_free'])
        self.assertFalse(report['gap_free'])
