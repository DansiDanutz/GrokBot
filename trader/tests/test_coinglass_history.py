"""CoinGlass history recorder tested offline with injected getters."""
from pathlib import Path
import sqlite3
import tempfile
import unittest

from paper_grid.coinglass import CoinGlassError
from trader.data.coinglass_history import run, _symbol_rows
from trader.data.store import Store

NOW_MS = 1_757_616_000_000  # 2025-09-11T12:00:00Z, hourly aligned.
HOUR_MS = 3_600_000


def history(now_s, hours=6, long_usd=1000.0, short_usd=500.0):
    start = (now_s // 3600 - hours) * 3600
    return [dict(time=start + index * 3600,
                 aggregated_long_liquidation_usd=long_usd,
                 aggregated_short_liquidation_usd=short_usd)
            for index in range(hours)]


class SymbolRowsTests(unittest.TestCase):
    def test_keeps_only_completed_hourly_rows(self):
        now_s = NOW_MS // 1000
        rows = history(now_s, hours=3)
        rows.append(dict(time=now_s, aggregated_long_liquidation_usd=1,
                         aggregated_short_liquidation_usd=1))
        rows.append(dict(time='bad', aggregated_long_liquidation_usd=1,
                         aggregated_short_liquidation_usd=1))
        kept, rejected = _symbol_rows('NEARUSDTM', rows, NOW_MS)
        floor = now_s // 3600
        self.assertEqual([row['time_ms'] for row in kept],
                         [(floor - index) * 3600 * 1000
                          for index in (3, 2, 1)])
        self.assertEqual(rejected, 2)

    def test_rejects_negative_and_non_finite_usd(self):
        now_s = NOW_MS // 1000
        rows = [dict(time=(now_s - 3600) * 1, aggregated_long_liquidation_usd=-1,
                     aggregated_short_liquidation_usd=1),
                dict(time=(now_s - 7200), aggregated_long_liquidation_usd='nan',
                     aggregated_short_liquidation_usd=1)]
        kept, rejected = _symbol_rows('NEARUSDTM', rows, NOW_MS)
        self.assertEqual((kept, rejected), ([], 2))


class RunTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name).resolve() / 'market.sqlite3'

    def store(self):
        store = Store(self.path)
        self.addCleanup(store.close)
        return store

    def test_persists_completed_rows_idempotently_with_quality(self):
        now_s = NOW_MS // 1000
        calls = []

        def getter(symbol, request_now, key):
            calls.append((symbol, request_now, key))
            return history(request_now, hours=4, long_usd=250.0)

        store = self.store()
        details = run(store, ['NEARUSDTM'], NOW_MS, getter=getter,
                      api_key='cg-test-key')
        self.assertEqual(details['status'], 'pass')
        self.assertEqual(details['rows'], 4)
        self.assertEqual(calls, [('NEARUSDTM', now_s, 'cg-test-key')])
        rows = store.query('SELECT * FROM coinglass_liquidations '
                           'ORDER BY time_ms')
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]['exchange'], 'Binance,OKX,Bybit')
        self.assertEqual(rows[0]['long_usd'], 250.0)
        self.assertEqual(rows[0]['time_ms'] % HOUR_MS, 0)
        quality = store.query("SELECT * FROM data_quality "
                              "WHERE check_name='coinglass_history'")
        self.assertEqual(len(quality), 1)
        self.assertEqual(quality[0]['status'], 'pass')
        self.assertEqual(run(store, ['NEARUSDTM'], NOW_MS, getter=getter,
                             api_key='cg-test-key')['rows'], 4)
        self.assertEqual(store.query('SELECT count(*) AS n FROM '
                                     'coinglass_liquidations')[0]['n'], 4)

    def test_symbol_failure_is_recorded_not_fatal(self):
        def getter(symbol, request_now, key):
            if symbol == 'BADUSDTM':
                raise CoinGlassError('history unavailable')
            return history(request_now, hours=2)

        store = self.store()
        details = run(store, ['BADUSDTM', 'NEARUSDTM'], NOW_MS,
                      getter=getter, api_key='cg-test-key')
        self.assertEqual(details['status'], 'warn')
        self.assertEqual(details['symbols_ok'], 1)
        self.assertEqual(details['failures'][0]['symbol'], 'BADUSDTM')
        self.assertEqual(details['failures'][0]['errors'],
                         ['history unavailable'])
        self.assertEqual(store.query('SELECT count(*) AS n FROM '
                                     'coinglass_liquidations')[0]['n'], 2)

    def test_missing_key_fails_closed_without_network(self):
        def getter(symbol, request_now, key):
            raise AssertionError('network must not be reached')

        store = self.store()
        details = run(store, ['NEARUSDTM'], NOW_MS, getter=getter,
                      key_path=str(self.path.parent / 'missing.env'))
        self.assertEqual(details['status'], 'fail')
        self.assertEqual(details['rows'], 0)
        self.assertEqual(details['failures'][0]['errors'],
                         ['API key unavailable'])


if __name__ == '__main__':
    unittest.main()
