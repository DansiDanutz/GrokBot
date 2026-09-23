"""The controller as it actually runs: read files, write three, leak nothing.

Every path here is a tempdir. The public team.json is the one file that leaves
this machine, so it is checked for local paths explicitly rather than by review.
"""
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from trader.doctor import __main__ as doctor
from trader.team import __main__ as cli, controller, facts as readings, public

NOW = 1789321500000                      # 2026-09-13 20:45 Europe/Bucharest


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


class Desk:
    """A miniature live circle on disk."""

    def __init__(self, root):
        self.root = Path(root)
        self.runtime = self.root / 'autopilot'
        self.evidence = self.root / 'team-evidence'
        self.experiments = self.root / 'team-experiments'
        self.radar = self.root / 'radar' / 'radar.json'
        self.database = self.root / 'market-data' / 'market.sqlite3'
        write(self.runtime / 'autopilot.json', dict(
            schema_version=1, heartbeat_ms=NOW - 5000, watchlist_scan_id=7,
            core_candidates=3, entries_stalled=True, structure_blackout=False,
            open_bots=[dict(bot_id=7, symbol='BTCUSDTM', direction='LONG')]))
        write(self.runtime / 'review-status.json', dict(
            evidence_headline='deferred: 4 directional opens < 5 minimum',
            proposals=dict(applied=0, deferred=1), last_run_at_ms=NOW - 9_000_000))
        (self.runtime / 'events.jsonl').write_text(
            json.dumps(dict(ts_ms=NOW - 1000, event_id=11, type='OPEN', bot_id=7,
                            symbol='BTCUSDTM')) + '\n')
        write(self.radar, dict(schema_version=1, generated_at_ms=NOW - 600_000,
                               rows=[], sections={}))
        self.experiments.mkdir(parents=True, exist_ok=True)

    def args(self, *extra):
        return ['--now-ms', str(NOW), '--evidence-dir', str(self.evidence),
                '--experiments-dir', str(self.experiments), '--runtime-dir',
                str(self.runtime), '--radar', str(self.radar),
                '--database', str(self.database), *extra]

    def run(self, *extra):
        with redirect_stdout(io.StringIO()):
            return cli.main(self.args(*extra))

    def json_at(self, path):
        return json.loads((self.root / path).read_text())


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.desk = Desk(self.tmp.name)

    def test_a_dry_run_writes_nothing_at_all(self):
        self.assertEqual(self.desk.run('--dry-run'), 0)
        self.assertFalse((self.desk.evidence / 'controller-state.json').exists())
        self.assertFalse((self.desk.evidence / 'dispatch.json').exists())
        self.assertFalse((self.desk.runtime / 'team.json').exists())

    def test_a_real_run_writes_the_state_the_board_and_the_public_file(self):
        self.assertEqual(self.desk.run(), 0)
        state = self.desk.json_at('team-evidence/controller-state.json')
        board = self.desk.json_at('team-evidence/dispatch.json')
        team = self.desk.json_at('autopilot/team.json')
        self.assertEqual(state['schema_version'], 2)
        self.assertEqual(state['scheduler_owner'], 'com.danslab.trader-team-controller')
        self.assertTrue(board['dispatches'])
        self.assertEqual(len(board['roster']), 21)
        self.assertEqual(team['open_dispatches'], board['open'])

    def test_it_never_writes_the_stewards_board(self):
        self.desk.run()
        self.assertFalse((self.desk.evidence / 'board.json').exists())

    def test_the_public_file_carries_no_local_path_or_host(self):
        self.desk.run()
        text = (self.desk.runtime / 'team.json').read_text()
        for banned in ('/Users', 'Sandbox', 'ZCodeProject', 'sqlite3', '127.0.0.1'):
            self.assertNotIn(banned, text)

    def test_the_public_file_gives_each_bot_its_dispatches_by_role_name(self):
        self.desk.run()
        team = self.desk.json_at('autopilot/team.json')
        roles = {row['role_name'] for row in team['dispatches']}
        self.assertIn('Grid Desk Lead', roles)
        for row in team['dispatches']:
            self.assertTrue(row['dispatch_id'] and row['instruction'] and row['due_at'])

    def test_a_second_run_does_not_repeat_the_same_dispatch(self):
        self.desk.run()
        first = self.desk.json_at('team-evidence/dispatch.json')['dispatches']
        self.desk.run()
        second = self.desk.json_at('team-evidence/dispatch.json')['dispatches']
        self.assertEqual([r['dispatch_id'] for r in first],
                         [r['dispatch_id'] for r in second])

    def test_a_steward_receipt_closes_the_dispatch_it_names(self):
        self.desk.run()
        board = self.desk.json_at('team-evidence/dispatch.json')
        target = board['dispatches'][0]
        write(self.desk.evidence / 'receipts' / (target['dispatch_id'] + '.json'),
              dict(dispatch_id=target['dispatch_id'], role=target['role_name'],
                   answered_at='2026-09-13T18:10:00Z', room=target['room'],
                   summary='answered in the room'))
        self.desk.run()
        after = {r['dispatch_id']: r for r in
                 self.desk.json_at('team-evidence/dispatch.json')['dispatches']}
        self.assertEqual(after[target['dispatch_id']]['status'], 'DONE')

    def test_the_doctor_turns_that_board_into_its_team_facts(self):
        self.desk.run()
        board = self.desk.json_at('team-evidence/dispatch.json')
        given = doctor._team(board, NOW + 600_000)
        self.assertEqual(given['team_cycle_age_s'], 600)
        self.assertIn('Publisher', given['team_idle_roles'])
        self.assertEqual(given['team_blocked'], [])

    def test_an_invalid_argument_is_rejected(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.main(['--now-ms', 'not-a-number'])

    def test_deferred_overflow_returns_nonzero_without_writing(self):
        state = dict(schema_version=controller.SCHEMA,
                     scheduler_owner=controller.SCHEDULER_OWNER,
                     cycles={}, daily_phase_outcomes={}, markers={},
                     deferred_events=[controller.events.event(
                         'BOT_CLOSED', str(n), bot_id=n, symbol='FIXTURE')
                         for n in range(controller.MAX_DEFERRED + 1)])
        state['dispatches'] = [dict(controller._dispatch(
            'BOT_OPENED', 'technical_interpreter', {}, NOW, n), key=str(n))
            for n in range(controller.MAX_OPEN)]
        paths = (self.desk.evidence / 'controller-state.json',
                 self.desk.evidence / 'dispatch.json',
                 self.desk.runtime / 'team.json')
        write(paths[0], state)
        write(paths[1], {'sentinel': 'dispatch'})
        write(paths[2], {'sentinel': 'public'})
        before = [path.read_bytes() for path in paths]
        error = io.StringIO()

        with patch.object(cli, '_engineering') as engineering, \
                redirect_stdout(io.StringIO()), redirect_stderr(error):
            result = cli.main(self.desk.args())

        self.assertEqual(result, 2)
        engineering.assert_not_called()
        self.assertIn('team controller refused cycle: deferred event queue overflow:',
                      error.getvalue())
        self.assertLess(len(error.getvalue()), 256)
        self.assertNotIn('Traceback', error.getvalue())
        self.assertEqual([path.read_bytes() for path in paths], before)

    def test_oversize_candidate_state_returns_nonzero_without_writing(self):
        state = dict(schema_version=controller.SCHEMA,
                     scheduler_owner=controller.SCHEDULER_OWNER,
                     cycles={}, daily_phase_outcomes={}, dispatches=[],
                     markers=dict(scan_id=7, candidates=3, event_id=11,
                                  entries_stalled=True, structure_blackout=False,
                                  review_run_ms=NOW - 9_000_000,
                                  phase_dates=dict(data='2026-09-13',
                                                   research='2026-09-13',
                                                   engineering='2026-09-13')),
                     deferred_events=[controller.events.event(
                         'BOT_CLOSED', 'large', detail='x' *
                         (controller.MAX_STATE_BYTES + 1))])
        paths = (self.desk.evidence / 'controller-state.json',
                 self.desk.evidence / 'dispatch.json',
                 self.desk.runtime / 'team.json')
        write(paths[0], state)
        write(paths[1], {'sentinel': 'dispatch'})
        write(paths[2], {'sentinel': 'public'})
        before = [path.read_bytes() for path in paths]
        error = io.StringIO()

        with patch.object(cli, '_engineering') as engineering, \
                redirect_stdout(io.StringIO()), redirect_stderr(error):
            result = cli.main(self.desk.args())

        self.assertEqual(result, 2)
        engineering.assert_not_called()
        self.assertIn('team controller refused cycle: controller state overflow:',
                      error.getvalue())
        self.assertLess(len(error.getvalue()), 256)
        self.assertEqual([path.read_bytes() for path in paths], before)


class FactsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.desk = Desk(self.tmp.name)

    def test_a_missing_file_is_a_missing_fact_not_an_exception(self):
        self.assertIsNone(readings.read_json(self.desk.root / 'nope.json'))
        self.assertIsNone(readings.mtime_ms(self.desk.root / 'nope.json'))
        self.assertEqual(readings.event_tail(self.desk.root / 'nope.jsonl'), [])

    def test_receipts_are_read_by_dispatch_id_and_bounded(self):
        write(self.desk.evidence / 'receipts' / 'D-1.json',
              dict(dispatch_id='D-1', role='Risk Sentinel', room='Trading',
                   answered_at='2026-09-13T18:10:00Z', summary='x' * 900))
        rows = readings.receipts(self.desk.evidence / 'receipts')
        self.assertEqual(len(rows['D-1']['summary']), 600)
        self.assertIsNotNone(rows['D-1']['answered_at_ms'])

    def test_a_receipt_without_an_identifier_is_ignored(self):
        write(self.desk.evidence / 'receipts' / 'bad.json', dict(role='Risk Sentinel'))
        self.assertEqual(readings.receipts(self.desk.evidence / 'receipts'), {})

    def test_pending_requests_exclude_the_ones_already_answered(self):
        write(self.desk.experiments / 'inbox' / 'REQ-1.json', dict(rule_id='x'))
        write(self.desk.experiments / 'inbox' / 'REQ-2.json', dict(rule_id='x'))
        write(self.desk.experiments / 'results' / 'REQ-1' / 'result.json',
              dict(status='DEFERRED'))
        done, pending = readings.experiments(self.desk.experiments)
        self.assertEqual((done, pending), (['REQ-1'], ['REQ-2']))

    def test_the_newest_counterfactual_report_is_the_one_reported(self):
        reports = self.desk.root / 'reports'
        write(reports / 'counterfactual-2026-09-11.json', {})
        write(reports / 'counterfactual-2026-09-12.json', {})
        date, at = readings.counterfactual(reports)
        self.assertEqual(date, '2026-09-12')
        self.assertIsNotNone(at)


class PublicTests(unittest.TestCase):
    def test_any_local_string_is_replaced_wholesale(self):
        self.assertEqual(public.text('/Users/davidai/secret'), 'redacted')
        self.assertEqual(public.text('Risk Sentinel'), 'Risk Sentinel')

    def test_safe_bounds_an_untrusted_file(self):
        source = dict(schema_version=2, cycle_id='CTRL-1', dispatches=[
            dict(dispatch_id='D-1', role_name='Risk Sentinel', event='DOCTOR_FAIL',
                 instruction='look', payload=dict(where='/Users/davidai/log'))],
            roster=[dict(name='Risk Sentinel', status='OK', installed=True)])
        out = public.safe(source)
        self.assertEqual(out['dispatches'][0]['payload']['where'], 'redacted')
        self.assertEqual(out['roster'][0]['name'], 'Risk Sentinel')

    def test_a_non_object_snapshot_is_refused(self):
        with self.assertRaises(ValueError):
            public.safe([1, 2, 3])


if __name__ == '__main__':
    unittest.main()
