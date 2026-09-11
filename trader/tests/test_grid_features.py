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
