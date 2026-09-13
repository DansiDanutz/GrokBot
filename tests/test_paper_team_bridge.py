"""Offline contract and boundary tests for the paper-team research bridge."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('paper_team_bridge',
    Path(__file__).resolve().parents[1] / 'scripts' / 'paper-team-bridge.py')
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)
NOW = datetime(2026, 9, 13, 12, tzinfo=timezone.utc)
REV = 'a' * 40


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'team-experiments'
        self.root.mkdir(mode=0o700)
        (self.root / 'inbox').mkdir(mode=0o700)
        self.source = self.base / 'source'
        self.source.mkdir()
        for name in ['daily.py', 'rules.py', '__init__.py']:
            path = self.source / 'trader' / 'review' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('# offline fixture\n')
        runtime = self.base / 'runtime'
        runtime.mkdir()
        self.state = runtime / 'state.json'
        self.state.write_text('{"paper":true}\n')
        self.evidence = runtime / 'radar.json'
        self.evidence.write_text('{"asof_ms":123}\n')
        database = self.base / 'market.sqlite3'
        database.write_bytes(b'fixture')
        self.config_path = self.base / 'operator.json'
        self.config_data = {'version': 1, 'experiments_root': str(self.root),
            'source_root': str(self.source), 'strategy_revision': REV,
            'python': str(Path(sys.executable).resolve()), 'state': str(self.state),
            'database': str(database), 'events': str(runtime),
            'evidence_files': {'radar': str(self.evidence)}}
        self.write_config()
        self.config = bridge.load_config(str(self.config_path))
        self.request = {'version': 1, 'request_id': 'round-001',
            'rule_id': 'require_trend_alignment', 'review_date': '2026-09-12',
            'created_at': NOW.isoformat(), 'strategy_revision': REV,
            'evidence_hashes': bridge.evidence_hashes(self.config)}
        self.request_path = self.root / 'inbox' / 'round-001.json'
        self.write_request()

    def tearDown(self):
        self.temp.cleanup()

    def write_config(self):
        self.config_path.write_text(json.dumps(self.config_data))
        self.config_path.chmod(0o600)

    def write_request(self):
        self.request_path.write_text(json.dumps(self.request))

    def run_request(self, **kwargs):
        with patch.object(bridge, 'source_revision', return_value=REV):
            return bridge.process(self.config, 'round-001', now=NOW, **kwargs)

    def manifest(self):
        return {p.relative_to(self.source).as_posix(): bridge.digest(p.read_bytes())
                for p in (self.source / 'trader').rglob('*.py')}

    def fake_evaluation(self, config, request, directory):
        manifest = self.manifest()
        bridge.atomic_json(directory / 'source-hashes.json', manifest)
        bridge.export_sources(config, manifest, directory)
        bridge.atomic_file(directory / 'state-input.json', self.state.read_bytes())
        bridge.atomic_file(directory / 'review.md', b'offline review')
        bridge.atomic_json(directory / 'metrics.json', {'requested_rule': request['rule_id'],
            'review_date': request['review_date'], 'rule_evidence_statuses': ['apply'], 'coverage_flagged': False})
        bridge.atomic_json(directory / 'proposals.json', {'date': request['review_date'],
            'proposals': [{'rule': request['rule_id']}]})
        result = {'status': 'SHADOW', 'reason': 'research evaluation only; no rule applied',
                  'candidate_observed': True, 'rule_evidence_statuses': ['apply']}
        for key, name in [('metrics_sha256', 'metrics.json'), ('state_input_sha256', 'state-input.json'),
                          ('proposals_sha256', 'proposals.json'), ('report_sha256', 'review.md'),
                          ('evaluator_source_sha256', 'source-hashes.json')]:
            result[key] = bridge.digest((directory / name).read_bytes())
        return result

    def test_context_is_read_only(self):
        before = list(self.root.rglob('*'))
        with patch.object(bridge, 'source_revision', return_value=REV):
            result = bridge.context(self.config, NOW)
        self.assertEqual(result['review_date'], '2026-09-12')
        self.assertEqual(result['evidence_hashes'], self.request['evidence_hashes'])
        self.assertEqual(before, list(self.root.rglob('*')))

    def test_success_and_idempotent_duplicate(self):
        with patch.object(bridge, 'evaluate', side_effect=self.fake_evaluation) as evaluator:
            result = self.run_request()
            self.assertEqual(result, self.run_request())
            evaluator.assert_called_once()
        self.assertEqual(result['status'], 'SHADOW')
        self.assertFalse(result['applied'])
        self.assertTrue((self.root / 'results/round-001/result.json').exists())

    def test_duplicate_changed_body_does_not_overwrite_receipt(self):
        with patch.object(bridge, 'evaluate', side_effect=self.fake_evaluation):
            prior = self.run_request()
        self.request['rule_id'] = 'symbol_cooldowns'
        self.write_request()
        with self.assertRaisesRegex(bridge.BridgeError, 'reused'):
            self.run_request()
        saved = json.loads((self.root / 'results/round-001/result.json').read_text())
        self.assertEqual(prior, saved)

    def test_cached_receipt_cannot_claim_application(self):
        with patch.object(bridge, 'evaluate', side_effect=self.fake_evaluation):
            original = self.run_request()
        receipt = self.root / 'results/round-001/result.json'
        for change in [{'applied': True}, {'status': 'APPLIED_VERIFIED'},
                       {'request_id': 'other-id'}, {'strategy_revision': 'b' * 40}]:
            with self.subTest(change=change):
                receipt.write_text(json.dumps(dict(original, **change)))
                with self.assertRaisesRegex(bridge.BridgeError, 'cached acknowledgment'):
                    self.run_request()

    def test_edited_receipt_or_artifact_is_rejected(self):
        with patch.object(bridge, 'evaluate', side_effect=self.fake_evaluation):
            self.run_request()
        directory = self.root / 'results/round-001'
        receipt = directory / 'result.json'
        original = receipt.read_bytes()
        value = json.loads(original)
        value['reason'] = 'invented success'
        receipt.write_text(json.dumps(value))
        with self.assertRaisesRegex(bridge.BridgeError, 'acknowledgment was edited'):
            self.run_request()
        receipt.write_bytes(original)
        (directory / 'metrics.json').write_text('{}')
        with self.assertRaisesRegex(bridge.BridgeError, 'artifact changed'):
            self.run_request()

    def test_cached_status_must_match_generated_metrics_even_with_updated_checksum(self):
        with patch.object(bridge, 'evaluate', side_effect=self.fake_evaluation):
            self.run_request()
        directory = self.root / 'results/round-001'
        receipt = directory / 'result.json'
        value = json.loads(receipt.read_text())
        value['status'] = 'DEFERRED'
        receipt.write_text(json.dumps(value))
        (directory / 'result.sha256').write_text(bridge.digest(receipt.read_bytes()))
        with self.assertRaisesRegex(bridge.BridgeError, 'contradicts evaluation'):
            self.run_request()

    def test_unknown_rule_routes_to_implementation_without_execution(self):
        self.request['rule_id'] = 'new_bollinger_hypothesis'
        self.write_request()
        with patch.object(bridge, 'evaluate') as evaluator:
            result = self.run_request()
            evaluator.assert_not_called()
        self.assertEqual(result['status'], 'NEEDS_IMPLEMENTATION')
        self.assertFalse(result['applied'])

    def test_code_and_command_fields_rejected(self):
        for key in ['code', 'command', 'parameters', 'output_path']:
            with self.subTest(key=key):
                request = dict(self.request, **{key: 'touch anything'})
                with self.assertRaisesRegex(bridge.BridgeError, 'unsupported request'):
                    bridge.validate_request(request, self.config, 'round-001', NOW)

    def test_stale_revision_or_evidence_rejected_without_evaluation(self):
        cases = [{'created_at': (NOW - timedelta(hours=2)).isoformat()},
                 {'strategy_revision': 'b' * 40}, {'evidence_hashes': {'radar': 'b' * 64}},
                 {'created_at': (NOW + timedelta(minutes=1)).isoformat()},
                 {'review_date': '2026-09-13'}, {'review_date': '2026-07-01'}]
        for change in cases:
            with self.subTest(change=change), patch.object(bridge, 'source_revision', return_value=REV):
                with self.assertRaises(bridge.BridgeError):
                    bridge.validate_request(dict(self.request, **change), self.config, 'round-001', NOW)

    def test_request_path_traversal_rejected(self):
        for request_id in ['../escape', '/absolute', 'a/b', 'bad;sh', '']:
            with self.assertRaises(bridge.BridgeError):
                bridge.process(self.config, request_id, now=NOW)

    def test_symlink_request_and_result_directory_rejected(self):
        self.request_path.unlink()
        self.request_path.symlink_to(self.evidence)
        with self.assertRaisesRegex(bridge.BridgeError, 'symlink'):
            self.run_request()
        self.request_path.unlink()
        self.write_request()
        (self.root / 'results').symlink_to(self.source, target_is_directory=True)
        with self.assertRaisesRegex(bridge.BridgeError, 'symlink'):
            self.run_request()

    def test_partial_result_reservation_is_not_replayed(self):
        (self.root / 'results/round-001').mkdir(parents=True)
        with patch.object(bridge, 'evaluate') as evaluator:
            with self.assertRaisesRegex(bridge.BridgeError, 'incomplete'):
                self.run_request()
            evaluator.assert_not_called()

    def test_atomic_output_refuses_replacement_and_symlink(self):
        output = self.root / 'ack.json'
        bridge.atomic_json(output, {'original': True})
        with self.assertRaises(bridge.BridgeError):
            bridge.atomic_json(output, {'original': False})
        self.assertEqual(json.loads(output.read_text()), {'original': True})
        output.unlink()
        output.symlink_to(self.evidence)
        with self.assertRaisesRegex(bridge.BridgeError, 'symlink'):
            bridge.atomic_json(output, {})

    def test_exclusive_lock_prevents_parallel_work(self):
        with bridge.locked(self.root):
            with self.assertRaisesRegex(bridge.BridgeError, 'another bridge'):
                with bridge.locked(self.root):
                    self.fail('second writer got lock')

    def test_private_config_and_output_boundaries(self):
        self.config_path.chmod(0o666)
        with self.assertRaisesRegex(bridge.BridgeError, 'operator config'):
            bridge.load_config(str(self.config_path))
        self.write_config()
        self.config_data['experiments_root'] = str(self.state.parent)
        self.state.parent.chmod(0o700)
        self.write_config()
        with self.assertRaisesRegex(bridge.BridgeError, 'isolated'):
            bridge.load_config(str(self.config_path))

    def test_config_cannot_be_native_inbox_content(self):
        path = self.root / 'operator.json'
        path.write_text(json.dumps(self.config_data))
        path.chmod(0o600)
        with self.assertRaisesRegex(bridge.BridgeError, 'outside'):
            bridge.load_config(str(path))

    def test_fifo_input_is_rejected_without_blocking(self):
        path = self.root / 'input-fifo'
        os.mkfifo(path)
        with self.assertRaisesRegex(bridge.BridgeError, 'regular file'):
            bridge.read_bytes(path)

    def test_duplicate_keys_and_oversized_request_rejected(self):
        with self.assertRaises(bridge.BridgeError):
            bridge.json_object(b'{"a":1,"a":2}')
        self.request_path.write_bytes(b' ' * (bridge.MAX_JSON + 1))
        with self.assertRaisesRegex(bridge.BridgeError, 'bounded'):
            self.run_request()

    def test_evaluator_failure_is_acknowledged_without_application(self):
        with patch.object(bridge, 'evaluate', side_effect=subprocess.TimeoutExpired('ignored', 180)):
            result = self.run_request()
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['reason'], 'evaluation failed: TimeoutExpired')
        self.assertFalse(result['applied'])

    def test_request_mutation_during_evaluation_rejected(self):
        def mutate(*_):
            self.request_path.write_text('{}')
            return {'status': 'SHADOW'}
        with patch.object(bridge, 'evaluate', side_effect=mutate):
            result = self.run_request()
        self.assertEqual(result['status'], 'REJECTED')
        self.assertIn('changed during', result['reason'])

    def test_pinned_source_checks_head_cleanliness_and_tracked_reviewer(self):
        def git(command, **kwargs):
            args = command[3:]
            value = str(self.source) if args == ['rev-parse', '--show-toplevel'] else (
                REV if args == ['rev-parse', 'HEAD'] else '')
            return subprocess.CompletedProcess(command, 0, value, '')
        with patch.object(bridge.subprocess, 'run', side_effect=git):
            self.assertEqual(bridge.source_revision(self.config), REV)
        def dirty(command, **kwargs):
            if 'status' in command:
                return subprocess.CompletedProcess(command, 0, ' M trader/review/daily.py', '')
            return git(command, **kwargs)
        with patch.object(bridge.subprocess, 'run', side_effect=dirty):
            with self.assertRaisesRegex(bridge.BridgeError, 'dirty'):
                bridge.source_revision(self.config)

    def test_untracked_evaluator_source_is_refused(self):
        with patch.object(bridge, 'source_revision', return_value=REV), \
             patch.object(bridge.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, b'')):
            with self.assertRaisesRegex(bridge.BridgeError, 'untracked'):
                bridge.source_manifest(self.config)

    def test_real_offline_child_recomputes_metrics_and_defers(self):
        # Real subprocess runs fixture reviewer/planner; only git attestation is mocked.
        (self.source / 'trader/review/daily.py').write_text("""
import argparse, json, sqlite3
from pathlib import Path
assert sqlite3.connect(':memory:').execute('select 1').fetchone() == (1,)
p = argparse.ArgumentParser()
for flag in ['date', 'state', 'db', 'events', 'out', 'proposals-out']:
    p.add_argument('--' + flag, required=True)
a = p.parse_args()
assert json.loads(Path(a.state).read_text()) == {'paper': True}
Path(a.out).write_text('Offline generated review')
Path(a.proposals_out).write_text(json.dumps({'date': a.date,
    'summary': {'opened': 4, 'closed': 2},
    'totals': {'closed_total': 8, 'data_coverage': {'flagged': False}},
    'proposals': [{'rule': 'require_trend_alignment', 'tier': 1}]}))
""")
        (self.source / 'trader/review/rules.py').write_text("""
def load_rules(path):
    return {}
def plan_changes(props, current, day):
    return [{'rule': 'require_trend_alignment', 'status': 'defer',
             'defer_reason': 'four opens below five required'}]
""")
        malicious = self.source / 'sqlite3'
        malicious.mkdir()
        (malicious / '__init__.py').write_text("raise RuntimeError('untracked package executed')\n")
        import importlib.machinery
        (self.source / ('sqlite3' + importlib.machinery.EXTENSION_SUFFIXES[0])).write_bytes(b'invalid native module')
        with patch.object(bridge, 'source_revision', return_value=REV), \
             patch.object(bridge, 'source_manifest', side_effect=lambda _: self.manifest()):
            result = bridge.process(self.config, 'round-001', now=NOW)
        self.assertEqual(result['status'], 'DEFERRED', result)
        metrics = json.loads((self.root / 'results/round-001/metrics.json').read_text())
        self.assertEqual(metrics['rule_evidence_statuses'], ['defer'])
        self.assertEqual(metrics['opened'], 4)
        self.assertEqual(metrics['defer_reasons'], ['four opens below five required'])
        self.assertFalse(result['applied'])

    def test_only_daily_invoked_with_isolated_outputs_and_no_secrets(self):
        directory = self.root / 'evaluation'
        directory.mkdir()
        def fake_daily(command, **kwargs):
            self.assertIn("runpy.run_module('trader.review.daily'", command[command.index('-c') + 1])
            self.assertNotIn('trader.review.apply', ' '.join(command))
            self.assertEqual(command[command.index('--state') + 1], str(directory / 'state-input.json'))
            self.assertEqual(command[command.index('--db') + 1], str(self.config['database']))
            self.assertEqual(kwargs['timeout'], 180)
            self.assertNotIn('FAKE_SECRET', kwargs['env'])
            self.assertEqual(kwargs['cwd'], directory / 'evaluator-source')
            self.assertNotIn(str(self.source), command)
            (directory / 'metrics.json').write_text(json.dumps({'requested_rule': 'require_trend_alignment',
                'review_date': '2026-09-12', 'rule_evidence_statuses': ['apply'], 'coverage_flagged': False}))
            (directory / 'review.md').write_text('offline fixture')
            (directory / 'proposals.json').write_text(json.dumps({'date': '2026-09-12',
                'proposals': [{'rule': 'require_trend_alignment'}],
                'totals': {'data_coverage': {'flagged': False}}}))
            return subprocess.CompletedProcess(command, 0)
        original = self.state.read_bytes()
        with patch.dict(os.environ, {'FAKE_SECRET': 'never pass through'}), \
             patch.object(bridge, 'source_manifest', side_effect=lambda _: self.manifest()), \
             patch.object(bridge.subprocess, 'run', side_effect=fake_daily):
            result = bridge.evaluate(self.config, self.request, directory)
        self.assertEqual(result['status'], 'SHADOW')
        self.assertEqual(self.state.read_bytes(), original)
        self.assertEqual((directory / 'state-input.json').read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
