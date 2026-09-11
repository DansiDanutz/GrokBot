import unittest
from dataclasses import dataclass
from unittest.mock import patch

from trader.research.grid_scanner import crossings, scan

HOUR = 3600000
NOW = 40 * 24 * HOUR


@dataclass
class FakeSizing:
    low: float = 95
    high: float = 105
    step: float = 0.52
    quantity: float = 25
    grids: int = 20
    profit_floor: float = 1

    def form_values(self, pair):
        return {'pair': pair, 'direction': 'neutral', 'low': self.low,
                'high': self.high, 'grids': self.grids, 'leverage': 5,
                'investment': 1000}


class FakeSnapshot:
    def __init__(self):
        self.gap = False
        self.data = {
            'ticker_observed_at_ms': NOW, 'book_observed_at_ms': NOW,
            'book_time_ms': NOW, 'time_ms': NOW, 'bid': 99.99, 'ask': 100.01,
            'bid_size': 300, 'ask_size': 300, 'turnover24': 5000000,
            'funding_rate': 0.0005, 'raw': {
                'status': 'Open', 'quoteCurrency': 'USDT', 'isInverse': False,
                'expireDate': None, 'firstOpenDate': NOW - 10 * 24 * HOUR,
                'multiplier': 0.1, 'lotSize': 1, 'tickSize': 0.01,
                'fundingRateGranularity': 8 * HOUR}}

    def symbols(self, at_ms):
        return ['XBTUSDTM']

    def market(self, pair, at_ms):
        return self.data

    def candles(self, pair, start_ms, end_ms):
        return [{'time_ms': t, 'close': 100 + ((t // 60000) % 4) * 0.52}
                for t in range(start_ms, end_ms, 60000)
                if not self.gap or t != start_ms + 60000]


class ScannerTests(unittest.TestCase):
    def test_crossings_keep_residual_anchor(self):
        self.assertEqual(crossings([100, 100.2, 100.4, 100.6], 0.5), 1)
        self.assertEqual(crossings([100, 101, 100], 0.5), 4)

    @patch('trader.research.grid_scanner.size_grid', return_value=FakeSizing())
    def test_candidate_reports_two_windows_and_explicit_proxy(self, sizing):
        result = scan(FakeSnapshot(), NOW)
        candidate = result['candidates'][0]
        self.assertGreater(candidate['rate_4h'], 0)
        self.assertEqual(candidate['expected_grids_per_hour'],
                         min(candidate['rate_4h'], candidate['rate_24h']))
        self.assertIn('proxy', result['metric_definition'])
        self.assertEqual(candidate['form']['direction'], 'neutral')
        self.assertEqual(candidate['bid_depth_base'], 30)
        self.assertEqual(candidate['multiplier'], 0.1)
        self.assertEqual(candidate['lot_size'], 1)
        self.assertEqual(candidate['tick_size'], 0.01)
        self.assertEqual(candidate['observed_at_ms'], NOW)

    @patch('trader.research.grid_scanner.size_grid', return_value=FakeSizing())
    def test_unknown_evidence_and_actual_threshold_failures_are_distinct(self, _):
        cases = [('bid_size', None, 'unknown'), ('turnover24', 4999999, 'filter'),
                 ('funding_rate', 0.00051, 'filter'),
                 ('book_observed_at_ms', NOW - HOUR, 'unknown')]
        for field, value, status in cases:
            with self.subTest(field=field):
                snapshot = FakeSnapshot()
                snapshot.data[field] = value
                report = scan(snapshot, NOW)
                self.assertEqual(report['candidates'], [])
                self.assertEqual(report['excluded'][0]['status'], status)

    @patch('trader.research.grid_scanner.size_grid', return_value=FakeSizing())
    def test_missing_minute_does_not_become_zero_crossings(self, _):
        snapshot = FakeSnapshot()
        snapshot.gap = True
        report = scan(snapshot, NOW)
        self.assertEqual(report['candidates'], [])
        self.assertIn('history', report['excluded'][0]['reason'])

    @patch('trader.research.grid_scanner.size_grid', return_value=FakeSizing())
    def test_unverified_funding_period_is_not_assumed_eight_hours(self, _):
        snapshot = FakeSnapshot()
        snapshot.data['raw'].pop('fundingRateGranularity')
        result = scan(snapshot, NOW)
        self.assertEqual(result['excluded'][0]['status'], 'unknown')

    @patch('trader.research.grid_scanner.size_grid', return_value=FakeSizing())
    def test_each_filter_threshold_is_applied_without_extra_economic_filters(self, _):
        for field, value, reason in [
            ('ask', 100.2, 'spread'), ('bid_size', 249, 'depth'),
            ('ask_size', 249, 'depth'), ('funding_rate', -0.00051, 'funding')]:
            with self.subTest(field=field):
                snapshot = FakeSnapshot()
                snapshot.data[field] = value
                report = scan(snapshot, NOW)
                self.assertIn(reason, report['excluded'][0]['reason'])
        snapshot = FakeSnapshot()
        snapshot.data['raw']['firstOpenDate'] = NOW - 6 * 24 * HOUR
        self.assertIn('listing', scan(snapshot, NOW)['excluded'][0]['reason'])
        snapshot.data['raw']['firstOpenDate'] = NOW - 7 * 24 * HOUR
        self.assertEqual(len(scan(snapshot, NOW)['candidates']), 1)

    @patch('trader.research.grid_scanner.size_grid', return_value=FakeSizing())
    def test_rank_ties_are_deterministic(self, _):
        snapshot = FakeSnapshot()
        snapshot.symbols = lambda timestamp: ['ZUSDTM', 'AUSDTM']
        report = scan(snapshot, NOW)
        self.assertEqual([row['pair'] for row in report['candidates']],
                         ['AUSDTM', 'ZUSDTM'])

    def test_no_asof_symbols_is_explicit_unknown_coverage(self):
        snapshot = FakeSnapshot()
        snapshot.symbols = lambda timestamp: []
        report = scan(snapshot, NOW)
        self.assertEqual(report['coverage']['status'], 'unknown')
        self.assertIn('no as-of', report['coverage']['reason'])

    def test_real_sizing_integration(self):
        report = scan(FakeSnapshot(), NOW)
        candidate = report['candidates'][0]
        self.assertGreaterEqual(candidate['sizing']['profit_floor'], 1)
        self.assertEqual(candidate['form']['leverage'], 5)
        self.assertEqual(candidate['form']['investment'], 1000)

    def test_four_hour_funding_is_normalized_for_filter_only(self):
        snapshot = FakeSnapshot()
        snapshot.data['raw']['fundingRateGranularity'] = 4 * HOUR
        snapshot.data['funding_rate'] = 0.0002
        candidate = scan(snapshot, NOW)['candidates'][0]
        self.assertAlmostEqual(candidate['funding_rate'], 0.0004)
        self.assertEqual(candidate['funding_raw_rate'], 0.0002)
        self.assertEqual(candidate['funding_source_period_ms'], 4 * HOUR)
        snapshot.data['funding_rate'] = 0.0003
        self.assertEqual(scan(snapshot, NOW)['excluded'][0]['status'], 'filter')

    def test_random_null_can_access_all_eligible_candidates(self):
        snapshot = FakeSnapshot()
        snapshot.symbols = lambda timestamp: [f'COIN{i:02}USDTM' for i in range(12)]
        report = scan(snapshot, NOW, limit=5)
        self.assertEqual(len(report['candidates']), 5)
        self.assertEqual(len(report['eligible_candidates']), 12)
        self.assertEqual(report['eligible_candidates'][:5], report['candidates'])

    def test_recent_observation_does_not_hide_stale_provider_time(self):
        snapshot = FakeSnapshot()
        snapshot.data['source_time_ms'] = NOW - HOUR
        report = scan(snapshot, NOW)
        self.assertEqual(report['candidates'], [])
        self.assertIn('source_time_ms', report['excluded'][0]['reason'])

    def test_limit_must_be_five_to_ten(self):
        with self.assertRaises(ValueError):
            scan(FakeSnapshot(), NOW, limit=11)


if __name__ == '__main__':
    unittest.main()
