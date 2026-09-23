"""Regression cases from the September 23 native-team recovery audit."""
import unittest

from trader.team import controller
from trader.doctor import __main__ as doctor_cli, checks
from trader.tests.test_team_controller import NOW, HOUR_MS, facts, quiet_state


def research_due():
    state = quiet_state()
    state['markers']['phase_dates'].pop('research')
    return state


def receipt(row, now=NOW + 60_000):
    return {'answered_at': controller._iso(now), 'answered_at_ms': now}


class PhaseRecoveryTests(unittest.TestCase):
    def test_transient_failure_survives_full_cap_until_capacity_returns(self):
        state = quiet_state()
        state['dispatches'] = [dict(controller._dispatch('BOT_OPENED',
            'technical_interpreter', {}, NOW, n), key=str(n)) for n in range(12)]
        bad = facts(doctor={'status': 'fail', 'failing': ['radar'], 'checks': []})
        state, created, _ = controller.cycle(state, bad, NOW)
        self.assertEqual(created, [])
        self.assertTrue(any(e['name'] == 'DOCTOR_FAIL' for e in state['deferred_events']))
        receipts = {r['dispatch_id']: receipt(r) for r in state['dispatches']}
        state, created, _ = controller.cycle(state, facts(receipts=receipts), NOW + HOUR_MS)
        self.assertEqual({r['role'] for r in created if r['event'] == 'DOCTOR_FAIL'},
                         {'risk_sentinel', 'senior_developer'})
        self.assertFalse(any(e['name'] == 'DOCTOR_FAIL' for e in state['deferred_events']))

    def test_bot_event_does_not_disappear_after_source_cursor_advances(self):
        state = quiet_state()
        state['dispatches'] = [dict(controller._dispatch('BOT_OPENED',
            'technical_interpreter', {}, NOW, n), key=str(n)) for n in range(12)]
        new_close = dict(event_id=99, type='CLOSE', bot_id=88, symbol='FIXTURE')
        state, _, _ = controller.cycle(state, facts(events=[new_close]), NOW)
        self.assertEqual(state['markers']['event_id'], 99)
        receipts = {r['dispatch_id']: receipt(r) for r in state['dispatches']}
        _, created, _ = controller.cycle(state, facts(receipts=receipts), NOW + HOUR_MS)
        self.assertTrue(any(r['event'] == 'BOT_CLOSED' and r['payload']['bot_id'] == 88
                            for r in created))

    def test_no_eligible_engineering_creates_no_task_and_remains_stable(self):
        state = quiet_state()
        state['markers']['phase_dates'].pop('engineering')
        state, created, summary = controller.cycle(state, facts(), NOW)
        self.assertFalse(any(r['event'] == 'ENGINEERING_DUE' for r in created))
        self.assertEqual(summary['phases']['engineering']['status'], 'NO_ELIGIBLE_NEED')
        _, _, summary = controller.cycle(state, facts(), NOW + HOUR_MS)
        self.assertEqual(summary['phases']['engineering']['status'], 'NO_ELIGIBLE_NEED')

    def test_full_cap_does_not_consume_research_and_retries_when_space_returns(self):
        state = research_due()
        state['dispatches'] = [dict(controller._dispatch('BOT_OPENED',
            'technical_interpreter', {}, NOW, n), key=str(n)) for n in range(12)]
        after, created, summary = controller.cycle(state, facts(), NOW)
        self.assertEqual(created, [])
        self.assertEqual(summary['open'], 12)
        self.assertEqual(summary['phases']['research']['status'], 'PENDING')
        self.assertNotIn('research', after['markers']['phase_dates'])
        receipts = {r['dispatch_id']: receipt(r) for r in after['dispatches']}
        after, created, summary = controller.cycle(after, facts(receipts=receipts), NOW + HOUR_MS)
        research = [r for r in created if r['event'] == 'RESEARCH_DUE']
        self.assertEqual({r['role'] for r in research}, {'research_scout', 'x_setup_researcher'})
        self.assertEqual(summary['phases']['research']['dispatch_ids'],
                         [r['dispatch_id'] for r in research])

    def test_phase_requires_both_research_answers(self):
        state, created, _ = controller.cycle(research_due(), facts(), NOW)
        research = [r for r in created if r['event'] == 'RESEARCH_DUE']
        receipts = {research[0]['dispatch_id']: receipt(research[0])}
        state, _, summary = controller.cycle(state, facts(receipts=receipts), NOW + HOUR_MS)
        self.assertEqual(summary['phases']['research']['status'], 'RUNNING')
        receipts[research[1]['dispatch_id']] = receipt(research[1], NOW + HOUR_MS)
        state, _, summary = controller.cycle(state, facts(receipts=receipts), NOW + HOUR_MS + 60_000)
        self.assertEqual(summary['phases']['research']['status'], 'COMPLETED')
        self.assertEqual(summary['phases']['research']['attempt_id'], '2026-09-13:research')

    def test_blocked_required_dispatch_blocks_its_phase(self):
        state, created, _ = controller.cycle(research_due(), facts(), NOW)
        research = [r for r in created if r['event'] == 'RESEARCH_DUE']
        # Still the same local date, just beyond the three-hour deadline.
        late = NOW + 3 * HOUR_MS + 60_000
        state, _, summary = controller.cycle(state, facts(), late)
        self.assertEqual(summary['phases']['research']['status'], 'BLOCKED')
        self.assertEqual(summary['phases']['research']['dispatch_ids'],
                         [r['dispatch_id'] for r in research])

    def test_legacy_running_without_dispatch_retries_instead_of_hanging(self):
        state = quiet_state(daily_phase_outcomes={'2026-09-13': {
            'research': dict(status='RUNNING', started_at=controller._iso(NOW - HOUR_MS))}})
        after, created, summary = controller.cycle(state, facts(), NOW)
        self.assertEqual(len([r for r in created if r['event'] == 'RESEARCH_DUE']), 2)
        self.assertEqual(summary['phases']['research']['status'], 'RUNNING')
        self.assertEqual(len(summary['phases']['research']['dispatch_ids']), 2)

    def test_legacy_phase_correlates_existing_blocked_dispatch_without_replay(self):
        row = dict(controller._dispatch('DATA_PHASE_DUE', 'senior_developer', {}, NOW, 1),
                   key='2026-09-13:data', status='BLOCKED')
        state = quiet_state(dispatches=[row], daily_phase_outcomes={'2026-09-13': {
            'data': {'status': 'RUNNING', 'request_id': None}}})
        after, created, summary = controller.cycle(state, facts(), NOW)
        self.assertEqual(summary['phases']['data']['status'], 'BLOCKED')
        self.assertEqual(summary['phases']['data']['dispatch_ids'], [row['dispatch_id']])
        self.assertFalse(any(r['event'] == 'DATA_PHASE_DUE' for r in created))

    def test_research_routing_is_atomic_when_only_one_slot_is_available(self):
        fired = [controller.events.event('RESEARCH_DUE', '2026-09-13:research')]
        self.assertEqual(controller._route(fired, set(), NOW, 1), [])


class HistoryAndIdleTests(unittest.TestCase):
    def test_doctor_current_block_state_comes_from_roster_not_history(self):
        board = {'last_cycle_at_ms': NOW, 'roster': [{'name': 'Risk Sentinel', 'status': 'OK'}],
                 'dispatches': [{'role_name': 'Risk Sentinel', 'status': 'BLOCKED'}]}
        state = doctor_cli._team(board, NOW)
        self.assertEqual(state['team_blocked'], [])
        self.assertEqual(checks._team(state)['status'], 'ok')

    def test_doctor_warns_about_installed_unobserved_roles(self):
        board = {'last_cycle_at_ms': NOW, 'roster': [
            {'name': 'Risk Sentinel', 'status': 'UNOBSERVED'}]}
        result = checks._team(doctor_cli._team(board, NOW))
        self.assertEqual(result['status'], 'warn')
        self.assertIn('unobserved: Risk Sentinel', result['detail'])

    def test_doctor_preserves_legacy_block_when_no_roster_exists(self):
        board = {'last_cycle_at_ms': NOW,
                 'dispatches': [{'role_name': 'Risk Sentinel', 'status': 'BLOCKED'}]}
        self.assertEqual(doctor_cli._team(board, NOW)['team_blocked'], ['Risk Sentinel'])

    def test_retention_keeps_recent_settled_rows_even_when_history_is_unsorted(self):
        recent = [dict(dispatch_id='new-%02d' % n, status='DONE',
                       created_at_ms=NOW + n) for n in range(60)]
        old = [dict(dispatch_id='old', status='BLOCKED', created_at_ms=1)]
        pending = dict(dispatch_id='pending', status='PENDING', created_at_ms=0)
        kept = controller._keep([pending] + recent + old)
        self.assertEqual(len(kept), 61)
        self.assertIn(pending, kept)
        self.assertNotIn(old[0], kept)

    def test_new_actual_role_answer_does_not_erase_historical_failure(self):
        row = dict(controller._dispatch('BOT_OPENED', 'technical_interpreter', {},
                                         NOW - 5 * HOUR_MS, 1), status='BLOCKED',
                   blocked_at=controller._iso(NOW - 2 * HOUR_MS))
        state, _, summary = controller.cycle(quiet_state(dispatches=[row]), facts(), NOW)
        role = next(r for r in summary['roster'] if r['role'] == 'technical_interpreter')
        self.assertEqual(role['status'], 'OK')
        self.assertEqual(next(r for r in state['dispatches']
                              if r['dispatch_id'] == row['dispatch_id'])['status'], 'BLOCKED')

    def test_installed_but_never_observed_is_not_reported_ok(self):
        _, _, summary = controller.cycle(quiet_state(), facts(role_seen_ms={}), NOW)
        role = next(r for r in summary['roster'] if r['role'] == 'risk_sentinel')
        self.assertEqual(role['status'], 'UNOBSERVED')

    def test_idle_acknowledgement_does_not_create_an_hourly_alert_loop(self):
        given = facts(role_seen_ms=dict(facts()['role_seen_ms'],
                                        senior_developer=NOW - 9 * HOUR_MS))
        state, created, _ = controller.cycle(quiet_state(), given, NOW)
        receipts = {r['dispatch_id']: receipt(r) for r in created}
        state, created, _ = controller.cycle(state, dict(given, receipts=receipts), NOW + HOUR_MS)
        self.assertFalse(any(r['event'] == 'ROLE_IDLE' and r.get('key') == 'senior_developer'
                             for r in created))


if __name__ == '__main__':
    unittest.main()
