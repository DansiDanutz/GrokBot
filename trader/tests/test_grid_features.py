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

    def test_timestamp_ms_gaps_rejected(self):
        bars = candles()
        for i, bar in enumerate(bars):
            bar['timestamp_ms'] = i*60000
        self.assertTrue(features(bars)['valid'])
        bars[800]['timestamp_ms'] += 60000
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
