import unittest

from trader.strategies.grid_features import features


def candles(count=10080, slope=0.0):
    return [{'open': 100 + slope*i, 'high': 102 + slope*i,
             'low': 98 + slope*i, 'close': 100 + slope*i} for i in range(count)]


class FeatureTests(unittest.TestCase):
    def test_flat_is_centered_and_has_zero_trend(self):
        result = features(candles(), 0)
        self.assertTrue(result['valid'])
        self.assertEqual(result['position_24h'], .5)
        self.assertEqual(result['position_7d'], .5)
        self.assertEqual(result['ema_slope_4h'], 0)
        self.assertEqual(result['structure_24h'], 0)
        self.assertEqual(result['funding_sign'], 0)

    def test_trend_and_funding_are_signed(self):
        up = features(candles(slope=.01), -.0002)
        down = features(candles(slope=-.001), .0002)
        self.assertGreater(up['ema_slope_4h'], 0)
        self.assertEqual(up['structure_24h'], 1)
        self.assertEqual(up['funding_sign'], -1)
        self.assertLess(down['ema_slope_24h'], 0)
        self.assertEqual(down['structure_4h'], -1)

    def test_history_and_nonfinite_ohlc_rejected(self):
        self.assertFalse(features(candles(100), 0)['valid'])
        bars = candles()
        bars[-1]['close'] = float('nan')
        self.assertFalse(features(bars, 0)['valid'])

    def test_fresh_extremes_exclude_current_candle(self):
        bars = candles()
        bars[-1] = {'open': 100, 'high': 110, 'low': 98, 'close': 105}
        self.assertTrue(features(bars)['fresh_high'])

    def test_conflicting_timestamp_duplicates_rejected(self):
        bars = candles()
        for i, bar in enumerate(bars):
            bar['timestamp_ms'] = i*60000
        self.assertTrue(features(bars)['valid'])
        bars[800]['timestamp_ms'] += 60000
        bars[800]['close'] = 101.
        self.assertFalse(features(bars)['valid'])

    def test_confirmed_swing_levels_require_two_closed_bars_on_right(self):
        bars = candles(10082)
        bars[10070]['low'] = 90.
        bars[10070]['high'] = 110.
        bars[10079]['low'] = 80.
        before = features(bars[:10080])['support_resistance']
        self.assertEqual(before['confirmation_bars'],2)
        self.assertEqual([level['price'] for level in before['supports']],[90.])
        self.assertEqual([level['price'] for level in before['resistances']],[110.])
        after = features(bars)['support_resistance']
        self.assertEqual([level['price'] for level in after['supports']],[80.,90.])
        self.assertEqual(before['support_pivot_count'],1)
        self.assertEqual(after['support_pivot_count'],2)
        self.assertEqual(features(bars[:10080])['support_resistance'],before)

    def test_flat_history_does_not_invent_confirmed_pivots(self):
        evidence = features(candles())['support_resistance']
        self.assertEqual(evidence['supports'],[])
        self.assertEqual(evidence['resistances'],[])
        self.assertEqual(evidence['lookback_minutes'],10080)

    def test_95_percent_history_forward_fills_for_indicators(self):
        bars = candles()
        for index,bar in enumerate(bars):
            bar['timestamp_ms'] = index*60000
        sparse = [bar for index,bar in enumerate(bars) if not 1000 <= index < 1504]
        result = features(sparse,asof_ms=10080*60000)
        self.assertTrue(result['valid'],result.get('reason'))
        self.assertEqual(result['coverage']['fraction'],.95)
        self.assertEqual(result['history_minutes'],10080)
        self.assertEqual(result['price'],100.)
        self.assertFalse(features(sparse[:-1],asof_ms=10080*60000)['valid'])

    def test_prepared_history_preserves_coverage_and_unknown_funding(self):
        from trader.strategies.candle_coverage import prepare
        bars = candles()
        for index,bar in enumerate(bars):
            bar['timestamp_ms'] = index*60000
        prepared = prepare(bars[:200]+bars[201:],10080*60000)
        result = features(prepared,funding_rate=None)
        self.assertTrue(result['valid'],result.get('reason'))
        self.assertEqual(result['coverage']['actual'],10079)
        self.assertTrue(result['funding_unknown'])
        self.assertIsNone(result['funding_rate'])
        self.assertEqual(result['funding_sign'],0)

    def test_synthetic_neighbors_do_not_confirm_swing_pivots(self):
        from trader.strategies.candle_coverage import prepare
        bars = candles()
        for index,bar in enumerate(bars):
            bar['timestamp_ms'] = index*60000
        bars[10070]['low'] = 90.
        observed = features(prepare(bars,10080*60000))
        self.assertEqual(observed['support_resistance']['support_pivot_count'],1)
        with_gap = features(prepare(bars[:10071]+bars[10072:],10080*60000))
        self.assertTrue(with_gap['valid'])
        self.assertEqual(with_gap['support_resistance']['support_pivot_count'],0)

    def test_malformed_prepared_ohlc_is_rejected(self):
        from trader.strategies.candle_coverage import prepare
        bars = candles()
        for index,bar in enumerate(bars):
            bar['timestamp_ms'] = index*60000
        prepared = prepare(bars,10080*60000)
        prepared['bars'][-1]['close'] = float('nan')
        self.assertFalse(features(prepared)['valid'])
