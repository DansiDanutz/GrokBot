"""One place that knows whether the whole circle is working.

Every failure on 2026-09-13 was silent because each component was healthy by
its own definition and nothing owned the gaps between them: the radar asked for
an hour the collector had not written, CoinGlass reported a full disk as a
missing key, and backup verification died unread. These checks look at outcomes
across components, not at whether a process is alive.
"""
import unittest

from trader.doctor import checks

HOUR_MS = 3_600_000
NOW = 1789300000000


def facts(**over):
    """A healthy circle; each test breaks exactly one thing."""
    base = dict(
        now_ms=NOW,
        hourly_committed_ms=(NOW // HOUR_MS) * HOUR_MS - HOUR_MS,
        minute_age_s=45,
        radar_generated_ms=NOW - 20 * 60_000,
        radar_rows=356, radar_verified=224, core_candidates=11,
        autopilot_tick_age_s=3, open_bots=5, max_bots=5,
        hours_since_open=1.2, vacancy_age_h=0.2,
        publisher_success_age_s=180,
        coinglass_status='pass', coinglass_symbols_ok=9, coinglass_symbols_requested=9,
        disk_free_bytes=21 * 1024 ** 3,
        team_cycle_age_s=600, team_idle_roles=[], team_blocked=[],
    )
    base.update(over)
    return base


def run(**over):
    return {c['name']: c for c in checks.assess(facts(**over))['checks']}


class DoctorTests(unittest.TestCase):
    def test_a_healthy_circle_reports_ok_everywhere(self):
        report = checks.assess(facts())
        self.assertEqual(report['status'], 'ok')
        self.assertEqual([c['name'] for c in report['checks'] if c['status'] != 'ok'], [])
        self.assertEqual(report['exit_code'], 0)

    def test_collector_that_missed_the_last_closed_hour_is_caught(self):
        got = run(hourly_committed_ms=(NOW // HOUR_MS) * HOUR_MS - 4 * HOUR_MS)
        self.assertEqual(got['collector']['status'], 'fail')
        self.assertIn('hour', got['collector']['detail'].lower())

    def test_a_radar_that_verified_nothing_is_a_plumbing_fault(self):
        """The 13 Sept freeze: 356 rows scanned, 0 admitted, no error anywhere."""
        got = run(radar_verified=0, core_candidates=0)
        self.assertEqual(got['radar']['status'], 'fail')
        self.assertIn('0 of 356', got['radar']['detail'])

    def test_an_empty_market_is_not_a_fault(self):
        """No candidates while structure still verifies is a quiet day."""
        got = run(core_candidates=0)
        self.assertEqual(got['radar']['status'], 'ok')

    def test_a_stale_radar_is_caught_even_if_its_last_run_was_good(self):
        got = run(radar_generated_ms=NOW - 3 * HOUR_MS)
        self.assertEqual(got['radar']['status'], 'fail')

    def test_free_slots_with_candidates_and_no_entry_is_a_stall(self):
        # The vacancy age is the signal; the last-entry time alone would fail a
        # desk that simply ran full and has only just closed something.
        got = run(open_bots=2, hours_since_open=7.0, vacancy_age_h=7.0)
        self.assertEqual(got['autopilot']['status'], 'fail')
        self.assertIn('slot', got['autopilot']['detail'].lower())

    def test_full_slots_are_never_a_stall(self):
        got = run(hours_since_open=40.0)
        self.assertEqual(got['autopilot']['status'], 'ok')

    def test_a_frozen_tick_is_caught(self):
        got = run(autopilot_tick_age_s=400)
        self.assertEqual(got['autopilot']['status'], 'fail')

    def test_a_publisher_that_stopped_deploying_is_caught(self):
        got = run(publisher_success_age_s=3000)
        self.assertEqual(got['publisher']['status'], 'warn')

    def test_disk_below_the_collectors_own_reserve_is_caught(self):
        """Below MIN_FREE_BYTES the collector refuses to write and backups stop."""
        got = run(disk_free_bytes=2 * 1024 ** 3)
        self.assertEqual(got['disk']['status'], 'fail')
        self.assertIn('GiB', got['disk']['detail'])

    def test_partial_coinglass_warns_but_does_not_fail_the_circle(self):
        got = run(coinglass_symbols_ok=8)
        self.assertEqual(got['coinglass']['status'], 'warn')

    def test_worst_check_sets_the_overall_status_and_exit_code(self):
        self.assertEqual(checks.assess(facts(coinglass_symbols_ok=8))['status'], 'warn')
        self.assertEqual(checks.assess(facts(coinglass_symbols_ok=8))['exit_code'], 0)
        bad = checks.assess(facts(disk_free_bytes=1024))
        self.assertEqual(bad['status'], 'fail')
        self.assertEqual(bad['exit_code'], 1)

    def test_every_failing_check_says_where_to_look(self):
        """A doctor that cannot point at the fix is only an alarm."""
        for over in (dict(radar_verified=0, core_candidates=0),
                     dict(disk_free_bytes=1024),
                     dict(open_bots=2, hours_since_open=9.0),
                     dict(hourly_committed_ms=NOW - 5 * HOUR_MS)):
            for check in checks.assess(facts(**over))['checks']:
                if check['status'] == 'fail':
                    self.assertTrue(check['where'], 'no remedy pointer on %s' % check['name'])

    def test_a_team_controller_that_stopped_cycling_is_a_failure(self):
        """The 13 Sept stall: the controller died and no bot was ever dispatched."""
        got = run(team_cycle_age_s=9 * 3600)
        self.assertEqual(got['team']['status'], 'fail')
        self.assertIn('9.0 hours', got['team']['detail'])
        self.assertIn('stalled', got['team']['where'])

    def test_no_controller_reading_is_unknown_not_healthy(self):
        got = run(team_cycle_age_s=None)
        self.assertEqual(got['team']['status'], 'unknown')

    def test_an_idle_role_warns_and_names_the_role_and_its_room(self):
        got = run(team_idle_roles=['Risk Sentinel'])
        self.assertEqual(got['team']['status'], 'warn')
        self.assertIn('Risk Sentinel', got['team']['detail'])
        self.assertIn('dispatch.json', got['team']['where'])
        self.assertIn('room', got['team']['where'])

    def test_a_blocked_dispatch_warns_without_failing_the_circle(self):
        report = checks.assess(facts(team_blocked=['Performance Analyst']))
        got = {c['name']: c for c in report['checks']}
        self.assertEqual(got['team']['status'], 'warn')
        self.assertIn('Performance Analyst', got['team']['detail'])
        self.assertEqual(report['exit_code'], 0)

    def test_a_fresh_cycle_with_every_role_answering_is_ok(self):
        self.assertEqual(run()['team']['status'], 'ok')

    def test_unknown_facts_never_read_as_healthy(self):
        """A missing reading is not a passing reading."""
        got = run(radar_generated_ms=None, publisher_success_age_s=None)
        self.assertNotEqual(got['radar']['status'], 'ok')
        self.assertNotEqual(got['publisher']['status'], 'ok')


class VacancyTests(unittest.TestCase):
    """A free slot is only a stall once it has been free for a while.

    The first live FAIL of this check was a false alarm: CAKEUSDTM closed at
    21:59, the doctor ran at 22:01, and the desk was judged on a last entry
    4.6h old. The decision pass had not even come round yet.
    """

    def test_a_slot_that_just_freed_is_not_a_stall(self):
        report = run(open_bots=4, hours_since_open=4.6, vacancy_age_h=0.03)
        self.assertEqual(report['autopilot']['status'], 'ok')

    def test_a_slot_empty_past_the_threshold_is_a_stall(self):
        report = run(open_bots=4, hours_since_open=9.0, vacancy_age_h=4.0)
        self.assertEqual(report['autopilot']['status'], 'fail')
        self.assertIn('4.0h', report['autopilot']['detail'])

    def test_an_empty_slot_with_no_candidates_is_not_a_stall(self):
        report = run(open_bots=4, core_candidates=0, vacancy_age_h=9.0)
        self.assertEqual(report['autopilot']['status'], 'ok')

    def test_a_full_desk_is_never_a_stall(self):
        report = run(open_bots=5, hours_since_open=40.0, vacancy_age_h=40.0)
        self.assertEqual(report['autopilot']['status'], 'ok')

    def test_an_unknown_vacancy_age_falls_back_to_the_last_entry(self):
        """A daemon that has never closed anything still reports a real stall."""
        report = run(open_bots=4, hours_since_open=6.0, vacancy_age_h=None)
        self.assertEqual(report['autopilot']['status'], 'fail')
