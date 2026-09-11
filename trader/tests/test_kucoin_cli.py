import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from trader.research.kucoin_cli import main, persist_report, load_running
from trader.tests.test_kucoin_snapshot import make_database


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def report(self, **changes):
        return dict(dict(schema_version=1, asof_ms=3600000, radar={'radar': []},
                         running=[], live_use={'actionable': False}), **changes)

    def test_new_output_is_atomic_and_identical_rerun_is_idempotent(self):
        output = self.root / 'offline-output'
        first = persist_report(self.report(), output)
        before = {str(path.relative_to(output)): path.read_bytes()
                  for path in output.rglob('*') if path.is_file()}
        second = persist_report(self.report(), output)
        after = {str(path.relative_to(output)): path.read_bytes()
                 for path in output.rglob('*') if path.is_file()}
        self.assertEqual(first, second)
        self.assertEqual(before, after)
        self.assertTrue((Path(first) / 'report.json').is_file())
        with self.assertRaisesRegex(ValueError, 'conflict'):
            persist_report(self.report(extra='changed'), output)

    def test_unowned_output_protected_root_symlink_and_hardlink_rejected(self):
        with self.assertRaisesRegex(ValueError, 'owned'):
            persist_report(self.report(), self.root)
        with self.assertRaisesRegex(ValueError, 'protected'):
            persist_report(self.report(), Path.home() / 'Library/LaunchAgents/example')
        link = self.root / 'link'
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            persist_report(self.report(), link / 'child')
        output = self.root / 'owned'
        result = Path(persist_report(self.report(), output))
        hardlink = self.root / 'copy'
        hardlink.hardlink_to(result / 'report.json')
        with self.assertRaisesRegex(ValueError, 'hardlink'):
            persist_report(self.report(), output)

    def test_running_input_rejects_unknown_fields_and_symlink(self):
        path = self.root / 'running.json'
        path.write_text(json.dumps({'schema_version': 1, 'bots': [], 'credentials': {}}))
        with self.assertRaises(ValueError):
            load_running(path, 3600000)
        link = self.root / 'link.json'
        link.symlink_to(path)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            load_running(link, 3600000)

    def test_cli_reads_detached_snapshot_and_writes_blocked_research_report(self):
        snapshot = make_database(self.root)
        running = self.root / 'running.json'
        running.write_text(json.dumps({'schema_version': 1, 'bots': []}))
        output = self.root / 'operator'
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = main(['operator', '--snapshot', str(snapshot), '--asof', '3600000',
                         '--running', str(running), '--output', str(output)])
        self.assertEqual(code, 0)
        document = json.loads(stdout.getvalue())
        self.assertFalse(document['report']['live_use']['actionable'])
        self.assertTrue((Path(document['artifact_directory']) / 'radar.json').is_file())

    def test_bad_input_fails_without_creating_output(self):
        output = self.root / 'absent'
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = main(['operator', '--snapshot', str(self.root / 'missing'),
                         '--asof', 'not-a-time', '--running', str(self.root / 'missing.json'),
                         '--output', str(output)])
        self.assertEqual(code, 2)
        self.assertFalse(output.exists())
        self.assertIn('error', json.loads(stderr.getvalue()))

    def test_submillisecond_asof_is_rejected_without_rounding(self):
        from trader.research.kucoin_cli import _asof
        with self.assertRaises(ValueError):
            _asof('1970-01-01T01:00:00.000001Z')

    def test_artifact_ledger_history_and_tracker_files_are_preserved(self):
        bot = dict(bot_id='alpha', ledger=[{'event_id': 1}], hourly_history=[{'asof_ms': 1}],
                   tracker={'fees': .1}, verdict={'action': 'keep'})
        output = Path(persist_report(self.report(running=[bot]), self.root/'artifacts'))
        self.assertEqual(json.loads((output/'ledger/alpha.json').read_text()), bot['ledger'])
        self.assertEqual(json.loads((output/'history/alpha.json').read_text()), bot['hourly_history'])

    def test_launchd_template_is_example_only_hourly_without_shell(self):
        import plistlib
        path = Path(__file__).resolve().parents[2]/'config/launchd/com.danslab.grid-radar.plist.example'
        document = plistlib.loads(path.read_bytes())
        self.assertEqual(document['StartInterval'], 3600)
        self.assertFalse(document['RunAtLoad'])
        self.assertIn('trader.research.kucoin_cli', document['ProgramArguments'])
        self.assertIn('latest', document['ProgramArguments'])
        self.assertNotIn('/bin/sh', document['ProgramArguments'])

    def test_latest_asof_uses_snapshot_completed_hour_without_clock(self):
        from trader.research.kucoin_cli import _snapshot_asof
        class LatestSnapshot:
            def market_bounds(self):
                return 1000, 9_000_000
        self.assertEqual(_snapshot_asof('latest', LatestSnapshot()), 7_200_000)
        class EmptySnapshot:
            def market_bounds(self):
                return None, None
        with self.assertRaises(ValueError):
            _snapshot_asof('latest', EmptySnapshot())
