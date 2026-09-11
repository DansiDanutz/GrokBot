from dataclasses import replace
import unittest
from unittest.mock import patch

from trader.research.grid_operator import operator_output
from trader.research.grid_runner import new_bot
from trader.tests.test_grid_runner import candidate, Data, START


class OperatorTests(unittest.TestCase):
    def scan(self, *args, **kwargs):
        c = candidate('BBB', 10000)
        return dict(candidates=[c], eligible_candidates=[c], excluded=[], coverage={})

    @patch('trader.research.grid_operator.scan')
    def test_form_values_and_replace_signal_with_both_rates(self, scan):
        scan.side_effect = self.scan
        state = replace(new_bot(candidate(), START, 1000), timestamp_ms=START+3600000)
        result = operator_output(Data(), START+3600000, state, START)
        self.assertEqual(result['form']['pair'], 'BBB')
        self.assertEqual(result['form']['direction'], 'neutral')
        self.assertEqual(result['form']['leverage'], 5)
        self.assertEqual(result['signal'], 'replace now with BBB')
        self.assertIn('realized_grids_per_hour', result['replacement'])
        self.assertIn('expected_grids_per_hour', result['replacement'])
        self.assertFalse(result['execution_enabled'])

    @patch('trader.research.grid_operator.scan')
    def test_no_eligible_coin_never_invents_form(self, scan):
        scan.return_value = dict(candidates=[], eligible_candidates=[], excluded=[], coverage={})
        result = operator_output(Data(), START)
        self.assertIsNone(result['form'])
        self.assertEqual(result['signal'], 'no eligible coin')

    @patch('trader.research.grid_operator.scan')
    def test_stale_bot_state_cannot_emit_replacement_signal(self, scan):
        scan.side_effect = self.scan
        state = new_bot(candidate(), START, 1000)
        with self.assertRaisesRegex(ValueError, 'stale'):
            operator_output(Data(), START+3600000, state, START)
