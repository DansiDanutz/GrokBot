"""Entry ranges use confirmed structure, never an arbitrary wider ATR stop."""
import unittest
from trader.radar.support import levels

class SupportTests(unittest.TestCase):
    def test_repeated_confirmed_lows_and_highs_define_range(self):
        candles=[]
        for i in range(30):
            low=90 if i in (4,12,20) else 96
            high=110 if i in (7,15,23) else 104
            candles.append((i*3600000,100,high,low,100,1))
        result=levels(candles,100,2)
        self.assertEqual(result['support'],90)
        self.assertEqual(result['resistance'],110)
        self.assertEqual(result['support_touches'],3)
        candles[-2:] = [(28*3600000,120,124,116,120,1),(29*3600000,120,124,116,120,1)]
        self.assertEqual(levels(candles,120,2)['support'],110)

    def test_unconfirmed_last_candle_and_unretested_level_are_not_support(self):
        candles=[(i*3600000,100,104,96,100,1) for i in range(20)]
        candles[-1]=(19*3600000,100,104,80,100,1)
        self.assertIsNone(levels(candles,100,2)['support'])
        self.assertIsNone(levels(candles,100,2)['resistance'])
