"""Offline contracts for the opportunity-cost close gate (T6).

Owner decision (2026-09-13): a losing bot whose grids still work is hedged
first; it is closed only when a better coin is free. Risk closes and
PROFILE_UPDATE stay unconditional.
"""
import unittest
from copy import deepcopy
from datetime import date, timedelta
from unittest import mock

from trader.autopilot import opportunity, policy
from trader.autopilot.constants import (MAX_AGE_HOURS, OPPORTUNITY_HOLD_MAX_AGE_HOURS,
                                        PROMOTION_MARGIN)
from trader.autopilot.storage import validate_event
from trader.review import rules as learned_rules
from trader.tests.test_autopilot_policy import legacy_closes, radar, row

HOUR = 3_600_000
NOW = 1_789_200_000_000


def weak(symbol, direction='LONG', **extra):
    """Score 55: young listing, thin turnover, wide spread."""
    return row(symbol, direction, listing_age_days=10,
               turnover_24h_usdt=3_000_000, spread_pct=.15, **extra)


def strong(symbol, direction='LONG', **extra):
    """Score 80 (76 as NEUTRAL): deep turnover and a tight spread."""
    return row(symbol, direction, turnover_24h_usdt=30_000_000,
               spread_pct=.05, **extra)


def marginal(symbol, direction='LONG', **extra):
    """Score 60: better than weak(), but by less than PROMOTION_MARGIN."""
    return row(symbol, direction, listing_age_days=10,
               turnover_24h_usdt=3_000_000, spread_pct=.05, **extra)


def set_rules(**kwargs):
    store = {'min_hold_hours_before_non_risk_close': None,
             'require_trend_alignment': False, 'symbol_cooldowns': {}}
    store.update(kwargs)
    return mock.patch.object(policy, '_learned_rules', return_value=store)


def open_weak_long(now_ms=0):
    """One weak LONG bot, opened from a radar that offers nothing else."""
    state, _ = policy.decide(policy.new_state(now_ms), radar(long=[weak('A')]),
                             {}, now_ms, 'a')
    return state


def blocks(events, rule):
    return [e for e in events if e['type'] == 'RULE_BLOCK' and e['rule'] == rule]


def skips(events, code):
    return [e for e in events if e['type'] == 'DECISION'
            and e['action'] == 'skip' and code in e['rule_blocks']]


class HoldsWithoutABetterCoinTests(unittest.TestCase):
    def test_label_flip_is_deferred_and_the_bot_keeps_trading(self):
        state = open_weak_long()
        held, events = policy.decide(state, radar(short=[weak('A', 'SHORT')]), {}, 1, 'b')
        self.assertEqual(len(held['open_bots']), 1)
        self.assertEqual(held['closed_bots'], [])
        self.assertEqual(held['open_bots'][0]['engine']['reason'], None)
        block = blocks(events, policy.DECISION_RULES['opportunity_hold'])
        self.assertEqual(len(block), 1)
        self.assertEqual(block[0]['best_candidate_score'], 0.0)
        self.assertGreater(block[0]['bot_score'], 0)
        for key, value in block[0].items():
            if key not in ('type', 'symbol'):
                self.assertIsInstance(value, (int, float), f'{key} must be numeric')
        validate_event(block[0])
        self.assertEqual(len(skips(events, policy.DECISION_RULES['opportunity_hold'])), 1)

    def test_max_age_and_dropped_are_deferred_too(self):
        aged, _ = policy.decide(open_weak_long(), radar(long=[weak('A')]),
                                {}, 73 * HOUR, 'b')
        self.assertEqual(len(aged['open_bots']), 1)
        state, _ = policy.decide(open_weak_long(), radar(), {}, 1, 'b')
        dropped, events = policy.decide(state, radar(), {}, 2, 'c')
        self.assertEqual(len(dropped['open_bots']), 1)
        self.assertTrue(blocks(events, policy.DECISION_RULES['opportunity_hold']))

    def test_a_candidate_inside_the_margin_is_not_better(self):
        state = open_weak_long()
        report = radar(short=[weak('A', 'SHORT')], long=[marginal('B')])
        held, _ = policy.decide(state, report, {}, 1, 'b')
        self.assertEqual(held['closed_bots'], [])
        self.assertIn('A', {w['engine']['symbol'] for w in held['open_bots']})

    def test_the_closing_bot_is_never_its_own_replacement(self):
        state = open_weak_long()
        # The same coin, re-scored far higher, still cannot take the slot it
        # would vacate: a non-risk close puts the symbol into cooldown.
        held, _ = policy.decide(state, radar(short=[strong('A', 'SHORT')]), {}, 1, 'b')
        self.assertEqual(len(held['open_bots']), 1)


class MaxAgeCeilingTests(unittest.TestCase):
    """A stale bot leaves even when the market offers nothing better."""

    def _decide_at(self, hours, report=None):
        state = open_weak_long()
        return policy.decide(state, report or radar(long=[weak('A')]), {},
                             int(hours * HOUR), 'b')

    def test_the_ceiling_is_twice_the_max_age(self):
        self.assertEqual(OPPORTUNITY_HOLD_MAX_AGE_HOURS, 2 * MAX_AGE_HOURS)

    def test_under_the_ceiling_a_max_age_bot_is_still_held(self):
        held, events = self._decide_at(OPPORTUNITY_HOLD_MAX_AGE_HOURS - 1)
        self.assertEqual(len(held['open_bots']), 1)
        self.assertEqual(held['closed_bots'], [])
        self.assertTrue(blocks(events, policy.DECISION_RULES['opportunity_hold']))

    def test_past_the_ceiling_a_max_age_bot_closes_without_any_candidate(self):
        closed, events = self._decide_at(OPPORTUNITY_HOLD_MAX_AGE_HOURS)
        self.assertEqual(closed['closed_bots'][0]['engine']['reason'], 'MAX_AGE')
        self.assertFalse(blocks(events, policy.DECISION_RULES['opportunity_hold']))
        close = next(e for e in events if e['type'] == 'DECISION' and e['action'] == 'close')
        self.assertEqual(close['rule_blocks'], [policy.DECISION_CLOSE_REASONS['MAX_AGE']])

    def test_the_ceiling_does_not_release_a_label_flip(self):
        held, events = self._decide_at(OPPORTUNITY_HOLD_MAX_AGE_HOURS + 24,
                                       radar(short=[weak('A', 'SHORT')]))
        self.assertEqual(len(held['open_bots']), 1)
        self.assertEqual(held['closed_bots'], [])
        self.assertTrue(blocks(events, policy.DECISION_RULES['opportunity_hold']))

    def test_the_ceiling_does_not_release_a_dropped_bot(self):
        state, _ = policy.decide(open_weak_long(), radar(), {}, 1, 'b')
        ceiling = int(OPPORTUNITY_HOLD_MAX_AGE_HOURS * HOUR) + HOUR
        held, events = policy.decide(state, radar(), {}, ceiling, 'c')
        self.assertEqual(len(held['open_bots']), 1)
        self.assertTrue(blocks(events, policy.DECISION_RULES['opportunity_hold']))


class ClosesWhenABetterCoinIsFreeTests(unittest.TestCase):
    def test_label_flip_closes_and_the_better_coin_takes_the_slot(self):
        state = open_weak_long()
        report = radar(short=[weak('A', 'SHORT')], long=[strong('B')])
        closed, events = policy.decide(state, report, {}, 1, 'b')
        self.assertEqual(closed['closed_bots'][0]['engine']['reason'], 'LABEL_FLIP')
        self.assertIn('B', {w['engine']['symbol'] for w in closed['open_bots']})
        self.assertFalse(blocks(events, policy.DECISION_RULES['opportunity_hold']))

    def test_the_freed_slot_may_be_borrowed_by_another_direction(self):
        state = open_weak_long()
        report = radar(short=[weak('A', 'SHORT')], neutral=[strong('B', 'NEUTRAL')])
        closed, _ = policy.decide(state, report, {}, 1, 'b')
        self.assertEqual(closed['closed_bots'][0]['engine']['reason'], 'LABEL_FLIP')
        self.assertIn('B', {w['engine']['symbol'] for w in closed['open_bots']})

    def test_capacity_is_judged_with_the_slot_already_free(self):
        # A full book must still be able to rotate: the bot under judgement is
        # removed before candidate admission, or bot_capacity would self-block.
        report = radar(long=[weak('A'), weak('C'), weak('D')],
                       short=[weak('E', 'SHORT'), weak('F', 'SHORT')])
        state, _ = policy.decide(policy.new_state(0), report, {}, 0, 'a')
        self.assertEqual(len(state['open_bots']), 5)
        flipped = radar(short=[weak('A', 'SHORT'), weak('E', 'SHORT'), weak('F', 'SHORT')],
                        long=[weak('C'), weak('D'), strong('B')])
        # Three hours in, so the challenger may actually take a Core seat.
        closed, _ = policy.decide(state, flipped, {}, 3 * HOUR, 'b')
        self.assertEqual(closed['closed_bots'][0]['engine']['symbol'], 'A')
        self.assertEqual(closed['closed_bots'][0]['engine']['reason'], 'LABEL_FLIP')


class RiskAndProfileClosesAreUnconditionalTests(unittest.TestCase):
    def test_range_break_closes_without_any_candidate(self):
        state = open_weak_long()
        state['open_bots'][0]['signals'] = ['RANGE_BREAK']
        closed, events = policy.decide(state, radar(long=[weak('A')]), {}, 1, 'b')
        self.assertEqual(closed['closed_bots'][0]['engine']['reason'], 'RANGE_BREAK')
        self.assertFalse(blocks(events, policy.DECISION_RULES['opportunity_hold']))

    def test_profile_update_closes_without_any_candidate(self):
        state = open_weak_long()
        state['open_bots'][0]['engine']['accounting_version'] = 0
        closed, events = policy.decide(state, radar(long=[weak('A')]), {'A': 100}, 1, 'b')
        self.assertEqual(closed['closed_bots'][0]['engine']['reason'], 'PROFILE_UPDATE')
        self.assertFalse(blocks(events, policy.DECISION_RULES['opportunity_hold']))

    def test_advance_risk_closes_are_untouched(self):
        state = open_weak_long()
        closed, events = policy.advance(state, {'A': {'ts_ms': 60_000, 'price': 140}})
        self.assertEqual(closed['closed_bots'][0]['engine']['reason'], 'RANGE_BREAK')
        self.assertFalse(blocks(events, policy.DECISION_RULES['opportunity_hold']))


class LatchTests(unittest.TestCase):
    def test_one_rule_block_per_bot_per_scan(self):
        flip = radar(short=[weak('A', 'SHORT')])
        first, events = policy.decide(open_weak_long(), flip, {}, 1, 'b')
        again, repeat = policy.decide(first, flip, {}, 2, 'b')
        self.assertEqual(len(blocks(events, 19)), 1)
        self.assertEqual(blocks(repeat, 19), [])
        # ... and one more when the radar scan changes.
        third, fresh = policy.decide(again, flip, {}, 3, 'c')
        self.assertEqual(len(blocks(fresh, 19)), 1)
        self.assertEqual(third['open_bots'][0]['rule_blocks']['opportunity_scan'], 'c')
        # The deferral itself is reported every pass, not only once.
        self.assertEqual(len(skips(repeat, 19)), 1)


class KillSwitchTests(unittest.TestCase):
    def test_constant_false_restores_the_unconditional_close(self):
        state = open_weak_long()
        with legacy_closes():
            closed, events = policy.decide(state, radar(short=[weak('A', 'SHORT')]), {}, 1, 'b')
        self.assertEqual(closed['closed_bots'][0]['engine']['reason'], 'LABEL_FLIP')
        self.assertFalse(any(e['type'] == 'RULE_BLOCK' for e in events))
        self.assertNotIn('rule_blocks', closed['closed_bots'][0])

    def test_learned_rule_false_matches_the_constant_being_false(self):
        state = open_weak_long()
        flip = radar(short=[weak('A', 'SHORT')])
        with set_rules(opportunity_cost_close=False):
            by_rule, rule_events = policy.decide(state, flip, {}, 1, 'b')
        with legacy_closes(), set_rules():
            by_constant, constant_events = policy.decide(state, flip, {}, 1, 'b')
        self.assertEqual(by_rule, by_constant)
        self.assertEqual(rule_events, constant_events)

    def test_learned_rule_true_overrides_a_disabled_constant(self):
        state = open_weak_long()
        with legacy_closes(), set_rules(opportunity_cost_close=True):
            held, _ = policy.decide(state, radar(short=[weak('A', 'SHORT')]), {}, 1, 'b')
        self.assertEqual(len(held['open_bots']), 1)

    def test_an_absent_learned_rule_leaves_the_constant_deciding(self):
        self.assertTrue(policy._opportunity_enabled({}))
        self.assertTrue(policy._opportunity_enabled({'opportunity_cost_close': None}))
        self.assertFalse(policy._opportunity_enabled({'opportunity_cost_close': False}))


class LearnedGatesAreRespectedTests(unittest.TestCase):
    def _sections_and_wrapper(self):
        state = open_weak_long()
        scored = policy._qualified_rows(radar(long=[strong('B')]), [strong('B')], 0)
        sections = policy._candidate_sections(radar(long=[strong('B')]), scored)
        return state, sections, state['open_bots'][0]

    def test_candidate_blocked_by_the_learned_trend_gate_is_not_better(self):
        state, sections, wrapper = self._sections_and_wrapper()
        rules = {'require_trend_alignment': True, 'symbol_cooldowns': {}}
        labels = {'B': 'TURNING-DOWN'}  # fresher radar read disagrees with the section
        better, detail = opportunity.better_candidate_exists(
            state, sections, wrapper, labels, 0, rules)
        self.assertFalse(better)
        self.assertEqual(detail['candidate_score'], 0.0)
        off = dict(rules, require_trend_alignment=False)
        self.assertTrue(opportunity.better_candidate_exists(
            state, sections, wrapper, labels, 0, off)[0])

    def test_candidate_inside_a_learned_symbol_cooldown_is_not_better(self):
        state = open_weak_long(NOW)
        until = (date.fromtimestamp(NOW / 1000) + timedelta(days=7)).isoformat()
        report = radar(short=[weak('A', 'SHORT')], long=[strong('B')])
        with set_rules(symbol_cooldowns={'B': until}):
            held, _ = policy.decide(state, report, {}, NOW, 'b')
        self.assertEqual(len(held['open_bots']), 1)
        with set_rules():
            closed, _ = policy.decide(state, report, {}, NOW, 'b')
        self.assertEqual(closed['closed_bots'][0]['engine']['reason'], 'LABEL_FLIP')

    def test_state_cooldown_keeps_a_recently_closed_coin_out(self):
        state, sections, wrapper = self._sections_and_wrapper()
        cooled = dict(state, cooldowns={'B': 10 * HOUR})
        self.assertFalse(opportunity.better_candidate_exists(
            cooled, sections, wrapper, {}, 0, {})[0])


class PureFunctionTests(unittest.TestCase):
    def test_detail_is_numeric_and_the_state_is_never_mutated(self):
        state = open_weak_long()
        scored = policy._qualified_rows(radar(long=[strong('B')]), [strong('B')], 0)
        sections = policy._candidate_sections(radar(long=[strong('B')]), scored)
        before = deepcopy(state)
        better, detail = opportunity.better_candidate_exists(
            state, sections, state['open_bots'][0], {}, 0, {})
        self.assertEqual(state, before)
        self.assertTrue(better)
        self.assertEqual(set(detail), {'candidate_score', 'bot_score', 'margin'})
        self.assertTrue(all(isinstance(v, float) for v in detail.values()))
        self.assertGreaterEqual(detail['margin'], PROMOTION_MARGIN)

    def test_a_bot_without_decision_context_scores_zero_and_is_replaceable(self):
        state = open_weak_long()
        wrapper = dict(state['open_bots'][0])
        wrapper.pop('decision_context')
        self.assertEqual(opportunity.opening_score(wrapper), 0.0)
        self.assertEqual(opportunity.opening_score({'decision_context': None,
                                                    'engine': {'direction': 'LONG'}}), 0.0)

    def test_fallback_direction_order_mirrors_decide(self):
        self.assertEqual(opportunity._slot_directions({'slot_direction': 'LONG'}),
                         ('LONG', 'NEUTRAL', 'SHORT'))
        self.assertEqual(opportunity._slot_directions({'slot_direction': 'NEUTRAL'}),
                         ('NEUTRAL', 'LONG', 'SHORT'))
        self.assertEqual(
            opportunity._slot_directions({'engine': {'direction': 'SHORT'}}),
            ('SHORT', 'NEUTRAL', 'LONG'))


class StoreSchemaTests(unittest.TestCase):
    def store(self, **rules):
        base = dict(learned_rules.EMPTY_RULES)
        base.update(rules)
        return {'version': learned_rules.SCHEMA_VERSION, 'updated_at_ms': NOW,
                'rules': base, 'notes': []}

    def test_absent_key_keeps_existing_stores_valid(self):
        self.assertEqual(learned_rules.validate_store(self.store()), [])
        self.assertNotIn('opportunity_cost_close', learned_rules.EMPTY_RULES)

    def test_boolean_values_are_accepted(self):
        for value in (True, False):
            self.assertEqual(
                learned_rules.validate_store(self.store(opportunity_cost_close=value)), [])

    def test_non_boolean_values_are_rejected(self):
        for value in (1, 0, 'true', [], {}):
            errors = learned_rules.validate_store(self.store(opportunity_cost_close=value))
            self.assertIn('opportunity_cost_close must be a boolean or absent', errors)


if __name__ == '__main__':
    unittest.main()
