import math
import unittest

from trader.research.grid_replacement import decide, realized_rate


class ReplacementTests(unittest.TestCase):
    def test_realized_rate_uses_actual_age_until_window_is_full(self):
        self.assertEqual(realized_rate([1800000], 3600000, 0, 4), 1)
        self.assertEqual(realized_rate([0, 3600000, 7200000], 7200000, 0, 1), 1)

    def test_covers_loss_spread_and_both_fees_over_payback_horizon(self):
        result = decide('A', 'B', 4, 2, -3, 1000, 2500, .2, .3,
                        lookback_hours=4, margin_gph=.1, payback_hours=4)
        self.assertAlmostEqual(result['cost_usdt'], 5.6)
        self.assertTrue(result['replace'])
        self.assertEqual(result['floating_pnl_realized_estimate'], -3)

    def test_positive_floating_pnl_cannot_subsidize_churn(self):
        result = decide('A', 'B', 2.1, 2, 100, 1000, 2500)
        self.assertFalse(result['replace'])
        self.assertAlmostEqual(result['cost_usdt'], 2.1)

    def test_equal_hurdle_does_not_switch_and_same_coin_does_not_switch(self):
        self.assertFalse(decide('A', 'B', 1, 1, 0, 0, 0)['replace'])
        self.assertFalse(decide('A', 'A', 100, 0, 0, 0, 0)['replace'])

    def test_range_exit_stops_even_without_an_eligible_candidate(self):
        result = decide('A', None, 0, 10, -4, 200, 0, outside_range=True)
        self.assertTrue(result['replace'])
        self.assertEqual(result['reason'], 'range_exit')
        self.assertIsNone(result['next_pair'])

    def test_profit_is_not_a_stop_trigger(self):
        self.assertFalse(decide('A', None, 0, 0, 10000, 0, 0)['replace'])

    def test_invalid_numbers_and_future_completions_rejected(self):
        for value in (math.nan, math.inf, -1):
            with self.assertRaises(ValueError):
                decide('A', 'B', value, 0, 0, 0, 0)
        with self.assertRaises(ValueError):
            realized_rate([200], 100, 0, 1)
