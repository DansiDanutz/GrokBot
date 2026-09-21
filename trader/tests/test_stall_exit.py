"""The learned stall exit: absent means today's behavior, present means a
stalled grid drifting to a boundary closes before the full range break."""
import unittest
from unittest.mock import patch

from trader.autopilot import policy
from trader.review import rules as store_rules
from trader.tests.test_autopilot_policy import radar, row

HOUR = 3_600_000
RULE = dict(warmup_hours=3, min_grids_per_hour=4, adverse_travel=0.7)


# A 15%-wide range keeps the same 40/60 entry split as the old 80..130 fixture
# while placing the adverse zone about 15% of notional under water at 5x. The old
# span put it near 50%, past policy.LOSS_CAP_PCT, so the floor closed these bots
# before the stall rule could be observed at all.
# Price sits where each direction's entry split expects it: 40% up the range for
# LONG, 60% for SHORT, the middle for NEUTRAL.
SPAN, ENTRY_AT = 15.0, {'LONG': .4, 'SHORT': .6, 'NEUTRAL': .5}


def geometry(direction, price=100.0):
    low = price - ENTRY_AT[direction] * SPAN
    return dict(range_low=round(low, 4), range_high=round(low + SPAN, 4), grids=25)


def opened(direction='LONG'):
    kind = {'LONG': 'long', 'SHORT': 'short', 'NEUTRAL': 'neutral'}[direction]
    state, _ = policy.decide(policy.new_state(0),
                             radar(**{kind: [row('A', direction, **geometry(direction))]}),
                             {}, 0, 'a')
    assert state['open_bots'], 'fixture bot did not open'
    return state


def tick(state, at_ms, price, learned):
    bot = state['open_bots'][0]['engine']
    bot['risk_metadata_at_ms'] = at_ms  # keep the risk-staleness close out of the way
    with patch.object(policy, '_learned_rules', return_value=dict(learned)):
        return policy.advance(state, {'A': {'ts_ms': at_ms, 'price': price}})


def adverse_price(state, direction='LONG', fraction=0.2):
    bot = state['open_bots'][0]['engine']
    span = bot['range_high'] - bot['range_low']
    if direction == 'SHORT':
        return bot['range_low'] + (1 - fraction) * span
    return bot['range_low'] + fraction * span


class StallExitTests(unittest.TestCase):
    def test_absent_rule_keeps_todays_behavior(self):
        state = opened()
        after, _ = tick(state, 4 * HOUR, adverse_price(state), {})
        self.assertEqual(len(after['open_bots']), 1)

    def test_stalled_adverse_bot_closes_with_evidence(self):
        state = opened()
        after, events = tick(state, 4 * HOUR, adverse_price(state), dict(stall_exit=RULE))
        self.assertEqual(after['open_bots'], [])
        self.assertEqual(after['closed_bots'][0]['engine']['reason'], 'STALL_EXIT')
        block = next(e for e in events if e['type'] == 'RULE_BLOCK' and e.get('rule') == 2)
        self.assertLess(block['grids_per_hour'], RULE['min_grids_per_hour'])
        decision = next(e for e in events if e['type'] == 'DECISION' and e['action'] == 'close')
        self.assertIn(109, decision['rule_blocks'])

    def test_warmup_hours_are_respected(self):
        state = opened()
        after, _ = tick(state, 2 * HOUR, adverse_price(state), dict(stall_exit=RULE))
        self.assertEqual(len(after['open_bots']), 1)

    def test_mid_range_price_is_not_adverse(self):
        state = opened()
        after, _ = tick(state, 4 * HOUR, adverse_price(state, fraction=0.5),
                        dict(stall_exit=RULE))
        self.assertEqual(len(after['open_bots']), 1)

    def test_a_fast_grid_is_immune(self):
        state = opened()
        state['open_bots'][0]['engine']['completed_grids'] = 50
        after, _ = tick(state, 4 * HOUR, adverse_price(state), dict(stall_exit=RULE))
        self.assertEqual(len(after['open_bots']), 1)

    def test_neutral_bot_stalls_on_either_boundary(self):
        state = opened('NEUTRAL')
        price = adverse_price(state, 'SHORT')
        # The run to the adverse zone completes grids; the bot then sits there
        # while its velocity decays below the floor - that is the stall.
        mid, _ = tick(state, HOUR, price, {})
        early = mid['open_bots'][0]['engine']['completed_grids']
        after, _ = tick(mid, 6 * HOUR, price * 1.0001, dict(stall_exit=RULE))
        self.assertLess(early / 6, RULE['min_grids_per_hour'])
        self.assertEqual(after['closed_bots'][0]['engine']['reason'], 'STALL_EXIT')

    def test_min_hold_gate_still_governs_this_non_risk_close(self):
        state = opened()
        after, events = tick(state, 4 * HOUR, adverse_price(state),
                             dict(stall_exit=RULE,
                                  min_hold_hours_before_non_risk_close=12))
        self.assertEqual(len(after['open_bots']), 1)
        self.assertTrue(any(e['type'] == 'RULE_BLOCK' and e.get('rule') == 1 for e in events))


class StallExitStoreTests(unittest.TestCase):
    def base(self, **overrides):
        store = store_rules.empty_store(now_ms=1_789_000_000_000)
        store['rules'].update(overrides)
        return store

    def test_absent_and_null_and_valid_are_accepted(self):
        self.assertEqual(store_rules.validate_store(self.base()), [])
        self.assertEqual(store_rules.validate_store(self.base(stall_exit=None)), [])
        self.assertEqual(store_rules.validate_store(self.base(stall_exit=dict(RULE))), [])

    def test_bad_shapes_are_rejected(self):
        for bad in (True, 3, dict(RULE, extra=1),
                    dict(RULE, warmup_hours=0.1), dict(RULE, warmup_hours=99),
                    dict(RULE, min_grids_per_hour=0), dict(RULE, min_grids_per_hour=101),
                    dict(RULE, adverse_travel=0.4), dict(RULE, adverse_travel=1.0)):
            self.assertTrue(store_rules.validate_store(self.base(stall_exit=bad)),
                            f'should reject {bad!r}')


if __name__ == '__main__':
    unittest.main()
