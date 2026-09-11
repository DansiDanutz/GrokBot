import unittest
from trader.autopilot.risk import sizing
from trader.tests.test_autopilot_policy import row

class EntryRiskTests(unittest.TestCase):
    def test_adverse_fills_remain_before_liquidation(self):
        r=row('A',price=100,range_low=50,range_high=150,grids=100,
              maintain_margin=.005,risk_limit=100000,multiplier=1,lot_size=1)
        with self.assertRaises(ValueError): sizing(r,'LONG',1000,5,100)

    def test_missing_metadata_fails_closed(self):
        r=row('A'); del r['maintain_margin']
        with self.assertRaises(ValueError):sizing(r,'LONG',1000,5,25)

    def test_ray_observation_is_used_only_for_matching_form(self):
        r=row('RAYUSDTM',price=1.5468,range_low=1.4,range_high=2,grids=70,
              maintain_margin=.015,risk_limit=100000,multiplier=1,lot_size=1)
        s=sizing(r,'LONG',1000,5,70)
        self.assertEqual(s['quantity_per_grid'],39)
        self.assertEqual(s['quantity_is_observed'],1)

    def test_reserve_transfer_is_collateral_not_profit(self):
        from trader.autopilot import policy
        from unittest.mock import patch
        s,_=policy.decide(policy.new_state(0),{'rows':[row('A')],'sections':{'long':[row('A')]}},{},0,'0')
        before=policy.snapshot(s,0,{})['equity']
        with patch('trader.autopilot.policy.protection_needed',side_effect=[True,False]):
            after,events=policy.advance(s,{'A':{'ts_ms':1000,'price':100}})
        w=after['open_bots'][0]
        self.assertEqual(w['reserve_usdt'],0)
        self.assertEqual(w['engine']['reserve_added_usdt'],200)
        self.assertEqual(policy.snapshot(after,1000,{})['equity'],before)
        self.assertEqual([e['type'] for e in events],['RESERVE'])

    def test_legacy_migration_closes_and_reopens_with_history(self):
        from trader.autopilot import policy
        r={'rows':[row('A')],'sections':{'long':[row('A')]}}
        s,_=policy.decide(policy.new_state(0),r,{},0,'0')
        del s['open_bots'][0]['engine']['accounting_version']
        after,events=policy.decide(s,r,{'A':100},1000,'1')
        self.assertEqual(after['closed_bots'][0]['engine']['reason'],'PROFILE_UPDATE')
        self.assertEqual(after['open_bots'][0]['engine']['accounting_version'],2)
        self.assertNotEqual(after['closed_bots'][0]['engine']['bot_id'],after['open_bots'][0]['engine']['bot_id'])

    def test_seed_gap_and_actual_seed_profit_match_ray_long(self):
        from trader.autopilot import policy
        from trader.papergrid import open_bot,step
        r=row('RAYUSDTM',price=1.5468,range_low=1.4,range_high=2,grids=70,tick_size=.0001,
              maintain_margin=.015,risk_limit=100000,multiplier=1,lot_size=1)
        spec,_=policy.profile(r,'LONG',1);bot=open_bot(spec,1.5468,0)
        self.assertEqual(bot['position_contracts'],2028)
        result,events=step(bot,{'ts_ms':1000,'price':1.5615})
        grid=next(e for e in events if e['type']=='GRID')
        self.assertAlmostEqual(grid['profit'],39*(1.5615-1.5468))

    def test_tiny_lot_size_is_bounded_and_risk_tier_updates_trigger_protection(self):
        from trader.autopilot.risk import protection_needed
        r=row('A',price=100,range_low=50,range_high=150,multiplier=1e-12,lot_size=1)
        s=sizing(r,'LONG',1000,5,100)
        self.assertLess(s['liquidation_bound'],49.5)
        bot=dict(position_contracts=100,avg_entry=100,notional_usdt=1000,realized_pnl=0,fees_paid=0,
                 funding_paid=0,maintain_margin=.005,risk_limit=1,last_price=100,range_low=90,range_high=110)
        self.assertTrue(protection_needed(bot))

    def test_public_reserve_and_hedge_fields_preserve_account_totals(self):
        from trader.autopilot import policy
        from paper_grid.public_autopilot import safe
        from unittest.mock import patch
        r=row('RAYUSDTM',direction='NEUTRAL')
        state,_=policy.decide(policy.new_state(0),{'rows':[r],'sections':{'neutral':[r]}},{},0,'0')
        bot=state['open_bots'][0]['engine'];bot['funding_interval_ms']=14400000;bot['funding_schedule_status']='ESTIMATED'
        with patch('trader.autopilot.policy.protection_needed',side_effect=[True,False]):
            state,_=policy.advance(state,{'RAYUSDTM':{'ts_ms':1000,'price':100}})
        view=safe(policy.snapshot(state,1000,{}))
        self.assertEqual(view['account']['allocated_margin'],1200)
        self.assertEqual(view['account']['reserved_margin'],0)
        self.assertEqual(view['open_bots'][0]['funding_interval_ms'],14400000)
        self.assertGreater(view['open_bots'][0]['long_contracts'],0)
        self.assertLess(view['open_bots'][0]['short_contracts'],0)
        self.assertEqual(len(view['open_bots'][0]['order_ladder']),2*bot['grids'])

    def test_live_entry_requires_current_tick_and_rejects_outside_range(self):
        from trader.autopilot import policy
        r=row('A');radar={'rows':[r],'sections':{'long':[r]}}
        for prices in ({},{'A':80}):
            state,_=policy.decide(policy.new_state(0),radar,prices,1000,'0',require_live_prices=True)
            self.assertEqual(state['open_bots'],[])
        state,_=policy.decide(policy.new_state(0),radar,{'A':101},1000,'0',require_live_prices=True)
        self.assertEqual(state['open_bots'][0]['engine']['opening_price'],101)
