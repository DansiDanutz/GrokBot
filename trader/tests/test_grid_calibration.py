import json
from pathlib import Path
import unittest

from trader.strategies.grid_calibration import calibration_report, fixture_config
from trader.strategies.kucoin_grid import preview

FIXTURE = Path(__file__).resolve().parents[2] / 'tests/fixtures/kucoin-grid-bots-20260911.json'


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        self.bots = json.loads(FIXTURE.read_text())['running_bots']

    def test_report_honestly_exposes_each_tolerance_failure(self):
        report = calibration_report(self.bots)
        self.assertEqual(len(report['bots']), 4)
        self.assertEqual(report['profit_tolerance'], .15)
        self.assertEqual(report['liquidation_tolerance'], .10)
        for observed, result in zip(self.bots, report['bots']):
            self.assertEqual(result['observed_profit_per_grid'],
                             observed['grid_profit'] / observed['arbitrage_total'])
            self.assertEqual(result['profit_pass'], result['profit_relative_error'] <= .15)
            self.assertEqual(result['liquidation_pass'], result['liquidation_relative_error'] <= .10)
        self.assertEqual(report['calibrated'], all(row['profit_pass'] and row['liquidation_pass']
                                                  for row in report['bots']))
        self.assertFalse(report['calibrated'])
        self.assertTrue(report['limitations'])

    def test_observed_profit_or_liquidation_never_changes_computation(self):
        first = calibration_report(self.bots)['bots']
        changed = [dict(bot, grid_profit=bot['grid_profit']*3,
                        est_liq_price=bot['est_liq_price']*2) for bot in self.bots]
        second = calibration_report(changed)['bots']
        for left, right in zip(first, second):
            self.assertEqual(left['computed_profit_per_grid'], right['computed_profit_per_grid'])
            self.assertEqual(left['computed_liquidation_price'], right['computed_liquidation_price'])

    def test_sol_reserve_is_not_leveraged(self):
        sol = fixture_config(self.bots[-1])
        self.assertEqual(sol.investment, 7000)
        self.assertEqual(sol.reserved_margin, 3000)
        self.assertEqual(sol.total_margin, 10000)
        self.assertEqual(sol.entry_price, 108.252)
        self.assertEqual(preview(sol)['order_count'], 99)

    def test_reconstruction_keeps_both_margin_hypotheses_and_raw_failure(self):
        from trader.strategies.grid_calibration import state_reconstruction_diagnostics
        diagnostic = state_reconstruction_diagnostics(self.bots)
        self.assertFalse(diagnostic['predictive_calibration'])
        self.assertEqual(len(diagnostic['bots']), 4)
        for row in diagnostic['bots']:
            hypotheses = row['margin_hypotheses']
            self.assertEqual([item['name'] for item in hypotheses],
                             ['margin_used_reserve_additional', 'margin_total_includes_reserve'])
            self.assertEqual(hypotheses[0]['quantity'], hypotheses[1]['quantity'])
        sol = diagnostic['bots'][-1]
        self.assertAlmostEqual(sol['reconstructed_quantity'], 2.71346, places=5)
        self.assertEqual([h['total_margin'] for h in sol['margin_hypotheses']], [13000, 10000])
        self.assertLess(sol['margin_hypotheses'][0]['liquidation_price'],
                        sol['margin_hypotheses'][1]['liquidation_price'])
        report = calibration_report(self.bots, include_reconstruction=True)
        self.assertEqual(report['state_reconstruction_diagnostics'], diagnostic)
        self.assertFalse(report['calibrated'])
        self.assertEqual(report['status'], 'blocked')

    def test_reconstruction_cannot_leak_profit_arbitrage_or_liquidation_targets(self):
        from trader.strategies.grid_calibration import state_reconstruction_diagnostics
        baseline = state_reconstruction_diagnostics(self.bots)
        changed = [dict(bot, grid_profit=-999, arbitrage_total=999999,
                        arbitrage_24h=999999, est_liq_price=999999) for bot in self.bots]
        self.assertEqual(baseline, state_reconstruction_diagnostics(changed))

    def test_reconstruction_uses_independent_unrealized_and_order_counts(self):
        from trader.strategies.grid_calibration import state_reconstruction_diagnostics
        baseline = state_reconstruction_diagnostics(self.bots)['bots'][0]
        doubled = dict(self.bots[0], unrealized_pnl=self.bots[0]['unrealized_pnl'] * 2)
        changed = state_reconstruction_diagnostics([doubled])['bots'][0]
        self.assertAlmostEqual(changed['reconstructed_quantity'],
                               baseline['reconstructed_quantity'] * 2)

    def test_zero_unrealized_at_exact_entry_does_not_identify_quantity(self):
        from trader.strategies.grid_calibration import state_reconstruction_diagnostics
        bot = dict(self.bots[0], grids_buy=0, price_at_capture=self.bots[0]['entry_price'],
                   unrealized_pnl=0)
        result = state_reconstruction_diagnostics([bot])['bots'][0]
        self.assertEqual(result['status'], 'underidentified')
        self.assertIsNone(result['reconstructed_quantity'])
        self.assertEqual(result['margin_hypotheses'], [])

    def test_user_margin_formula_is_tested_honestly_against_four_longs(self):
        for bot, row in zip(self.bots, calibration_report(self.bots)['bots']):
            expected = ((bot['margin_usdt'] - bot['reserved_margin']) / (bot['grids_buy'] + bot['grids_sell'])
                        * (((bot['range_high'] - bot['range_low'])
                            / (bot['grids_buy'] + bot['grids_sell']) / bot['entry_price']
                            - .0012) * bot['leverage']))
            self.assertAlmostEqual(row['margin_formula_profit_per_grid'], expected)
            error = abs(expected - row['observed_profit_per_grid']) / row['observed_profit_per_grid']
            self.assertAlmostEqual(row['margin_formula_relative_error'], error)
            self.assertEqual(row['margin_formula_pass'], error <= .15)

    def test_ray_creation_separates_predicted_quantity_from_observed_lots(self):
        from trader.strategies.grid_calibration import neutral_creation_calibration
        ray = json.loads(FIXTURE.read_text())['neutral_bot_creation_example']
        metadata = {'multiplier': 1, 'lotSize': 1, 'tickSize': .0001,
                    'maintainMargin': .015, 'source': 'historical contract metadata, 2026-09-11'}
        report = neutral_creation_calibration(ray, metadata)
        self.assertTrue(report['percentage_pass'])
        self.assertEqual(report['predicted_quantity'], 22)
        self.assertEqual(report['observed_quantity'], 17)
        self.assertFalse(report['quantity_pass'])
        self.assertFalse(report['calibrated'])
        self.assertEqual(report['observed_quantity_snapshot']['long_quantity'], 544)
        self.assertEqual(report['observed_quantity_snapshot']['short_quantity'], 629)
        self.assertTrue(report['observed_quantity_snapshot']['order_split_pass'])
        self.assertEqual(report['observed_quantity_snapshot']['computed_unrealized_pnl'], 0)
        self.assertFalse(report['unrealized_reconstructed'])
        self.assertEqual(report['percentage_tolerance_points'], .3)
        self.assertEqual(report['liquidation_tolerance'], .10)
        changed = dict(ray, est_long_liq_price=.01, est_short_liq_price=20,
                       profit_per_grid_pct_shown='10% ~ 20%')
        other = neutral_creation_calibration(changed, metadata)
        self.assertEqual(report['predicted_quantity'], other['predicted_quantity'])
        self.assertEqual(report['observed_quantity'], other['observed_quantity'])
