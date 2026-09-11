"""Run directly: python3 paper_grid/test_engine.py (no application imports)."""
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from engine import default_config, initial_state, step, status


T = 1_800_000_000.0


def quote(symbol='A', price=100.0, now=T, **kwargs):
    row = dict(symbol=symbol, bid=price, ask=price * 1.0001, mark=price,
               bid_size=1_000_000, ask_size=1_000_000,
               quote_time=now, lot_size=1, multiplier=0.001, tick_size=0.001,
               funding_rate=0.0001, funding_interval_hours=8, score=70,
               eligible=True, add_eligible=False, atr_pct=2.0,
               entry_edge_pct=4.0, turnover24h=10_000_000.0)
    row.update(kwargs)
    return row


class PaperEngineTest(unittest.TestCase):
    def setUp(self):
        self.c = default_config()
        self.s = initial_state(self.c, T)

    def assertNoDecisionEvents(self, events):
        self.assertFalse([e for e in events if e['type'] != 'buy_rejected'])

    def tick(self, rows, now=T):
        self.s, events = step(self.s, {r['symbol']: r for r in rows}, now, self.c)
        return events

    def test_entry_rounding_cash_and_pure_input(self):
        original = json.dumps(self.s, sort_keys=True)
        new, events = step(self.s, {'A': quote(lot_size=7)}, T, self.c)
        self.assertEqual(json.dumps(self.s, sort_keys=True), original)
        p = new['positions']['A']
        self.assertEqual(p['contracts'] % 7, 0)
        self.assertLessEqual(p['cost_basis'], 50)
        self.assertAlmostEqual(new['cash'], 1000 - p['cost_basis'] - p['entry_fees'])
        self.assertEqual(events[0]['type'], 'open')
        self.assertGreaterEqual(new['cash'], 0)
        json.dumps(new, allow_nan=False)

    def test_whole_position_target_subtracts_fees_slippage_and_funding(self):
        self.tick([quote()])
        # Gross $1 is insufficient after fees, slippage and funding.
        events = self.tick([quote(price=102.02, now=T+60)], T+60)
        self.assertFalse(any(e['type'] == 'close' for e in events))
        events = self.tick([quote(price=103, now=T+120)], T+120)
        close = next(e for e in events if e['type'] == 'close')
        self.assertEqual(close['reason'], 'net_profit_target')
        self.assertGreaterEqual(close['net_pnl'], 1)
        self.assertGreater(close['funding_model_cost'], 0)
        self.assertGreater(close['entry_fees'] + close['exit_fee'], 0)
        self.assertAlmostEqual(self.s['cash'], 1000 + close['net_pnl'])
        self.assertAlmostEqual(self.s['realized_pnl'], close['net_pnl'])
        self.assertNotIn('A', self.s['positions'])

    def test_larger_adds_bounded_by_rebound_cooldown_and_two_add_limit(self):
        self.tick([quote()])
        self.assertNoDecisionEvents(self.tick([quote(price=98.5, now=T+60, add_eligible=True)], T+60))
        self.assertNoDecisionEvents(self.tick([quote(price=98.5, now=T+301)], T+301))
        e1 = self.tick([quote(price=98.5, now=T+302, add_eligible=True)], T+302)
        self.assertEqual(e1[0]['type'], 'add')
        self.assertGreater(e1[0]['notional'], 50)
        e2 = self.tick([quote(price=96, now=T+603, add_eligible=True)], T+603)
        self.assertEqual(e2[0]['type'], 'add')
        self.assertGreater(e2[0]['notional'], e1[0]['notional'])
        self.assertEqual(self.s['positions']['A']['adds'], 2)
        self.assertLessEqual(self.s['positions']['A']['cost_basis'], 200)
        e3 = self.tick([quote(price=95, now=T+904, add_eligible=True)], T+904)
        self.assertFalse(any(e['type'] == 'add' for e in e3))

    def test_position_cap_and_contract_round_down(self):
        self.c['position_notional_cap'] = 110
        self.tick([quote()])
        self.tick([quote(price=98, now=T+301, add_eligible=True)], T+301)
        self.tick([quote(price=95.5, now=T+602, add_eligible=True)], T+602)
        self.assertLessEqual(self.s['positions']['A']['cost_basis'], 110)
        self.assertGreaterEqual(self.s['cash'], 0)

    def test_price_stop_and_same_tick_reentry_prohibited(self):
        self.tick([quote()])
        events = self.tick([quote(price=94, now=T+301, add_eligible=True)], T+301)
        self.assertEqual(events[0]['reason'], 'price_stop')
        self.assertLess(events[0]['net_pnl'], 0)
        self.assertFalse(self.s['positions'])
        self.assertNoDecisionEvents(self.tick([quote(price=94, now=T+302)], T+302))

    def test_dollar_stop(self):
        self.c['max_position_loss'] = 1
        self.tick([quote()])
        events = self.tick([quote(price=97, now=T+10)], T+10)
        self.assertEqual(events[0]['reason'], 'position_loss_limit')

    def test_daily_total_equity_loss_closes_and_halts(self):
        self.c['daily_loss_limit'] = 2
        self.tick([quote('A'), quote('B')])
        events = self.tick([quote('A',98,T+10), quote('B',98,T+10)], T+10)
        self.assertEqual(events[0]['type'], 'daily_halt')
        self.assertEqual(len([e for e in events if e['type'] == 'close']), 2)
        self.assertFalse(self.s['positions'])
        self.assertIsNotNone(self.s['halted_day'])
        self.assertNoDecisionEvents(self.tick([quote('C',100,T+20)], T+20))

    def test_daily_halt_retries_unpriced_exit(self):
        self.c['daily_loss_limit'] = 1
        self.tick([quote('A'), quote('B')])
        events = self.tick([quote('A',97,T+10)], T+10)
        self.assertIn('B', events[0]['unpriced_positions'])
        self.assertIn('B', self.s['positions'])
        events = self.tick([quote('B',100,T+20)], T+20)
        self.assertEqual(events[0]['reason'], 'daily_loss_limit')
        self.assertFalse(self.s['positions'])

    def test_floating_loss_in_total_equity(self):
        self.tick([quote()])
        self.tick([quote(price=98.5,now=T+30)], T+30)
        report = status(self.s, {'A':quote(price=98.5,now=T+30)}, T+30,self.c)
        self.assertLess(report['unrealized_net_pnl'], -0.5)
        self.assertEqual(report['realized_pnl'], 0)
        self.assertAlmostEqual(report['equity']-1000,report['unrealized_net_pnl'])

    def test_invalid_and_stale_quotes_fail_closed(self):
        for update in [dict(quote_time=T-91),dict(bid=float('nan')),dict(ask=99),
                       dict(funding_rate=None),dict(lot_size=0),dict(score=101),
                       dict(eligible='true'),dict(entry_edge_pct=None),dict(bid=99)]:
            with self.subTest(update=update):
                _, events = step(self.s, {'A':quote(**update)}, T,self.c)
                self.assertNoDecisionEvents(events)
        _, events = step(self.s, {}, T,self.c)
        self.assertNoDecisionEvents(events)
        self.tick([quote()])
        self.assertNoDecisionEvents(self.tick([quote('B',now=T+100)],T+100))
        report = status(self.s, {}, T+100,self.c)
        self.assertTrue(report['equity_is_estimate'])
        self.assertEqual(report['unpriced_positions'],['A'])

    def test_missing_entry_metadata_does_not_block_safe_exit(self):
        self.tick([quote()])
        r = dict(symbol='A',bid=105,ask=105.01,mark=105,quote_time=T+10,bid_size=1_000_000)
        events = self.tick([r],T+10)
        self.assertEqual(events[0]['reason'],'net_profit_target')

    def test_replay_after_json_restart_is_noop(self):
        self.tick([quote()])
        self.s = json.loads(json.dumps(self.s))
        before = json.dumps(self.s,sort_keys=True)
        self.assertNoDecisionEvents(self.tick([quote(price=90)],T))
        self.assertEqual(json.dumps(self.s,sort_keys=True),before)
        self.assertNoDecisionEvents(self.tick([quote(price=90)],T-1))

    def test_rotation_hysteresis_costs_and_cooldown(self):
        self.tick([quote('A',score=60),quote('B',score=80)])
        events = self.tick([quote('A',now=T+100,score=60),quote('B',now=T+100,score=80),
                            quote('C',now=T+100,score=95)],T+100)
        self.assertNoDecisionEvents(events)
        events = self.tick([quote('A',now=T+1801,score=60),quote('B',now=T+1801,score=80),
                            quote('C',now=T+1801,score=74)],T+1801)
        self.assertNoDecisionEvents(events)
        events = self.tick([quote('A',now=T+1802,score=60),quote('B',now=T+1802,score=80),
                            quote('C',now=T+1802,score=95)],T+1802)
        self.assertEqual([e['type'] for e in events if e['type'] != 'buy_rejected'],['close','open'])
        self.assertEqual(events[0]['reason'],'rotation')
        self.assertLess(events[0]['net_pnl'],0)
        self.assertEqual(set(self.s['positions']),{'B','C'})
        events = self.tick([quote('B',now=T+2000,score=60),quote('C',now=T+2000,score=95),
                            quote('D',now=T+2000,score=100)],T+2000)
        self.assertNoDecisionEvents(events)

    def test_rotation_does_not_dump_large_loss(self):
        self.c['max_price_drop_pct']=10
        self.tick([quote('A',score=60),quote('B',score=80)])
        events = self.tick([quote('A',price=95,now=T+1801,score=60),quote('B',now=T+1801,score=80),
                            quote('C',now=T+1801,score=95)],T+1801)
        self.assertNoDecisionEvents(events)
        self.assertEqual(set(self.s['positions']),{'A','B'})

    def test_negative_funding_never_credited(self):
        self.tick([quote(funding_rate=-0.1)])
        self.tick([quote(now=T+3600,funding_rate=-0.1)],T+3600)
        self.assertEqual(self.s['positions']['A']['funding_accrued'],0)

    def test_zero_contracts_or_insufficient_cash_never_open(self):
        self.assertNoDecisionEvents(self.tick([quote(multiplier=100)]))
        self.s = initial_state(self.c,T)
        self.s['cash']=0.01
        self.s['day_start_equity']=0.01
        self.assertNoDecisionEvents(self.tick([quote()]))
        self.assertGreaterEqual(self.s['cash'],0)

    def test_max_two_and_no_forced_entries(self):
        self.tick([quote('A',score=59),quote('B',eligible=False)])
        self.assertFalse(self.s['positions'])
        self.tick([quote('A',now=T+1,score=80),quote('B',now=T+1,score=90),
                   quote('C',now=T+1,score=70)],T+1)
        self.assertEqual(set(self.s['positions']),{'A','B'})

    def test_edge_too_small_for_dollar_target_rejects_without_upsizing(self):
        self.assertNoDecisionEvents(self.tick([quote(entry_edge_pct=2)]))
        self.assertEqual(self.s['cash'], 1000)
        self.assertFalse(self.s['positions'])

    def test_invalid_persisted_state_and_leverage_fail_closed(self):
        for key,value in [('cash',-1),('cash',float('nan')),('positions',None),
                          ('last_run',float('inf')),('symbol_closed_at',{'A':'bad'})]:
            s = initial_state(self.c,T)
            s[key]=value
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):
                step(s,{'A':quote()},T,self.c)
        for key,value in [('max_positions',3),('leverage',2)]:
            with self.assertRaises(ValueError):
                initial_state(dict(self.c,**{key:value}),T)
        self.tick([quote()])
        for key,value in [('quantity',-1),('contracts',float('nan')),('adds',True),('cost_basis',250)]:
            s=json.loads(json.dumps(self.s))
            s['positions']['A'][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):
                step(s,{},T+1,self.c)

    def test_day_rollover_uses_bucharest_not_utc(self):
        now=datetime(2026,9,11,20,59,tzinfo=timezone.utc).timestamp()
        self.s=initial_state(self.c,now)
        self.assertEqual(self.s['day'],'2026-09-11')
        self.s['halted_day']=self.s['day']
        self.tick([],now+120)
        self.assertEqual(self.s['day'],'2026-09-12')
        self.assertIsNone(self.s['halted_day'])

    def test_extreme_funding_debt_not_hidden_and_blocks_future_risk(self):
        self.tick([quote(funding_rate=1,funding_interval_hours=1)])
        self.tick([],T+3600*30)
        self.assertTrue(self.s['halted_day'])
        self.tick([quote(now=T+3600*30+1)],T+3600*30+1)
        self.assertFalse(self.s['positions'])
        self.assertEqual(self.s['cash'],0)
        self.assertGreater(self.s['unpaid_liabilities'],0)
        report=status(self.s,{},T+3600*30+1,self.c)
        self.assertLess(report['equity'],0)
        self.assertAlmostEqual(report['equity'],1000+self.s['realized_pnl'])

    def test_new_funding_rate_is_not_applied_retroactively(self):
        self.tick([quote(funding_rate=0.0001)])
        p=self.s['positions']['A']
        expected=p['quantity']*p['last_mark']*0.0001/8
        self.tick([quote(now=T+3600,funding_rate=0.01)],T+3600)
        self.assertAlmostEqual(self.s['positions']['A']['funding_accrued'],expected)

    def test_thin_or_missing_ask_depth_rejects_entry_and_add(self):
        for size in [1,None,float('nan'),0]:
            s,events=step(initial_state(self.c,T),{'A':quote(ask_size=size)},T,self.c)
            self.assertNoDecisionEvents(events)
            self.assertFalse(s['positions'])
        self.tick([quote()])
        contracts=self.s['positions']['A']['contracts']
        events=self.tick([quote(price=98,now=T+301,add_eligible=True,ask_size=1)],T+301)
        self.assertNoDecisionEvents(events)
        self.assertEqual(self.s['positions']['A']['contracts'],contracts)
        self.assertEqual(self.s['positions']['A']['adds'],0)

    def test_thin_bid_defers_profit_and_stop_without_fake_cash_or_adds(self):
        for price in [105,94]:
            with self.subTest(price=price):
                self.s=initial_state(self.c,T)
                self.tick([quote()])
                cash=self.s['cash']
                events=self.tick([quote(price=price,now=T+301,bid_size=1,add_eligible=True),
                                  quote('B',now=T+301)],T+301)
                self.assertEqual(len(events),1)
                self.assertEqual(events[0]['type'],'deferred_exit')
                self.assertEqual(set(self.s['positions']),{'A'})
                self.assertEqual(self.s['cash'],cash)
                self.assertEqual(self.s['realized_pnl'],0)
                report=status(self.s,{'A':quote(price=price,now=T+301,bid_size=1)},T+301,self.c)
                self.assertEqual(report['insufficient_exit_depth'],['A'])
                events=self.tick([quote(price=price,now=T+302)],T+302)
                self.assertEqual(events[0]['type'],'close')

    def test_missing_bid_depth_retains_position_and_replay_is_noop(self):
        self.tick([quote()])
        events=self.tick([quote(price=105,now=T+1,bid_size=None)],T+1)
        self.assertEqual(events[0]['type'],'deferred_exit')
        self.assertIn('A',self.s['positions'])
        self.assertNoDecisionEvents(self.tick([quote(price=105,now=T+1)],T+1))

    def test_rotation_cannot_open_replacement_until_full_exit_fills(self):
        self.tick([quote('A',score=60),quote('B',score=80)])
        rows=[quote('A',now=T+1801,score=60,bid_size=1),
              quote('B',now=T+1801,score=80),quote('C',now=T+1801,score=95)]
        events=self.tick(rows,T+1801)
        self.assertEqual([e['type'] for e in events if e['type'] != 'buy_rejected'],['deferred_exit'])
        self.assertEqual(events[0]['reason'],'rotation')
        self.assertEqual(set(self.s['positions']),{'A','B'})
        self.assertIsNone(self.s['last_rotation'])
        self.assertEqual(self.s['realized_pnl'],0)

    def test_daily_halt_with_thin_depth_retains_and_retries_stop(self):
        self.c['daily_loss_limit']=1
        self.tick([quote()])
        events=self.tick([quote(price=97,now=T+10,bid_size=1)],T+10)
        self.assertEqual([e['type'] for e in events if e['type'] != 'buy_rejected'],['daily_halt','deferred_exit'])
        self.assertEqual(events[1]['reason'],'daily_loss_limit')
        self.assertIn('A',self.s['positions'])
        events=self.tick([quote(price=100,now=T+20)],T+20)
        self.assertEqual(events[0]['reason'],'daily_loss_limit')
        self.assertFalse(self.s['positions'])



class TelemetryTests(unittest.TestCase):
    def test_execution_rejections_are_finite_and_do_not_mutate_state(self):
        from copy import deepcopy
        from unittest.mock import patch
        import engine
        c = default_config()
        cases = [
            ('invalid_unit_cost', quote(ask=1e308, multiplier=1e308), {}, None),
            ('below_minimum_lot', quote(multiplier=100), {}, None),
            ('insufficient_ask_depth', quote(ask_size=float('nan')), {}, None),
            ('cash_or_position_cap', quote(), {}, 1000000),
            ('immediate_risk_limit', quote(), {'max_position_loss': .001}, None),
            ('insufficient_expected_net', quote(entry_edge_pct=1), {}, None),
        ]
        for reason, record, overrides, fake_lots in cases:
            with self.subTest(reason=reason):
                state = initial_state(c, T)
                before = deepcopy(state)
                events = []
                if fake_lots is None:
                    result = engine._buy(state, 'A', record, T, dict(c, **overrides), events)
                else:
                    with patch.object(engine.math, 'floor', return_value=fake_lots):
                        result = engine._buy(state, 'A', record, T, c, events)
                self.assertFalse(result)
                self.assertEqual(state, before)
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0]['type'], 'buy_rejected')
                self.assertEqual(events[0]['reason'], reason)
                self.assertEqual(events[0]['action'], 'open')
                json.dumps(events, allow_nan=False)

    def test_add_rejection_and_failed_rotation_never_emit_trial_fills(self):
        c = default_config()
        state, _ = step(initial_state(c, T), {'A': quote(score=60), 'B': quote('B', score=80)}, T, c)
        before_cash = state['cash']
        state, events = step(state, {'A': quote(now=T+1801, score=60),
                                    'B': quote('B', now=T+1801, score=80),
                                    'C': quote('C', now=T+1801, score=95, ask_size=1)}, T+1801, c)
        self.assertEqual(state['cash'], before_cash)
        self.assertEqual(state['realized_pnl'], 0)
        self.assertEqual(set(state['positions']), {'A', 'B'})
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['stage'], 'rotation_trial')
        self.assertEqual(events[0]['reason'], 'insufficient_ask_depth')
        state, events = step(state, {'A': quote(price=98, now=T+2101, add_eligible=True, ask_size=1),
                                    'B': quote('B', now=T+2101)}, T+2101, c)
        self.assertEqual(events[0]['action'], 'add')
        self.assertEqual(state['positions']['A']['adds'], 0)

    def test_selection_rejections_exclude_ineligible_coins_and_replays(self):
        c = default_config()
        state, events = step(initial_state(c, T), {'A': quote(score=59),
                             'B': quote('B', eligible=False)}, T, c)
        self.assertEqual([(e['symbol'], e['reason']) for e in events], [('A', 'below_min_score')])
        _, events = step(state, {'A': quote(score=59)}, T, c)
        self.assertEqual(events, [])

    def test_decision_state_and_fills_match_pre_telemetry_golden(self):
        import hashlib
        import engine
        digest = hashlib.sha256(json.dumps(parity_scenarios(engine), sort_keys=True,
                                          allow_nan=False).encode()).hexdigest()
        self.assertEqual(digest, '9e148504954a84064a492fd1b79eb035595a46d73a903d35625b253235d370e9')


def parity_scenarios(module):
    """Golden from engine seal595278bc, including dynamic-score rotation unchanged."""
    results = []
    for variant in range(12):
        c = default_config()
        state = module.initial_state(c, T)
        all_events = []
        ticks = [
            [quote('A', score=60), quote('B', score=80)],
            [quote('A', price=101, now=T+1801, score=60), quote('B', now=T+1801, score=80),
             quote('C', now=T+1801, score=95, ask_size=1 if variant % 2 else 1000000)],
            [quote('A', price=98, now=T+2102, add_eligible=True), quote('B', price=98, now=T+2102, add_eligible=True),
             quote('C', price=98, now=T+2102, add_eligible=True)],
            [quote('A', price=95.5, now=T+2403, add_eligible=True), quote('B', price=95.5, now=T+2403, add_eligible=True),
             quote('C', price=95.5, now=T+2403, add_eligible=True)],
            [quote(s, price=94 if variant % 3 else 105, now=T+2704,
                   bid_size=1 if variant % 4 == 0 else 1000000) for s in ('A','B','C')],
        ]
        for rows, at in zip(ticks, (T,T+1801,T+2102,T+2403,T+2704)):
            state, events = module.step(state, {r['symbol']: r for r in rows}, at, c)
            all_events.extend(e for e in events if e['type'] != 'buy_rejected')
        results.append(dict(state=state, events=all_events))
    return results


if __name__ == '__main__':
    unittest.main()
