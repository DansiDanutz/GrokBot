"""Freshness uses the symbol-leading candle index, not a full history scan."""
import sqlite3
import unittest

from trader.doctor.__main__ import _latest_klines


class FreshnessQueryTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE klines (symbol TEXT, interval TEXT, '
                        'time_ms INTEGER, PRIMARY KEY(symbol, interval, time_ms)) '
                        'WITHOUT ROWID')

    def test_empty_and_missing_intervals_remain_unknown(self):
        self.assertEqual(_latest_klines(self.db), (None, None))
        self.db.execute("INSERT INTO klines VALUES ('DELISTED', '1m', 42)")
        self.assertEqual(_latest_klines(self.db), (None, 42))

    def test_matches_full_scan_across_all_stored_symbols_and_intervals(self):
        rows = [('A', '1h', 100), ('A', '1m', 900), ('A', '1m', 950),
                ('DELISTED', '1h', 800), ('Z', '1h', 700), ('Z', '1m', 200)]
        self.db.executemany('INSERT INTO klines VALUES (?, ?, ?)', rows)
        expected = tuple(self.db.execute('SELECT MAX(time_ms) FROM klines '
                                        'WHERE interval=?', (interval,)).fetchone()[0]
                         for interval in ('1h', '1m'))
        self.assertEqual(_latest_klines(self.db), expected)

    def test_work_is_bounded_by_symbols_not_candle_count(self):
        self.db.executemany('INSERT INTO klines VALUES (?, ?, ?)',
                            ((str(symbol), interval, stamp)
                             for symbol in range(10)
                             for interval in ('1m', '1h')
                             for stamp in range(2000)))
        callbacks = 0

        def budget():
            nonlocal callbacks
            callbacks += 1
            return callbacks > 100  # At most 10,000 SQLite VM instructions.

        self.db.set_progress_handler(budget, 100)
        self.assertEqual(_latest_klines(self.db), (1999, 1999))
        self.db.set_progress_handler(None, 0)
