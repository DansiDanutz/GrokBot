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
        hours_since_open=1.2,
        publisher_success_age_s=180,
        coinglass_status='pass', coinglass_symbols_ok=9, coinglass_symbols_requested=9,
        disk_free_bytes=21 * 1024 ** 3,
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
        got = run(open_bots=2, hours_since_open=7.0)
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

    def test_unknown_facts_never_read_as_healthy(self):
        """A missing reading is not a passing reading."""
        got = run(radar_generated_ms=None, publisher_success_age_s=None)
        self.assertNotEqual(got['radar']['status'], 'ok')
        self.assertNotEqual(got['publisher']['status'], 'ok')
