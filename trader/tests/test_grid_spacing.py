import unittest
from trader.radar.spacing import economics, choose_count
from trader.autopilot import policy
from trader.tests.test_autopilot_policy import row

class SpacingTests(unittest.TestCase):
    def test_two_percent_with_seventy_grids_loses_after_fees(self):
        result = economics(100, 102, 70)
        self.assertFalse(result['viable'])
        self.assertLess(result['net_per_unit_low'], 0)
        self.assertAlmostEqual(result['step_pct'], .028571, places=5)

    def test_count_fits_fixed_range_and_twenty_percent_fee_cushion(self):
        count = choose_count(100, 102)
        self.assertEqual(count, 8)  # was 6 at taker 0.0006; maker 0.0002 fits more grids
        result = economics(100, 102, count)
        self.assertTrue(result['viable'])
        self.assertGreaterEqual(result['net_per_unit_low'], .2*result['fees_per_unit_low'])
        self.assertEqual(choose_count(100, 100.1), 0)

    def test_admission_uses_actual_lines_not_claimed_step(self):
        candidate = row('A', price=101, range_low=100, range_high=102, grids=70, step_pct=.8)
        self.assertFalse(policy.eligible(policy.new_state(0), candidate, 'LONG', 'long', 0))

    def test_trend_also_requires_resistance_and_support(self):
        candidate = row('A', resistance=None)
        self.assertFalse(policy.eligible(policy.new_state(0), candidate, 'LONG', 'long', 0))
