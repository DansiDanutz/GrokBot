import unittest

from trader.strategies.candle_coverage import prepare


MINUTE = 60000
BASE = 100*MINUTE


def bar(index, price=100.):
    return {'timestamp_ms':BASE+index*MINUTE,'open':price,'high':price+1.,
            'low':price-1.,'close':price,'volume':10.}


class CoverageTests(unittest.TestCase):
    def test_exact_95_percent_threshold_and_forward_fill_flags(self):
        source = [bar(i) for i in range(20) if i != 8]
        result = prepare(source,BASE+20*MINUTE,window_minutes=20)
        self.assertTrue(result['valid'],result['reason'])
        self.assertEqual(result['coverage']['actual'],19)
        self.assertEqual(result['coverage']['expected'],20)
        self.assertEqual(result['coverage']['fraction'],.95)
        self.assertTrue(result['coverage']['eligible'])
        self.assertEqual(len(result['bars']),20)
        gap = result['bars'][8]
        self.assertTrue(gap['synthetic'])
        self.assertFalse(gap['crossing_eligible'])
        self.assertTrue(all(gap[key] == 100. for key in ('open','high','low','close')))
        self.assertEqual(gap['volume'],0.)
        self.assertFalse(result['bars'][9]['crossing_eligible'])
        self.assertTrue(result['bars'][10]['crossing_eligible'])
        self.assertFalse(prepare(source[:-1],BASE+20*MINUTE,window_minutes=20)['valid'])

    def test_asof_excludes_current_unclosed_candle_and_future_values(self):
        past = [bar(i) for i in range(20)]
        future = dict(bar(20),close=float('nan'))
        result = prepare(past+[future],BASE+20*MINUTE+30000,window_minutes=20)
        self.assertTrue(result['valid'])
        self.assertEqual(result['bars'][-1]['timestamp_ms'],BASE+19*MINUTE)
        self.assertEqual(result['coverage']['actual'],20)

    def test_leading_gap_requires_strictly_earlier_actual_seed(self):
        source = [bar(i,200.) for i in range(1,20)]
        unseeded = prepare(source,BASE+20*MINUTE,window_minutes=20)
        self.assertTrue(unseeded['coverage']['eligible'])
        self.assertFalse(unseeded['coverage']['indicators_valid'])
        self.assertFalse(unseeded['valid'])
        self.assertIsNone(unseeded['bars'][0]['close'])
        seeded = prepare(source,BASE+20*MINUTE,window_minutes=20,prior_seed=bar(-1,90.))
        self.assertTrue(seeded['valid'])
        self.assertEqual(seeded['bars'][0]['close'],90.)
        self.assertFalse(seeded['bars'][1]['crossing_eligible'])
        self.assertFalse(prepare(source,BASE+20*MINUTE,window_minutes=20,prior_seed=bar(1))['valid'])

    def test_earlier_observed_bar_can_seed_but_does_not_inflate_coverage(self):
        result = prepare([bar(-1,90.)]+[bar(i) for i in range(1,20)],BASE+20*MINUTE,window_minutes=20)
        self.assertTrue(result['valid'])
        self.assertEqual(result['coverage']['actual'],19)
        self.assertEqual(result['bars'][0]['close'],90.)

    def test_identical_duplicates_deduplicate_conflicts_reject(self):
        source = [bar(i) for i in range(20)]
        duplicate = prepare(source+[bar(8)],BASE+20*MINUTE,window_minutes=20)
        self.assertTrue(duplicate['valid'])
        self.assertEqual(duplicate['coverage']['actual'],20)
        self.assertEqual(duplicate['coverage']['duplicates'],1)
        conflict = prepare(source+[bar(8,101.)],BASE+20*MINUTE,window_minutes=20)
        self.assertFalse(conflict['valid'])
        self.assertIn('Conflicting duplicate',conflict['reason'])

    def test_schema_alignment_and_synthetic_rows_cannot_inflate_coverage(self):
        for change in ({'close':float('nan')},{'low':102.},{'timestamp_ms':BASE+1},{'volume':-1.}):
            with self.subTest(change=change):
                source = [bar(i) for i in range(20)]
                source[5].update(change)
                self.assertFalse(prepare(source,BASE+20*MINUTE,window_minutes=20)['valid'])
        source = [dict(bar(i),synthetic=i in (5,6)) for i in range(20)]
        result = prepare(source,BASE+20*MINUTE,window_minutes=20)
        self.assertEqual(result['coverage']['actual'],18)
        self.assertFalse(result['coverage']['eligible'])

    def test_output_bounded_and_input_unchanged(self):
        source = [bar(i) for i in range(10080)]
        before = dict(source[0])
        result = prepare(source,BASE+10080*MINUTE)
        self.assertEqual(len(result['bars']),10080)
        self.assertEqual(source[0],before)
        self.assertFalse(prepare(source,BASE+10080*MINUTE,window_minutes=10081)['valid'])

    def test_observed_turnover_survives_preparation_and_identical_deduplication(self):
        source = [dict(bar(index),turnover=1000.) for index in range(20)]
        result = prepare(source+[dict(source[8])],BASE+20*MINUTE,window_minutes=20)
        self.assertTrue(result['valid'],result['reason'])
        self.assertEqual([row['turnover'] for row in result['bars']],[1000.]*20)
        self.assertEqual(result['coverage']['actual'],20)
        self.assertEqual(result['coverage']['duplicates'],1)
        gappy = prepare(source[:8]+source[9:],BASE+20*MINUTE,window_minutes=20)
        self.assertEqual(gappy['bars'][8]['turnover'],0.)
        self.assertEqual(sum(row['turnover'] for row in gappy['bars']),19000.)

    def test_invalid_or_conflicting_turnover_rejects_prepared_history(self):
        for turnover in (-1.,float('nan'),float('inf'),True,None):
            with self.subTest(turnover=turnover):
                source = [dict(bar(index),turnover=1000.) for index in range(20)]
                source[5]['turnover'] = turnover
                result = prepare(source,BASE+20*MINUTE,window_minutes=20)
                self.assertFalse(result['valid'])
        source = [dict(bar(index),turnover=1000.) for index in range(20)]
        conflict = prepare(source+[dict(source[8],turnover=999.)],BASE+20*MINUTE,window_minutes=20)
        self.assertFalse(conflict['valid'])
        self.assertIn('Conflicting duplicate',conflict['reason'])

    def test_quote_turnover_reaches_radar_after_full_window_preparation(self):
        from unittest.mock import patch
        from trader.research.kucoin_radar import radar
        from trader.tests.test_kucoin_radar import candles,market,setup,NOW
        source = [dict(row,turnover=1000.) for row in candles()]
        metadata = market(filter_mode='candle-only filters',quote_turnover_24h=None,
                          candle_turnover_unit='quote_usdt')
        with patch('trader.research.kucoin_radar.features',return_value={}), \
                patch('trader.research.kucoin_radar.build_setup',setup):
            result = radar([dict(pair='TURNOVER',bars=source,market=metadata)],NOW)
        self.assertEqual(len(result['radar']),1,result['rejected'])
        self.assertEqual(result['radar'][0]['quote_turnover_24h'],1440000.)
        self.assertEqual(result['radar'][0]['candle_coverage']['actual'],10080)
