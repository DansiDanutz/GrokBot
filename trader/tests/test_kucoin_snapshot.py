import json
import hashlib
from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path

from trader.research.kucoin_snapshot import Snapshot, SnapshotError


def make_database(root):
    path = root / 'copy.sqlite3'
    db = sqlite3.connect(path)
    db.executescript('''
      CREATE TABLE klines(symbol TEXT, interval TEXT, time_ms INTEGER,
        open REAL, high REAL, low REAL, close REAL, volume REAL, turnover REAL);
      CREATE TABLE ticker_snapshots(symbol TEXT, time_ms INTEGER,
        observed_at_ms INTEGER, source_time_ms INTEGER, last REAL, mark_price REAL,
        index_price REAL, volume_24h REAL, turnover_24h REAL, open_interest REAL,
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

    def test_records_keep_oldest_fact_time_and_contract_units(self):
        raw = dict(status='Open', assetClass='CRYPTO', quoteCurrency='USDT',
                   expireDate=None, isInverse=False, multiplier=2, lotSize=1,
                   tickSize=.01, currentFundingRateGranularity=14400000,
                   fundingRateGranularity=28800000, firstOpenDate=0)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('INSERT INTO ticker_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                       ('RAYUSDTM', 100, 150, 90, 10, 10, 10, 3, 600000, 1,
                        .0004, json.dumps(raw)))
            db.execute('INSERT INTO top_of_book VALUES (?,?,?,?,?,?,?)',
                       ('RAYUSDTM', 80, 160, 9.99, 10, 20, 30))
            db.execute('INSERT INTO klines VALUES (?,?,?,?,?,?,?,?,?)',
                       ('RAYUSDTM', '1m', 0, 10, 11, 9, 10, 1, 10))
        with Snapshot(self.path) as data:
            records = data.records(60000)
            self.assertEqual(len(records), 1)
            market = records[0]['market']
            self.assertEqual(market['observed_at_ms'], 80)
            self.assertEqual(market['funding_interval_hours'], 4)
            self.assertEqual(market['quote_turnover_24h'], 600000)
            self.assertEqual(market['bestBidSize'], 20)
            self.assertTrue(market['perpetual'])
            self.assertEqual(len(records[0]['bars']), 1)
            self.assertEqual(data.market_bounds(), (160, 160))
            self.assertEqual(data.records(60000, pairs=['UNKNOWN']), [])

    def test_absent_book_or_perpetual_facts_are_not_invented(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('INSERT INTO ticker_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                       ('UNKNOWN', 100, 100, None, 10, 10, 10, 1, 100, 1, 0, '{}'))
        with Snapshot(self.path) as data:
            market = data.records(200)[0]['market']
            self.assertIsNone(market['observed_at_ms'])
            self.assertIsNone(market['perpetual'])
            self.assertEqual(data.market_bounds(), (None, None))

    def test_schema_requires_full_ohlc(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('ALTER TABLE klines DROP COLUMN high')
        with self.assertRaisesRegex(SnapshotError, 'schema'):
            Snapshot(self.path)

    def test_universe_iterator_materializes_only_the_next_coins_history(self):
        from unittest.mock import patch
        with closing(sqlite3.connect(self.path)) as db, db:
            for pair in ('A', 'B'):
                db.execute('INSERT INTO ticker_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                           (pair, 100, 100, None, 10, 10, 10, 1, 100, 1, 0, '{}'))
        with Snapshot(self.path) as data, patch.object(data, 'candles', return_value=[]) as read:
            stream = data.iter_records(200)
            self.assertEqual(read.call_count, 0)
            self.assertEqual(next(stream)['pair'], 'A')
            self.assertEqual(read.call_count, 1)
            self.assertEqual(next(stream)['pair'], 'B')
            self.assertEqual(read.call_count, 2)
            self.assertEqual(list(stream), [])

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


    def test_historical_membership_is_causal_and_metadata_does_not_supply_future_prices(self):
        from trader.research.kucoin_snapshot import HistoricalSnapshot
        day = 86400000
        raw = dict(tickSize=.01, lotSize=1, multiplier=2, quoteCurrency='USDT',
                   expireDate=None, isInverse=False, assetClass='CRYPTO')
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executemany('INSERT INTO klines VALUES (?,?,?,?,?,?,?,?,?)',
                [('AAAUSDTM', '1m', t, 10, 11, 9, 10, 2, 40) for t in range(0, 9*day, 60000)])
            db.execute('INSERT INTO klines VALUES (?,?,?,?,?,?,?,?,?)',
                       ('FUTUREUSDTM', '1m', 9*day, 999, 999, 999, 999, 1, 999))
            db.execute('INSERT INTO ticker_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                ('AAAUSDTM', 10*day, 10*day, None, 999, 999, 999, 1, 999, 1, .1, json.dumps(raw)))
        with Snapshot(self.path) as source:
            historical = HistoricalSnapshot(source, {'historical_spread_bps': 0})
            self.assertEqual(historical.symbols(8*day), ['AAAUSDTM'])
            record = historical.records(8*day)[0]
            self.assertEqual(record['market']['filter_mode'], 'candle-only filters')
            self.assertEqual(record['market']['price'], 10)
            self.assertEqual(record['market']['quote_turnover_24h'], 40*1440)
            self.assertEqual(record['market']['metadata_basis'], 'retrospective copied contract specifications')
            self.assertEqual(historical.market('AAAUSDTM', 8*day)['bid'], 10)
            self.assertEqual(record['market']['membership_basis'], 'observed_candles')

    def test_indicator_gaps_are_flagged_but_never_appear_in_execution_candles(self):
        from trader.research.kucoin_snapshot import HistoricalSnapshot
        day = 86400000
        missing = 8*day-60000
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executemany('INSERT INTO klines VALUES (?,?,?,?,?,?,?,?,?)',
                [('AAAUSDTM', '1m', t, 10, 11, 9, 10, 2, 40)
                 for t in range(day, 8*day, 60000) if t != missing])
        with Snapshot(self.path) as source:
            historical = HistoricalSnapshot(source)
            bars, coverage = historical.history('AAAUSDTM', 8*day)
            self.assertEqual(len(bars), 10080)
            self.assertTrue(bars[-1]['indicator_only'])
            self.assertEqual(coverage['observed'], 10079)
            execution = historical.candles('AAAUSDTM', 8*day-3600000, 8*day)
            self.assertEqual(len(execution), 59)
            self.assertTrue(all(row.get('time_ms') != missing for row in execution))

    def test_daily_history_cache_avoids_reloading_the_same_seven_days_each_hour(self):
        from trader.research.kucoin_snapshot import HistoricalSnapshot
        from unittest.mock import patch
        day = 86400000
        with Snapshot(self.path) as source, patch.object(source, 'candles', return_value=[]) as reads:
            historical = HistoricalSnapshot(source)
            historical.history('A', 8*day)
            historical.history('A', 8*day+3600000)
            self.assertEqual(reads.call_count, 1)
            historical.history('A', 9*day)
            self.assertEqual(reads.call_count, 2)
            self.assertEqual(reads.call_args.args[1:], (9*day, 10*day))


    def test_contract_count_turnover_uses_multiplier_once_and_does_not_treat_missing_zero_as_quote(self):
        from trader.research.kucoin_snapshot import _quote_turnover
        bars = [dict(volume=1000, close=60000, turnover=0)]
        value, basis = _quote_turnover(bars, {'multiplier': .001}, 'contracts')
        self.assertEqual(value, 60000)
        self.assertIn('contracts', basis)
        self.assertIn('multiplier', basis)
        self.assertIsNone(_quote_turnover(bars, {}, 'contracts')[0])
        self.assertEqual(_quote_turnover(bars, {}, 'base')[0], 60000000)

    def test_prefetched_future_prices_do_not_enter_an_earlier_history_window(self):
        from trader.research.kucoin_snapshot import HistoricalSnapshot
        day = 86400000
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executemany('INSERT INTO klines VALUES (?,?,?,?,?,?,?,?,?)',
                [('AAAUSDTM', '1m', t, 10, 10, 10, 10, 1, 10) for t in range(day, 8*day, 60000)])
            db.execute('INSERT INTO klines VALUES (?,?,?,?,?,?,?,?,?)',
                ('AAAUSDTM', '1m', 8*day+60000, 999, 999, 999, 999, 1, 999))
        with Snapshot(self.path) as source:
            historical = HistoricalSnapshot(source)
            bars, coverage = historical.history('AAAUSDTM', 8*day)
            self.assertTrue(all(row['close'] == 10 for row in bars))
            self.assertTrue(all(row['timestamp_ms'] < 8*day for row in bars))
            self.assertEqual(coverage['ratio'], 1)


    def test_historical_record_preserves_an_actual_seed_for_a_leading_indicator_gap(self):
        from trader.research.kucoin_snapshot import HistoricalSnapshot
        from trader.strategies.candle_coverage import prepare
        day = 86400000
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executemany('INSERT INTO klines VALUES (?,?,?,?,?,?,?,?,?)',
                [('AAAUSDTM', '1m', t, 10, 10, 10, 10, 1, 10)
                 for t in range(day-60000, 8*day, 60000) if t != day])
        with Snapshot(self.path) as source:
            record = HistoricalSnapshot(source).records(8*day)[0]
            result = prepare(record['bars'], 8*day, prior_seed=record.get('prior_seed'))
            self.assertTrue(result['valid'])
            self.assertEqual(result['coverage']['actual'], 10079)
            self.assertEqual(result['coverage']['seed_timestamp_ms'], day-60000)
            self.assertTrue(result['bars'][0]['synthetic'])

    def test_historical_snapshot_rejects_conflicting_observed_and_seed_duplicates(self):
        from trader.research.kucoin_snapshot import HistoricalSnapshot
        day = 86400000
        for duplicate_time in (day, day-60000):
            with self.subTest(duplicate_time=duplicate_time):
                with closing(sqlite3.connect(self.path)) as db, db:
                    db.execute('DELETE FROM klines')
                    db.executemany('INSERT INTO klines VALUES (?,?,?,?,?,?,?,?,?)',
                        [('AAAUSDTM', '1m', t, 10, 10, 10, 10, 1, 10)
                         for t in range(day-60000, day+120000, 60000)])
                    db.execute('INSERT INTO klines VALUES (?,?,?,?,?,?,?,?,?)',
                        ('AAAUSDTM', '1m', duplicate_time, 11, 11, 11, 11, 1, 11))
                with Snapshot(self.path) as source:
                    with self.assertRaisesRegex(SnapshotError, 'Conflicting duplicate'):
                        HistoricalSnapshot(source).history('AAAUSDTM', 8*day)


if __name__ == '__main__':
    unittest.main()
