import unittest
from trader.research.kucoin_replacement import decide_replacement

HOUR = 3_600_000


def current(**changes):
    data = dict(pair='OLD', asof_ms=3*HOUR, price=100, low=90, high=110,
                direction='long', expected_start_gph=10, realized_gph_6h=1,
                floating_pnl=-45, close_fee=2, direction_flip=False,
                stop_loss_hit=False, status='running', running_pairs=['OLD', 'BUSY'])
    return dict(data, **changes)


def history(**changes):
    return [dict(asof_ms=t*HOUR, realized_gph_6h=1, direction_flip=False, **changes)
            for t in (2,)]


class ReplacementTests(unittest.TestCase):
    def test_table_of_causes(self):
        radar = [dict(pair='NEW', score=5, direction='long')]
        for changes, rows, reason in [({}, history(), 'low_grid_rate'),
              ({'price': 111, 'realized_gph_6h': 2}, [], 'range_exit'),
              ({'stop_loss_hit': True, 'status': 'stopped'}, [], 'stop_loss'),
              ({'direction_flip': True}, [dict(asof_ms=2*HOUR,
                  realized_gph_6h=10, direction_flip=True)], 'direction_flip')]:
            with self.subTest(reason=reason):
                result = decide_replacement(current(**changes), radar, rows)
                self.assertEqual(result['action'], 'replace')
                self.assertIn(reason, result['triggers'])
                self.assertEqual(result['switch_cost']['floating_pnl'], -45)
                self.assertEqual(result['switch_cost']['close_fees'], 2)
                self.assertEqual(result['switch_cost']['net_realized_on_close'], -47)
                self.assertEqual(result['switch_cost']['loss_cost'], 47)

    def test_consecutive_hours_are_required(self):
        for rows in ([], [dict(asof_ms=HOUR, realized_gph_6h=1)],
                     [dict(asof_ms=2*HOUR, realized_gph_6h=10)]):
            self.assertEqual(decide_replacement(current(), [], rows)['triggers'], [])

    def test_no_qualifier_keeps_or_waits_if_already_closed(self):
        for closed, action in [(False, 'keep'), (True, 'waiting')]:
            result = decide_replacement(current(stop_loss_hit=closed,
                status='stopped' if closed else 'running'),
                [dict(pair='NEW', score=2)], history())
            self.assertEqual(result['action'], action)
            self.assertIn('no qualifying', result['reason'])

    def test_go_down_radar_skip_running_and_strict_factor(self):
        rows = [dict(pair='BUSY', score=90), dict(pair='TIE', score=2),
                dict(pair='WIN', score=2.01)]
        result = decide_replacement(current(), rows, history())
        self.assertEqual(result['replacement']['pair'], 'WIN')

    def test_same_coin_new_direction_only_when_flip_held(self):
        candidate = dict(pair='OLD', direction='short', score=5)
        result = decide_replacement(current(direction_flip=True), [],
            [dict(asof_ms=2*HOUR, direction_flip=True, realized_gph_6h=10)],
            {'direction_flip_candidate': candidate})
        self.assertEqual(result['replacement']['direction'], 'short')

    def test_threshold_never_below_two(self):
        result = decide_replacement(current(expected_start_gph=1), [], [])
        self.assertEqual(result['minimum_gph'], 2)

    def test_future_and_duplicate_rows_cannot_fake_persistence(self):
        rows = [dict(asof_ms=4*HOUR, realized_gph_6h=0),
                dict(asof_ms=3*HOUR, realized_gph_6h=0)]
        self.assertEqual(decide_replacement(current(), [], rows)['triggers'], [])

    def test_closed_stop_loss_uses_actual_realized_close_cost(self):
        running = current(status='stopped', stop_loss_hit=True, floating_pnl=0,
                          close_fee=0, switch_close_gross_pnl=-45,
                          switch_close_fee_paid=2)
        cost = decide_replacement(running, [], [])['switch_cost']
        self.assertEqual(cost['net_realized_on_close'], -47)
        self.assertEqual(cost['floating_pnl'], -45)

    def test_preregistered_minimum_rate_factor_and_explicit_override(self):
        for options, expected in [({'minimum_rate_factor': .75}, 7.5),
                                  ({'minimum_rate_factor': .5}, 5),
                                  ({'minimum_rate_factor': .75, 'minimum_gph': 3}, 3)]:
            with self.subTest(options=options):
                result = decide_replacement(current(), [], [], options)
                self.assertEqual(result['minimum_gph'], expected)


def policy_candidate(pair='NEW', score=6, **changes):
    setup = dict(eligible=True, used_margin=1000, reserved_margin=200, total_margin=1200,
                 leverage=5, preview={'profit_per_grid_min': 1}, opening_fee_budget=1)
    setup.update(changes)
    return dict(pair=pair, direction='long', score=score, setup=setup)


def policy_current(pair, rate, **changes):
    row = dict(current(pair=pair, realized_gph_6h=rate), bot_id=pair, grid_net_profit=100,
               completed_grids=100, net_equity=1200, mark_floating_pnl=0,
               floating_pnl=-.1, close_fee=.3, distance_liquidation_pct=20)
    return dict(row, **changes)


class PortfolioReplacementTests(unittest.TestCase):
    def decide(self, rows, candidates, parameters=None):
        from trader.research.kucoin_replacement import decide_portfolio_replacement
        return decide_portfolio_replacement(rows, candidates, parameters=dict({'available_cash': 100}, **(parameters or {})))

    def test_more_productive_challenger_replaces_only_worse_bot_without_held_trigger(self):
        result = self.decide([policy_current('GOOD', 10), policy_current('WORST', 3)],
                             [policy_candidate(score=6)])
        self.assertEqual(result['action'], 'replace')
        self.assertEqual(result['worst_bot_id'], 'WORST')
        self.assertEqual(result['replacement']['pair'], 'NEW')
        self.assertIn('better_cost_adjusted_grid_income', result['triggers'])
        self.assertEqual(sum(row['action'] == 'replace' for row in result['verdicts']), 1)
        self.assertGreater(result['comparison']['net_advantage_usdt'], 0)

    def test_switch_loss_and_opening_fees_can_defeat_higher_grid_rate(self):
        result = self.decide([policy_current('GOOD', 10), policy_current('WORST', 3, floating_pnl=-100)],
                             [policy_candidate(score=6)])
        self.assertEqual(result['action'], 'keep')
        self.assertEqual(result['worst_bot_id'], 'WORST')
        self.assertLess(result['rejected_candidates'][0]['comparison']['net_advantage_usdt'], 0)

    def test_no_unsafe_subtarget_tied_or_running_challenger(self):
        choices = [policy_candidate('GOOD', 30), policy_candidate('TIE', 3),
                   policy_candidate('BAD', 30, eligible=False),
                   policy_candidate('LOW', 30, minimum_grid_net_usdt=1, preview={'profit_per_grid_min': .99})]
        result = self.decide([policy_current('GOOD', 10), policy_current('WORST', 3)], choices)
        self.assertEqual(result['action'], 'keep')
        self.assertEqual(len(result['rejected_candidates']), 4)

    def test_five_percent_liquidation_warning_is_separate_from_one_percent_range_barrier(self):
        result = self.decide([policy_current('RISK', 10, distance_liquidation_pct=4.5),
                             policy_current('WORST', 3)], [policy_candidate(score=50)])
        self.assertEqual(result['action'], 'emergency')
        self.assertEqual(result['emergency_actions'][0]['bot_id'], 'RISK')
        self.assertIsNone(result['replacement'])
        self.assertIn('no additional capital', result['emergency_actions'][0]['action'])

    def test_insufficient_released_cash_never_forces_unfunded_ordinary_close(self):
        result = self.decide([policy_current('GOOD', 10), policy_current('WORST', 3)],
                             [policy_candidate(score=50)], {'available_cash': 0})
        self.assertEqual(result['action'], 'keep')
        self.assertIn('capital', result['rejected_candidates'][0]['reason'])

    def test_preregistered_horizon_changes_cost_recovery_and_does_not_spend_profit(self):
        rows = [policy_current('GOOD', 10), policy_current('WORST', 3, floating_pnl=-8)]
        short = self.decide(rows, [policy_candidate(score=5)], {'replacement_horizon_hours': 4})
        long = self.decide(rows, [policy_candidate(score=5)], {'replacement_horizon_hours': 6})
        self.assertEqual(short['action'], 'keep')
        self.assertEqual(long['action'], 'replace')
        self.assertEqual(long['comparison']['horizon_hours'], 6)
        profit = self.decide([policy_current('WORST', 3, floating_pnl=100)], [policy_candidate(score=5)])
        self.assertEqual(profit['comparison']['close_loss_cost'], 0)

    def test_random_baseline_preserves_randomized_qualifier_order(self):
        rows = [policy_current('WORST', 1)]
        choices = [policy_candidate('FIRST', 3), policy_candidate('BEST', 6)]
        normal = self.decide(rows, choices)
        random_order = self.decide(rows, choices, {'random_pick_order': True})
        self.assertEqual(normal['replacement']['pair'], 'BEST')
        self.assertEqual(random_order['replacement']['pair'], 'FIRST')


    def test_missing_available_cash_never_authorizes_a_replacement(self):
        from trader.research.kucoin_replacement import decide_portfolio_replacement
        rows = [policy_current('WORST', 1, net_equity=1500)]
        for options in ({}, {'available_cash': None}):
            with self.subTest(options=options):
                result = decide_portfolio_replacement(rows, [policy_candidate(score=50)], options)
                self.assertEqual(result['action'], 'keep')
                self.assertIsNone(result['replacement'])
                self.assertIn('unknown', result['rejected_candidates'][0]['reason'])


class FundedEntryTests(unittest.TestCase):
    def select(self, rows, occupied=(), cash=None):
        from trader.research.kucoin_replacement import select_funded_entries
        return select_funded_entries(rows, occupied, cash)

    def test_unknown_cash_keeps_research_ranking_but_withholds_forms(self):
        result = self.select([policy_candidate('A'), policy_candidate('B')])
        self.assertEqual(result['selected'], [])
        self.assertEqual(len(result['research_ranking']), 2)
        self.assertFalse(result['available_cash_known'])
        self.assertIsNone(result['remaining_cash'])
        self.assertTrue(all('unknown' in row['reason'] for row in result['rejected']))

    def test_one_vacancy_uses_net_income_ranking_and_reserves_cash_once(self):
        rows = [policy_candidate('RAW_RATE', 10, opening_fee_budget=40),
                policy_candidate('NET_INCOME', 8, opening_fee_budget=1)]
        result = self.select(rows, ['LIVE'], 1200)
        self.assertEqual([row['pair'] for row in result['selected']], ['NET_INCOME'])
        self.assertEqual(result['remaining_cash'], 0)
        self.assertEqual(result['required_cash'], 1200)
        self.assertEqual(result['research_ranking'][0]['pair'], 'NET_INCOME')

    def test_no_duplicate_existing_unsafe_or_unfunded_entries(self):
        rows = [policy_candidate('LIVE'), policy_candidate('A'), policy_candidate('A'),
                policy_candidate('UNSAFE', eligible=False), policy_candidate('B')]
        result = self.select(rows, ['LIVE'], 2400)
        self.assertEqual([row['pair'] for row in result['selected']], ['A'])
        self.assertEqual(result['remaining_cash'], 1200)
        self.assertEqual(self.select(rows, [], 1199)['selected'], [])
        full = self.select([policy_candidate('A'), policy_candidate('B'), policy_candidate('C')], [], 2400)
        self.assertEqual(len(full['selected']), 2)
        self.assertEqual(full['remaining_cash'], 0)


class PositiveNetPolicyTests(unittest.TestCase):
    def test_candidate_accepts_only_known_strictly_positive_cash_net(self):
        from trader.research.kucoin_replacement import candidate_economics
        for floor, accepted in ((.25, True), (1, True), (0, False), (-.1, False), (None, False)):
            with self.subTest(floor=floor):
                result = candidate_economics(policy_candidate(preview={'profit_per_grid_min': floor}))
                self.assertEqual(result['eligible'], accepted)

    def test_declared_and_explicit_requirements_cannot_contradict_or_weaken_each_other(self):
        from trader.research.kucoin_replacement import candidate_economics
        for changes, parameters in (({'minimum_grid_net_usdt': 1}, None),
                ({'minimum_grid_net_usdt': 0, 'target_profit_per_grid': 1}, None),
                ({'minimum_grid_net_usdt': 0}, {'minimum_grid_net_usdt': 1}),
                ({'minimum_grid_net_usdt': None}, None), ({'target_profit_per_grid': -1}, None)):
            with self.subTest(changes=changes, parameters=parameters):
                row = policy_candidate(preview={'profit_per_grid_min': .25}, **changes)
                self.assertFalse(candidate_economics(row, parameters=parameters)['eligible'])
        row = policy_candidate(minimum_grid_net_usdt=1, target_profit_per_grid=1)
        self.assertTrue(candidate_economics(row, parameters={'minimum_grid_net_usdt': 1})['eligible'])

    def test_funded_entries_use_positive_cash_and_explicit_floor(self):
        from trader.research.kucoin_replacement import select_funded_entries
        row = policy_candidate(preview={'profit_per_grid_min': .25})
        self.assertEqual(len(select_funded_entries([row], available_cash=1200)['selected']), 1)
        legacy = select_funded_entries([row], available_cash=1200, parameters={'minimum_grid_net_usdt': 1})
        self.assertEqual(legacy['selected'], [])

    def test_positive_subunit_challenger_can_recover_switch_costs(self):
        from trader.research.kucoin_replacement import decide_portfolio_replacement
        row = policy_candidate(score=30, preview={'profit_per_grid_min': .25})
        incumbent = policy_current('OLD', 1)
        result = decide_portfolio_replacement([incumbent], [row], {'available_cash': 100})
        self.assertEqual(result['action'], 'replace')
        legacy = decide_portfolio_replacement([incumbent], [row],
                    {'available_cash': 100, 'minimum_grid_net_usdt': 1})
        self.assertEqual(legacy['action'], 'keep')

    def test_no_completed_history_never_invents_unit_income(self):
        from trader.research.kucoin_replacement import _current_economics, decide_portfolio_replacement
        row = policy_current('OLD', 1, completed_grids=0, grid_net_profit=0)
        result = decide_portfolio_replacement([row], [policy_candidate(score=50)], {'available_cash': 100})
        self.assertEqual(result['action'], 'keep')
        self.assertIn('per-grid income', result['reason'])
        self.assertIsNone(_current_economics(row, 6))
        supplied = dict(row, actual_net_usdt_per_grid=.25)
        estimate = _current_economics(supplied, 6)
        self.assertEqual(estimate['current_net_per_grid'], .25)
        self.assertEqual(estimate['current_projected_income'], 1.5)
        self.assertIn('supplied actual', estimate['profit_basis'])
        nominal = dict(row, target_profit_per_grid=1)
        self.assertIsNone(_current_economics(nominal, 6))

    def test_unknown_income_does_not_mask_mandatory_risk_warning(self):
        from trader.research.kucoin_replacement import decide_portfolio_replacement
        for status in ('running', 'liquidated'):
            with self.subTest(status=status):
                row = policy_current('RISK', 0, completed_grids=0, grid_net_profit=0,
                                     status=status, distance_liquidation_pct=4)
                result = decide_portfolio_replacement([row], [], {'available_cash': 100})
                self.assertEqual(result['action'], 'emergency')
                self.assertEqual(result['emergency_actions'][0]['bot_id'], 'RISK')
                self.assertIsNone(result['replacement'])

    def test_actual_cash_prior_is_visible_and_completed_mean_takes_precedence(self):
        from trader.research.kucoin_replacement import decide_portfolio_replacement
        for count, grid_net, expected, basis in ((0, 0, .25, 'supplied actual'),
                                                (4, 2, .5, 'observed mean')):
            with self.subTest(count=count):
                row = policy_current('OLD', 1, completed_grids=count, grid_net_profit=grid_net,
                                     actual_net_usdt_per_grid=.25)
                result = decide_portfolio_replacement([row], [policy_candidate(score=10)],
                                                      {'available_cash': 100})
                self.assertEqual(result['comparison']['current_net_per_grid'], expected)
                self.assertIn(basis, result['comparison']['current_profit_basis'])
