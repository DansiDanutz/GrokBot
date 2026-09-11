import copy
import json
from pathlib import Path
import unittest

from trader.strategies.grid_order_observations import (
    validate_observations, net_cycle, quantity_prediction,
    quantity_rule_diagnostics, observation_report, accounting_band_checks,
)

FIXTURE = Path(__file__).resolve().parents[2] / 'tests/fixtures/kucoin-order-details-v3.json'


class OrderObservationTests(unittest.TestCase):
    def setUp(self):
        self.document = json.loads(FIXTURE.read_text())

    def test_fixture_preserves_order_quantities_and_sol_split(self):
        validated = validate_observations(self.document)
        self.assertEqual([row['order_quantity_lots'] for row in validated['observations']],
                         [114, 68, 1698, 27, 171, 17])
        sol = validated['observations'][3]
        self.assertEqual(sol['used_margin_current_usdt'], 7000)
        self.assertEqual(sol['reserved_margin_current_usdt'], 3000)
        self.assertEqual(sol['total_margin_current_usdt'], 10000)
        self.assertTrue(all(row['original_used_margin_usdt'] is None
                            for row in validated['observations'] if row['direction'] == 'long'))
        ray = validated['observations'][-1]
        self.assertEqual(ray['original_used_margin_usdt'], 1000)
        self.assertEqual(quantity_prediction(ray, 'high_notional')['margin_basis'],
                         'observed_original_used_margin')
        self.assertFalse(quantity_rule_diagnostics(validated)['calibrated'])

    def test_strict_validator_rejects_unknown_sensitive_fields_and_bad_units(self):
        changes = [('order_quantity_lots', -1), ('contract_multiplier', 0),
                   ('lot_size', 7), ('entry_price', float('nan')),
                   ('leverage', True), ('total_margin_current_usdt', 1),
                   ('account_id', 'not-an-account'), ('cookie', 'not-a-cookie')]
        for key, value in changes:
            with self.subTest(key=key):
                document = copy.deepcopy(self.document)
                document['observations'][0][key] = value
                with self.assertRaises(ValueError):
                    validate_observations(document)
        document = dict(self.document, cookies=[])
        with self.assertRaises(ValueError):
            validate_observations(document)

    def test_strict_validator_rejects_conflicting_counts_duplicate_pairs_and_ticks(self):
        for mutate in [lambda d: d['observations'][-1].update(open_orders=139),
                       lambda d: d['observations'][-1].update(buy_orders=75),
                       lambda d: d['observations'][0].update(entry_price=.0106501),
                       lambda d: d['observations'].append(copy.deepcopy(d['observations'][0]))]:
            document = copy.deepcopy(self.document)
            mutate(document)
            with self.assertRaises(ValueError):
                validate_observations(document)

    def test_all_ray_cycles_match_observed_display_without_changing_quantity(self):
        report = observation_report(self.document)
        self.assertEqual(len(report['cycles']), 3)
        self.assertTrue(all(row['within_display_tolerance'] for row in report['cycles']))
        self.assertAlmostEqual(report['cycles'][0]['computed']['net'], .18484576)
        self.assertAlmostEqual(report['cycles'][1]['computed']['net'], .18510688)
        self.assertAlmostEqual(report['cycles'][2]['computed']['net'], .18432352)
        self.assertTrue(all(row['computed']['quantity_lots'] == 17 for row in report['cycles']))
        self.assertFalse(report['calibrated'])
        self.assertEqual(report['status'], 'blocked')

    def test_cycle_uses_base_quantity_multiplier_once_and_preserves_losses(self):
        result = net_cycle(2, 10, 100, 101)
        self.assertEqual(result['quantity_base'], 20)
        self.assertEqual(result['gross'], 20)
        self.assertAlmostEqual(result['fees'], 2.412)
        self.assertAlmostEqual(result['net'], 17.588)
        self.assertLess(net_cycle(2, 10, 101, 100)['net'], 0)
        for values in [(0, 1, 1, 2), (1, 0, 1, 2), (1, 1, float('inf'), 2)]:
            with self.assertRaises(ValueError):
                net_cycle(*values)

    def test_fixed_unfitted_hypotheses_expose_long_failures_even_when_ray_matches(self):
        report = quantity_rule_diagnostics(self.document)
        rules = {rule['name']: rule for rule in report['rules']}
        self.assertEqual([row['predicted_lots'] for row in rules['entry_notional']['observations']],
                         [134, 98, 5045, 32, 197, 22])
        self.assertEqual([row['predicted_lots'] for row in rules['high_notional']['observations']],
                         [95, 68, 2777, 23, 152, 17])
        self.assertEqual([row['predicted_lots'] for row in rules['high_notional_with_fees']['observations']],
                         [94, 68, 2757, 23, 151, 17])
        self.assertTrue(rules['high_notional']['held_out_quantity_match'])
        self.assertFalse(rules['high_notional']['all_long_quantities_match'])
        self.assertFalse(report['calibrated'])
        self.assertIn('original margin', ' '.join(report['blocked_reasons']))

    def test_predicted_quantity_cannot_use_observed_quantity_or_cycle_profit_targets(self):
        original = self.document['observations'][-1]
        changed = dict(original, order_quantity_lots=999,
                       cycles=[dict(original['cycles'][0], observed_net_usdt=999)])
        for rule in ('entry_notional', 'high_notional', 'high_notional_with_fees'):
            self.assertEqual(quantity_prediction(original, rule), quantity_prediction(changed, rule))

    def test_quantity_prediction_reacts_only_to_independent_allocation_inputs(self):
        row = self.document['observations'][0]
        original = quantity_prediction(row, 'entry_notional')
        changed = quantity_prediction(dict(row, original_used_margin_usdt=1000), 'entry_notional')
        self.assertLess(changed['predicted_lots'], original['predicted_lots'])
        self.assertEqual(changed['margin_basis'], 'observed_original_used_margin')
        reserve = quantity_prediction(dict(row, reserved_margin_current_usdt=99999), 'entry_notional')
        self.assertEqual(reserve, original)

    def test_observed_quantities_place_all_four_prior_long_means_inside_bands(self):
        old_path = FIXTURE.parent/'kucoin-grid-bots-20260911.json'
        old = json.loads(old_path.read_text())['running_bots']
        checks = accounting_band_checks(self.document, old)
        compared = [row for row in checks if row['reference_mean_net_usdt'] is not None]
        self.assertEqual(len(compared), 4)
        self.assertTrue(all(row['reference_mean_inside_band'] for row in compared))
        hemi = compared[0]
        self.assertAlmostEqual(hemi['minimum_net_usdt'], .84505008)
        self.assertAlmostEqual(hemi['maximum_net_usdt'], .93189072)
        self.assertIn('inferred', hemi['step_basis'])
        ray = checks[-1]
        self.assertEqual(ray['step'], .0128)
        self.assertEqual(ray['step_basis'], 'observed order-history interval')
        self.assertIsNone(checks[4]['reference_mean_net_usdt'])

    def test_band_predictions_do_not_change_when_prior_profit_targets_change(self):
        old_path = FIXTURE.parent/'kucoin-grid-bots-20260911.json'
        old = json.loads(old_path.read_text())['running_bots']
        first = accounting_band_checks(self.document, old)
        changed = [dict(row, grid_profit=9999999) for row in old]
        second = accounting_band_checks(self.document, changed)
        for before, after in zip(first, second):
            self.assertEqual(before['minimum_net_usdt'], after['minimum_net_usdt'])
            self.assertEqual(before['maximum_net_usdt'], after['maximum_net_usdt'])
        self.assertFalse(second[0]['reference_mean_inside_band'])

    def test_known_original_margin_is_not_mislabeled_but_allocation_stays_blocked(self):
        document = copy.deepcopy(self.document)
        for row in document['observations']:
            row['original_used_margin_usdt'] = row['used_margin_current_usdt']
        report = quantity_rule_diagnostics(document)
        self.assertTrue(report['original_margin_known'])
        self.assertFalse(report['calibrated'])
        self.assertFalse(any('original margin' in reason for reason in report['blocked_reasons']))
