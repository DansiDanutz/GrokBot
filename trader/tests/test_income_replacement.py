"""Income-chart decisions use measured six-hour income, never dollar priors."""
import copy
import unittest

from trader.research.kucoin_replacement import (
    candidate_economics, decide_portfolio_replacement, select_funded_entries,
)

HOUR = 3_600_000
OPTIONS = {'strategy': 'income_chart_v3', 'available_cash': 1200}


def candidate(pair='NEW', income=4., gph=1., **changes):
    form = dict(can_arm=True, funded_economics_eligible=True, used_margin=1000,
                reserved_margin=200, leverage=5, opening_fee_budget=1.,
                preview={'profit_per_grid_min': .01})
    form.update(changes)
    return dict(pair=pair, setup=form, grid_income_per_hour=income,
                expected_gph=gph, score=999, funded_entry_eligible=True)


def current(pair='OLD', income=1., gph=10., **changes):
    row = dict(bot_id=pair, pair=pair, status='running', start_ms=0, asof_ms=6*HOUR,
               realized_grid_income_per_hour_6h=income, realized_gph_6h=gph,
               income_coverage_6h_known=True, expected_start_income_per_hour=4.,
               completed_grids=60, grid_net_profit=99999., floating_pnl=-2.,
               mark_floating_pnl=-2., close_fee=.5, net_equity=1198.,
               distance_liquidation_pct=20)
    return dict(row, **changes)


class IncomeReplacementTests(unittest.TestCase):
    def decide(self, rows, choices, **options):
        return decide_portfolio_replacement(rows, choices, dict(OPTIONS, **options))

    def test_selected_funding_adjusted_income_drives_forecast_without_calibration_gate(self):
        row = candidate(income=4, gph=1, eligible=False, calibrated=False)
        estimate = candidate_economics(row, 6, OPTIONS)
        self.assertTrue(estimate['eligible'], estimate)
        self.assertEqual(estimate['expected_gph'], 1)
        self.assertEqual(estimate['projected_grid_income'], 24)
        self.assertEqual(estimate['projected_net_income'], 23)
        self.assertIn('funding', estimate['forecast_basis'])

    def test_waiting_or_unfunded_setups_cannot_enter(self):
        for changes in ({'can_arm': False}, {'funded_economics_eligible': False},
                        {'leverage': 4}, {'used_margin': 800}, {'reserved_margin': 0}):
            with self.subTest(changes=changes):
                self.assertFalse(candidate_economics(candidate(**changes), 6, OPTIONS)['eligible'])
        row = candidate()
        row['funded_entry_eligible'] = False
        self.assertFalse(candidate_economics(row, 6, OPTIONS)['eligible'])

    def test_missing_nonfinite_or_nonpositive_income_is_not_invented_from_grid_floor(self):
        for income in (None, 0, -.1, True, float('nan'), float('inf')):
            with self.subTest(income=income):
                self.assertFalse(candidate_economics(candidate(income=income), 6, OPTIONS)['eligible'])
        for gph in (None, 0, -1, True, float('nan')):
            self.assertFalse(candidate_economics(candidate(gph=gph), 6, OPTIONS)['eligible'])
        self.assertFalse(candidate_economics(candidate(opening_fee_budget=None), 6, OPTIONS)['eligible'])

    def test_lower_gph_challenger_can_replace_when_income_recovers_all_costs(self):
        result = self.decide([current()], [candidate(income=4, gph=1)])
        self.assertEqual(result['action'], 'replace')
        comparison = result['comparison']
        self.assertEqual(comparison['current_projected_income'], 6)
        self.assertEqual(comparison['challenger_projected_income'], 24)
        self.assertEqual(comparison['close_loss_cost'], 2.5)
        self.assertEqual(comparison['net_advantage_usdt'], 14.5)
        self.assertIn('underperforming_income_6h', result['triggers'])

    def test_more_gph_without_better_cost_adjusted_income_is_rejected(self):
        result = self.decide([current(income=4)], [candidate(income=4, gph=100)])
        self.assertEqual(result['action'], 'keep')
        self.assertLess(result['rejected_candidates'][0]['comparison']['net_advantage_usdt'], 0)

    def test_costs_and_capital_still_block_replacement(self):
        expensive = self.decide([current(floating_pnl=-100)], [candidate()])
        self.assertEqual(expensive['action'], 'keep')
        unfunded = self.decide([current()], [candidate()], available_cash=0)
        self.assertEqual(unfunded['action'], 'keep')
        self.assertIn('capital', unfunded['rejected_candidates'][0]['reason'])

    def test_six_hours_and_explicit_known_coverage_are_required(self):
        for changes in ({'asof_ms': 6*HOUR-1}, {'income_coverage_6h_known': False},
                        {'income_coverage_6h_known': None},
                        {'realized_grid_income_per_hour_6h': None},
                        {'realized_gph_6h': None}):
            with self.subTest(changes=changes):
                result = self.decide([current(**changes)], [candidate(income=100)])
                self.assertEqual(result['action'], 'keep')
                self.assertEqual(result['triggers'], [])
                self.assertIsNone(result['comparison'])

    def test_unknown_second_slot_does_not_make_it_zero_or_block_a_measured_incumbent(self):
        rows = [current('MEASURED'), current('WARMUP', asof_ms=HOUR)]
        result = self.decide(rows, [candidate()])
        self.assertEqual(result['action'], 'replace')
        self.assertEqual(result['worst_bot_id'], 'MEASURED')
        self.assertEqual(sum(row['action'] == 'replace' for row in result['verdicts']), 1)

    def test_underperformance_is_an_or_condition_and_never_forces_close(self):
        for income, gph, trigger in ((1., 10., 'underperforming_income_6h'),
                                     (4., 1., 'underperforming_gph_6h')):
            result = self.decide([current(income=income, gph=gph)], [])
            self.assertEqual(result['action'], 'keep')
            self.assertIn(trigger, result['triggers'])
            self.assertIn(trigger, result['verdicts'][0]['triggers'])
        result = self.decide([current(income=2., gph=2.)], [])
        self.assertEqual(result['triggers'], [])

    def test_start_income_baseline_is_not_overwritten_or_replaced_with_current_radar(self):
        row = current(expected_start_income_per_hour=10, expected_gph=999,
                      grid_income_per_hour=.01)
        before = copy.deepcopy(row)
        result = self.decide([row], [])
        self.assertIn('underperforming_income_6h', result['triggers'])
        self.assertEqual(result['expected_start_income_per_hour'], 10)
        self.assertEqual(row, before)

    def test_higher_income_can_switch_without_an_underperformance_trigger(self):
        result = self.decide([current(income=4, gph=3)], [candidate(income=6, gph=1)])
        self.assertEqual(result['action'], 'replace')
        self.assertEqual(result['triggers'], ['better_cost_adjusted_grid_income'])

    def test_funded_entry_ranking_uses_selected_income_and_preserves_two_slots(self):
        choices = [candidate('FAST', income=1, gph=100), candidate('INCOME', income=3, gph=1)]
        result = select_funded_entries(choices, available_cash=1200, parameters=OPTIONS)
        self.assertEqual([row['pair'] for row in result['selected']], ['INCOME'])
        self.assertEqual(result['remaining_cash'], 0)
        duplicate = self.decide([current('INCOME')], choices)
        self.assertNotEqual(duplicate['replacement'] and duplicate['replacement']['pair'], 'INCOME')

    def test_unknown_income_never_suppresses_active_emergency(self):
        result = self.decide([current(asof_ms=HOUR, distance_liquidation_pct=2)], [candidate()])
        self.assertEqual(result['action'], 'emergency')
        self.assertIsNone(result['replacement'])

    def test_known_zero_or_negative_income_is_distinct_from_missing_income(self):
        for income in (0., -1.):
            result = self.decide([current(income=income, gph=0)], [candidate()])
            self.assertEqual(result['action'], 'replace')
            self.assertEqual(result['comparison']['current_projected_income'], income*6)
            self.assertIn('underperforming_income_6h', result['triggers'])
            self.assertIn('underperforming_gph_6h', result['triggers'])

    def test_invalid_elapsed_boolean_coverage_and_nonfinite_actual_income_are_unknown(self):
        for changes in ({'start_ms': True}, {'asof_ms': -1},
                        {'income_coverage_6h_known': 1},
                        {'realized_grid_income_per_hour_6h': float('inf')},
                        {'realized_grid_income_per_hour_6h': True},
                        {'realized_gph_6h': -1}):
            result = self.decide([current(**changes)], [candidate()])
            self.assertEqual(result['action'], 'keep')
            self.assertEqual(result['triggers'], [])

    def test_overflowed_forecast_never_becomes_valid_income(self):
        self.assertFalse(candidate_economics(candidate(income=1e308), 6, OPTIONS)['eligible'])
        result = self.decide([current(income=-1e308)], [candidate()])
        self.assertEqual(result['action'], 'keep')
        self.assertIsNone(result['comparison'])

    def test_default_path_keeps_legacy_candidate_and_higher_gph_requirement(self):
        row = candidate()
        self.assertFalse(candidate_economics(row)['eligible'])
        row['setup'].update(eligible=True, opening_fee_budget=1,
                            preview={'profit_per_grid_min': 10})
        incumbent = current(grid_net_profit=60)
        result = decide_portfolio_replacement([incumbent], [dict(row, score=1)], {'available_cash': 1200})
        self.assertEqual(result['action'], 'keep')
        self.assertIn('grids per hour', result['rejected_candidates'][0]['reason'])
