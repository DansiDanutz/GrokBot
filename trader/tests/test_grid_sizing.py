import unittest
from trader.strategies.grid_sizing import size_grid


class SizingTests(unittest.TestCase):
    def test_twenty_grid_reference_case(self):
        sizing = size_grid(centre=100, multiplier=.001, lot_size=1)
        self.assertAlmostEqual(sizing.quantity, 2.5)
        self.assertAlmostEqual(sizing.step / 100, .00526, delta=.00002)
        self.assertGreaterEqual(sizing.profit_floor, 1 - 1e-10)
        self.assertEqual(sizing.form_values('XBTUSDTM')['direction'], 'neutral')

    def test_rounding_maintains_floor(self):
        sizing = size_grid(centre=3.25, tick_size=.01, multiplier=.1, lot_size=1)
        self.assertAlmostEqual(sizing.low / .01, round(sizing.low / .01))
        self.assertAlmostEqual(sizing.high / .01, round(sizing.high / .01))
        self.assertGreaterEqual(sizing.profit_floor, 1 - 1e-10)
        self.assertLessEqual(sizing.quantity * 20 * 3.25, 5000 + 1e-9)

    def test_impossible_lot_and_range(self):
        for kwargs in ({'centre': 10000, 'multiplier': 1},
                       {'centre': 100, 'target_profit': 1000, 'multiplier': .001}):
            with self.assertRaises(ValueError):
                size_grid(**kwargs)

    def test_validation(self):
        for kwargs in ({'centre': float('nan')}, {'centre': 1, 'leverage': 11},
                       {'centre': 1, 'grids': 2.5}, {'centre': 1, 'tick_size': -1}):
            with self.assertRaises(ValueError):
                size_grid(**kwargs)
