"""One cycle: reconcile what was asked, route what happened, report who is idle.

The failure this replaces is concrete. On 2026-09-13 the Codex heartbeat lost
its tokens, controller-state.json froze at CTRL-20260913-05 RUNNING at 02:34 UTC
and the 08:15 / 09:15 / 10:15 phases never ran, while every bot waited politely
for a cue that could not arrive.
"""
import json
import unittest
from unittest.mock import patch
from datetime import datetime
from zoneinfo import ZoneInfo

from trader.team import controller, roster

ZONE = ZoneInfo('Europe/Bucharest')
HOUR_MS = 3_600_000


def at(text):
    return int(datetime.fromisoformat(text).replace(tzinfo=ZONE).timestamp() * 1000)


NOW = at('2026-09-13T20:45:00')


def facts(**over):
    base = dict(doctor=dict(status='ok', failing=[], checks=[]),
                watchlist_scan_id=100, core_candidates=4, entries_stalled=False,
                structure_blackout=False, open_directions={7: 'LONG'}, events=[],
                review=dict(headline='', applied=0, last_run_at_ms=None),
                counterfactual_date=None, liq_clusters_ms=None, receipts={},
                engineering_results=[], pending_requests=[],
                role_seen_ms={role['id']: NOW - 60_000 for role in roster.ROLES})
    base.update(over)
    return base


def quiet_state(now_ms=NOW, **over):
    """A state whose markers already saw everything facts() reports."""
    base = dict(schema_version=controller.SCHEMA,
                scheduler_owner=controller.SCHEDULER_OWNER, cycles={},
                dispatches=[], daily_phase_outcomes={},
                markers=dict(cycle_at_ms=now_ms - HOUR_MS, doctor_failing=[],
                             scan_id=100, candidates=4, event_id=0,
                             entries_stalled=False, structure_blackout=False,
                             review_run_ms=None, counterfactual_date=None,
                             liq_clusters_ms=None, discovery_date='2026-09-13',
                             phase_dates=dict(data='2026-09-13', research='2026-09-13',
                                              engineering='2026-09-13')))
    base.update(over)
    return base


def roles_of(dispatches):
    return sorted((row['role'], row['event']) for row in dispatches)


class RoutingTests(unittest.TestCase):
    def test_a_doctor_failure_reaches_risk_and_the_steward_with_a_lead_synthesis(self):
        given = facts(doctor=dict(status='fail', failing=['radar'],
                                  checks=[dict(name='radar', status='fail',
                                               detail='stale', where='radar log')]))
        _, created, summary = controller.cycle(quiet_state(), given, NOW)
        self.assertIn(('risk_sentinel', 'DOCTOR_FAIL'), roles_of(created))
        self.assertIn(('senior_developer', 'DOCTOR_FAIL'), roles_of(created))
        self.assertIn(('grid_desk_lead', 'SYNTHESIS'), roles_of(created))
        self.assertIn(('paper_desk_secretary', 'FINAL_RESPONSE'), roles_of(created))

    def test_an_open_goes_to_the_interpreter_and_a_close_to_performance(self):
        rows = [dict(event_id=5, type='OPEN', bot_id=7, symbol='BTCUSDTM'),
                dict(event_id=6, type='CLOSE', bot_id=7, symbol='BTCUSDTM', reason_code=3)]
        _, created, _ = controller.cycle(quiet_state(), facts(events=rows), NOW)
        self.assertIn(('technical_interpreter', 'BOT_OPENED'), roles_of(created))
        self.assertIn(('performance_analyst', 'BOT_CLOSED'), roles_of(created))

    def test_a_repeat_scan_with_unchanged_candidates_reaches_nobody(self):
        _, created, _ = controller.cycle(quiet_state(),
                                         facts(watchlist_scan_id=101), NOW)
        self.assertNotIn('data_structure', [r['role'] for r in created])

    def test_a_changed_candidate_set_reaches_data_and_structure(self):
        _, created, _ = controller.cycle(
            quiet_state(), facts(watchlist_scan_id=101, core_candidates=9), NOW)
        self.assertIn(('data_structure', 'RADAR_SCAN'), roles_of(created))

    def test_a_quiet_cycle_dispatches_nothing_at_all(self):
        _, created, summary = controller.cycle(quiet_state(), facts(), NOW)
        self.assertEqual(created, [])
        self.assertEqual(summary['open'], 0)

    def test_the_secretary_only_speaks_when_the_user_has_an_outcome(self):
        rows = [dict(event_id=5, type='OPEN', bot_id=7, symbol='BTCUSDTM')]
        _, created, _ = controller.cycle(quiet_state(), facts(events=rows), NOW)
        self.assertNotIn('paper_desk_secretary', [r['role'] for r in created])

    def test_every_dispatch_carries_an_id_instruction_and_a_due_time(self):
        given = facts(entries_stalled=True)
        _, created, _ = controller.cycle(quiet_state(), given, NOW)
        for row in created:
            self.assertTrue(row['dispatch_id'].startswith('D-20260913-20-'))
            self.assertEqual(row['status'], 'PENDING')
            self.assertEqual(row['due_at_ms'], NOW + 2 * HOUR_MS)
            self.assertTrue(row['instruction'])
            self.assertTrue(row['room'])

    def test_a_phase_dispatch_gets_three_hours_not_two(self):
        state = quiet_state(markers=dict(quiet_state()['markers'], phase_dates={}))
        _, created, _ = controller.cycle(state, facts(), NOW)
        phase = [r for r in created if r['event'] == 'DATA_PHASE_DUE'][0]
        self.assertEqual(phase['due_at_ms'], NOW + 3 * HOUR_MS)


class ReconciliationTests(unittest.TestCase):
    def _stalled(self):
        state, created, _ = controller.cycle(quiet_state(),
                                             facts(entries_stalled=True), NOW)
        return state, created[0]

    def test_a_receipt_closes_its_dispatch(self):
        state, first = self._stalled()
        later = NOW + HOUR_MS
        receipt = {first['dispatch_id']: dict(answered_at='2026-09-13T18:00:00Z',
                                              answered_at_ms=later, room='Office',
                                              summary='done')}
        after, _, summary = controller.cycle(state, facts(receipts=receipt,
                                                          entries_stalled=True), later)
        row = [r for r in after['dispatches'] if r['dispatch_id'] == first['dispatch_id']][0]
        self.assertEqual(row['status'], 'DONE')
        self.assertIn(first['dispatch_id'], summary['done'])

    def test_an_unanswered_dispatch_blocks_after_its_due_time(self):
        state, first = self._stalled()
        late = NOW + 3 * HOUR_MS
        after, _, summary = controller.cycle(state, facts(entries_stalled=True), late)
        row = [r for r in after['dispatches'] if r['dispatch_id'] == first['dispatch_id']][0]
        self.assertEqual(row['status'], 'BLOCKED')
        self.assertIn(first['dispatch_id'], summary['blocked'])

    def test_a_blocked_dispatch_escalates_that_role_to_the_lead(self):
        state, first = self._stalled()
        late = NOW + 3 * HOUR_MS
        _, created, _ = controller.cycle(state, facts(entries_stalled=True), late)
        idle = [r for r in created if r['event'] == 'ROLE_IDLE'
                and first['role_name'] in r['instruction']]
        self.assertTrue(idle, 'the blocked role was never escalated')
        self.assertEqual(idle[0]['role'], 'grid_desk_lead')

    def test_a_silent_role_past_its_budget_is_reported_as_idle(self):
        state = quiet_state()
        given = facts(role_seen_ms=dict(senior_developer=NOW - 9 * HOUR_MS))
        _, created, summary = controller.cycle(state, given, NOW)
        row = [r for r in summary['roster'] if r['role'] == 'senior_developer'][0]
        self.assertEqual(row['status'], 'IDLE')
        self.assertIn(('grid_desk_lead', 'ROLE_IDLE'), roles_of(created))

    def test_an_uninstalled_role_is_never_blocked_only_not_installed(self):
        """A gap Dan has to close is not a role failing to answer.

        Pinned to a synthetic uninstalled role so the rule survives whichever
        bots actually exist in the app on any given day.
        """
        absent = dict(roster.BY_ID['discovery_auditor'], installed=False)
        patched = dict(roster.BY_ID, discovery_auditor=absent)
        roles = tuple(absent if r['id'] == 'discovery_auditor' else r
                      for r in roster.ROLES)
        state = quiet_state(markers=dict(quiet_state()['markers'], discovery_date=None))
        with patch.object(roster, 'BY_ID', patched), \
                patch.object(roster, 'ROLES', roles):
            state, created, summary = controller.cycle(state, facts(), NOW)
            discovery = [r for r in created if r['event'] == 'DISCOVERY'][0]
            after, _, summary = controller.cycle(state, facts(), NOW + 5 * HOUR_MS)
        row = [r for r in after['dispatches']
               if r['dispatch_id'] == discovery['dispatch_id']][0]
        self.assertEqual(row['status'], 'NOT_INSTALLED')
        self.assertEqual([r['status'] for r in summary['roster']
                          if r['role'] == 'discovery_auditor'], ['NOT_INSTALLED'])

    def test_an_installed_role_that_never_answers_does_block(self):
        """The installed flag is not a promise that the bot works."""
        state = quiet_state(markers=dict(quiet_state()['markers'], discovery_date=None))
        state, created, _ = controller.cycle(state, facts(), NOW)
        discovery = [r for r in created if r['event'] == 'DISCOVERY'][0]
        after, _, _ = controller.cycle(state, facts(), NOW + 5 * HOUR_MS)
        row = [r for r in after['dispatches']
               if r['dispatch_id'] == discovery['dispatch_id']][0]
        self.assertEqual(row['status'], 'BLOCKED')

    def test_an_engineering_result_file_closes_the_engineering_dispatch(self):
        state = quiet_state(markers=dict(quiet_state()['markers'], phase_dates={}))
        state, created, _ = controller.cycle(
            state, facts(pending_requests=['REQ-1']), NOW)
        after, _, summary = controller.cycle(
            state, facts(pending_requests=[], engineering_results=['REQ-1']),
            NOW + HOUR_MS)
        row = [r for r in after['dispatches'] if r['event'] == 'ENGINEERING_DUE'][0]
        self.assertEqual(row['status'], 'DONE')
        self.assertEqual(after['daily_phase_outcomes']['2026-09-13']['engineering']
                         ['status'], 'COMPLETED')


class CapTests(unittest.TestCase):
    def _many(self):
        return facts(events=[dict(event_id=i, type='OPEN', bot_id=i, symbol='B%dUSDTM' % i)
                             for i in range(1, 30)])

    def test_no_more_than_twelve_dispatches_are_ever_open(self):
        state, created, summary = controller.cycle(quiet_state(), self._many(), NOW)
        self.assertLessEqual(summary['open'], controller.MAX_OPEN)
        self.assertLessEqual(len(created), controller.MAX_OPEN)

    def test_the_same_role_event_and_key_is_not_dispatched_twice(self):
        state, created, _ = controller.cycle(quiet_state(),
                                             facts(entries_stalled=True), NOW)
        again = dict(state)
        markers = dict(again['markers'], entries_stalled=False)
        after, second, _ = controller.cycle(dict(again, markers=markers),
                                            facts(entries_stalled=True),
                                            NOW + 600_000)
        repeats = [r for r in second if r['event'] == 'ENTRIES_STALLED']
        self.assertEqual(repeats, [])

    def test_the_lead_synthesis_is_never_starved_by_the_cap(self):
        _, created, _ = controller.cycle(quiet_state(), self._many(), NOW)
        self.assertIn(('grid_desk_lead', 'SYNTHESIS'), roles_of(created))


class PhaseTests(unittest.TestCase):
    def test_a_phase_due_earlier_today_is_caught_up_in_order(self):
        state = quiet_state(markers=dict(quiet_state()['markers'], phase_dates={}))
        after, created, summary = controller.cycle(state, facts(), NOW)
        order = list(dict.fromkeys(r['event'] for r in created if r['event'] in
                     ('DATA_PHASE_DUE', 'RESEARCH_DUE', 'ENGINEERING_DUE')))
        self.assertEqual(order[:3], ['DATA_PHASE_DUE', 'RESEARCH_DUE', 'ENGINEERING_DUE'])
        today = after['daily_phase_outcomes']['2026-09-13']
        self.assertEqual(today['data']['status'], 'RUNNING')
        self.assertEqual(today['data']['due_at'][:16], '2026-09-13T08:15')

    def test_engineering_with_nothing_pending_records_no_eligible_need(self):
        state = quiet_state(markers=dict(quiet_state()['markers'], phase_dates={}))
        after, _, _ = controller.cycle(state, facts(pending_requests=[]), NOW)
        self.assertEqual(after['daily_phase_outcomes']['2026-09-13']['engineering']
                         ['status'], 'NO_ELIGIBLE_NEED')

    def test_yesterdays_unfinished_phases_become_missed_not_replayed(self):
        state = quiet_state(markers=dict(quiet_state()['markers'], phase_dates={}))
        state, _, _ = controller.cycle(state, facts(), NOW)
        tomorrow = at('2026-09-14T11:00:00')
        after, created, _ = controller.cycle(state, facts(), tomorrow)
        self.assertEqual(after['daily_phase_outcomes']['2026-09-13']['data']['status'],
                         'MISSED')
        self.assertEqual(after['daily_phase_outcomes']['2026-09-14']['data']['status'],
                         'RUNNING')
        self.assertIn(('senior_developer', 'DATA_PHASE_DUE'), roles_of(created))

    def test_a_phase_before_its_local_time_stays_pending(self):
        morning = at('2026-09-14T07:00:00')
        state = quiet_state(markers=dict(quiet_state()['markers'], phase_dates={}))
        after, created, _ = controller.cycle(state, facts(), morning)
        self.assertEqual(after['daily_phase_outcomes']['2026-09-14']['data']['status'],
                         'PENDING')
        self.assertNotIn('DATA_PHASE_DUE', [r['event'] for r in created])


LEGACY = json.loads("""
{"schema_version": 1, "scheduler_owner": "Codex heartbeat",
 "automation_id": "paper-team-supabase-evidence-bridge", "native_timer": "PAUSED",
 "cycles": {"CTRL-20260913-05": {"status": "RUNNING", "trigger": "codex_heartbeat",
   "observed_utc": "2026-09-13T02:34:25.384411+00:00", "receipt": null}},
 "daily_phase_outcomes": {"2026-09-13": {"data": {"due_at": "2026-09-13T08:15:00+03:00",
   "status": "PENDING", "request_id": null, "started_at": null,
   "updated_at": "2026-09-13T02:34:25.384411+00:00", "completed_at": null,
   "receipt": null, "blocker": null}}},
 "last_wake_at": "2026-09-13T02:34:25.384411+00:00"}
""")


class MigrationTests(unittest.TestCase):
    def test_the_stuck_cycle_is_marked_missed_with_its_real_reason(self):
        after = controller.migrate(LEGACY)
        stuck = after['cycles']['CTRL-20260913-05']
        self.assertEqual(stuck['status'], 'MISSED')
        self.assertEqual(stuck['reason'], 'codex heartbeat lost')
        self.assertEqual(stuck['observed_utc'], '2026-09-13T02:34:25.384411+00:00')

    def test_the_scheduler_owner_moves_to_the_launchagent(self):
        after = controller.migrate(LEGACY)
        self.assertEqual(after['scheduler_owner'], 'com.danslab.trader-team-controller')
        self.assertEqual(after['schema_version'], 2)
        self.assertEqual(after['native_timer'], 'PAUSED')

    def test_prior_phase_records_survive_the_migration(self):
        after = controller.migrate(LEGACY)
        self.assertIn('2026-09-13', after['daily_phase_outcomes'])

    def test_migrating_is_idempotent(self):
        once = controller.migrate(LEGACY)
        self.assertEqual(controller.migrate(once)['cycles'], once['cycles'])

    def test_a_migrated_state_resumes_and_catches_up_its_missed_phases(self):
        after, created, summary = controller.cycle(LEGACY, facts(), NOW)
        self.assertEqual(summary['migrated_from'], 1)
        self.assertIn('CTRL-20260913-05 (codex heartbeat lost)', summary['missed_cycles'])
        self.assertIn('CONTROLLER_RESUMED(resume)', summary['events'])
        self.assertIn(('senior_developer', 'DATA_PHASE_DUE'), roles_of(created))
        self.assertIn(('x_setup_researcher', 'RESEARCH_DUE'), roles_of(created))


if __name__ == '__main__':
    unittest.main()
