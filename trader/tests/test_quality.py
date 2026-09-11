"""Hand-counted data-quality checks, entirely synthetic."""
import json
from pathlib import Path
import tempfile
import unittest

from trader.data import quality
from trader.data.store import Store

MINUTE = 60000


def candle(at, volume=1):
    return dict(symbol='TESTUSDTM', interval='1m', time_ms=at, open=10,
                high=11, low=9, close=10, volume=volume, turnover=volume*10)


class QualityTests(unittest.TestCase):
    def test_duplicates_and_input_order_are_distinct(self):
        rows = [candle(MINUTE), candle(0), candle(MINUTE)]
        result = quality.assess_candles(rows, '1m', 0, 2*MINUTE)
        self.assertEqual(result['duplicates']['count'], 1)
        self.assertEqual(result['input_order']['count'], 1)
        self.assertEqual(result['candle_gaps']['missing'], 0)
        self.assertEqual(result['duplicates']['status'], 'fail')

    def test_gaps_and_zero_runs_are_not_padded(self):
        rows = [candle(i*MINUTE, 0) for i in (0, 1, 2, 4, 5)]
        result = quality.assess_candles(rows, '1m', 0, 7*MINUTE)
        self.assertEqual(result['candle_gaps']['missing'], 2)
        self.assertEqual(result['candle_gaps']['ranges'], [[3*MINUTE,4*MINUTE],[6*MINUTE,7*MINUTE]])
        self.assertEqual(result['zero_volume_runs']['count'], 1)
        self.assertEqual(result['zero_volume_runs']['longest'], 3)
        self.assertEqual(len(rows), 5)

    def test_funding_period_unknown_is_not_assumed_eight_hours(self):
        rows = [dict(time_ms=0, period_ms=None), dict(time_ms=8*3600000, period_ms=None)]
        result = quality.assess_funding(rows)
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['unknown_periods'], 1)
        rows[1]['period_ms'] = 4*3600000
        result = quality.assess_funding(rows)
        self.assertEqual(result['status'], 'warn')
        self.assertEqual(result['missing_minimum'], 1)

    def test_empty_and_unaligned_windows_do_not_claim_quality(self):
        result = quality.assess_candles([], '1m', 0, MINUTE)
        self.assertEqual(result['candle_gaps']['status'], 'unknown')
        for begin, end in ((1, MINUTE), (MINUTE, 0), (0, 0)):
            with self.subTest(begin=begin, end=end), self.assertRaises(ValueError):
                quality.assess_candles([], '1m', begin, end)

    def test_database_report_persists_idempotently_and_discloses_order(self):
        with tempfile.TemporaryDirectory() as folder:
            with Store(Path(folder).resolve()/'market.sqlite3') as store:
                store.upsert('klines', [candle(0), candle(2*MINUTE)])
                first = quality.generate(store, 0, 3*MINUTE, ['TESTUSDTM'], ['1m'], 3*MINUTE)
                second = quality.generate(store, 0, 3*MINUTE, ['TESTUSDTM'], ['1m'], 3*MINUTE)
                self.assertEqual(first, second)
                self.assertEqual(len(store.query('SELECT * FROM data_quality')), 5)
                checks = {r['check_name']:r for r in first['checks']}
                self.assertEqual(checks['candle_gaps']['details']['missing'], 1)
                self.assertEqual(checks['input_order']['status'], 'unknown')
                self.assertEqual(checks['funding_gaps']['status'], 'unknown')
                self.assertEqual(quality.latest(store)['checks'], first['checks'])
                json.dumps(first, allow_nan=False)

    def test_latest_order_is_stable_across_intervals_and_collector_updates(self):
        with tempfile.TemporaryDirectory() as folder:
            with Store(Path(folder).resolve()/'market.sqlite3') as store:
                report = quality.generate(store, 0, 60*MINUTE, ['TESTUSDTM'],
                                          ['1m', '1h'], 60*MINUTE)
                store.upsert('data_quality', [dict(check_name='collection_cycle',
                    symbol='*', interval='5m', start_ms=60*MINUTE, end_ms=65*MINUTE,
                    checked_at_ms=60*MINUTE+1, status='pass', details_json='{}')])
                self.assertEqual(quality.latest(store), report)

    def test_listing_boundary_is_disclosed_and_excluded(self):
        with tempfile.TemporaryDirectory() as folder:
            with Store(Path(folder).resolve()/'market.sqlite3') as store:
                store.upsert('universe', [dict(symbol='TESTUSDTM',updated_at_ms=5*MINUTE,
                    first_candle_ms=2*MINUTE, listed_at_ms=MINUTE+1, turnover_30d=None,
                    atr_pct=None,multiplier=1,lot_size=1,active=1,coverage_json='{}')])
                store.upsert('klines', [candle(2*MINUTE),candle(3*MINUTE)])
                report=quality.generate(store,0,4*MINUTE,['TESTUSDTM'],['1m'],5*MINUTE)
                gap=next(r for r in report['checks'] if r['check_name']=='candle_gaps')
                self.assertEqual(gap['details']['missing'],0)
                self.assertEqual(gap['details']['effective_start_ms'],2*MINUTE)


if __name__ == '__main__':
    unittest.main()
