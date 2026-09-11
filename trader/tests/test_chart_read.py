"""Synthetic closed-bar acceptance for independent chart interpretation."""
import copy
import math
import unittest

from trader.features.chart_read import read_timeframe, read_chart, interpret_entry

MINUTE = 60000
DURATIONS = {'5m': 5*MINUTE, '15m': 15*MINUTE, '1h': 60*MINUTE,
             '4h': 240*MINUTE, '1d': 1440*MINUTE}
ASOF = 100*DURATIONS['1d']


def bars(timeframe='4h', trend=0, count=80):
    duration = DURATIONS[timeframe]
    result = []
    for index in range(count):
        close = 100+trend*(index-count+1)+2*math.sin(index*math.pi/4)
        result.append(dict(timestamp_ms=ASOF-(count-index)*duration,
            open=close-.05, high=close+.3, low=close-.3, close=close, volume=100))
    return result


def entry_cells(direction='long'):
    long = direction == 'long'
    context = dict(close=100, previous_close=99 if long else 101, last_high=101,
                   last_low=99, prior_high=105, prior_low=95, ema20=100, estimated=False,
                   lookback_observed_fraction=1., latest_observed_minutes=5)
    return {'1h': dict(available=True, indicator_observed_fraction=1., support_resistance={'support': 95, 'resistance': 105}),
            **{tf: dict(available=True, indicator_observed_fraction=1., entry_context=dict(context)) for tf in ('15m', '5m')}}


class ChartReadTests(unittest.TestCase):
    def test_synthetic_up_down_and_range_have_explained_indicator_evidence(self):
        for trend, expected in ((.45, 'up'), (-.45, 'down'), (0, 'range')):
            with self.subTest(trend=trend):
                result = read_timeframe(bars(trend=trend), '4h', ASOF)
                self.assertTrue(result['available'])
                self.assertEqual(result['regime'], expected)
                self.assertEqual(result['direction'], expected if trend else 'neutral')
                self.assertIsNotNone(result['ema20'])
                self.assertIsNotNone(result['ema50_slope_atr'])
                self.assertGreater(result['atr14'], 0)
                self.assertLessEqual(result['confidence'], 1)
                self.assertIn('not calibrated probability', result['confidence_basis'])
                self.assertEqual(result['last_closed_ms'], ASOF)
                self.assertTrue(all(p['confirmed_at_ms'] <= ASOF
                                    for p in result['structure']['highs']+result['structure']['lows']))

    def test_daily_ema50_requires_fifty_and_its_slope_requires_fifty_five_closed_bars(self):
        for count in (12, 49, 50, 54, 55):
            with self.subTest(count=count):
                result = read_timeframe(bars('1d', .45, count), '1d', ASOF)
                self.assertEqual(result['availability']['ema50'], count >= 50)
                self.assertEqual(result['availability']['ema50_slope'], count >= 55)
                self.assertEqual(result['available'], count >= 55)
                if count < 55:
                    self.assertIsNone(result['confidence'])
                    self.assertEqual(result['regime'], 'unknown')

    def test_open_future_and_unconfirmed_pivots_cannot_change_current_read(self):
        source = bars(trend=.45)
        before = copy.deepcopy(source)
        future = dict(timestamp_ms=ASOF, open=10000, high=20000, low=1, close=15000, volume=1)
        self.assertEqual(read_timeframe(source, '4h', ASOF),
                         read_timeframe(source+[future], '4h', ASOF))
        self.assertEqual(source, before)
        source[-1]['high'] = 1000
        result = read_timeframe(source, '4h', ASOF)
        self.assertTrue(all(p['price'] != 1000 for p in result['structure']['highs']))
        self.assertTrue(all(p['timestamp_ms'] <= source[-3]['timestamp_ms']
                            for p in result['structure']['highs']+result['structure']['lows']))

    def test_missing_or_stale_bars_never_manufacture_indicator_confidence(self):
        source = bars(trend=.45)
        del source[-20]
        result = read_timeframe(source, '4h', ASOF)
        self.assertFalse(result['available'])
        self.assertEqual(result['contiguous_bars'], 19)
        self.assertIsNone(result['confidence'])
        stale = read_timeframe(bars(trend=.45)[:-1], '4h', ASOF)
        self.assertFalse(stale['available'])
        self.assertFalse(stale['fresh'])
        self.assertIsNone(stale['confidence'])

    def test_five_cells_use_daily_and_four_hour_agreement_or_explicit_variant(self):
        frames = {tf: bars(tf, .45) for tf in DURATIONS}
        same = read_chart(frames, ASOF)
        self.assertEqual(set(same['five_cells']), set(DURATIONS))
        self.assertEqual(same['direction'], 'long')
        frames['1d'] = bars('1d', -.45)
        self.assertEqual(read_chart(frames, ASOF)['direction'], 'neutral')
        self.assertEqual(read_chart(frames, ASOF, bias_mode='4h-only')['direction'], 'long')
        frames['1d'] = bars('1d', .45, 20)
        incomplete = read_chart(frames, ASOF)
        self.assertEqual(incomplete['direction'], 'neutral')
        self.assertIn('unavailable', incomplete['bias_reason'])
        with self.assertRaises(ValueError):
            read_chart(frames, ASOF, bias_mode='5m-only')

    def test_pullback_trigger_stays_inside_one_hour_levels_for_each_direction(self):
        for direction, trigger in [('long', 101), ('short', 99)]:
            with self.subTest(direction=direction):
                result = interpret_entry(entry_cells(direction), direction)
                self.assertTrue(result['eligible'])
                self.assertEqual(result['trigger'], trigger)
                self.assertEqual((result['low'], result['high']), (95, 105))
                self.assertEqual(result['entry_basis'], '15m and 5m EMA20 pullback with close reversal')
        neutral = interpret_entry(entry_cells(), 'neutral')
        self.assertTrue(neutral['eligible'])
        self.assertEqual(neutral['trigger'], 100)

    def test_breakout_is_not_chased_and_edge_trigger_is_rejected(self):
        for direction, field, extreme in [('long', 'last_high', 106), ('short', 'last_low', 94)]:
            cells = entry_cells(direction)
            cells['5m']['entry_context'][field] = extreme
            result = interpret_entry(cells, direction)
            self.assertFalse(result['eligible'])
            self.assertIn('fresh', result['reason'])
        cells = entry_cells()
        cells['1h']['support_resistance']['resistance'] = 101
        self.assertFalse(interpret_entry(cells, 'long')['eligible'])
        cells = entry_cells()
        cells['15m']['entry_context']['close'] = 98
        self.assertFalse(interpret_entry(cells, 'long')['eligible'])

    def test_malformed_closed_inputs_fail_closed(self):
        for changes in ({'close': float('nan')}, {'high': 1}, {'volume': -1}):
            source = bars()
            source[-1].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                read_timeframe(source, '4h', ASOF)

    def test_neutral_entries_wait_near_edges_breakouts_or_missing_data(self):
        cells = entry_cells()
        cells['5m']['entry_context']['close'] = 96
        self.assertFalse(interpret_entry(cells, 'neutral')['eligible'])
        self.assertTrue(interpret_entry(cells, 'neutral', neutral_band=(.05, .95))['eligible'])
        cells = entry_cells()
        cells['15m']['entry_context']['last_high'] = 106
        self.assertFalse(interpret_entry(cells, 'neutral')['eligible'])
        cells = entry_cells()
        cells['1h']['available'] = False
        self.assertFalse(interpret_entry(cells, 'neutral')['eligible'])

    def test_unfilled_timeframe_buckets_reset_indicator_history(self):
        for changes in ({'observed': False, 'open': None, 'high': None, 'low': None,
                         'close': None, 'volume': None, 'closed': True},):
            source = bars(trend=.45)
            source[-20].update(changes)
            result = read_timeframe(source, '4h', ASOF)
            self.assertFalse(result['available'])
            self.assertEqual(result['contiguous_bars'], 19)
            self.assertEqual(result['unavailable_buckets'], 1)
            self.assertIsNone(result['confidence'])

    def test_low_confidence_bias_is_neutral_and_strength_does_not_invent_swings(self):
        frames = {tf: bars(tf, .45) for tf in DURATIONS}
        for index, row in enumerate(frames['1d']):
            row.update(open=100+index, high=101+index, low=99+index, close=100+index)
        result = read_chart(frames, ASOF)
        self.assertEqual(result['five_cells']['1d']['structure']['direction'], 'unknown')
        self.assertLess(result['five_cells']['1d']['confidence'], .75)
        self.assertEqual(result['direction'], 'neutral')
        self.assertIn('confidence', result['bias_reason'])

    def test_missing_signal_values_wait_and_invalid_neutral_band_is_rejected(self):
        cells = entry_cells()
        del cells['5m']['entry_context']['ema20']
        self.assertFalse(interpret_entry(cells, 'long')['eligible'])
        cells = entry_cells()
        cells['15m']['entry_context']['prior_high'] = float('nan')
        self.assertFalse(interpret_entry(cells, 'neutral')['eligible'])
        for band in ((True, .8), (0, 1), (.8, .2), (float('nan'), .8), None):
            with self.subTest(band=band), self.assertRaises(ValueError):
                interpret_entry(entry_cells(), 'neutral', neutral_band=band)

    def test_real_timeframe_builder_incomplete_buckets_reset_the_chart(self):
        from trader.features.timeframes import build_timeframes
        minute_rows = [dict(timestamp_ms=ASOF-(400-index)*MINUTE,
                            open=100+index*.01, high=101+index*.01,
                            low=99+index*.01, close=100+index*.01, volume=1)
                       for index in range(400)]
        frames = build_timeframes(minute_rows, ASOF)
        self.assertTrue(read_timeframe(frames['5m'], '5m', ASOF)['available'])
        del minute_rows[-98]
        frames = build_timeframes(minute_rows, ASOF)
        result = read_timeframe(frames['5m'], '5m', ASOF)
        self.assertFalse(result['available'])
        self.assertEqual(result['unavailable_buckets'], 1)
        self.assertEqual(result['contiguous_bars'], 19)

    def test_causally_filled_daily_buckets_keep_ema_atr_and_disclose_coverage_penalty(self):
        source = bars('1d', .45)
        original = read_timeframe(source, '1d', ASOF)
        source[5].update(observed=False, synthetic=True, indicator_only=True,
                         observed_minutes=1439, expected_minutes=1440, missing_minutes=1)
        result = read_timeframe(source, '1d', ASOF)
        self.assertTrue(result['available'])
        self.assertEqual(result['contiguous_bars'], 80)
        self.assertEqual(result['ema50'], original['ema50'])
        self.assertEqual(result['atr14'], original['atr14'])
        self.assertEqual(result['estimated_buckets'], 1)
        self.assertEqual(result['missing_indicator_minutes'], 1)
        self.assertLess(result['confidence'], original['confidence'])
        self.assertIn('filled', ' '.join(result['warnings']))
        self.assertEqual(result['structure']['estimated_windows'], 5)
        self.assertAlmostEqual(result['indicator_observed_fraction'], 1-1/(80*1440))

    def test_filled_pivot_or_entry_candle_cannot_be_claimed_as_observed_signal(self):
        source = bars('5m', .45)
        source[-3].update(synthetic=True, indicator_only=True, observed=False)
        result = read_timeframe(source, '5m', ASOF)
        self.assertTrue(result['available'])
        self.assertTrue(result['entry_context']['estimated'])
        self.assertTrue(all(p['estimated'] for p in result['structure']['highs']+result['structure']['lows']
                            if p['timestamp_ms'] == source[-3]['timestamp_ms']))

    def test_timeframe_builder_filled_gap_retains_indicator_continuity(self):
        from trader.features.timeframes import build_timeframes
        rows = [dict(timestamp_ms=ASOF-(400-index)*MINUTE,
                     open=100+index*.01, high=101+index*.01, low=99+index*.01,
                     close=100+index*.01, volume=1) for index in range(400)]
        del rows[-98]
        frames = build_timeframes(rows, ASOF, indicator_fill=True)
        result = read_timeframe(frames['5m'], '5m', ASOF)
        self.assertTrue(result['available'])
        self.assertEqual(result['contiguous_bars'], 80)
        self.assertEqual(result['missing_indicator_minutes'], 1)
        self.assertEqual(result['estimated_buckets'], 1)

    def test_daily_ema_remains_available_when_every_bucket_has_one_causally_filled_minute(self):
        source = bars('1d', .45, 60)
        for row in source:
            row.update(observed=False, synthetic=True, indicator_only=True,
                       observed_minutes=1439, expected_minutes=1440, missing_minutes=1)
        result = read_timeframe(source, '1d', ASOF)
        self.assertTrue(result['available'])
        self.assertTrue(result['availability']['ema50'])
        self.assertTrue(result['availability']['ema50_slope'])
        self.assertIsNotNone(result['atr14'])
        self.assertEqual(result['estimated_buckets'], 60)
        self.assertEqual(result['missing_indicator_minutes'], 60)
        self.assertAlmostEqual(result['confidence_coverage_penalty'], 1439/1440)

    def test_tiny_daily_gaps_keep_estimated_up_structure_and_low_coverage_reduces_confidence(self):
        source = bars('1d', .45, 80)
        for row in source:
            row.update(observed=False, synthetic=True, indicator_only=True,
                       observed_minutes=1439, expected_minutes=1440)
        result = read_timeframe(source, '1d', ASOF)
        self.assertEqual(result['regime'], 'up')
        self.assertEqual(result['structure']['direction'], 'up')
        self.assertGreater(result['confidence'], .95)
        self.assertGreater(result['structure']['estimated_pivot_count'], 0)
        self.assertEqual(result['structure']['observed_pivot_count'], 0)
        self.assertTrue(all(p['estimated'] for p in result['structure']['highs']+result['structure']['lows']))
        for row in source:
            row['observed_minutes'] = 720
        low = read_timeframe(source, '1d', ASOF)
        self.assertLess(low['confidence'], result['confidence'])
        self.assertAlmostEqual(low['confidence'], .5)

    def test_estimated_entry_patterns_and_range_pass_known_95_percent_coverage(self):
        for direction in ('long', 'short', 'neutral'):
            cells = entry_cells(direction)
            cells['1h']['support_resistance']['estimated'] = True
            cells['1h']['indicator_observed_fraction'] = .99
            for tf in ('15m', '5m'):
                cells[tf]['entry_context'].update(estimated=True, lookback_observed_fraction=.99,
                                                   latest_observed_minutes=4)
            result = interpret_entry(cells, direction)
            self.assertTrue(result['eligible'])
            self.assertTrue(result['estimated'])
            self.assertTrue(result['requires_actual_observed_quote'])
            self.assertEqual(result['entry_observed_fraction'], .99)
            cells['5m']['entry_context']['lookback_observed_fraction'] = .94
            self.assertFalse(interpret_entry(cells, direction)['eligible'])
            self.assertTrue(interpret_entry(cells, direction, minimum_entry_coverage=.9)['eligible'])

    def test_fully_synthetic_latest_signal_or_unknown_estimated_range_coverage_waits(self):
        cells = entry_cells()
        cells['5m']['entry_context'].update(estimated=True, latest_observed_minutes=0)
        self.assertFalse(interpret_entry(cells, 'neutral')['eligible'])
        cells = entry_cells()
        cells['1h']['support_resistance']['estimated'] = True
        del cells['1h']['indicator_observed_fraction']
        self.assertFalse(interpret_entry(cells, 'neutral')['eligible'])
