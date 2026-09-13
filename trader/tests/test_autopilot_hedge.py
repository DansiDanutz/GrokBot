"""Hedge trigger: thresholds, cluster gate, two-book accounting and the flag."""
from copy import deepcopy
import unittest

from trader.autopilot import hedge
from trader.autopilot import policy
from trader.autopilot.risk import protection_needed
from trader.papergrid import open_bot, close_bot, step
from trader.review import rules as learned_rules

HOUR = 3_600_000
ON = {'hedge_enabled': True}


def carrying(**extra):
    """A minimal hedgeable ledger shape: 100 of a 200-contract full range."""
    base = dict(bot_id=7, symbol='XUSDTM', direction='LONG', closed_ms=None,
                grids=20, contracts_per_line=10.0, position_contracts=100.0,
                grid_profit=0.0, realized_pnl=0.0, unrealized_pnl=-10.0,
                fees_paid=0.0, funding_paid=0.0)
    base.update(extra)
    return base


def spec(**extra):
    base = dict(bot_id=1, symbol='XUSDTM', direction='LONG', range_low=1.0,
                range_high=2.0, grid_interval=.05, grids=20, step_pct=.5,
                quantity_per_grid=10, notional_usdt=1000, leverage=5,
                funding_pct=.01, accounting_version=2, maintain_margin=.005,
                risk_limit=1_000_000)
    base.update(extra)
    return base


def adverse(bot, at=60_000, low=1.3):
    """Drive the price down so the long ladder accumulates adverse inventory."""
    top = bot['last_price']
    return step(bot, dict(ts_ms=at, open=top, high=top, low=low, close=low))[0]


class HedgeThresholdTests(unittest.TestCase):
    def test_inventory_ratio_boundary_is_strict(self):
        # inventory_pnl excludes the grid ledger, so loss == 5 - unrealized.
        at_threshold = carrying(grid_profit=5.0, unrealized_pnl=-5.0)
        self.assertEqual(hedge.metrics(at_threshold)['inventory_pnl'], -10.0)
        self.assertFalse(hedge.triggered(at_threshold, None, 0, 2.0, .5, None)[0])
        past = carrying(grid_profit=5.0, unrealized_pnl=-5.01)
        self.assertTrue(hedge.triggered(past, None, 0, 2.0, .5, None)[0])
        # A winning inventory never triggers, however small the grid ledger.
        winning = carrying(grid_profit=0.0, unrealized_pnl=1.0, fees_paid=0.0)
        self.assertFalse(hedge.triggered(winning, None, 0, 2.0, .5, None)[0])

    def test_position_fraction_boundary_and_costs_count_as_inventory(self):
        self.assertTrue(hedge.triggered(carrying(), None, 0, 2.0, .5, None)[0])
        self.assertFalse(hedge.triggered(carrying(position_contracts=99.9),
                                         None, 0, 2.0, .5, None)[0])
        self.assertTrue(hedge.triggered(carrying(position_contracts=150.0),
                                        None, 0, 2.0, .75, None)[0])
        # Fees and funding sit in the inventory bucket, as the owner's -760.62 does.
        costs = carrying(unrealized_pnl=0.0, fees_paid=3.0, funding_paid=1.0)
        self.assertEqual(hedge.metrics(costs)['inventory_pnl'], -4.0)
        self.assertTrue(hedge.triggered(costs, None, 0, 2.0, .5, None)[0])

    def test_cluster_gate_respected_missing_and_stale_fail_closed(self):
        near = dict(liq_below_pct=1.5, liq_above_pct=9.0,
                    liq_clusters_generated_at_ms=0)
        self.assertTrue(hedge.triggered(carrying(), near, HOUR, 2.0, .5, 2.0)[0])
        far = dict(near, liq_below_pct=2.5)
        self.assertFalse(hedge.triggered(carrying(), far, HOUR, 2.0, .5, 2.0)[0])
        # Missing annotation, an unusable zero, and a stale file all fail closed.
        for clusters in (None, {}, dict(near, liq_below_pct=0.0),
                         dict(near, liq_clusters_generated_at_ms=-9 * HOUR)):
            self.assertFalse(hedge.triggered(carrying(), clusters, HOUR,
                                             2.0, .5, 2.0)[0], clusters)
        # With the gate switched off the same missing data is irrelevant.
        self.assertTrue(hedge.triggered(carrying(), None, HOUR, 2.0, .5, None)[0])

    def test_losing_side_is_the_side_the_inventory_sits_on(self):
        rows = dict(liq_below_pct=1.0, liq_above_pct=8.0)
        self.assertEqual(hedge.cluster_distance_pct(carrying(), rows, 0), 1.0)
        short = carrying(position_contracts=-100.0)
        self.assertEqual(hedge.cluster_distance_pct(short, rows, 0), 8.0)
        self.assertIsNone(hedge.cluster_distance_pct(
            carrying(position_contracts=0.0), rows, 0))


class HedgeFlagTests(unittest.TestCase):
    def test_flag_off_never_hedges_under_any_input(self):
        for rules in ({}, {'hedge_enabled': False}, {'hedge_enabled': None},
                      {'hedge_enabled': 'yes'}, {'hedge_enabled': 1}, None):
            fired, detail = hedge.should_hedge(carrying(), None, rules, 0)
            self.assertFalse(fired, rules)
            self.assertEqual(detail, {})

    def test_override_on_still_obeys_the_shipped_thresholds(self):
        self.assertTrue(hedge.should_hedge(carrying(), None, ON, 0)[0])
        self.assertFalse(hedge.should_hedge(
            carrying(position_contracts=10.0), None, ON, 0)[0])

    def test_already_hedged_flat_and_closed_bots_are_not_hedgeable(self):
        self.assertFalse(hedge.hedgeable(carrying(position_contracts=0.0)))
        self.assertFalse(hedge.hedgeable(carrying(closed_ms=1)))
        self.assertFalse(hedge.hedgeable(carrying(hedge_books=[])))
        with self.assertRaises(ValueError):
            hedge.hedge_leg(carrying(position_contracts=0.0), 1.0, 0)

    def test_validate_store_accepts_and_rejects_the_new_key(self):
        store = learned_rules.empty_store(0)
        self.assertNotIn('hedge_enabled', learned_rules.EMPTY_RULES)
        self.assertEqual(learned_rules.validate_store(store), [])
        for value in (True, False, None):
            store['rules']['hedge_enabled'] = value
            self.assertEqual(learned_rules.validate_store(store), [])
        for value in ('true', 1, 0.0, [], {}):
            store['rules']['hedge_enabled'] = value
            self.assertEqual(learned_rules.validate_store(store),
                             ['hedge_enabled must be a boolean or absent'])


class HedgedBookTests(unittest.TestCase):
    def setUp(self):
        self.bot = adverse(open_bot(spec(), 1.5, 0))
        self.assertGreater(self.bot['position_contracts'], 0)

    def test_equity_loses_exactly_the_entry_fee_and_the_net_goes_flat(self):
        before, position = self.bot['equity'], self.bot['position_contracts']
        hedged, events = hedge.hedge_leg(self.bot, 1.3, 60_000)
        fee = position * 1.3 * self.bot['fee_rate_taker']
        self.assertAlmostEqual(hedged['equity'], before - fee)
        self.assertEqual(hedged['position_contracts'], 0.0)
        self.assertEqual((hedged['long_contracts'], hedged['short_contracts']),
                         (position, -position))
        self.assertEqual([e['type'] for e in events], ['HEDGE'])
        self.assertTrue(all(isinstance(v, (int, float)) for k, v in events[0].items()
                            if k not in ('type', 'symbol')))
        self.assertEqual(self.bot['position_contracts'], position)  # input untouched

    def test_short_inventory_puts_the_offsetting_book_on_the_long_side(self):
        short = step(open_bot(spec(direction='SHORT'), 1.5, 0),
                     dict(ts_ms=60_000, open=1.5, high=1.7, low=1.5, close=1.7))[0]
        self.assertLess(short['position_contracts'], 0)
        hedged, _ = hedge.hedge_leg(short, 1.7, 60_000)
        self.assertEqual(hedged['hedge_books'][0]['direction'], 'LONG')
        self.assertGreater(hedged['long_contracts'], 0)
        self.assertEqual(hedged['position_contracts'], 0.0)

    def test_grids_keep_being_counted_across_both_books(self):
        hedged, _ = hedge.hedge_leg(self.bot, 1.3, 60_000)
        grids_at_hedge = hedged['completed_grids']
        later, events = step(hedged, dict(ts_ms=120_000, open=1.3, high=1.45,
                                          low=1.3, close=1.45))
        self.assertGreater(later['completed_grids'], grids_at_hedge)
        self.assertEqual(later['completed_grids'],
                         sum(b['completed_grids'] for b in later['hedge_books']))
        self.assertAlmostEqual(later['grid_profit'],
                               sum(b['grid_profit'] for b in later['hedge_books']))
        self.assertTrue(any(e['type'] == 'GRID' for e in events))

    def test_funding_is_charged_on_both_books(self):
        unmanaged = adverse(open_bot(spec(funding_managed=False), 1.5, 0))
        hedged, _ = hedge.hedge_leg(unmanaged, 1.3, 60_000)
        later, _ = step(hedged, dict(ts_ms=8 * HOUR, price=1.3))
        charges = [book['funding_paid'] for book in later['hedge_books']]
        self.assertGreater(charges[0], 0)
        self.assertLess(charges[1], 0)
        self.assertAlmostEqual(later['funding_paid'], sum(charges))

    def test_liquidation_guard_iterates_every_leg(self):
        tight = adverse(open_bot(spec(risk_limit=50), 1.5, 0))
        hedged, _ = hedge.hedge_leg(tight, 1.3, 60_000)
        self.assertTrue(protection_needed(hedged))
        only_offset = deepcopy(hedged)
        only_offset['hedge_books'][0]['position_contracts'] = 0.0
        self.assertTrue(protection_needed(only_offset))
        flat = deepcopy(only_offset)
        flat['hedge_books'][1]['position_contracts'] = 0.0
        self.assertFalse(protection_needed(flat))

    def test_close_after_hedge_flattens_both_books_exactly_once(self):
        hedged, _ = hedge.hedge_leg(self.bot, 1.3, 60_000)
        closed, events = close_bot(hedged, 1.35, 120_000, 'RANGE_BREAK')
        self.assertEqual([e['type'] for e in events], ['FILL', 'FILL', 'CLOSE'])
        self.assertEqual(sorted(e['book'] for e in events if e['type'] == 'FILL'),
                         [0, 1])
        self.assertEqual((closed['long_contracts'], closed['short_contracts']), (0, 0))
        self.assertEqual(closed['reason'], 'RANGE_BREAK')
        again, repeated = close_bot(closed, 1.35, 180_000, 'RANGE_BREAK')
        self.assertEqual(repeated, [])
        self.assertEqual(again, closed)

    def test_replay_is_deterministic(self):
        def run():
            bot, events = hedge.hedge_leg(self.bot, 1.3, 60_000)
            for at, price in ((120_000, 1.45), (180_000, 1.2), (240_000, 1.4)):
                bot, emitted = step(bot, dict(ts_ms=at, price=price))
                events.extend(emitted)
            return bot, events
        first, second = run(), run()
        self.assertEqual(first[1], second[1])
        self.assertEqual(first[0], second[0])


class HedgePolicyTests(unittest.TestCase):
    """decide() must be byte-identical while the flag is off."""

    def setUp(self):
        self.original = policy._learned_rules

    def tearDown(self):
        policy._learned_rules = self.original

    def _state(self):
        from trader.tests.test_autopilot_policy import radar, row
        state, _ = policy.decide(policy.new_state(0), radar(long=[row('A')]), {}, 0, 'a')
        wrapper = state['open_bots'][0]
        wrapper['engine'] = adverse(wrapper['engine'], at=60_000, low=82.0)
        self.report = radar(long=[dict(row('A'), price=82.0, liq_below_pct=1.0,
                                       liq_above_pct=9.0)])
        return state

    def test_decide_is_unchanged_with_the_flag_off(self):
        state = self._state()
        policy._learned_rules = lambda: {}
        after, events = policy.decide(deepcopy(state), self.report, {'A': 82.0}, HOUR, 'b')
        self.assertNotIn('hedge_books', after['open_bots'][0]['engine'])
        self.assertFalse(any(e['type'] == 'HEDGE' for e in events))

    def test_decide_hedges_before_the_close_decision_when_enabled(self):
        state = self._state()
        policy._learned_rules = lambda: dict(ON)
        after, events = policy.decide(deepcopy(state), self.report, {'A': 82.0}, HOUR, 'b')
        engine = after['open_bots'][0]['engine']
        self.assertIn('hedge_books', engine)
        self.assertEqual(engine['position_contracts'], 0.0)
        hedges = [e for e in events if e['type'] == 'HEDGE']
        self.assertEqual(len(hedges), 1)
        self.assertEqual(hedges[0]['price'], 82.0)
        decision = next(e for e in events if e['type'] == 'DECISION'
                        and e['action'] == 'hedge')
        self.assertEqual(decision['rule_blocks'],
                         [policy.DECISION_RULES['hedge_trigger']])
        # Hedging replaces the close, it does not precede one in the same cycle.
        self.assertFalse(any(e['type'] == 'CLOSE' for e in events))


if __name__ == '__main__':
    unittest.main()
