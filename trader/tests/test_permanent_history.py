"""Permanent history must survive operational retention and checkpoint retries."""
from contextlib import closing
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from trader.autopilot import history_archive


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.state = dict(started_ms=100, equity_curve=[[101, 9999]], open_bots=[],
                          closed_bots=[dict(engine=dict(bot_id=1, closed_ms=102, reason='RANGE_BREAK'),
                                            decision_context={'direction': 'LONG'})],
                          pending_events=[dict(event_id=1, ts_ms=102, bot_id=1,
                                               symbol='TEST', type='CLOSE', price=10)])

    def rows(self, table):
        with closing(sqlite3.connect(self.root/'history.sqlite3')) as db:
            return db.execute('select * from '+table).fetchall()

    def test_retry_and_new_run_do_not_duplicate_or_overwrite_old_history(self):
        history_archive.checkpoint(self.root, self.state)
        history_archive.checkpoint(self.root, self.state)
        self.assertEqual(len(self.rows('equity')), 1)
        self.assertEqual(len(self.rows('positions')), 1)
        self.assertEqual(len(self.rows('events')), 1)
        self.state['started_ms'] = 200
        history_archive.checkpoint(self.root, self.state)
        self.assertEqual(len(self.rows('events')), 2)
        self.assertEqual((self.root/'history.sqlite3').stat().st_mode & 0o777, 0o600)

    def test_retained_record_is_full_wrapper_and_equity_survives_empty_window(self):
        history_archive.checkpoint(self.root, self.state)
        self.state.update(equity_curve=[], closed_bots=[], pending_events=[])
        history_archive.checkpoint(self.root, self.state)
        self.assertEqual(len(self.rows('equity')), 1)
        self.assertEqual(json.loads(self.rows('positions')[0][-1])['decision_context']['direction'], 'LONG')

    def test_invalid_checkpoint_rolls_back_all_rows(self):
        self.state['pending_events'][0]['price'] = float('nan')
        with self.assertRaises(ValueError):
            history_archive.checkpoint(self.root, self.state)
        self.assertEqual(self.rows('equity'), [])
        self.assertEqual(self.rows('positions'), [])

    def test_import_event_logs_is_idempotent_and_preserves_prestart_rows(self):
        event = dict(event_id=8, ts_ms=50, bot_id=2, symbol='TEST', type='GRID', profit=1)
        (self.root/'events-2026-09-01.jsonl').write_text(json.dumps(event)+'\n')
        history_archive.checkpoint(self.root, self.state, import_logs=True)
        history_archive.checkpoint(self.root, self.state, import_logs=True)
        self.assertEqual(len(self.rows('events')), 2)
        self.assertIn(0, [row[0] for row in self.rows('events')])

    def test_symlink_database_is_rejected(self):
        target = self.root/'other'; target.write_text('unchanged')
        (self.root/'history.sqlite3').symlink_to(target)
        with self.assertRaises(ValueError):
            history_archive.checkpoint(self.root, self.state)
        self.assertEqual(target.read_text(), 'unchanged')

    def test_unknown_runs_with_reused_event_ids_preserve_both_events(self):
        a = dict(event_id=1, ts_ms=30, bot_id=1, symbol='TEST', type='GRID', profit=1)
        b = dict(a, ts_ms=50)
        (self.root/'events.jsonl').write_text(json.dumps(a)+'\n'+json.dumps(b)+'\n')
        history_archive.checkpoint(self.root, self.state, import_logs=True)
        self.assertEqual(len(self.rows('events')), 3)

    def test_operational_log_can_expire_only_after_permanent_copy(self):
        from trader.autopilot.storage import EventLog
        log = EventLog(self.root)
        event = dict(event_id=10, ts_ms=1000, bot_id=1, symbol='TEST', type='GRID', profit=2)
        log.append([event])
        later = 40*24*3600000
        log.append([dict(event, event_id=11, ts_ms=later)], prune=False)
        self.assertTrue(list(self.root.glob('events-*.jsonl')))
        history_archive.checkpoint(self.root, self.state, import_logs=True)
        log.prune(later)
        self.assertFalse(list(self.root.glob('events-*.jsonl')))
        self.assertEqual(len(self.rows('events')), 3)
