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
        self.assertEqual(count, 6)  # Futures Trading Bot charges 0.06% on each leg
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


class FundingStressTests(unittest.TestCase):
    def scenario(self, **kw):
        from trader.radar.spacing import funding_stress
        args=dict(low=100,high=150,grids=70,rate_pct=.01,settlements=1,direction='LONG')
        args.update(kw)
        return funding_stress(**args)

    def test_fee_only_and_zero_settlement_match(self):
        self.assertAlmostEqual(self.scenario(settlements=0)['profit_pct_min'],
                               economics(100,150,70)['profit_pct_min'])

    def test_adverse_funding_costs_cash_not_leveraged_cash(self):
        a=self.scenario(); b=self.scenario(leverage=10)
        self.assertAlmostEqual(a['funding_per_unit'],.015)
        self.assertEqual(a['funding_per_unit'],b['funding_per_unit'])
        self.assertAlmostEqual(b['profit_pct_min'],2*a['profit_pct_min'])

    def test_receipts_never_rescue_floor_and_neutral_does_not_net_sides(self):
        long=self.scenario(rate_pct=-.01)
        self.assertEqual(long['funding_per_unit'],0)
        self.assertGreater(self.scenario(direction='SHORT',rate_pct=-.01)['funding_per_unit'],0)
        for rate in (-.01,.01):
            self.assertGreater(self.scenario(direction='NEUTRAL',rate_pct=rate)['funding_per_unit'],0)

    def test_funding_can_erase_fee_only_floor(self):
        result=self.scenario(low=.582,high=.7504,tick_size=.0001,rate_pct=.01)
        self.assertTrue(economics(.582,.7504,70,tick_size=.0001)['viable'])
        self.assertFalse(result['passes_floor'])
        self.assertLess(result['profit_pct_min'],1)
        self.assertEqual(result['status'],'SCENARIO')

    def test_missing_rate_is_unknown_not_zero(self):
        for rate in (None,float('nan'),float('inf'),True,'0.01'):
            self.assertEqual(self.scenario(rate_pct=rate),{'status':'UNKNOWN_RATE'})

    def test_invalid_scenario_is_rejected(self):
        for args in ({'settlements':-1},{'settlements':True},{'settlements':1.5},
                     {'direction':'OTHER'}):
            with self.assertRaises(ValueError):self.scenario(**args)
