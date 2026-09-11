"""Offline parallel scheduling uses one client and separate SQLite connections."""
import threading
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from trader.data.kucoin_backfill import run, _arguments
from trader.data.kucoin_public import MAX_CANDLES
from trader.data.store import Store
from trader.tests.test_kucoin_backfill import Client


class ParallelClient(Client):
    def __init__(self, fail_after=None, overlap=False):
        super().__init__()
        prototype = self.metadata[0]
        self.metadata = [{**prototype, 'symbol': symbol} for symbol in
                         ('ZZZUSDTM', 'AAAUSDTM', 'BBBUSDTM', 'CCCUSDTM')]
        self.fail_after = fail_after
        self.lock = threading.Lock()
        self.threads = set()
        self.active = self.maximum_active = 0
        self.barrier = threading.Barrier(4) if overlap else None

    def klines(self, symbol, interval, start, end):
        with self.lock:
            self.threads.add(threading.get_ident())
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
        try:
            if self.barrier is not None and interval == '1m' and start == 0:
                self.barrier.wait(timeout=10)
            time.sleep(.002)
            if self.fail_after is not None and symbol == 'BBBUSDTM' and start >= self.fail_after:
                raise RuntimeError('untrusted raw provider error should not appear')
            return super().klines(symbol, interval, start, end)
        finally:
            with self.lock:
                self.active -= 1


class ParallelBackfillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name).resolve() / 'market.sqlite'
        self.addCleanup(self.temp.cleanup)

    def test_workers_share_client_but_own_connections_and_order_is_stable(self):
        client = ParallelClient(overlap=True)
        stores = []
        class TrackedStore(Store):
            def __init__(self, path):
                super().__init__(path)
                stores.append((threading.get_ident(), self.connection))
        with Store(self.path) as store, patch('trader.data.store.Store', TrackedStore):
            result = run(store, client, 0, 3600000, now_ms=3600000, workers=4)
        self.assertEqual(result['workers'], 4)
        self.assertTrue(result['complete'])
        self.assertTrue(result['gap_free'])
        self.assertEqual(result['written_rows'], 4 * 61)
        self.assertEqual(result['failures'], [])
        self.assertGreater(client.maximum_active, 1)
        self.assertLessEqual(client.maximum_active, 4)
        self.assertEqual(len({id(connection) for _, connection in stores}), 4)
        symbols = [row['symbol'] for row in result['series']]
        self.assertEqual(symbols, sorted(symbols))
        with Store(self.path) as store:
            self.assertEqual(store.query('SELECT count(*) n FROM klines')[0]['n'], 244)

    def test_partial_failed_worker_retains_rows_checkpoint_and_visible_error(self):
        end = (MAX_CANDLES + 1) * 60000
        client = ParallelClient(fail_after=MAX_CANDLES * 60000)
        progress = []
        with Store(self.path) as store:
            result = run(store, client, 0, end, now_ms=end, workers=4, progress=progress.append)
            rows = store.query("SELECT * FROM checkpoints WHERE symbol='BBBUSDTM' AND interval='1m'")
            self.assertEqual(rows[0]['next_time_ms'], MAX_CANDLES * 60000)
            self.assertEqual(store.query("SELECT count(*) n FROM klines WHERE symbol='BBBUSDTM' AND interval='1m'")[0]['n'], MAX_CANDLES)
            self.assertEqual(result['written_rows'], store.query('SELECT count(*) n FROM klines')[0]['n'])
        self.assertFalse(result['complete'])
        self.assertFalse(result['gap_free'])
        self.assertEqual(len(result['failures']), 1)
        self.assertEqual(len([row for row in progress if row.get('status') == 'failed']), 1)
        failed = next(x for x in result['series'] if x['symbol'] == 'BBBUSDTM' and x['interval'] == '1m')
        self.assertEqual(failed['status'], 'failed')
        self.assertEqual(failed['next_time_ms'], MAX_CANDLES * 60000)
        self.assertNotIn('untrusted raw provider', str(result))

    def test_max_pages_forces_sequential_bounded_run(self):
        client = ParallelClient()
        with Store(self.path) as store:
            result = run(store, client, 0, 3600000, now_ms=3600000, workers=4, max_pages=1)
        self.assertEqual(result['workers'], 1)
        self.assertEqual(result['pages'], 1)
        self.assertEqual(client.maximum_active, 1)
        self.assertFalse(result['complete'])

    def test_worker_limits_and_cli_default(self):
        for workers in (0, 5, True, 1.5):
            with self.subTest(workers=workers), Store(self.path) as store:
                with self.assertRaisesRegex(ValueError, 'workers'):
                    run(store, ParallelClient(), 0, 3600000, now_ms=3600000, workers=workers)
        self.assertEqual(_arguments(['--database', str(self.path)]).workers, 4)

class StorageReserveTests(unittest.TestCase):
    def test_low_space_rejects_fetch_without_advancing_checkpoint(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            with Store(Path(directory).resolve() / 'market.sqlite') as store:
                client = Client()
                with patch('shutil.disk_usage', return_value=SimpleNamespace(free=1024)):
                    with self.assertRaisesRegex(ValueError, 'insufficient_storage'):
                        run(store, client, 0, 3600000, now_ms=3600000)
                self.assertEqual(client.calls, [])
                self.assertEqual(store.query('SELECT * FROM checkpoints'), [])

    def test_space_drop_after_fetch_prevents_page_commit(self):
        from types import SimpleNamespace
        usage = [SimpleNamespace(free=10 * 1024 ** 3), SimpleNamespace(free=1024)]
        with tempfile.TemporaryDirectory() as directory:
            with Store(Path(directory).resolve() / 'market.sqlite') as store:
                client = Client()
                with patch('shutil.disk_usage', side_effect=usage):
                    with self.assertRaisesRegex(ValueError, 'insufficient_storage'):
                        run(store, client, 0, 3600000, now_ms=3600000)
                self.assertEqual(len(client.calls), 1)
                self.assertEqual(store.query('SELECT * FROM klines'), [])
                self.assertEqual(store.query('SELECT * FROM checkpoints'), [])

    def test_parallel_low_space_is_visible_in_failures(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            with Store(Path(directory).resolve() / 'market.sqlite') as store:
                client = ParallelClient()
                with patch('shutil.disk_usage', return_value=SimpleNamespace(free=1024)):
                    result = run(store, client, 0, 3600000, now_ms=3600000, workers=4)
                self.assertFalse(result['complete'])
                self.assertEqual(client.calls, [])
                self.assertTrue(all(row['error_kind'] == 'insufficient_storage' for row in result['failures']))
                self.assertEqual(len(result['failures']), 8)

    def test_storage_exhaustion_preserves_last_successful_page(self):
        from types import SimpleNamespace
        high = SimpleNamespace(free=10 * 1024 ** 3)
        usage = [high, high, SimpleNamespace(free=1024)]
        end = (MAX_CANDLES + 1) * 60000
        with tempfile.TemporaryDirectory() as directory:
            with Store(Path(directory).resolve() / 'market.sqlite') as store:
                with patch('shutil.disk_usage', side_effect=usage):
                    with self.assertRaisesRegex(ValueError, 'insufficient_storage'):
                        run(store, Client(), 0, end, now_ms=end)
                self.assertEqual(store.query('SELECT count(*) n FROM klines')[0]['n'], MAX_CANDLES)
                self.assertEqual(store.query('SELECT next_time_ms FROM checkpoints')[0]['next_time_ms'], MAX_CANDLES * 60000)
