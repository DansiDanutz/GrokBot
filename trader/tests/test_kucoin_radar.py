import json
import unittest
from pathlib import Path
from unittest.mock import patch
from trader.research.kucoin_radar import (
    radar, normalize_market, crossing_score, compare_volatility, volatility_proxy,
)

HOUR = 3_600_000
NOW = 8 * 24 * HOUR


def candles(hours=168, trend=False):
    return [{'timestamp_ms': NOW - hours * HOUR + i * 60_000,
             'open': 100, 'high': 100, 'low': 100,
             'close': 100 + 6 * i / (hours * 60) if trend else 100 + i % 2}
            for i in range(hours * 60)]


def market(**changes):
    result = dict(active=True, quote_currency='USDT', perpetual=True,
                  asset_class='crypto', quote_turnover_24h=900_000, bid=99.99, ask=100.01,
                  listed_at_ms=0, funding_rate=.0001, funding_interval_hours=8,
                  bid_depth_usdt=1000, ask_depth_usdt=1000, observed_at_ms=NOW)
    return dict(result, **changes)


def setup(*args, **kwargs):
    return dict(eligible=True, direction='long', reason='test', low=90, high=110,
                grids=20, quantity=1, entry=100, preview={})


class RadarTests(unittest.TestCase):
    def test_one_way_six_percent_is_crossing_proxy_not_completed_fills(self):
        result = crossing_score(candles(trend=True), setup(), NOW)
        self.assertEqual(result['kind'], 'hysteretic_step_crossing_proxy')
        self.assertLess(result['score'], 1)

    def test_recent_volatility_collapse_reduces_score_to_zero(self):
        bars = candles()
        for bar in bars[-240:]:
            bar['close'] = 100
        result = crossing_score(bars, setup(), NOW)
        self.assertEqual(result['score'], 0)
        self.assertGreater(result['rate_24h'], 0)

    def test_small_jitter_around_grid_level_does_not_inflate_score(self):
        bars = candles()
        for i, bar in enumerate(bars):
            bar['close'] = 100 + .01 * (-1) ** i
        self.assertEqual(crossing_score(bars, setup(), NOW)['score'], 0)

    def test_quote_turnover_is_never_substituted_by_base_volume(self):
        raw = market(quote_turnover_24h=None, volumeOf24h=999999999)
        self.assertIsNone(normalize_market(raw, NOW)['quote_turnover_24h'])

    @patch('trader.research.kucoin_radar.build_setup', setup)
    @patch('trader.research.kucoin_radar.features', return_value={})
    def test_filter_table_and_always_exclude_running(self, _):
        cases = [('LOW', {'quote_turnover_24h': 499999}),
                 ('BASE', {'quote_turnover_24h': None, 'volumeOf24h': 99999999}),
                 ('SPREAD', {'ask': 102}), ('AGE', {'listed_at_ms': NOW-HOUR}),
                 ('FUND', {'funding_rate': .002}), ('DEPTH', {'ask_depth_usdt': 1}),
                 ('STALE', {'observed_at_ms': NOW-2*HOUR}),
                 ('FUTURE', {'observed_at_ms': NOW+1})]
        records = [dict(pair=name, bars=candles(), market=market(**changes))
                   for name, changes in cases]
        records += [dict(pair=str(i), bars=candles(), market=market()) for i in range(8)]
        result = radar(records, NOW, running_pairs=['0'])
        self.assertEqual(len(result['radar']), 7)
        self.assertNotIn('0', [row['pair'] for row in result['radar']])
        self.assertTrue(all(row['pair'].isdigit() for row in result['radar']))
        self.assertEqual(len(result['rejected']), 9)

    def test_rejection_distinguishes_missing_data_from_known_filter_failure(self):
        cases = [(dict(quote_turnover_24h=1), False),
                 (dict(quote_turnover_24h=None), True),
                 (dict(ask=102), False), (dict(ask=None), True),
                 (dict(funding_rate=.002), False), (dict(funding_rate=None), True),
                 (dict(listed_at_ms=NOW-HOUR), False), ({}, True)]
        for changes, expected in cases:
            with self.subTest(changes=changes):
                result = radar([dict(pair='T', bars=[], market=market(**changes))], NOW)
                self.assertEqual(result['rejected'][0]['coverage_issue'], expected)

    def test_score_uses_accepted_tick_interval_not_a_finer_recomputed_grid(self):
        form = dict(setup(), interval=2)
        self.assertEqual(crossing_score(candles(), form, NOW)['step'], 2)

    def test_funding_normalizes_to_eight_hours(self):
        result = normalize_market(market(funding_rate=.0006, funding_interval_hours=4), NOW)
        self.assertAlmostEqual(result['funding_rate_8h'], .0012)

    def test_fixture_does_not_prove_volatility_formula(self):
        path = Path(__file__).resolve().parents[2] / 'tests/fixtures/kucoin-grid-bots-20260911.json'
        fixture = json.loads(path.read_text())['volatility_list_usdt_m_top']
        report = compare_volatility(fixture, {})
        self.assertEqual(report['unknown'], 23)
        self.assertEqual(report['verified'], 0)
        self.assertFalse(report['validated'])
        self.assertAlmostEqual(volatility_proxy(110, 100), 10)

    @patch('trader.research.kucoin_radar.build_setup', setup)
    @patch('trader.research.kucoin_radar.features', return_value={})
    def test_future_candles_never_enter_setup(self, mock_features):
        bars = candles() + [dict(timestamp_ms=NOW, open=999, high=999, low=999, close=999)]
        radar([dict(pair='T', bars=bars, market=market())], NOW)
        used = mock_features.call_args.args[0]
        self.assertEqual(max(row['timestamp_ms'] for row in used), NOW - 60_000)

    def test_crossings_outside_the_setup_range_are_not_scored(self):
        bars = candles()
        for i, bar in enumerate(bars):
            bar['close'] = 111 + i % 2
        self.assertEqual(crossing_score(bars, setup(), NOW)['score'], 0)

    @patch('trader.research.kucoin_radar.build_setup', setup)
    @patch('trader.research.kucoin_radar.features', return_value={})
    def test_noncrypto_or_unknown_asset_class_is_excluded(self, _):
        for asset in ('stock', 'forex', None):
            with self.subTest(asset=asset):
                result = radar([dict(pair='T', bars=candles(),
                                     market=market(asset_class=asset))], NOW)
                self.assertEqual(result['radar'], [])
                self.assertIn('crypto', result['rejected'][0]['reason'])

    def test_malformed_funding_granularity_is_unknown(self):
        data = market(funding_interval_hours=None, fundingRateGranularity='bad')
        self.assertIsNone(normalize_market(data, NOW)['funding_rate_8h'])


    @patch('trader.research.kucoin_radar.build_setup', setup)
    @patch('trader.research.kucoin_radar.features', return_value={})
    def test_default_ten_and_configurable_five_preserve_proxy_context(self, unused):
        records = [dict(pair=str(i), bars=candles(), market=market()) for i in range(12)]
        result = radar(records, NOW)
        self.assertEqual(len(result['radar']), 10)
        self.assertEqual(len(result['volatility_universe']), 12)
        self.assertFalse(result['radar'][0]['volatility_definition_verified'])
        self.assertIn('independent', result['volatility_basis'])
        self.assertEqual(len(radar(records, NOW, parameters={'radar_size': 5})['radar']), 5)
        for count in (4, 11, True):
            with self.assertRaises(ValueError):
                radar(records, NOW, parameters={'radar_size': count})


if __name__ == '__main__':
    unittest.main()
