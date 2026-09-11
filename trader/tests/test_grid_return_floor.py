import unittest
from trader.radar.spacing import economics, choose_count, align_bounds
from trader.papergrid import open_bot

class ReturnFloorTests(unittest.TestCase):
    def test_ray_long_matches_fee_deducted_form(self):
        e = economics(1.4, 2, 70, tick_size=.0001, leverage=5)
        self.assertAlmostEqual(e['interval'], .0085)
        self.assertAlmostEqual(e['profit_pct_min'], 1.53, delta=.01)
        self.assertAlmostEqual(e['profit_pct_max'], 2.43, delta=.01)
        self.assertTrue(e['viable'])

    def test_ray_neutral_matches_fee_deducted_form(self):
        e = economics(1.1, 2, 70, tick_size=.0001, leverage=5)
        self.assertAlmostEqual(e['interval'], .0128)
        self.assertAlmostEqual(e['profit_pct_min'], 2.61, delta=.01)
        self.assertAlmostEqual(e['profit_pct_max'], 5.21, delta=.01)

    def test_every_grid_must_strictly_exceed_one_percent(self):
        self.assertFalse(economics(100, 102, 10)['viable'])
        n = choose_count(100, 102)
        self.assertGreater(economics(100,102,n)['profit_pct_min'],1)
        self.assertFalse(economics(100,102,n+1)['viable'])
        self.assertEqual(choose_count(100,100.1),0)

    def test_arithmetic_orders_use_the_validated_interval(self):
        spec=dict(bot_id=1,symbol='RAYUSDTM',direction='LONG',range_low=1.4,range_high=2,
                  step_pct=.55,grids=70,notional_usdt=1000,leverage=5,funding_pct=0,grid_interval=.0085)
        b=open_bot(spec,1.5468,0)
        self.assertAlmostEqual(b['lines'][1],1.4085)
        self.assertAlmostEqual(b['lines'][-1],1.995)

    def test_exactly_one_percent_and_short_worst_pair(self):
        delta = 100 * (.002 + .0012) / (1 - .0006)
        self.assertFalse(economics(100,100+delta,1)['viable'])
        n=choose_count(1.4,2,tick_size=.0001,direction='SHORT')
        e=economics(1.4,2,n,tick_size=.0001,direction='SHORT')
        self.assertGreater(e['profit_pct_min'],1)
        for i in range(n):
            buy=1.4+i*e['interval']; sell=buy+e['interval']
            self.assertGreater((sell-buy-.0006*(buy+sell))/sell*500,1)

    def test_neutral_must_cover_short_pair_margin_too(self):
        self.assertTrue(economics(1,1.0296,9,direction='LONG')['viable'])
        self.assertFalse(economics(1,1.0296,9,direction='NEUTRAL')['viable'])

    def test_pivot_medians_align_inward_to_contract_tick(self):
        self.assertEqual(align_bounds(1.40005,1.99995,.0001),(1.4001,1.9999))
