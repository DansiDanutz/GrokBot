"""Offline portfolio policy contracts, independent of daemon I/O."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from trader.autopilot import policy
from trader.radar.rates import expected_grids_per_hour

HOUR = 3_600_000


def row(symbol, direction='LONG', **extra):
    result = dict(symbol=symbol, direction=direction, price=100, range_low=90,
                range_high=110, low_7d=92, high_7d=108, atr_4h_pct=4,
                atr_1h_pct=6.2, turnover_24h_usdt=8_000_000, step_pct=.8,
                grids=25, rank_score=10, expected_grids_per_hour=14,
                funding_pct=0, passes_liquidity=True, spread_pct=.05, listing_age_days=30,
                snapshot_age_min=0, position_7d=.5, change_24h_pct=0)
    result.update(extra)
    return result


def radar(**sections):
    return {'sections': sections, 'rows': [r for rows in sections.values() for r in rows]}


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.radar = json.loads((Path(__file__).parents[2] /
                                'tests/fixtures/autopilot/radar.json').read_text())

    def test_balanced_and_immutable(self):
        state = policy.new_state(0)
        before = deepcopy((state, self.radar))
        result, events = policy.decide(state, self.radar, {}, 0, 'a')
        self.assertEqual({d: sum(b['engine']['direction'] == d for b in result['open_bots'])
                          for d in ('LONG', 'SHORT', 'NEUTRAL')},
                         {'LONG': 2, 'SHORT': 1, 'NEUTRAL': 2})
        self.assertEqual(len(events), 5)
        self.assertEqual((state, self.radar), before)
        self.assertEqual(result['open_bots'][2]['source_section'], 'turning_up')

    def test_only_longs_borrow_short_slots_with_neutral_movers_exempt(self):
        report = radar(long=[row(f'L{i}') for i in range(6)],
                       movers=[row('M1', 'NEUTRAL', expected_grids_per_hour=20),
                               row('M2', 'NEUTRAL', expected_grids_per_hour=20)])
        state, _ = policy.decide(policy.new_state(0), report, {}, 0, 'a')
        self.assertEqual(sum(b['engine']['direction'] == 'LONG' for b in state['open_bots']), 3)
        self.assertEqual(sum(b['engine']['direction'] == 'NEUTRAL' for b in state['open_bots']), 2)

    def test_movers_cap_trend_only_and_majors_any_profile(self):
        state, _ = policy.decide(policy.new_state(0), radar(long=[row('A')]), {}, 0, 'a')
        state['open_bots'][0]['source_section'] = 'movers'
        self.assertFalse(policy.eligible(state, row('B'), 'LONG', 'movers', 0))
        self.assertTrue(policy.eligible(state, row('B'), 'NEUTRAL', 'movers', 0))
        state['open_bots'][0]['engine']['symbol'] = 'SOLUSDTM'
        self.assertFalse(policy.eligible(state, row('ETHUSDTM'), 'NEUTRAL', 'neutral', 0))

    def test_short_close_keeps_cooldown_and_cannot_admit_bench(self):
        state, _ = policy.decide(policy.new_state(0), self.radar, {}, 0, 'a')
        victim = next(b for b in state['open_bots'] if b['engine']['direction'] == 'SHORT')
        symbol = victim['engine']['symbol']
        victim['signals'] = ['RANGE_BREAK']
        report = deepcopy(self.radar)
        report['sections']['short'].append(row('REPLACE', 'SHORT'))
        state, events = policy.decide(state, report, {}, 300_000, 'b')
        self.assertEqual(state['closed_bots'][0]['engine']['reason'], 'RANGE_BREAK')
        self.assertFalse(any(b['engine']['symbol'] == 'REPLACE' for b in state['open_bots']))
        self.assertFalse(policy.eligible(state, row(symbol), 'SHORT', 'short', 301_000))
        self.assertTrue(any(e['type'] == 'CLOSE' for e in events))

    def test_drop_requires_two_distinct_scans_and_other_closes(self):
        state, _ = policy.decide(policy.new_state(0), radar(long=[row('A')]), {}, 0, 'a')
        state, _ = policy.decide(state, radar(), {}, 1, 'b')
        same, _ = policy.decide(state, radar(), {}, 2, 'b')
        self.assertEqual(len(same['open_bots']), 1)
        ended, _ = policy.decide(same, radar(), {}, 3, 'c')
        self.assertEqual(ended['closed_bots'][0]['engine']['reason'], 'DROPPED')
        for reason in ('LABEL_FLIP', 'MAX_AGE', 'STOP_LOSS'):
            with self.subTest(reason=reason):
                state, _ = policy.decide(policy.new_state(0), radar(long=[row('A')]), {}, 0, 'a')
                report = radar(short=[row('A', 'SHORT')]) if reason == 'LABEL_FLIP' else radar(long=[row('A')])
                if reason == 'STOP_LOSS':
                    state['open_bots'][0]['signals'] = ['STOP_LOSS']
                state, _ = policy.decide(state, report, {}, 73 * HOUR if reason == 'MAX_AGE' else 1, 'b')
                self.assertEqual(state['closed_bots'][0]['engine']['reason'], reason)

    def test_profile_rates_and_reserve_no_double_count(self):
        spec, reserve = policy.profile(row('RAY'), 'NEUTRAL', 1)
        self.assertEqual((spec['leverage'], reserve, spec['step_pct']), (5, 200, .45))
        self.assertEqual((spec['range_low'], spec['range_high']), (92, 108))
        self.assertTrue(17 <= expected_grids_per_hour(6.2, .43, 8_000_000) <= 21)
        self.assertAlmostEqual(expected_grids_per_hour(6.2, .8, 8_000_000), 1.9*6.2/.8)
        self.assertAlmostEqual(expected_grids_per_hour(6.2, .52, 50_000_000), .45*6.2/.52)
        state, _ = policy.decide(policy.new_state(0), radar(neutral=[row('N', 'NEUTRAL')]), {}, 0, 'a')
        view = policy.snapshot(state, HOUR, {})
        self.assertEqual(view['equity'], 10_000)
        self.assertEqual(view['open_bots'][0]['equity'], 1200)
        self.assertEqual(view['groups']['NEUTRAL']['totals']['bots'], 1)

    def test_tick_latches_risk_and_retention_preserves_net(self):
        state, _ = policy.decide(policy.new_state(0), radar(long=[row('A')]), {}, 0, 'a')
        before = deepcopy(state)
        updated, events = policy.advance(state, {'A': {'ts_ms': 60_000, 'price': 80}})
        self.assertEqual(state, before)
        self.assertIn('STOP_LOSS', updated['open_bots'][0]['signals'])
        self.assertTrue(events)
        closed, _ = policy.decide(updated, radar(), {}, 60_000, 'b')
        equity = policy.snapshot(closed, 60_000, {})['equity']
        pruned = policy.sample(closed, 31 * 24 * HOUR)
        self.assertEqual(pruned['closed_bots'], [])
        self.assertEqual(policy.snapshot(pruned, 31*24*HOUR, {})['equity'], equity)

    def test_admission_filters_and_neutral_movers_order(self):
        state = policy.new_state(0)
        self.assertFalse(policy.eligible(state, row('A', passes_liquidity=False), 'LONG', 'long', 0))
        self.assertFalse(policy.eligible(state, row('A', atr_1h_pct=.01), 'LONG', 'long', 0))
        report = radar(movers=[row('LOW', 'NEUTRAL', atr_1h_pct=2), row('HIGH', 'NEUTRAL', atr_1h_pct=8),
                               row('MID', 'NEUTRAL', atr_1h_pct=5), row('FOUR', 'NEUTRAL', atr_1h_pct=4),
                               row('FIVE', 'NEUTRAL')])
        for candidate in report['rows']:
            candidate['position_7d'] = .8
        result, _ = policy.decide(state, report, {}, 0, 'a')
        self.assertEqual([w['engine']['symbol'] for w in result['open_bots']], ['HIGH', 'FIVE', 'MID', 'FOUR'])
        self.assertEqual(len(result['open_bots']), 4)

    def test_borrowed_slot_does_not_rebalance_and_ticks_idempotent(self):
        report = radar(long=[row(f'L{i}') for i in range(4)], movers=[row('M1', 'NEUTRAL'), row('M2', 'NEUTRAL')])
        state, _ = policy.decide(policy.new_state(0), report, {}, 0, 'a')
        report['sections']['short'] = [row('SHORT', 'SHORT')]
        report['rows'].append(report['sections']['short'][0])
        result, _ = policy.decide(state, report, {}, 1, 'b')
        self.assertFalse(any(w['engine']['symbol'] == 'SHORT' for w in result['open_bots']))
        update = {'L0': {'ts_ms': 60_000, 'price': 99}}
        updated, _ = policy.advance(result, update)
        again, events = policy.advance(updated, update)
        self.assertEqual(again, updated)
        self.assertEqual(events, [])
        sampled = policy.sample(updated, 60_000)
        sampled_twice = policy.sample(sampled, 60_001)
        self.assertEqual(sampled_twice['equity_curve'], sampled['equity_curve'])

    def test_snapshot_bounds_and_retention_cleanup(self):
        state, _ = policy.decide(policy.new_state(0), radar(neutral=[row('N', 'NEUTRAL')]), {}, 0, 'a')
        state['equity_curve'] = [[i*60_000, 10_000+i*.01] for i in range(43_200)]
        wrapper = deepcopy(state['open_bots'][0])
        wrapper['engine']['closed_ms'] = 60_000
        wrapper['pnl_curve'] = [[i*60_000, i*.001] for i in range(4_321)]
        state['closed_bots'] = [deepcopy(wrapper) for _ in range(60)]
        state['cooldowns'] = {'EXPIRED': 1, 'ACTIVE': 100_000}
        state['radar_seen']['EXPIRED'] = [{'scan_id': 'a', 'present': False}]
        cleaned = policy.sample(state, 60_000)
        self.assertNotIn('EXPIRED', cleaned['cooldowns'])
        self.assertNotIn('EXPIRED', cleaned['radar_seen'])
        view = policy.snapshot(cleaned, 60_000, {})
        self.assertEqual(len(view['closed_bots']), 20)
        self.assertEqual(view['groups']['NEUTRAL']['totals']['bots'], 61)
        self.assertEqual(len(view['groups']['NEUTRAL']['closed_bots']), 20)
        self.assertLessEqual(len(view['equity_curve']), 2000)
        self.assertEqual(view['equity_curve'][0], state['equity_curve'][0])
        self.assertEqual(view['equity_curve'][-1], cleaned['equity_curve'][-1])
        self.assertLessEqual(len(view['closed_bots'][0]['pnl_curve']), 120)
        self.assertLess(len(json.dumps(view)), 2*1024*1024)

    def test_reject_invalid_profile_before_open(self):
        for field, value in [('price', float('nan')), ('range_low', float('inf')),
                             ('funding_pct', float('nan')), ('grids', 0), ('step_pct', -1)]:
            with self.subTest(field=field):
                self.assertFalse(policy.eligible(policy.new_state(0), row('A', **{field: value}), 'LONG', 'long', 0))

    def test_neutral_reserve_peak_drawdown_and_monotonic_samples(self):
        state, _ = policy.decide(policy.new_state(0), radar(neutral=[row('N', 'NEUTRAL')]), {}, 0, 'a')
        view = policy.snapshot(state, 0, {})['open_bots'][0]
        self.assertEqual((view['equity'], view['peak_equity']), (1200, 1200))
        updated, _ = policy.advance(state, {'N': {'ts_ms': 60_000, 'price': 91}})
        view = policy.snapshot(updated, 60_000, {})['open_bots'][0]
        self.assertEqual(view['peak_equity'], 1200)
        self.assertAlmostEqual(view['max_drawdown_pct'], (1200-view['equity'])/1200*100)
        sampled = policy.sample(updated, 60_000)
        older = policy.sample(sampled, 30_000)
        self.assertEqual(older['equity_curve'], sampled['equity_curve'])
        self.assertEqual(older['open_bots'][0]['pnl_curve'], sampled['open_bots'][0]['pnl_curve'])
