import json
import hashlib
from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path

from trader.research.grid_snapshot import Snapshot, SnapshotError


def make_database(root):
    path = root / 'copy.sqlite3'
    db = sqlite3.connect(path)
    db.executescript('''
      CREATE TABLE klines(symbol TEXT, interval TEXT, time_ms INTEGER,
        open REAL, high REAL, low REAL, close REAL, volume REAL, turnover REAL);
      CREATE TABLE ticker_snapshots(symbol TEXT, time_ms INTEGER,
        observed_at_ms INTEGER, source_time_ms INTEGER, last REAL, mark REAL,
        "index" REAL, volume24 REAL, turnover24 REAL, open_interest REAL,
        funding_rate REAL, raw_json TEXT);
      CREATE TABLE top_of_book(symbol TEXT, time_ms INTEGER,
        observed_at_ms INTEGER, bid REAL, ask REAL, bid_size REAL, ask_size REAL);
      CREATE TABLE funding(symbol TEXT, time_ms INTEGER, rate REAL, period_ms INTEGER);
    ''')
    db.commit()
    db.close()
    (root / 'snapshot.json').write_text(json.dumps({
        'offline_copy': True, 'source': 'kucoin-public'}))
    return path


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.path = make_database(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_manifest_is_required_before_database_open(self):
        (self.root / 'snapshot.json').unlink()
        with self.assertRaises(SnapshotError):
            Snapshot(self.path)

    def test_protected_path_rejected_before_open(self):
        with self.assertRaisesRegex(SnapshotError, 'protected'):
            Snapshot(Path.home() / 'Sandbox/grokbot/market-data/missing.sqlite3')

    def test_symlink_hardlink_and_live_wal_rejected(self):
        link = self.root / 'link.sqlite3'
        link.symlink_to(self.path)
        with self.assertRaises(SnapshotError):
            Snapshot(link)
        link.unlink()
        link.hardlink_to(self.path)
        with self.assertRaises(SnapshotError):
            Snapshot(self.path)
        link.unlink()
        Path(str(self.path) + '-wal').touch()
        with self.assertRaises(SnapshotError):
            Snapshot(self.path)

    def test_other_protected_roots_rejected_before_filesystem_access(self):
        from unittest.mock import patch
        roots = ('ZCodeProject/GrokBot', '.openclaw-secrets', '.openclaw',
                 'Sandbox/grokbot/zmarty-paper-runtime', 'Library/LaunchAgents')
        for root in roots:
            with patch.object(Path, 'lstat', side_effect=AssertionError('must not inspect')):
                with self.assertRaisesRegex(SnapshotError, 'protected'):
                    Snapshot(Path.home() / root / 'input.sqlite3')

    def test_complete_candles_only_and_read_only(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executemany('INSERT INTO klines VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', [
                ('XBTUSDTM', '1m', t, 10, 11, 9, 10, 1, 10)
                for t in (0, 60000, 120000)])
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        with Snapshot(self.path) as data:
            self.assertEqual([r['time_ms'] for r in data.candles(
                'XBTUSDTM', 0, 90000)], [0])
            self.assertEqual(data.bounds(), (0, 180000))
            with self.assertRaises(sqlite3.OperationalError):
                data.connection.execute('DELETE FROM klines')
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)
        self.assertFalse(Path(str(self.path) + '-wal').exists())

    def test_asof_excludes_later_observations_and_provider_times(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            rows = [('OLD', 100, 100, 100), ('FUTURE', 100, 400, 100),
                    ('PROVIDER', 100, 100, 400)]
            for pair, timestamp, observed, source in rows:
                db.execute('INSERT INTO ticker_snapshots VALUES '
                           '(?,?,?,?,?,?,?,?,?,?,?,?)',
                           (pair, timestamp, observed, source, 10, 10, 10,
                            1, 5000000, 1, 0, '{}'))
            db.execute('INSERT INTO top_of_book VALUES (?,?,?,?,?,?,?)',
                       ('OLD', 100, 100, 9, 11, 2, 3))
        with Snapshot(self.path) as data:
            self.assertEqual(data.symbols(200), ['OLD'])
            self.assertEqual(data.market('OLD', 200)['bid'], 9)
            self.assertIsNone(data.market('FUTURE', 200))

    def test_schema_requires_full_ohlc(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('ALTER TABLE klines DROP COLUMN high')
        with self.assertRaisesRegex(SnapshotError, 'schema'):
            Snapshot(self.path)

    def test_funding_uses_settlements_and_exclusive_start(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executemany('INSERT INTO funding VALUES (?,?,?,?)', [
                ('XBTUSDTM', 100, 0.0001, 28800000),
                ('XBTUSDTM', 200, 0.0002, 28800000),
                ('XBTUSDTM', 300, 0.0003, None),
                ('XBTUSDTM', 400, 0.0004, 14400000)])
        with Snapshot(self.path) as data:
            records = data.funding('XBTUSDTM', 100, 300)
            self.assertEqual([row['time_ms'] for row in records], [200])
            self.assertEqual(records[0]['rate'], 0.0002)


if __name__ == '__main__':
    unittest.main()
