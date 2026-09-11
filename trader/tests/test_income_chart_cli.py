"""Offline CLI strategy wiring; all file inputs are temporary synthetic fixtures."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from trader.research.kucoin_cli import main, load_running
from trader.research.chart_snapshot import ChartSnapshot
from trader.research.kucoin_snapshot import Snapshot, HistoricalSnapshot
from trader.tests.test_kucoin_snapshot import make_database
from trader.tests.test_kucoin_operator import running

HOUR = 3600000


class IncomeChartCliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.snapshot = make_database(self.root)
        self.running = self.root/'running.json'
        self.running.write_text(json.dumps({'schema_version': 1, 'bots': []}))
        self.output = self.root/'offline-artifacts'

    def tearDown(self):
        self.temporary.cleanup()

    def args(self, *extra):
        return ['operator', '--snapshot', str(self.snapshot), '--running', str(self.running),
                '--asof', str(HOUR), '--output', str(self.output), *extra]

    def execute(self, *extra):
        stdout, stderr = io.StringIO(), io.StringIO()
        report = dict(asof_ms=HOUR, radar={'radar': []}, running=[],
                      five_cells={'1d': {'regime': 'unknown'}}, live_use={'actionable': False})
        with patch('trader.research.kucoin_cli.operator_report', return_value=report) as operator, patch(
                'trader.research.kucoin_cli.persist_report', return_value=str(self.output)), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(self.args(*extra))
        return code, operator, stdout.getvalue(), stderr.getvalue()

    def test_strategy_bias_and_regime_parameters_reach_wrapper_and_operator(self):
        code, operator, stdout, _ = self.execute('--strategy', 'income_chart_v3',
                                               '--bias-mode', '4h-only', '--regime-gate', 'off')
        self.assertEqual(code, 0)
        wrapped = operator.call_args.args[0]
        self.assertIsInstance(wrapped, ChartSnapshot)
        self.assertIsInstance(wrapped.source, Snapshot)
        expected = {'strategy': 'income_chart_v3', 'bias_mode': '4h-only', 'regime_gate': False}
        for key, value in expected.items():
            self.assertEqual(wrapped.parameters[key], value)
            self.assertEqual(operator.call_args.kwargs['parameters'][key], value)
        self.assertEqual(wrapped._chart_options['bias_mode'], '4h-only')
        self.assertFalse(wrapped._regime_enabled)
        self.assertIn('five_cells', json.loads(stdout)['report'])
        self.assertIsNone(wrapped._terms('XBTUSDTM', HOUR)[0])

    def test_new_default_bias_and_regime_are_explicit_and_candle_only_is_opt_in(self):
        code, operator, _, _ = self.execute('--strategy', 'income_chart_v3', '--candle-only')
        self.assertEqual(code, 0)
        wrapped = operator.call_args.args[0]
        self.assertIsInstance(wrapped.source, HistoricalSnapshot)
        parameters = operator.call_args.kwargs['parameters']
        self.assertEqual(parameters['bias_mode'], '1d+4h')
        self.assertIs(parameters['regime_gate'], True)
        self.assertIs(parameters['historical_candle_only'], True)

    def test_legacy_default_preserves_snapshot_and_three_argument_report_call(self):
        code, operator, _, _ = self.execute()
        self.assertEqual(code, 0)
        self.assertIsInstance(operator.call_args.args[0], Snapshot)
        self.assertEqual(operator.call_args.kwargs, {})
        code, operator, _, _ = self.execute('--candle-only')
        self.assertEqual(code, 0)
        self.assertIsInstance(operator.call_args.args[0], HistoricalSnapshot)

    def test_income_running_validation_receives_strategy_before_report(self):
        self.running.write_text(json.dumps(running(expected_start_income_per_hour=.5)))
        with self.assertRaises(ValueError):
            load_running(self.running, HOUR)
        loaded = load_running(self.running, HOUR, parameters={'strategy': 'income_chart_v3'})
        self.assertEqual(loaded['bots'][0]['expected_start_income_per_hour'], .5)
        code, operator, _, stderr = self.execute('--strategy', 'income_chart_v3')
        self.assertEqual((code, stderr), (0, ''))
        self.assertEqual(operator.call_args.args[2], loaded)

    def test_public_funding_lists_and_checkpoint_are_loaded_into_chart_wrapper(self):
        rows = [{'symbol': 'XBTUSDTM', 'timestamp_ms': 0, 'rate': .0001}]
        checkpoint = dict(symbol='XBTUSDTM', start_ms=0, end_ms=HOUR, next_to_ms=-1,
                          records=rows, complete=True, exhausted=False, pages=1, coverage_verified=False)
        for value in (rows, checkpoint):
            with self.subTest(checkpoint=isinstance(value, dict)):
                path = self.root/'funding.json'
                path.write_text(json.dumps({'XBTUSDTM': value}))
                code, operator, _, _ = self.execute('--strategy', 'income_chart_v3', '--funding-history', str(path))
                self.assertEqual(code, 0)
                self.assertEqual(operator.call_args.args[0]._funding['XBTUSDTM'], rows)

    def test_funding_history_rejects_oversize_links_noncanonical_and_duplicate_fields(self):
        invalid = ['[]', '{"XBTUSDTM":null}', '{"XBTUSDTM":[],"XBTUSDTM":[]}',
                   '{"XBTUSDTM":[{"symbol":"XBTUSDTM","timestamp_ms":0,"rate":NaN}]}',
                   '{"XBTUSDTM":[{"symbol":"XBTUSDTM","timestamp_ms":0,"rate":0,"credentials":{}}]}']
        for index, content in enumerate(invalid):
            path = self.root/('bad'+str(index)+'.json')
            path.write_text(content)
            code, operator, _, stderr = self.execute('--strategy', 'income_chart_v3', '--funding-history', str(path))
            self.assertEqual(code, 2)
            self.assertIn('error', json.loads(stderr))
            operator.assert_not_called()
        oversized = self.root/'large.json'
        with oversized.open('wb') as stream:
            stream.truncate(32*1024*1024+1)
        link = self.root/'link.json'
        link.symlink_to(oversized)
        for path in (oversized, link, Path.home()/'.codex'/'forbidden.json'):
            code, operator, _, _ = self.execute('--strategy', 'income_chart_v3', '--funding-history', str(path))
            self.assertEqual(code, 2)
            operator.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_legacy_cannot_silently_ignore_chart_funding_options(self):
        path = self.root/'funding.json'
        path.write_text('{}')
        for options in (('--funding-history', str(path)), ('--bias-mode', '4h-only'), ('--regime-gate', 'off')):
            code, operator, _, _ = self.execute(*options)
            self.assertEqual(code, 2)
            operator.assert_not_called()
