"""Acceptance checks for a supplied partial history; no fabricated RED-first claim."""
from collections import Counter
from decimal import Decimal, ROUND_DOWN
import hashlib
import json
from pathlib import Path
import unittest

from trader.strategies.grid_order_observations import net_cycle

FIXTURE = Path(__file__).resolve().parents[2] / 'tests/fixtures/kucoin-ray-history-v3.json'
ROWS_SHA256 = 'e92672fbc02244023c027ee3596f5a59ee7ce1f985eceb468adf37b15bfa8f7c'


class RayHistoryEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.document = json.loads(FIXTURE.read_text())
        self.rows = self.document['rows']

    def test_all_100_rows_and_display_order_are_preserved(self):
        self.assertEqual(len(self.rows), 100)
        encoded = json.dumps(self.rows, sort_keys=True, separators=(',', ':')).encode()
        self.assertEqual(hashlib.sha256(encoded).hexdigest(), ROWS_SHA256)
        self.assertEqual(self.document['rows_sha256'], ROWS_SHA256)
        self.assertEqual(self.rows[0]['execution_time_display'], '09/11/2026, 12:57:30')
        self.assertEqual(self.rows[-1]['execution_time_display'], '09/11/2026, 11:27:17')
        times = [row['execution_time_display'] for row in self.rows]
        self.assertEqual(times, sorted(times, reverse=True))

    def test_duplicate_profit_rows_are_retained_without_unique_cycle_claim(self):
        numeric = [row for row in self.rows if row['grid_profit_usdt'] is not None]
        self.assertEqual(len(numeric), 25)
        displayed_sum = sum((Decimal(row['grid_profit_usdt']) for row in numeric), Decimal(0))
        self.assertEqual(displayed_sum, Decimal('4.6195'))
        self.assertEqual(self.document['summary']['displayed_numeric_profit_sum_usdt'], '4.6195')
        duplicates = Counter(tuple(row.items()) for row in numeric)
        self.assertEqual(Counter(duplicates.values()), {2: 12, 1: 1})
        single = next(row for row in numeric if duplicates[tuple(row.items())] == 1)
        self.assertEqual(single['execution_time_display'], '09/11/2026, 12:50:10')
        self.assertEqual(single['grid_profit_usdt'], '0.1853')
        self.assertFalse(self.document['deduplicated'])
        self.assertFalse(self.document['paired_execution_links_available'])

    def test_every_quantity_is_17_lots_and_amount_matches_display_precision(self):
        multiplier = Decimal(str(self.document['independent_accounting_context']['contract_multiplier_base_per_lot']))
        self.assertEqual(multiplier, 1)
        for row in self.rows:
            with self.subTest(time=row['execution_time_display'], price=row['avg_price_usdt']):
                self.assertEqual(row['quantity_lots'], 17)
                price, amount = Decimal(row['avg_price_usdt']), Decimal(row['executed_amount_usdt'])
                expected = Decimal(row['quantity_lots'])*multiplier*price
                half_display_unit = Decimal(1).scaleb(amount.as_tuple().exponent)/2
                self.assertLessEqual(abs(amount-expected), half_display_unit)

    def test_profit_values_are_consistent_with_inferred_adjacent_prices_not_pair_links(self):
        context = self.document['independent_accounting_context']
        self.assertIn('infer', context['comparison'])
        self.assertIn('no explicit pair links', context['interval_basis'])
        step, quantum = Decimal(context['interval']), Decimal('0.0001')
        self.assertEqual(step, Decimal('0.0128'))
        numeric = [row for row in self.rows if row['grid_profit_usdt'] is not None]
        self.assertEqual(Counter(row['grid_profit_usdt'] for row in numeric),
                         {'0.1843': 4, '0.1845': 6, '0.1848': 6, '0.1851': 6, '0.1853': 3})
        for row in numeric:
            displayed_price = Decimal(row['avg_price_usdt'])
            inferred_lower_price = displayed_price-step
            computed = net_cycle(row['quantity_lots'], context['contract_multiplier_base_per_lot'],
                                 inferred_lower_price, displayed_price, Decimal(context['fee_rate_per_fill']))
            modeled_net, displayed_net = Decimal(str(computed['net'])), Decimal(row['grid_profit_usdt'])
            self.assertGreaterEqual(modeled_net-displayed_net, 0)
            self.assertLess(modeled_net-displayed_net, quantum)
            self.assertEqual(modeled_net.quantize(quantum, rounding=ROUND_DOWN), displayed_net)
            self.assertIsNone(row['side'])

    def test_unknown_sides_pending_statuses_and_partial_footer_remain_explicit(self):
        nonnumeric = [row for row in self.rows if row['grid_profit_usdt'] is None]
        self.assertEqual(Counter(row['grid_profit_display'] for row in nonnumeric),
                         {'--': 37, 'To be closed': 38})
        self.assertTrue(all(row['side'] is None for row in self.rows))
        self.assertFalse(self.document['complete_history'])
        self.assertFalse(self.document['execution_ids_available'])
        self.assertFalse(self.document['sides_available'])
        self.assertEqual(self.document['source_footer'], 'Only shows the last 100 results')
        self.assertIn('timezone', self.document['execution_time_basis'])
        self.assertTrue(any('not a lifetime' in text for text in self.document['limitations']))

    def test_sanitized_fixture_contains_no_execution_identifiers_or_private_paths(self):
        forbidden = {'account_id', 'bot_id', 'order_id', 'execution_id', 'cookies', 'cookie', 'source_path'}
        self.assertFalse(forbidden.intersection(self.document))
        self.assertTrue(all(not forbidden.intersection(row) for row in self.rows))
        text = FIXTURE.read_text()
        self.assertNotIn('/Users/', text)
        self.assertNotIn('.codex/attachments', text)
