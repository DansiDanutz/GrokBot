"""Prospective per-grid provenance, without rewriting legacy history."""
import json
import unittest
from trader.papergrid import open_bot, step
from trader.autopilot.storage import validate_event


def spec(direction):
    return dict(bot_id=1,symbol='RAYUSDTM',direction=direction,range_low=1.4,
                range_high=2,grid_interval=.0085,grids=70,step_pct=.55,
                quantity_per_grid=39,notional_usdt=1000,leverage=5,funding_pct=0,
                accounting_version=2,fee_rate_maker=.0006,fee_rate_taker=.0006)


class PairEvidenceTests(unittest.TestCase):
    def test_roundtrip_links_both_fills_and_fees_across_restart(self):
        for side in ('LONG','SHORT','NEUTRAL'):
            with self.subTest(side=side):
                bot=open_bot(spec(side),1.5468,1000)
                opening_price=1.5445 if side!='SHORT' else 1.5615
                after,events=step(bot,dict(ts_ms=2000,price=opening_price))
                opening=[e for e in events if e['type']=='FILL'][-1 if side=='SHORT' else 0]
                reloaded=json.loads(json.dumps(after))
                closing_price=1.553
                end,events=step(reloaded,dict(ts_ms=3000,price=closing_price))
                grids=[e for e in events if e['type']=='GRID']
                grid=next(e for e in grids if e['opening_ts_ms']==2000)
                self.assertEqual(grid['opening_fill_id'],opening['fill_id'])
                self.assertEqual(grid['book'],opening['book'])
                self.assertEqual(grid['holding_ms'],1000)
                self.assertEqual(grid['pair_evidence'],1)
                self.assertEqual(grid['funding_known'],0)
                self.assertAlmostEqual(grid['net_before_funding'],grid['profit']-grid['opening_fee']-grid['closing_fee'])
                self.assertAlmostEqual(grid['opening_fee'],opening['fee'])
                validate_event(grid)
                self.assertEqual(step(end,dict(ts_ms=3000,price=closing_price)),(end,[]))

    def test_seed_close_has_allocated_opening_fee_and_seed_marker(self):
        for side in ('LONG','SHORT','NEUTRAL'):
            with self.subTest(side=side):
                bot=open_bot(spec(side),1.5468,1000)
                _,events=step(bot,dict(ts_ms=2000,price=1.536 if side=='SHORT' else 1.5615))
                grid=next(e for e in events if e['type']=='GRID')
                self.assertEqual(grid['seeded'],1)
                self.assertEqual(grid['opening_ts_ms'],1000)
                self.assertAlmostEqual(grid['opening_fee'],39*1.5468*.0006)

    def test_legacy_order_does_not_invent_opening_provenance(self):
        for side in ('LONG','SHORT','NEUTRAL'):
            with self.subTest(side=side):
                bot=open_bot(spec(side),1.5468,1000)
                for book in bot.get('hedge_books',[bot]):
                    for order in book['orders']:
                        order.pop('pair_evidence',None)
                _,events=step(bot,dict(ts_ms=2000,price=1.536 if side=='SHORT' else 1.5615))
                grid=next(e for e in events if e['type']=='GRID')
                self.assertEqual(grid['pair_evidence'],0)
                self.assertNotIn('opening_ts_ms',grid)
                self.assertNotIn('net_before_funding',grid)
