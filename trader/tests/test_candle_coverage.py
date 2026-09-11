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


class ValidatedCoverageTests(unittest.TestCase):
    def test_fast_preparation_matches_reference_across_boundaries_and_errors(self):
        from trader.strategies.candle_coverage import validate_history,prepare_validated
        source = [dict(bar(i),turnover=1000.) for i in range(20)]
        scenarios = [source, source[:8]+source[9:], source[1:], source[:-2],
                     source+[dict(source[8])], source+[dict(source[8],turnover=999.)],
                     source+[dict(bar(20),close=float('nan'))],
                     [dict(source[0],close=float('nan'))]+source[1:]]
        for rows in scenarios:
            for seed in (None,bar(-1,90.),bar(1)):
                with self.subTest(rows=len(rows),seed=seed):
                    expected = prepare(rows,BASE+20*MINUTE,window_minutes=20,prior_seed=seed)
                    actual = prepare_validated(validate_history(rows),BASE+20*MINUTE,window_minutes=20,prior_seed=seed)
                    self.assertEqual(actual,expected)

    def test_validated_day_blocks_combine_and_views_do_not_invent_prior_seeds(self):
        from trader.strategies.candle_coverage import validate_history,combine_histories,prepare_validated
        source = [bar(i) for i in range(-1,40) if i != 0]
        blocks = [validate_history(source[:15]),validate_history(source[15:])]
        history = combine_histories(blocks)
        self.assertEqual(list(history.rows),source)
        view = history.window(BASE,BASE+20*MINUTE)
        self.assertEqual(prepare_validated(view,BASE+20*MINUTE,window_minutes=20),
                         prepare([row for row in source if BASE <= row['timestamp_ms'] < BASE+20*MINUTE],BASE+20*MINUTE,window_minutes=20))
        self.assertFalse(prepare_validated(view,BASE+20*MINUTE,window_minutes=20)['valid'])
        self.assertTrue(prepare_validated(view,BASE+20*MINUTE,window_minutes=20,prior_seed=source[0])['valid'])

    def test_rolling_windows_reuse_immutable_rows_and_keep_exact_output(self):
        from trader.strategies.candle_coverage import validate_history,prepare_validated
        source = [bar(i) for i in range(40) if i not in (8,28)]
        history = validate_history(source)
        previous = prepare_validated(history,BASE+20*MINUTE,window_minutes=20)
        for end in range(21,40):
            current = prepare_validated(history,BASE+end*MINUTE,window_minutes=20,previous=previous)
            self.assertEqual(current,prepare(source,BASE+end*MINUTE,window_minutes=20))
            self.assertIs(current['bars'][5],previous['bars'][6])
            previous = current
        with self.assertRaises(TypeError):
            current['coverage']['eligible'] = False
        with self.assertRaises(TypeError):
            current['bars'][0]['close'] = 0.
        with self.assertRaises(TypeError):
            current['bars'].append(bar(40))
        with self.assertRaises(TypeError):
            prepare_validated({'coverage':{'eligible':True}},BASE+20*MINUTE)
        source[0]['close'] = 99.
        self.assertEqual(history.rows[0]['close'],100.)

    def test_combined_duplicate_errors_and_rolling_seed_views_match_reference(self):
        from trader.strategies.candle_coverage import validate_history,combine_histories,prepare_validated
        first = [bar(i) for i in range(10)]
        second = [bar(i) for i in range(10,40) if i not in (20,21)]
        for duplicate in (bar(8),bar(8,101.)):
            history = combine_histories([validate_history(first),validate_history(second+[duplicate])])
            self.assertEqual(prepare_validated(history,BASE+20*MINUTE,window_minutes=20),
                             prepare(first+second+[duplicate],BASE+20*MINUTE,window_minutes=20))
        source = first+second
        history = validate_history(source)
        previous = None
        for end in range(20,41):
            rows = [row for row in source if BASE+(end-20)*MINUTE <= row['timestamp_ms'] < BASE+end*MINUTE]
            prior = [row for row in source if row['timestamp_ms'] < BASE+(end-20)*MINUTE]
            for seed in (None,prior[-1] if prior else None):
                view = history.window(BASE+(end-20)*MINUTE,BASE+end*MINUTE)
                current = prepare_validated(view,BASE+end*MINUTE,20,seed,previous)
                self.assertEqual(current,prepare(rows,BASE+end*MINUTE,20,seed))
                previous = current

    def test_trusted_output_is_json_compatible_and_cannot_be_reinitialized(self):
        import json
        from copy import deepcopy
        from trader.strategies.candle_coverage import validate_history,prepare_validated,PreparedHistory
        source = [bar(i) for i in range(20)]
        value = prepare_validated(validate_history(source),BASE+20*MINUTE,20)
        self.assertEqual(json.loads(json.dumps(value)),prepare(source,BASE+20*MINUTE,20))
        self.assertIs(deepcopy(value),value)
        self.assertEqual(len(value.observed_bars),20)
        with self.assertRaises(TypeError):
            PreparedHistory({'valid':True})
        with self.assertRaises(TypeError):
            value['bars'][0].__init__({'close':0.})
        with self.assertRaises(TypeError):
            value['bars'].__init__([])

    def test_cached_same_time_view_membership_changes_match_reference(self):
        from trader.strategies.candle_coverage import validate_history,prepare_validated
        source = [bar(i,100.+i) for i in range(40)]
        history = validate_history(source)
        for before,after in (((0,19),(0,20)),((0,20),(0,19)),
                             ((2,20),(0,20)),((0,20),(2,20))):
            with self.subTest(before=before,after=after):
                old_view = history.window(BASE+before[0]*MINUTE,BASE+before[1]*MINUTE)
                new_view = history.window(BASE+after[0]*MINUTE,BASE+after[1]*MINUTE)
                previous = prepare_validated(old_view,BASE+20*MINUTE,20)
                current = prepare_validated(new_view,BASE+20*MINUTE,20,previous=previous)
                expected_rows = [row for row in source if BASE+after[0]*MINUTE <= row['timestamp_ms'] < BASE+after[1]*MINUTE]
                self.assertEqual(current,prepare(expected_rows,BASE+20*MINUTE,20))
                self.assertEqual(sum(not row['synthetic'] for row in current['bars']),current['coverage']['actual'])

    def test_future_only_view_extension_preserves_safe_overlap_reuse(self):
        from trader.strategies.candle_coverage import validate_history,prepare_validated
        history = validate_history([bar(i) for i in range(40)])
        previous = prepare_validated(history.window(BASE,BASE+20*MINUTE),BASE+20*MINUTE,20)
        current = prepare_validated(history.window(BASE,BASE+40*MINUTE),BASE+20*MINUTE,20,previous=previous)
        self.assertEqual(current,previous)
        self.assertIs(current['bars'][5],previous['bars'][5])

    def test_factory_provenance_rejects_uninitialized_dict_subclass_forgery(self):
        from trader.strategies.candle_coverage import PreparedHistory,is_prepared_history,prepare_validated,validate_history
        forged = dict.__new__(PreparedHistory)
        dict.__setitem__(forged,'valid',True)
        dict.__setitem__(forged,'coverage',{'eligible':True})
        self.assertFalse(is_prepared_history(forged))
        actual = prepare_validated(validate_history([bar(i) for i in range(20)]),BASE+20*MINUTE,20)
        self.assertTrue(is_prepared_history(actual))
        self.assertFalse(is_prepared_history(dict(actual)))
        current = prepare_validated(validate_history([bar(i) for i in range(20)]),BASE+20*MINUTE,20,previous=forged)
        self.assertEqual(current,actual)
