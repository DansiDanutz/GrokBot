"""Entry provenance and costs remain frozen independently of later accounting."""
from copy import deepcopy
import json
import math
import unittest

from trader.autopilot import policy, setup_evidence
from trader.papergrid import open_bot
from trader.tests.test_autopilot_policy import row, radar


class SetupEvidenceTests(unittest.TestCase):
    def make(self, **overrides):
        candidate = row('EVIDENCEUSDTM', tick_size=.01)
        candidate.update(overrides)
        spec, _ = policy.profile(candidate, 'LONG', 1)
        spec.update(fee_rate_maker=.0006, fee_rate_taker=.0006)
        bot = open_bot(spec, candidate['price'], 1000)
        return candidate, bot

    def test_hash_is_stable_and_input_changes_are_isolated(self):
        candidate, bot = self.make()
        dossier = setup_evidence.build(candidate, bot)
        self.assertEqual(dossier, setup_evidence.build(deepcopy(candidate), deepcopy(bot)))
        self.assertEqual(len(dossier['evidence_id']), 64)
        self.assertEqual(dossier['evidence_id'], setup_evidence.evidence_id(dossier))
        candidate['range_evidence']['selected_support']['pivot_times_ms'].append(30)
        bot['lines'][0] = 1
        self.assertEqual(dossier['range_evidence']['selected_support']['pivot_times_ms'], [-36000000, -18000000])
        self.assertEqual(dossier['layout']['grid_lines'][0], 80)
        altered = deepcopy(dossier)
        altered['opened_ms'] += 1
        self.assertNotEqual(setup_evidence.evidence_id(altered), dossier['evidence_id'])
        self.assertEqual(json.loads(json.dumps(dossier)), dossier)

    def test_entry_motivation_is_frozen_with_score_parts_and_jev(self):
        candidate, bot = self.make(
            score=71.5,
            score_parts=[
                {'code': 'OSCILLATION', 'value': 18.4, 'points': 27.6},
                {'code': 'ROOM', 'value': 1.6, 'points': 12.0},
            ],
            jev={
                'model': 'jev-latest', 'direction': 'LONG',
                'direction_confidence': .82, 'range_quality': 2.5,
                'range_confidence': .76, 'entry_probability': .68,
                'evidence_probability': .91, 'latency_ms': 412.3,
                'input_tokens': 832,
            },
        )
        decision = {
            'direction': 'LONG', 'radar_direction': 'LONG',
            'radar_score': 71.5, 'expected_grids_per_hour': 18.4,
            'range_width_pct': 12.5, 'funding_rate': -.01, 'kucoin_ok': 1,
        }
        dossier = setup_evidence.build(
            candidate, bot, decision_context=decision,
            source_section='long', slot_direction='LONG', open_label='LONG')

        self.assertEqual(dossier['entry_decision']['context'], decision)
        self.assertEqual(dossier['entry_decision']['source_section'], 'long')
        self.assertEqual(dossier['entry_decision']['score_parts'][0]['code'], 'OSCILLATION')
        self.assertEqual(dossier['entry_decision']['jev']['direction'], 'LONG')
        candidate['score_parts'][0]['points'] = -99
        candidate['jev']['direction'] = 'REJECT'
        decision['radar_score'] = 0
        self.assertEqual(dossier['entry_decision']['context']['radar_score'], 71.5)
        self.assertEqual(dossier['entry_decision']['score_parts'][0]['points'], 27.6)
        self.assertEqual(dossier['entry_decision']['jev']['direction'], 'LONG')

    def test_fee_math_and_return_denominator_are_explicit(self):
        candidate, bot = self.make()
        dossier = setup_evidence.build(candidate, bot)
        pair = dossier['grid_pairs']['lowest_price_pair']
        quantity = bot['contracts_per_line']
        self.assertAlmostEqual(pair['both_fill_fees_usdt'], (pair['buy']+pair['sell'])*quantity*.0006)
        self.assertAlmostEqual(pair['net_usdt'], (pair['sell']-pair['buy'])*quantity-pair['both_fill_fees_usdt'])
        self.assertAlmostEqual(pair['short_margin_return_pct'], pair['net_usdt']/(pair['sell']*quantity/5)*100)
        self.assertEqual(dossier['costs']['seed_fee_paid_usdt'], bot['fees_paid'])
        self.assertTrue(dossier['fees']['matches_reference'])
        self.assertIsNone(dossier['whole_bot']['future_net_pnl_usdt'])
        seeded = dossier['grid_pairs']['seeded_closes'][0]['minimum_return_pair']
        self.assertEqual(seeded['entry_price'], bot['opening_price'])
        self.assertAlmostEqual(seeded['seed_and_close_fees_usdt'],
                               (seeded['entry_price']+seeded['exit_price'])*quantity*.0006)
        self.assertAlmostEqual(seeded['margin_return_pct'], seeded['net_usdt']/(100*quantity/5)*100)

    def test_missing_funding_schedule_stays_unknown_not_zero(self):
        candidate, bot = self.make(funding_pct=0)
        funding = setup_evidence.build(candidate, bot)['costs']['funding_4h']
        self.assertEqual(funding['status'], 'UNKNOWN')
        self.assertIsNone(funding['constant_rate_seed_position_cost_usdt'])
        self.assertIsNone(funding['scheduled_settlements'])

    def test_hedge_flatten_fees_use_gross_not_net_position(self):
        candidate, _ = self.make()
        spec, _ = policy.profile(candidate, 'NEUTRAL', 1)
        spec.update(fee_rate_maker=.0006, fee_rate_taker=.0006)
        bot = open_bot(spec, candidate['price'], 1000)
        costs = setup_evidence.build(candidate, bot)['costs']
        gross = sum(abs(leg['position_contracts']) for leg in bot['hedge_books']) * 100
        self.assertAlmostEqual(costs['flatten_seed_inventory_same_price_fee_usdt'], gross*.0006)
        self.assertGreater(gross, abs(costs['seed_signed_notional_usdt']))
        self.assertAlmostEqual(costs['seed_and_flatten_spread_cost_usdt'], gross*candidate['spread_pct']/100)
        candidate['spread_pct'] = None
        self.assertIsNone(setup_evidence.build(candidate, bot)['costs']['seed_and_flatten_spread_cost_usdt'])

    def test_four_hour_funding_uses_supplied_cadence_and_sign(self):
        candidate, bot = self.make(funding_pct=-.01, funding_interval_ms=3600000,
                                   next_funding_ms=3601000, funding_asof_ms=999)
        dossier = setup_evidence.build(candidate, bot)
        funding = dossier['costs']['funding_4h']
        self.assertEqual(funding['scheduled_settlements'], 4)
        self.assertEqual(funding['status'], 'SCENARIO_ONLY')
        self.assertLess(funding['constant_rate_seed_position_cost_usdt'], 0)
        self.assertGreater(funding['adverse_absolute_rate_gross_cost_usdt'], 0)
        self.assertIsNone(funding['future_actual_cost_usdt'])
        candidate['next_funding_ms'] = 1000
        self.assertEqual(setup_evidence.build(candidate, bot)['costs']['funding_4h']['status'], 'UNKNOWN')

    def test_wrapper_persistence_snapshot_isolation_and_legacy_missing(self):
        candidate, _ = self.make()
        state, events = policy.decide(policy.new_state(0), radar(long=[candidate]), {}, 1000, 'one')
        wrapper = state['open_bots'][0]
        frozen = deepcopy(wrapper['setup_evidence'])
        candidate['range_evidence']['candles_sha256'] = 'later-candles'
        updated, _ = policy.decide(state, radar(long=[candidate]), {}, 2000, 'two')
        self.assertEqual(updated['open_bots'][0]['setup_evidence'], frozen)
        view = policy.snapshot(updated, 2000, {})
        view['open_bots'][0]['setup_evidence']['fees']['grid_fill_rate'] = 1
        self.assertNotIn('grid_lines', view['open_bots'][0]['setup_evidence']['layout'])
        self.assertEqual(updated['open_bots'][0]['setup_evidence'], frozen)
        self.assertTrue(all('setup_evidence' not in event for event in events))
        del updated['open_bots'][0]['setup_evidence']
        legacy = policy.snapshot(updated, 3000, {})['open_bots'][0]['setup_evidence']
        self.assertEqual(legacy['status'], 'MISSING')
        self.assertIsNone(legacy['evidence_id'])
        self.assertNotIn('setup_evidence', updated['open_bots'][0])

    def test_entry_rejects_missing_mismatched_or_future_provenance(self):
        candidate, _ = self.make()
        for key, value in [('status', 'REJECTED'), ('version', True), ('grid_count', 10),
                           ('rounded_bounds', [79, 130]), ('analysis_asof_ms', 2000),
                           ('candles_sha256', 'not-a-digest'), ('coverage_ratio', .99),
                           ('grid_interval', 100), ('fee_rate_maker', .0002), ('coverage_ratio', True)]:
            invalid = deepcopy(candidate)
            invalid['range_evidence'][key] = value
            with self.subTest(key=key):
                self.assertFalse(policy.eligible(policy.new_state(0), invalid, 'LONG', 'long', 1000))
        invalid = deepcopy(candidate)
        invalid['range_evidence']['selected_support']['confirmed_at_ms'][-1] = 2000
        self.assertFalse(policy.eligible(policy.new_state(0), invalid, 'LONG', 'long', 1000))
        candidate.pop('range_evidence')
        self.assertFalse(policy.eligible(policy.new_state(0), candidate, 'LONG', 'long', 1000))

    def test_maximum_hedge_layout_has_bounded_snapshot_projection(self):
        candidate = row('TESTUSDTM', 'NEUTRAL', price=125, range_low=25,
                        range_high=225, grids=200)
        spec, _ = policy.profile(candidate, 'NEUTRAL', 1)
        bot = open_bot(spec, 125, 1000)
        full = setup_evidence.build(candidate, bot)
        view = setup_evidence.for_snapshot({'setup_evidence': full})
        self.assertEqual(len(full['layout']['grid_lines']), 201)
        self.assertEqual(len(full['layout']['opening_orders']), 400)
        self.assertEqual(view['evidence_id'], full['evidence_id'])
        self.assertLess(len(json.dumps(view)), 12000)

    def test_current_epoch_provenance_qualifies_and_opens(self):
        now = 1789257600000
        candidate, _ = self.make()
        proof = candidate['range_evidence']
        for key in ('window_start_ms', 'window_end_ms', 'analysis_asof_ms', 'candle_asof_ms'):
            proof[key] += now
        for name in ('selected_support', 'selected_resistance'):
            for key in ('pivot_times_ms', 'confirmed_at_ms'):
                proof[name][key] = [t+now for t in proof[name][key]]
        state, _ = policy.decide(policy.new_state(now), radar(long=[candidate]), {}, now, str(now))
        self.assertEqual(len(state['open_bots']), 1)
        self.assertEqual(state['open_bots'][0]['setup_evidence']['range_evidence']['analysis_asof_ms'], now)
        self.assertEqual(state['open_bots'][0]['engine']['opened_ms'], now)

    def test_conditional_break_even_recovers_costs_without_forecasting(self):
        candidate, bot = self.make(funding_pct=.01, funding_interval_ms=3600000,
                                   next_funding_ms=3601000, funding_asof_ms=999)
        dossier = setup_evidence.build(candidate, bot)
        costs = dossier['costs']
        recovery = costs['break_even']
        pair_net = min((b-a)*bot['contracts_per_line']-(a+b)*bot['contracts_per_line']*.0006
                       for a,b in zip(bot['lines'],bot['lines'][1:]))
        base = bot['fees_paid'] + costs['flatten_seed_inventory_same_price_fee_usdt']
        spread_total = base + costs['seed_and_flatten_spread_cost_usdt']
        combined = spread_total + costs['funding_4h']['adverse_absolute_rate_gross_cost_usdt']
        self.assertEqual(recovery['status'], 'CONDITIONAL')
        self.assertAlmostEqual(recovery['minimum_adjacent_pair_net_usdt'], pair_net)
        self.assertEqual(recovery['fee_only_completed_pairs'], math.ceil(base/pair_net))
        self.assertEqual(recovery['including_spread_completed_pairs'], math.ceil(spread_total/pair_net))
        self.assertEqual(recovery['including_spread_adverse_funding_4h_completed_pairs'], math.ceil(combined/pair_net))
        self.assertEqual(recovery['actual_future_break_even_status'], 'UNKNOWN')
        self.assertIsNone(recovery['actual_future_completed_pairs'])
        candidate.pop('funding_interval_ms')
        missing = setup_evidence.build(candidate, bot)['costs']['break_even']
        self.assertIsNone(missing['including_spread_adverse_funding_4h_completed_pairs'])
        self.assertIsNone(missing['including_spread_adverse_funding_4h_cost_usdt'])
        candidate['spread_pct'] = None
        self.assertIsNone(setup_evidence.build(candidate, bot)['costs']['break_even']['including_spread_completed_pairs'])

    def test_coinglass_clusters_are_explicitly_unavailable(self):
        candidate, bot = self.make(coinglass_liquidation_total_usd=123456)
        clusters = setup_evidence.build(candidate, bot)['coinglass_liquidation_clusters']
        self.assertEqual(clusters['status'], 'UNAVAILABLE_NOT_WIRED')
        self.assertEqual(clusters['heatmap_kind'], 'MODELED_POTENTIAL_LIQUIDATION_LEVELS')
        self.assertEqual(clusters['historical_kind'], 'REPORTED_PAST_LIQUIDATION_TOTALS')
        self.assertFalse(clusters['historical_totals_are_cluster_levels'])
        self.assertIsNone(clusters['cluster_levels'])


if __name__ == '__main__':
    unittest.main()
