"""Offline portfolio policy contracts, independent of daemon I/O."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from trader.autopilot import policy
from trader.radar.rates import expected_grids_per_hour

HOUR = 3_600_000


def row(symbol, direction='LONG', **extra):
    low, high = {'LONG': (80, 130), 'TURNING-UP': (80, 130),
                 'SHORT': (70, 120), 'TURNING-DOWN': (70, 120), 'NEUTRAL': (75, 125)}[direction]
    result = dict(maintain_margin=.005, risk_limit=1000000, multiplier=.001, lot_size=1, symbol=symbol, direction=direction, range_verified=1, price=100, range_low=low,
                range_high=high, low_7d=92, high_7d=108, atr_4h_pct=4,
                atr_1h_pct=6.2, turnover_24h_usdt=8_000_000, step_pct=.8,
                grids=70, rank_score=10, expected_grids_per_hour=14,
                funding_pct=0, passes_liquidity=True, spread_pct=.05, listing_age_days=30,
                snapshot_age_min=0, position_7d=.5, change_24h_pct=0)
    result.update(extra)
    result.setdefault('support', result['range_low'])
    result.setdefault('resistance', result['range_high'])
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
        self.assertTrue(policy.eligible(state, row('B', 'NEUTRAL'), 'NEUTRAL', 'movers', 0))
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
        for reason in ('LABEL_FLIP', 'MAX_AGE'):
            with self.subTest(reason=reason):
                state, _ = policy.decide(policy.new_state(0), radar(long=[row('A')]), {}, 0, 'a')
                report = radar(short=[row('A', 'SHORT')]) if reason == 'LABEL_FLIP' else radar(long=[row('A')])
                if reason == 'STOP_LOSS':
                    state['open_bots'][0]['signals'] = ['STOP_LOSS']
                state, _ = policy.decide(state, report, {}, 73 * HOUR if reason == 'MAX_AGE' else 1, 'b')
                self.assertEqual(state['closed_bots'][0]['engine']['reason'], reason)

    def test_neutral_bot_on_trending_mover_survives_its_opening_label(self):
        mover = row('RAY', 'LONG', range_low=75, range_high=125, change_24h_pct=24, position_7d=.85)
        state, _ = policy.decide(policy.new_state(0), radar(movers=[mover]), {}, 0, 'a')
        bot = state['open_bots'][0]
        self.assertEqual((bot['engine']['direction'], bot['open_label']), ('NEUTRAL', 'LONG'))
        still, _ = policy.decide(state, radar(movers=[mover]), {}, 1, 'b')
        self.assertEqual(len(still['open_bots']), 1)
        flipped, _ = policy.decide(still, radar(movers=[row('RAY', 'SHORT', change_24h_pct=24, position_7d=.85)]), {}, 2, 'c')
        self.assertEqual(flipped['closed_bots'][0]['engine']['reason'], 'LABEL_FLIP')
        neutral = row('N', 'NEUTRAL')
        state, _ = policy.decide(policy.new_state(0), radar(neutral=[neutral]), {}, 0, 'a')
        self.assertEqual(state['open_bots'][0]['open_label'], 'NEUTRAL')
        trending, _ = policy.decide(state, radar(long=[row('N', 'LONG')]), {}, 1, 'b')
        self.assertEqual(trending['closed_bots'][0]['engine']['reason'], 'LABEL_FLIP')

    def test_profile_rates_and_reserve_no_double_count(self):
        spec, reserve = policy.profile(row('RAY'), 'NEUTRAL', 1)
        self.assertEqual((spec['leverage'], reserve), (5, 200))
        self.assertGreater(spec['profit_pct_min'], 1)
        self.assertEqual((spec['range_low'], spec['range_high']), (80, 130))
        self.assertTrue(17 <= expected_grids_per_hour(6.2, .43, 8_000_000) <= 21)
        self.assertAlmostEqual(expected_grids_per_hour(6.2, .8, 8_000_000), 1.9*6.2/.8)
        self.assertAlmostEqual(expected_grids_per_hour(6.2, .52, 50_000_000), .45*6.2/.52)
        state, _ = policy.decide(policy.new_state(0), radar(neutral=[row('N', 'NEUTRAL')]), {}, 0, 'a')
        view = policy.snapshot(state, HOUR, {})
        self.assertAlmostEqual(view['equity'], 10_000-view['open_bots'][0]['fees_paid'])
        self.assertAlmostEqual(view['open_bots'][0]['equity'], 1200-view['open_bots'][0]['fees_paid'])
        self.assertEqual(view['groups']['NEUTRAL']['totals']['bots'], 1)

    def test_tick_latches_risk_and_retention_preserves_net(self):
        state, _ = policy.decide(policy.new_state(0), radar(long=[row('A')]), {}, 0, 'a')
        before = deepcopy(state)
        updated, events = policy.advance(state, {'A': {'ts_ms': 60_000, 'price': 80}})
        self.assertEqual(state, before)
        self.assertEqual(updated['open_bots'], [])
        self.assertEqual(updated['closed_bots'][0]['engine']['reason'], 'RANGE_BREAK')
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

    def test_window_changes_are_none_without_an_older_equity_sample(self):
        state = policy.new_state(0)
        state['equity_curve'] = [[2 * HOUR, 10_000]]
        view = policy.snapshot(state, 3 * HOUR, {})
        self.assertIsNone(view['change_24h_pct'])
        self.assertIsNone(view['change_7d_pct'])
        state['equity_curve'] = [[HOUR, 10_000], [2 * HOUR, 9_900]]
        view = policy.snapshot(state, 26 * HOUR, {})
        self.assertAlmostEqual(view['change_24h_pct'], 100 * (10_000 / 9_900 - 1))
        self.assertIsNone(view['change_7d_pct'])

    def test_reject_invalid_profile_before_open(self):
        for field, value in [('price', float('nan')), ('range_low', float('inf')),
                             ('funding_pct', float('nan')), ('grids', 0), ('step_pct', -1)]:
            with self.subTest(field=field):
                self.assertFalse(policy.eligible(policy.new_state(0), row('A', **{field: value}), 'LONG', 'long', 0))

    def test_neutral_reserve_peak_drawdown_and_monotonic_samples(self):
        state, _ = policy.decide(policy.new_state(0), radar(neutral=[row('N', 'NEUTRAL')]), {}, 0, 'a')
        view = policy.snapshot(state, 0, {})['open_bots'][0]
        self.assertAlmostEqual(view['equity'],1200-view['fees_paid'])
        self.assertEqual(view['peak_equity'],1200)
        updated, _ = policy.advance(state, {'N': {'ts_ms': 60_000, 'price': 93}})
        view = policy.snapshot(updated, 60_000, {})['open_bots'][0]
        self.assertEqual(view['peak_equity'], 1200)
        self.assertAlmostEqual(view['max_drawdown_pct'], (1200-view['equity'])/1200*100)
        sampled = policy.sample(updated, 60_000)
        older = policy.sample(sampled, 30_000)
        self.assertEqual(older['equity_curve'], sampled['equity_curve'])
        self.assertEqual(older['equity_hourly'], sampled['equity_hourly'])
        self.assertEqual(older['open_bots'][0]['pnl_curve'], sampled['open_bots'][0]['pnl_curve'])


class EquityHourlyTests(unittest.TestCase):
    def test_bucket_creation_and_extension_across_hour_boundary(self):
        state = policy.new_state(0)
        state = policy.sample(state, HOUR + 10 * 60_000)
        state = policy.sample(state, HOUR + 40 * 60_000)
        self.assertEqual(state['equity_hourly'], [[HOUR, 10_000, 10_000, 10_000, 10_000]])
        state = policy.sample(state, 2 * HOUR + 5 * 60_000)
        self.assertEqual(len(state['equity_hourly']), 2)
        self.assertEqual(state['equity_hourly'][-1][0], 2 * HOUR)
        self.assertEqual(state['equity_hourly'][-1][1], 10_000)

    def test_high_low_close_extend_within_one_hour(self):
        state = policy.new_state(0)
        base = 5 * HOUR
        equity0 = policy.snapshot(state, base, {})['equity']
        state['equity_hourly'] = []
        state['equity_curve'] = []
        # Simulate one sample per minute with equity moving up then down.
        values = [equity0 - 3, equity0 + 7, equity0 - 1]
        for i, value in enumerate(values):
            state['archived_net'] = value - 10_000
            state = policy.sample(state, base + i * 60_000)
        bucket = state['equity_hourly'][-1]
        self.assertEqual(bucket[0], base)
        self.assertEqual(bucket[1], values[0])
        self.assertEqual(bucket[2], max(values))
        self.assertEqual(bucket[3], min(values))
        self.assertEqual(bucket[4], values[-1])

    def test_seven_day_cap_evicts_oldest_and_keeps_chronological(self):
        state = policy.new_state(0)
        for h in range(1, 200):
            state = policy.sample(state, h * HOUR)
        self.assertEqual(len(state['equity_hourly']), 168)
        self.assertEqual(state['equity_hourly'][0][0], (200 - 168) * HOUR)
        self.assertEqual(state['equity_hourly'][-1][0], 199 * HOUR)
        starts = [bucket[0] for bucket in state['equity_hourly']]
        self.assertEqual(starts, sorted(starts))

    def test_sample_backfills_missing_hourly_list_and_snapshot_exposes_it(self):
        state = policy.new_state(0)
        del state['equity_hourly']
        state = policy.sample(state, 30 * 60_000)
        self.assertEqual(state['equity_hourly'], [[0, 10_000, 10_000, 10_000, 10_000]])
        view = policy.snapshot(state, 30 * 60_000, {})
        self.assertEqual(view['equity_hourly'], state['equity_hourly'])
        self.assertEqual(view['equity_hourly'][0], [0, 10_000, 10_000, 10_000, 10_000])


class FiveXBoundaryTests(unittest.TestCase):
    def test_every_direction_gets_five_x_and_two_hundred_reserve(self):
        for direction in ('LONG', 'SHORT', 'NEUTRAL'):
            spec, reserve = policy.profile(row('A'), direction, 1)
            self.assertEqual((spec['notional_usdt'], spec['leverage'], reserve), (1000, 5, 200))

    def test_first_boundary_tick_closes_at_observed_gap_price_and_no_more_grids(self):
        for price in (80, 130, 70, 140):
            state, _ = policy.decide(policy.new_state(0), radar(long=[row('A')]), {}, 0, 'a')
            count = state['open_bots'][0]['engine']['completed_grids']
            ended, events = policy.advance(state, {'A': dict(ts_ms=1000, price=price)})
            self.assertFalse(ended['open_bots'])
            bot = ended['closed_bots'][0]['engine']
            self.assertEqual((bot['reason'], bot['last_price'], bot['completed_grids']), ('RANGE_BREAK', price, count))
            self.assertEqual([e['type'] for e in events].count('CLOSE'), 1)
            self.assertGreater(ended['cooldowns']['A'], 1000)
            repeated, again = policy.advance(ended, {'A': dict(ts_ms=1000, price=price)})
            self.assertFalse(again)

    def test_inside_and_stale_ticks_do_not_close_and_backfill_excursion_does(self):
        state, _ = policy.decide(policy.new_state(0), radar(long=[row('A')]), {}, 0, 'a')
        inside, _ = policy.advance(state, {'A': dict(ts_ms=1000, price=100)})
        stale, _ = policy.advance(inside, {'A': dict(ts_ms=1000, price=80)})
        self.assertEqual(len(stale['open_bots']), 1)
        ended, _ = policy.advance(stale, {'A': dict(ts_ms=2000, open=100, high=131, low=99, close=100)})
        self.assertEqual(ended['closed_bots'][0]['engine']['last_price'], 131)

    def test_old_profile_closes_then_new_bot_opens_without_rewriting_history(self):
        state, _ = policy.decide(policy.new_state(0), radar(long=[row('A')]), {}, 0, 'a')
        state['open_bots'][0]['engine']['leverage'] = 3
        state['open_bots'][0]['reserve_usdt'] = 0
        old_id = state['open_bots'][0]['engine']['bot_id']
        changed, events = policy.decide(state, radar(long=[row('A')]), {'A':100}, 1000, 'a')
        self.assertEqual(changed['closed_bots'][0]['engine']['reason'], 'PROFILE_UPDATE')
        self.assertEqual(changed['closed_bots'][0]['engine']['leverage'], 3)
        self.assertEqual(changed['open_bots'][0]['engine']['leverage'], 5)
        self.assertNotEqual(changed['open_bots'][0]['engine']['bot_id'], old_id)
        self.assertEqual(changed['open_bots'][0]['reserve_usdt'], 200)

    def test_loss_amount_does_not_override_the_configured_price_boundary(self):
        state, _ = policy.decide(policy.new_state(0), radar(long=[row('A')]), {}, 0, 'a')
        state['open_bots'][0]['engine']['fees_paid'] = 130
        ended, events = policy.advance(state, {'A': dict(ts_ms=1000, price=100)})
        self.assertEqual(len(ended['open_bots']), 1)
        self.assertFalse(any(e['type'] in ('STOP_LOSS', 'CLOSE') for e in events))

    def test_unverified_structure_is_not_admitted_and_verified_neutral_range_is_preserved(self):
        bad = row('A', range_verified=0)
        self.assertFalse(policy.eligible(policy.new_state(0), bad, 'LONG', 'long', 0))
        good = row('A', range_verified=1, range_low=94, range_high=106, grids=25)
        spec, reserve = policy.profile(good, 'NEUTRAL', 1)
        self.assertEqual((spec['range_low'], spec['range_high']), (94,106))

    def test_neutral_borrowing_requires_both_structural_edges(self):
        candidate = row("A", range_low=94, range_high=106, support=94, resistance=None, grids=25)
        self.assertFalse(policy.eligible(policy.new_state(0), candidate, "NEUTRAL", "movers", 0))
        candidate["resistance"] = 106
        spec, _ = policy.profile(candidate, "NEUTRAL", 1)
        self.assertEqual((spec["range_low"], spec["range_high"]), (94, 106))

    def test_verification_flag_alone_cannot_admit_missing_structure(self):
        candidate = row("A")
        del candidate["support"]
        self.assertFalse(policy.eligible(policy.new_state(0), candidate, "LONG", "long", 0))

class EntryLayoutAdmissionTests(unittest.TestCase):
    def candidate(self, **extra):
        return row('LAYOUT', range_low=80, range_high=130, grids=70, **extra)

    def test_minimum_grid_count_and_direction_split(self):
        good = self.candidate()
        self.assertTrue(policy.eligible(policy.new_state(0), good, 'LONG', 'long', 0))
        for changes in ({'grids': 11}, {'price': 105}):
            with self.subTest(changes=changes):
                self.assertFalse(policy.eligible(policy.new_state(0), dict(good, **changes), 'LONG', 'long', 0))

    def test_live_entry_rechecks_split_without_moving_structure(self):
        candidate = self.candidate()
        report = radar(long=[candidate])
        state, _ = policy.decide(policy.new_state(0), report, {'LAYOUT': 100}, 0, 'a', require_live_prices=True)
        self.assertEqual(len(state['open_bots']), 1)
        unchanged, _ = policy.decide(state, report, {'LAYOUT': 105}, 1, 'b', require_live_prices=True)
        self.assertEqual(unchanged['open_bots'][0]['engine'], state['open_bots'][0]['engine'])
        rejected, _ = policy.decide(policy.new_state(0), report, {'LAYOUT': 105}, 0, 'a', require_live_prices=True)
        self.assertEqual(rejected['open_bots'], [])

    def test_existing_smaller_layout_is_not_migrated(self):
        from trader.papergrid import open_bot
        candidate = self.candidate()
        report = radar(long=[candidate])
        state, _ = policy.decide(policy.new_state(0), report, {}, 0, 'a')
        historical = row('LAYOUT', range_low=90, range_high=110, grids=25)
        spec, _ = policy.profile(historical, 'LONG', 1)
        state['open_bots'][0]['engine'] = open_bot(spec, 100, 0)
        before = deepcopy(state['open_bots'][0]['engine'])
        updated, events = policy.decide(state, report, {'LAYOUT': 100}, 1, 'b', require_live_prices=True)
        self.assertEqual(updated['open_bots'][0]['engine'], before)
        self.assertFalse(any(event['type'] in ('OPEN', 'CLOSE') for event in events))

    def test_neutral_preserves_offered_count(self):
        candidate = row('N', 'NEUTRAL', range_low=75, range_high=125, grids=70)
        spec, _ = policy.profile(candidate, 'NEUTRAL', 1)
        self.assertEqual(spec['grids'], 70)
