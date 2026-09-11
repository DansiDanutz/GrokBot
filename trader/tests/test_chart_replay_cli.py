"""Only temporary detached fixtures and pure mocked replay chunks are used."""
import contextlib
import gzip
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from trader.research.chart_replay_cli import main
from trader.research.chart_snapshot import ChartSnapshot
from trader.research.kucoin_snapshot import HistoricalSnapshot
from trader.tests.test_kucoin_snapshot import make_database

HOUR = 3600000
SOURCE = 'a'*40


class ChartReplayCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.snapshot = make_database(self.root)
        self.funding = self.root/'funding.json'
        self.funding.write_text('{"XBTUSDTM":[]}')
        self.registration = self.root/'registration.json'
        self.document = dict(id='grid-kucoin-v3-income-chart', random_seed=7,
            fixed_parameters=dict(strategy='income_chart_v3',historical_candle_only=True,
                                  minimum_grid_net_usdt=0,range_exit_stop_pct=0,adaptive_range_stops=False),
            sweep={'bias_mode':['4h-only','1d+4h'],'regime_gate':[False,True]})
        self.registration.write_text(json.dumps(self.document))
        self.output = self.root/'results'

    def tearDown(self):
        self.temp.cleanup()

    def args(self, **changes):
        values = dict(snapshot=self.snapshot, funding_history=self.funding, registration=self.registration,
                      start=0, end=12*HOUR, bias_mode='1d+4h', regime_gate='on', mode='system', output=self.output)
        values.update(changes)
        return [item for key,value in values.items() for item in ('--'+key.replace('_','-'),str(value))]

    def invoke(self, runner, source_revision=SOURCE, **changes):
        out, err = io.StringIO(), io.StringIO()
        with patch('trader.research.chart_replay_cli._registered', return_value=self.document), patch(
                'trader.research.chart_replay_cli._source_revision', return_value=source_revision), patch(
                'trader.research.chart_replay_cli.run_chunk', side_effect=runner) as called, contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(self.args(**changes))
        return code, called, json.loads(out.getvalue() or err.getvalue())

    def runner(self, snapshot, start, end, **options):
        prior = options['checkpoint']
        cursor = min(end, (prior['cursor_ms'] if prior else start)+options['max_hours']*HOUR)
        complete = cursor == end
        packet = dict(cursor_ms=cursor, complete=complete, marker='mock immutable replay state')
        report = dict(status='modeled_with_assumptions', completed_hours=(cursor-start)/HOUR,
            metrics={'net':-2,'completed_positive_net':4}, verified_metrics={'net':None},
            partial_metrics={'net':-2}, funded_cycle_diagnostics={'unknown_funding_cycles':1},
            coverage={'complete':False}, bots=[], ledger=[{'kind':'grid'}],
            start_ms=start,end_ms=end) if complete else None
        return dict(complete=complete, checkpoint=packet, result=report)

    def test_default_runs_one_six_hour_chunk_then_resumes_and_finishes_idempotently(self):
        code, calls, progress = self.invoke(self.runner)
        self.assertEqual(code, 0)
        self.assertEqual(calls.call_count, 1)
        self.assertFalse(progress['complete'])
        self.assertEqual(progress['cursor_ms'], 6*HOUR)
        self.assertTrue((self.output/'checkpoint.json.gz').is_file())
        self.assertFalse((self.output/'result.json.gz').exists())
        code, calls, progress = self.invoke(self.runner)
        self.assertEqual(code, 0)
        self.assertTrue(progress['complete'])
        self.assertEqual(calls.call_args.kwargs['checkpoint']['cursor_ms'], 6*HOUR)
        with gzip.open(self.output/'result.json.gz', 'rt') as stream:
            report = json.load(stream)
        with gzip.open(self.output/'summary.json.gz', 'rt') as stream:
            summary = json.load(stream)
        self.assertEqual(report['metrics']['net'], -2)
        self.assertIsNone(summary['verified_metrics']['net'])
        self.assertEqual(summary['ledger_rows'], 1)
        self.assertEqual(summary['partial_metrics'], {'net':-2})
        self.assertEqual(summary['funded_cycle_diagnostics'], {'unknown_funding_cycles':1})
        self.assertEqual(report['provenance']['source_revision'], SOURCE)
        before = (self.output/'checkpoint.json.gz').read_bytes()
        code, calls, progress = self.invoke(self.runner)
        self.assertEqual(code, 0)
        calls.assert_not_called()
        self.assertEqual((self.output/'checkpoint.json.gz').read_bytes(), before)

    def test_registered_parameters_funding_identity_and_random_mode_are_forwarded(self):
        code, calls, progress = self.invoke(self.runner, bias_mode='4h-only', regime_gate='off',
                                            mode='random_radar_identical_rules', chunk_hours=2, max_chunks=2)
        self.assertEqual(code, 0)
        self.assertEqual(calls.call_count, 2)
        snapshot = calls.call_args.args[0]
        self.assertIsInstance(snapshot, ChartSnapshot)
        self.assertIsInstance(snapshot.source, HistoricalSnapshot)
        options = calls.call_args.kwargs
        self.assertEqual(options['parameters']['strategy'], 'income_chart_v3')
        self.assertEqual(options['parameters']['bias_mode'], '4h-only')
        self.assertFalse(options['parameters']['regime_gate'])
        self.assertEqual(options['seed'], 7)
        self.assertEqual(options['mode'], 'random_radar_identical_rules')
        self.assertEqual(options['identity']['source'], SOURCE)
        self.assertEqual(len(options['identity']['data']), 64)
        self.assertEqual(progress['cursor_ms'], 4*HOUR)

    def test_changed_input_or_variant_cannot_resume_before_runner(self):
        self.invoke(self.runner)
        original_funding = self.funding.read_bytes()
        for changes in ({'bias_mode':'4h-only'}, {'regime_gate':'off'}, {'end':13*HOUR}, {'mode':'random_radar_identical_rules'}):
            code, calls, _ = self.invoke(self.runner, **changes)
            self.assertEqual(code, 2)
            calls.assert_not_called()
        self.funding.write_bytes(original_funding+b' ')
        code, calls, error = self.invoke(self.runner)
        self.assertEqual(code, 2)
        calls.assert_not_called()
        self.assertIn('binding', error['error'])
        self.funding.write_bytes(original_funding)
        self.registration.write_text(self.registration.read_text()+' ')
        code, calls, _ = self.invoke(self.runner)
        self.assertEqual(code, 2)
        calls.assert_not_called()

    def test_invalid_window_registration_paths_and_foreign_output_create_nothing(self):
        for changes in ({'start':1}, {'start':12*HOUR}, {'chunk_hours':0}, {'max_chunks':0},
                        {'snapshot':self.root/'missing'}, {'output':Path.home()/'.codex'/'forbidden'}):
            code, calls, _ = self.invoke(self.runner, **changes)
            self.assertEqual(code, 2)
            calls.assert_not_called()
            self.assertFalse(self.output.exists())
        self.document['sweep']['bias_mode'] = ['4h-only']
        code, calls, _ = self.invoke(self.runner)
        self.assertEqual(code, 2)
        self.assertFalse(self.output.exists())
        self.document['sweep']['bias_mode'].append('1d+4h')
        self.output.mkdir()
        (self.output/'unrelated.txt').write_text('keep')
        code, calls, _ = self.invoke(self.runner)
        self.assertEqual(code, 2)
        self.assertEqual((self.output/'unrelated.txt').read_text(),'keep')
        self.assertEqual(len(list(self.output.iterdir())), 1)

    def test_interrupted_chunk_keeps_previous_checkpoint(self):
        self.invoke(self.runner)
        before = (self.output/'checkpoint.json.gz').read_bytes()
        def interrupted(*args, **kwargs):
            raise ValueError('synthetic interrupted chunk')
        code, calls, _ = self.invoke(interrupted)
        self.assertEqual(code, 2)
        self.assertEqual((self.output/'checkpoint.json.gz').read_bytes(), before)
        self.assertFalse((self.output/'result.json.gz').exists())

    def test_uncommitted_registration_rejected_without_output(self):
        out = io.StringIO()
        with patch('trader.research.chart_replay_cli._source_revision', return_value=SOURCE), contextlib.redirect_stderr(out):
            code = main(self.args())
        self.assertEqual(code, 2)
        self.assertFalse(self.output.exists())
        self.assertIn('committed', out.getvalue())

    def test_source_and_snapshot_bytes_are_part_of_resume_identity(self):
        self.invoke(self.runner)
        code, calls, error = self.invoke(self.runner, source_revision='b'*40)
        self.assertEqual(code, 2)
        calls.assert_not_called()
        self.assertIn('binding', error['error'])
        with self.snapshot.open('ab') as stream:
            stream.write(b'changed detached bytes')
        code, calls, error = self.invoke(self.runner)
        self.assertEqual(code, 2)
        calls.assert_not_called()
        self.assertIn('binding', error['error'])

    def test_completed_result_damage_cannot_be_reported_as_success(self):
        self.invoke(self.runner, max_chunks=2)
        with (self.output/'result.json.gz').open('ab') as stream:
            stream.write(b'damaged artifact')
        code, calls, error = self.invoke(self.runner)
        self.assertEqual(code, 2)
        calls.assert_not_called()
        self.assertIn('artifact', error['error'])

    def test_checkpoint_symlink_is_rejected_without_touching_its_target(self):
        self.invoke(self.runner)
        path = self.output/'checkpoint.json.gz'
        target = self.root/'separate.gz'
        path.rename(target)
        before = target.read_bytes()
        path.symlink_to(target)
        code, calls, error = self.invoke(self.runner)
        self.assertEqual(code, 2)
        calls.assert_not_called()
        self.assertIn('symlink', error['error'])
        self.assertEqual(target.read_bytes(), before)

    def test_actual_chunk_serializes_unavailable_temporary_snapshot_without_verified_claims(self):
        from trader.research.kucoin_replay import run_chunk
        code, calls, progress = self.invoke(run_chunk)
        self.assertEqual(code, 0)
        self.assertTrue(progress['complete'])
        self.assertFalse(progress['summary']['coverage']['complete'])
        self.assertTrue(all(value is None for value in progress['summary']['verified_metrics'].values()))
        self.assertEqual(progress['summary']['provenance']['source_revision'], SOURCE)

    def test_incomplete_checkpoint_cannot_drop_resume_state_even_with_valid_envelope_digest(self):
        from trader.research.chart_replay_cli import _digest
        self.invoke(self.runner)
        path = self.output/'checkpoint.json.gz'
        with gzip.open(path, 'rt') as stream:
            packet = json.load(stream)
        packet['replay_checkpoint'] = None
        packet['sha256'] = _digest({key:value for key,value in packet.items() if key != 'sha256'})
        with gzip.open(path, 'wt') as stream:
            json.dump(packet, stream)
        code, calls, error = self.invoke(self.runner)
        self.assertEqual(code, 2)
        calls.assert_not_called()
        self.assertIn('checkpoint', error['error'])

    def test_registry_bytes_bind_resume_even_if_source_revision_is_unchanged(self):
        from trader.research import kucoin_cli
        path = self.root/'membership.json'
        path.write_bytes(kucoin_cli.MEMBERSHIP_PATH.read_bytes())
        with patch('trader.research.kucoin_cli.MEMBERSHIP_PATH', path):
            code, calls, _ = self.invoke(self.runner)
            self.assertEqual(code, 0)
            with gzip.open(self.output/'checkpoint.json.gz', 'rt') as stream:
                packet = json.load(stream)
            self.assertIn('membership_sha256', packet['binding'])
            changed = json.loads(path.read_text())
            changed['basis'] += ' revised'
            path.write_text(json.dumps(changed))
            code, calls, error = self.invoke(self.runner)
        self.assertEqual(code, 2)
        calls.assert_not_called()
        self.assertIn('binding', error['error'])

    def test_dirty_registry_or_preregistration_cannot_be_labeled_as_frozen_source(self):
        from types import SimpleNamespace
        from trader.research.chart_replay_cli import _source_revision
        for scope in ('config', 'research/preregistration'):
            def git_result(command, **kwargs):
                if 'rev-parse' in command:
                    return SimpleNamespace(stdout=SOURCE, returncode=0)
                if 'diff' in command:
                    return SimpleNamespace(stdout='', returncode=int(scope in command))
                return SimpleNamespace(stdout='', returncode=0)
            with self.subTest(scope=scope), patch('trader.research.chart_replay_cli.subprocess.run', side_effect=git_result):
                with self.assertRaisesRegex(ValueError, 'frozen|clean'):
                    _source_revision()
