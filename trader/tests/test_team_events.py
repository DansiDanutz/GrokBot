"""An event fires once. A fault that lasts nine hours is one dispatch, not nine.

The markers a cycle persists are the whole mechanism, so every test here runs
two cycles: the first must speak, the second must stay silent.
"""
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from trader.team import events

ZONE = ZoneInfo('Europe/Bucharest')


def at(text):
    return int(datetime.fromisoformat(text).replace(tzinfo=ZONE).timestamp() * 1000)


NOW = at('2026-09-13T20:45:00')


def facts(**over):
    base = dict(now_ms=NOW, doctor=dict(status='ok', failing=[], checks=[]),
                watchlist_scan_id=100, core_candidates=4, entries_stalled=False,
                structure_blackout=False, open_directions={7: 'LONG'}, events=[],
                review=dict(headline='', applied=0, last_run_at_ms=None),
                counterfactual_date=None, liq_clusters_ms=None,
                pending_requests=[], role_idle_h={},
                local_date='2026-09-13', local_minutes=20 * 60 + 45)
    base.update(over)
    return base


def names(fired):
    return [item['name'] for item in fired]


def quiet(**over):
    """Markers of a cycle that has already seen everything in facts()."""
    base = dict(cycle_at_ms=NOW, doctor_failing=[], scan_id=100, candidates=4,
                event_id=4, entries_stalled=False, structure_blackout=False,
                review_run_ms=None, counterfactual_date=None, liq_clusters_ms=None,
                phase_dates=dict(data='2026-09-13', research='2026-09-13',
                                 engineering='2026-09-13'))
    base.update(over)
    return base


def twice(over, markers=None):
    """Derive, persist the markers, derive again with the same facts."""
    given, markers = facts(**over), quiet() if markers is None else markers
    first = events.derive(given, markers)
    after = events.advance(markers, given, first)
    return names(first), names(events.derive(given, after))


class EventTests(unittest.TestCase):
    def test_a_new_doctor_failure_speaks_once(self):
        first, second = twice(dict(doctor=dict(
            status='fail', failing=['radar'],
            checks=[dict(name='radar', status='fail', detail='stale',
                         where='radar did not fire')])))
        self.assertEqual(first, ['DOCTOR_FAIL'])
        self.assertEqual(second, [])

    def test_recovery_speaks_once_after_a_failure(self):
        fired = events.derive(facts(), quiet(doctor_failing=['radar']))
        self.assertEqual(names(fired), ['DOCTOR_RECOVERED'])

    def test_a_second_failing_check_joining_is_its_own_event(self):
        fired = events.derive(facts(doctor=dict(
            status='fail', failing=['radar', 'disk'], checks=[])),
            quiet(doctor_failing=['radar']))
        self.assertEqual(names(fired), ['DOCTOR_FAIL'])
        self.assertEqual(fired[0]['payload']['check'], 'disk')

    def test_a_radar_scan_fires_once_per_scan_id(self):
        first, second = twice(dict(watchlist_scan_id=101),
                              quiet())
        self.assertEqual(first, ['RADAR_SCAN'])
        self.assertEqual(second, [])

    def test_a_scan_reports_whether_its_candidate_count_moved(self):
        fired = events.derive(facts(watchlist_scan_id=101, core_candidates=4),
                              quiet())
        self.assertFalse(fired[0]['payload']['candidates_changed'])
        moved = events.derive(facts(watchlist_scan_id=101, core_candidates=9),
                              quiet())
        self.assertTrue(moved[0]['payload']['candidates_changed'])

    def test_opens_and_closes_come_from_the_event_log_cursor(self):
        rows = [dict(event_id=5, type='OPEN', bot_id=7, symbol='BTCUSDTM'),
                dict(event_id=6, type='CLOSE', bot_id=7, symbol='BTCUSDTM', reason_code=1),
                dict(event_id=4, type='OPEN', bot_id=3, symbol='ETHUSDTM')]
        fired = events.derive(facts(events=rows), quiet(event_id=4))
        self.assertEqual(names(fired), ['BOT_OPENED', 'BOT_CLOSED'])
        self.assertEqual(fired[0]['payload']['direction'], 'LONG')
        self.assertEqual(fired[1]['payload']['reason'], 'RANGE_BREAK')

    def test_an_unseen_direction_is_unknown_not_invented(self):
        fired = events.derive(facts(events=[dict(event_id=9, type='OPEN', bot_id=42,
                                                 symbol='SOLUSDTM')]),
                              quiet(event_id=8))
        self.assertEqual(fired[0]['payload']['direction'], 'UNKNOWN')

    def test_a_stall_flag_is_edge_triggered(self):
        first, second = twice(dict(entries_stalled=True))
        self.assertEqual(first, ['ENTRIES_STALLED'])
        self.assertEqual(second, [])

    def test_a_blackout_is_edge_triggered(self):
        first, second = twice(dict(structure_blackout=True))
        self.assertEqual(first, ['STRUCTURE_BLACKOUT'])
        self.assertEqual(second, [])

    def test_a_deferred_review_fires_once_per_run(self):
        first, second = twice(dict(review=dict(headline='deferred: 4 opens < 5',
                                               applied=0, last_run_at_ms=11)))
        self.assertEqual(first, ['LEARNER_DEFERRED'])
        self.assertEqual(second, [])

    def test_an_applied_rule_is_a_different_event(self):
        fired = events.derive(facts(review=dict(headline='applied', applied=1,
                                                last_run_at_ms=11)),
                              quiet())
        self.assertEqual(names(fired), ['LEARNER_APPLIED'])

    def test_new_artifacts_announce_themselves_once(self):
        first, second = twice(dict(counterfactual_date='2026-09-12',
                                   liq_clusters_ms=NOW - 1000))
        self.assertEqual(first, ['COUNTERFACTUAL_READY', 'LIQ_CLUSTERS_READY'])
        self.assertEqual(second, [])

    def test_phases_are_due_in_order_and_only_once_a_day(self):
        first, second = twice({}, quiet(phase_dates={}))
        self.assertEqual(first, ['DATA_PHASE_DUE', 'RESEARCH_DUE', 'ENGINEERING_DUE'])
        self.assertEqual(second, [])

    def test_a_phase_is_not_due_before_its_local_time(self):
        fired = events.derive(facts(local_minutes=8 * 60),
                              quiet(phase_dates={}))
        self.assertEqual(names(fired), [])

    def test_a_new_local_date_makes_the_phases_due_again(self):
        markers = quiet(phase_dates={})
        after = events.advance(markers, facts(), events.derive(facts(), markers))
        tomorrow = facts(local_date='2026-09-14')
        self.assertIn('DATA_PHASE_DUE', names(events.derive(tomorrow, after)))

    def test_a_role_past_its_own_idle_budget_is_reported(self):
        fired = events.derive(facts(role_idle_h=dict(senior_developer=7.5,
                                                     risk_sentinel=3.0)),
                              quiet())
        self.assertEqual(names(fired), ['ROLE_IDLE'])
        self.assertEqual(fired[0]['payload']['role'], "Dan's Senior Developer")

    def test_an_uninstalled_role_is_never_called_idle(self):
        fired = events.derive(facts(role_idle_h=dict(discovery_auditor=99.0)),
                              quiet())
        self.assertEqual(names(fired), [])

    def test_a_gap_longer_than_two_hours_is_a_resume(self):
        fired = events.derive(facts(), quiet(cycle_at_ms=NOW - 9 * 3_600_000))
        self.assertEqual(names(fired)[-1], 'CONTROLLER_RESUMED')
        self.assertEqual(fired[-1]['payload']['gap_h'], 9.0)

    def test_an_hourly_cadence_is_not_a_resume(self):
        self.assertNotIn('CONTROLLER_RESUMED',
                         names(events.derive(facts(), quiet(cycle_at_ms=NOW - 3_600_000))))


if __name__ == '__main__':
    unittest.main()
