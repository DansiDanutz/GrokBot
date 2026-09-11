"""Entry selection keeps chart structure, fee room and actual order balance."""
import unittest
from trader.radar.layout import order_split, layout_valid, select_range
from trader.radar.support import candidates, levels


class LayoutTests(unittest.TestCase):
    def test_direction_splits_and_neutral_two_books(self):
        self.assertEqual(order_split(80, .5, 100, 100, 'LONG'), (40, 60))
        self.assertEqual(order_split(70, .5, 100, 100, 'SHORT'), (60, 40))
        self.assertEqual(order_split(75, .5, 100, 100, 'NEUTRAL'), (99, 101))
        for direction, low in [('LONG', 80), ('SHORT', 70), ('NEUTRAL', 75)]:
            self.assertTrue(layout_valid(low, .5, 100, 100, direction))
        self.assertFalse(layout_valid(80, 2, 10, 100, 'LONG'))
        self.assertFalse(layout_valid(80, .5, 100, 105, 'LONG'))

    def test_split_matches_engine_on_and_between_grid_lines(self):
        from trader.papergrid.engine import open_bot
        for direction in ('LONG','SHORT','NEUTRAL'):
            for price in (100,100.13):
                spec=dict(bot_id=1,symbol='TEST',direction=direction,range_low=75,
                          range_high=125,grid_interval=.5,step_pct=.5,grids=100,
                          notional_usdt=1000,leverage=5,funding_pct=0,
                          accounting_version=2,quantity_per_grid=.1)
                bot=open_bot(spec,price,0)
                buys=sum(o['side']=='buy' for o in bot['orders'])
                sells=sum(o['side']=='sell' for o in bot['orders'])
                self.assertEqual(order_split(75,.5,100,price,direction),(buys,sells))

    def test_one_order_tolerance_is_inclusive(self):
        self.assertTrue(layout_valid(80, .5, 100, 100.5, 'LONG'))
        self.assertFalse(layout_valid(80, .5, 100, 101, 'LONG'))
        self.assertFalse(layout_valid(80, 0, 100, 100, 'LONG'))

    def test_selector_rejects_narrow_structure_and_selects_confirmed_outer_pair(self):
        row=dict(symbol='TEST', price=100, tick_size=.01, maintain_margin=.005,
                 risk_limit=1_000_000, multiplier=.001, lot_size=1)
        result, reason=select_range([(99, 3), (80, 2)], [(101, 3), (130, 2)], row, 'LONG')
        self.assertEqual(reason, '')
        self.assertEqual((result['range_low'],result['range_high']), (80,130))
        self.assertGreaterEqual(result['grids'],70)
        self.assertGreater(result['profit_pct_min'],1)
        self.assertTrue(layout_valid(80,result['grid_interval'],result['grids'],100,'LONG'))
        self.assertGreater(result['quantity_per_grid'],0)
        missing, reason=select_range([(99,3)],[(101,3)],row,'LONG')
        self.assertIsNone(missing)
        self.assertEqual(reason,'INSUFFICIENT_GRID_ROOM')

    def test_selector_rejects_wrong_ratio_and_missing_risk(self):
        row=dict(symbol='TEST',price=100,tick_size=0)
        self.assertEqual(select_range([(80,2)],[(105,2)],row,'LONG')[1], 'ENTRY_SPLIT')
        self.assertEqual(select_range([(80,2)],[(130,2)],row,'LONG')[1], 'LIQUIDATION_OR_LOT_LIMIT')
        self.assertEqual(select_range([],[(130,2)],row,'LONG')[1], 'MISSING_STRUCTURE')

    def test_all_confirmed_levels_are_exposed_without_changing_nearest_api(self):
        candles=[]
        for i in range(60):
            low=80 if i in (4,12) else 90 if i in (20,28) else 96
            high=130 if i in (7,15) else 110 if i in (23,31) else 104
            candles.append((i*3600000,100,high,low,100,1))
        supports,resistances=candidates(candles,100,2)
        self.assertEqual(supports,[(80,2),(90,2)])
        self.assertEqual(resistances,[(110,2),(130,2)])
        self.assertEqual(levels(candles,100,2)['support'],90)

class GridFloorTests(unittest.TestCase):
    """A confirmed structural range must stay usable when it is narrow."""

    def test_narrow_confirmed_range_keeps_a_feasible_layout(self):
        """A 12% structural range has no fee-safe 70-grid layout but must stay usable."""
        from trader.radar.layout import MIN_GRIDS, layout_valid
        from trader.radar.spacing import choose_count, economics
        low, high, tick = .13404, .15055, .00001
        for direction, price in (('SHORT', .1463), ('NEUTRAL', .1420)):
            with self.subTest(direction=direction):
                largest = choose_count(low, high, tick_size=tick, direction=direction)
                self.assertLess(largest, 70)
                feasible = [count for count in range(largest, MIN_GRIDS - 1, -1)
                            if economics(low, high, count, tick_size=tick,
                                         direction=direction)['viable']
                            and layout_valid(low, economics(low, high, count, tick_size=tick,
                                             direction=direction)['interval'],
                                             count, price, direction)]
                self.assertTrue(feasible, 'no fee-safe layout survives the grid floor')
                self.assertLess(max(feasible), 70)
