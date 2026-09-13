"""The daemon must alarm when it stops working, not only when it stops running.

On 2026-09-13 a range-assurance release made the radar reject all 356 symbols.
The autopilot kept ticking with KuCoin reachable, so it stayed "healthy" by its
own definition for seven hours while opening no bots at all. Nothing alerted.
"""
import tempfile
import unittest
from pathlib import Path

from trader.autopilot.constants import BLACKOUT_MIN_ROWS, ENTRY_STALL_ALERT_H
from trader.autopilot.runtime import Runner, guarded_paths
from trader.autopilot.storage import atomic_json, read_events
from trader.data.store import Store
from trader.tests.test_autopilot_policy import radar, row
from trader.tests.test_autopilot_runtime import Client, NOW

HOUR_MS = 3_600_000


def blackout_radar(count, asof):
    """A radar that admitted nothing: every row failed structure verification."""
    rows = [row('S%d' % i, 'NEUTRAL', range_verified=0) for i in range(count)]
    report = radar(neutral=[])
    report['rows'] = rows
    report.update(schema_version=1, asof_ms=asof)
    return report


class SilentFailureAlarmTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.database = self.root / 'market.sqlite3'
        with Store(self.database):
            pass
        self.clock = [NOW]
        self.client = Client(self.clock)
        self.radar = self.root / 'radar.json'
        self.state = self.root / 'autopilot' / 'state.json'
        self.snapshot = self.root / 'autopilot.json'

    def runner(self):
        return Runner(self.database, self.state, self.radar, self.snapshot,
                      client=self.client, now_ms=lambda: self.clock[0])

    def write(self, report):
        atomic_json(self.radar, report)

    def test_a_radar_that_verifies_nothing_raises_a_structure_blackout(self):
        self.write(blackout_radar(BLACKOUT_MIN_ROWS, NOW))
        view = self.runner().pass_once()
        self.assertEqual(view['radar_rows'], BLACKOUT_MIN_ROWS)
        self.assertEqual(view['radar_verified'], 0)
        self.assertTrue(view['structure_blackout'])
        types = {e['type'] for e in read_events(self.state.parent, 0)}
        self.assertIn('ALERT', types)

    def test_a_working_radar_raises_nothing(self):
        report = radar(neutral=[row('A', 'NEUTRAL')])
        report['rows'] = [row('S%d' % i, 'NEUTRAL') for i in range(BLACKOUT_MIN_ROWS)]
        report.update(schema_version=1, asof_ms=NOW)
        self.write(report)
        view = self.runner().pass_once()
        self.assertFalse(view['structure_blackout'])
        self.assertFalse(view['entries_stalled'])
        self.assertNotIn('ALERT', {e['type'] for e in read_events(self.state.parent, 0)})

    def test_a_short_radar_does_not_trip_the_blackout_guard(self):
        """A partial scan is not evidence of a broken gate."""
        self.write(blackout_radar(BLACKOUT_MIN_ROWS - 1, NOW))
        view = self.runner().pass_once()
        self.assertFalse(view['structure_blackout'])

    def test_free_slots_with_candidates_and_no_entry_raises_a_stall(self):
        report = radar(neutral=[row('A', 'NEUTRAL')])
        report.update(schema_version=1, asof_ms=NOW)
        self.write(report)
        runner = self.runner()
        runner.pass_once()
        self.assertEqual(len(runner.state['open_bots']), 1)
        # Time passes with a slot free and a candidate on the radar, yet nothing opens.
        self.clock[0] += int((ENTRY_STALL_ALERT_H + 1) * HOUR_MS)
        report.update(asof_ms=self.clock[0])
        self.write(report)
        view = runner.pass_once()
        self.assertGreater(view['free_slots'], 0)
        self.assertGreater(view['core_candidates'], 0)
        self.assertGreaterEqual(view['hours_since_open'], ENTRY_STALL_ALERT_H)
        self.assertTrue(view['entries_stalled'])

    def test_no_stall_alarm_while_the_radar_offers_nothing(self):
        """An empty market is a quiet day, not a fault: only a blackout is a fault."""
        report = radar(neutral=[row('A', 'NEUTRAL')])
        report.update(schema_version=1, asof_ms=NOW)
        self.write(report)
        runner = self.runner()
        runner.pass_once()
        empty = radar()
        empty['rows'] = [row('S%d' % i, 'NEUTRAL') for i in range(BLACKOUT_MIN_ROWS)]
        self.clock[0] += int((ENTRY_STALL_ALERT_H + 1) * HOUR_MS)
        empty.update(schema_version=1, asof_ms=self.clock[0])
        self.write(empty)
        view = runner.pass_once()
        self.assertEqual(view['core_candidates'], 0)
        self.assertFalse(view['entries_stalled'])
        self.assertFalse(view['structure_blackout'])


class AlarmMessageTests(unittest.TestCase):
    def message(self, **snapshot):
        from trader.autopilot import telegram
        base = dict(tick_age_s=0, kucoin_ok=True, kucoin_down_since_ms=None,
                    structure_blackout=False, entries_stalled=False,
                    radar_rows=0, radar_verified=0, core_candidates=0,
                    free_slots=0, hours_since_open=None,
                    open_bots=[], closed_bots=[], events=[], watchlist={})
        base.update(snapshot)
        queued, _ = telegram.notifications(base, [], NOW, {})
        return [m['text'] for m in queued]

    def test_blackout_message_names_the_radar_not_the_feed(self):
        texts = self.message(structure_blackout=True, radar_rows=356)
        self.assertTrue(any('structure' in t.lower() and '356' in t for t in texts),
                        'expected a blackout message naming the row count, got %r' % texts)

    def test_stall_message_names_the_idle_slots(self):
        texts = self.message(entries_stalled=True, free_slots=3,
                             core_candidates=12, hours_since_open=7.0)
        self.assertTrue(any('slot' in t.lower() for t in texts),
                        'expected a stall message naming free slots, got %r' % texts)
