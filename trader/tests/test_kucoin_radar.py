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
             'open': 100, 'high': 106 if trend else 101, 'low': 100,
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
                grids=20, quantity=1, entry=100, preview={'profit_per_grid_min': 1})


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
        used = mock_features.call_args.args[0]['bars']
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

    @patch('trader.research.kucoin_radar.build_setup', setup)
    @patch('trader.research.kucoin_radar.features', return_value={})
    def test_coverage_threshold_is_95_percent_and_gaps_are_indicator_only(self, mock_features):
        rows = candles()
        missing = set(range(19, 10080, 20))
        partial = [row for index, row in enumerate(rows) if index not in missing]
        result = radar([dict(pair='T', bars=partial, market=market())], NOW)
        self.assertEqual(len(result['radar']), 1)
        coverage = result['radar'][0]['candle_coverage']
        self.assertEqual(coverage['actual'], 9576)
        self.assertEqual(coverage['fraction'], .95)
        prepared = mock_features.call_args.args[0]
        self.assertEqual(len(prepared['bars']), 10080)
        self.assertEqual(sum(row.get('synthetic', False) for row in prepared['bars']), 504)
        below = [row for row in partial if row['timestamp_ms'] != rows[1]['timestamp_ms']]
        rejected = radar([dict(pair='T', bars=below, market=market())], NOW)['rejected'][0]
        self.assertTrue(rejected['coverage_issue'])
        self.assertLess(rejected['candle_coverage']['fraction'], .95)

    def test_crossings_do_not_jump_over_missing_or_synthetic_minutes(self):
        bars = [dict(timestamp_ms=NOW-180000, close=90),
                dict(timestamp_ms=NOW-60000, close=110)]
        self.assertEqual(crossing_score(bars, setup(), NOW)['score'], 0)
        bars.insert(1, dict(timestamp_ms=NOW-120000, close=100, synthetic=True,
                            crossing_eligible=False))
        self.assertEqual(crossing_score(bars, setup(), NOW)['score'], 0)

    @patch('trader.research.kucoin_radar.build_setup', setup)
    @patch('trader.research.kucoin_radar.features', return_value={})
    def test_candle_only_filters_disclose_skipped_filters_for_every_candidate(self, mock_features):
        metadata = market(filter_mode='candle-only filters', bid=None, ask=None,
                          bid_depth_usdt=None, ask_depth_usdt=None, funding_rate=None,
                          observed_at_ms=None, active=None, listed_at_ms=None,
                          membership_basis='observed_candles',
                          turnover_basis='observed candle turnover in USDT')
        records = [dict(pair=pair, bars=candles(), market=metadata) for pair in ('A', 'B')]
        result = radar(records, NOW)
        self.assertEqual(len(result['radar']), 2)
        for row in result['radar']:
            self.assertEqual(row['filter_mode'], 'candle-only filters')
            self.assertEqual(row['skipped_filters'], ['spread', 'depth', 'funding_window'])
            self.assertIn('candle-only filters', row['reason'])
            self.assertFalse(row['quantity_calibrated'])
            self.assertEqual(row['membership_basis'], 'observed_candles')
            self.assertTrue(row['metadata_retrospective'])
            self.assertIn('survivorship', row['metadata_disclosure'])
        self.assertIsNone(mock_features.call_args.args[1])

    @patch('trader.research.kucoin_radar.build_setup', setup)
    @patch('trader.research.kucoin_radar.features', return_value={})
    def test_candle_quote_turnover_conversion_requires_explicit_volume_units(self, _):
        bars = [dict(row, volume=10) for row in candles()]
        base = market(filter_mode='candle-only filters', quote_turnover_24h=None,
                      candle_volume_unit='base', bid=None, ask=None)
        result = radar([dict(pair='T', bars=bars, market=base)], NOW)
        self.assertEqual(len(result['radar']), 1)
        self.assertGreater(result['radar'][0]['quote_turnover_24h'], 500000)
        self.assertIn('base', result['radar'][0]['turnover_basis'])
        unknown = dict(base, candle_volume_unit=None)
        rejected = radar([dict(pair='T', bars=bars, market=unknown)], NOW)['rejected'][0]
        self.assertTrue(rejected['coverage_issue'])
        self.assertIn('turnover', rejected['reason'])

    @patch('trader.research.kucoin_radar.features', return_value={})
    def test_income_ranking_can_prefer_lower_gph_using_actual_net_floor(self, _):
        def forms(pair, *unused):
            return dict(setup(), grids=40 if pair == 'FAST' else 20,
                        preview={'profit_per_grid_min': 1 if pair == 'FAST' else 3})
        with patch('trader.research.kucoin_radar.build_setup', forms):
            result = radar([dict(pair=pair, bars=candles(), market=market())
                            for pair in ('FAST', 'INCOME')], NOW)
        first, second = result['radar']
        self.assertEqual(first['pair'], 'INCOME')
        self.assertLess(first['score'], second['score'])
        self.assertGreater(first['grid_income_per_hour'], second['grid_income_per_hour'])
        self.assertEqual(first['grid_income_per_hour'], first['score']*3)
        self.assertEqual(first['actual_net_usdt_per_grid'], 3)

    @patch('trader.research.kucoin_radar.build_setup', setup)
    @patch('trader.research.kucoin_radar.features', return_value={})
    def test_contract_count_volume_uses_explicit_multiplier_once(self, _):
        bars = [dict(row, volume=10000) for row in candles()]
        for multiplier in (.001, .01, 100):
            metadata = market(filter_mode='candle-only filters', quote_turnover_24h=None,
                              candle_volume_unit='contracts', multiplier=multiplier)
            result = radar([dict(pair='T', bars=bars, market=metadata)], NOW)
            expected = sum(row['volume']*multiplier*row['close'] for row in bars[-1440:])
            self.assertEqual(result['radar'][0]['quote_turnover_24h'], expected)
            self.assertIn('contracts', result['radar'][0]['turnover_basis'])

    @patch('trader.research.kucoin_radar.build_setup', setup)
    @patch('trader.research.kucoin_radar.features', return_value={})
    def test_duplicate_identical_candle_does_not_inflate_coverage_or_crash_context(self, _):
        bars = candles()
        result = radar([dict(pair='T', bars=bars+[dict(bars[-100])], market=market())], NOW)
        self.assertEqual(result['radar'][0]['candle_coverage']['actual'], 10080)
        self.assertEqual(result['radar'][0]['candle_coverage']['fraction'], 1)

    @patch('trader.research.kucoin_radar.features', return_value={})
    def test_unknown_net_floor_is_coverage_gap_but_known_subtarget_floor_is_not(self, _):
        for floor, missing in [(None, True), (.5, False)]:
            form = dict(setup(), preview={'profit_per_grid_min': floor})
            with patch('trader.research.kucoin_radar.build_setup', return_value=form):
                result = radar([dict(pair='T', bars=candles(), market=market())], NOW)
            self.assertEqual(result['radar'], [])
            self.assertEqual(result['rejected'][0]['coverage_issue'], missing)

    @patch('trader.research.kucoin_radar.features', return_value={})
    def test_candle_only_setup_cannot_use_retrospective_quote_or_funding_values(self, _):
        metadata = market(filter_mode='candle-only filters', bid=9999, ask=10000,
                          observed_at_ms=NOW+HOUR, bestBidPrice=9999,
                          turnover_basis='observed candle quote turnover')
        with patch('trader.research.kucoin_radar.build_setup', return_value=setup()) as mocked:
            result = radar([dict(pair='T', bars=candles(), market=metadata)], NOW)
        self.assertEqual(len(result['radar']), 1)
        used = mocked.call_args.args[2]
        self.assertIsNone(used['bid'])
        self.assertIsNone(used['ask'])
        self.assertIsNone(used['funding_rate'])
        self.assertNotIn('bestBidPrice', used)
        self.assertEqual(used['price'], candles()[-1]['close'])


if __name__ == '__main__':
    unittest.main()
