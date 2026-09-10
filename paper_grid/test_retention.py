"""Cold-history rollover, replay and accounting preservation, without network."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from paper_grid import cli, retention


def timestamp(day, seconds=0):
    return datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp() + seconds


class RetentionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.now = timestamp('2026-09-11', 3600)
        self.old = timestamp('2026-09-07', 86399)
        self.cutoff = timestamp('2026-09-08')
        event = dict(time=self.old, type='close', symbol='AAA', net_pnl=-2)
        self.doc = dict(
            observations=[dict(time=self.old, market={'AAA': {'bid': 7}}),
                          dict(time=self.cutoff, market={}), dict(time=self.now, market={})],
            events=[dict(event, account='baseline')],
            errors=[dict(time=self.old, type='source', details={'reason': 'stale'})],
            history=[dict(time=self.old, baseline_equity=998)],
            accounts={'baseline': dict(events=[event],
                state=dict(cash=998, positions={'BBB': {'contracts': 2}}),
                statistics=dict(trade_count=1, total_fees=.4))})

    def archive(self, doc=None, now=None):
        return retention.archive_history(self.root, self.doc if doc is None else doc,
                                         self.now if now is None else now)

    def rows(self, start=0, end=None):
        return retention.read_archives(self.root, start, self.now if end is None else end)

    def test_keeps_three_complete_days_plus_today_and_all_accounting(self):
        before = deepcopy(self.doc)
        result = self.archive()
        self.assertEqual(self.doc, before)
        self.assertEqual([row['time'] for row in result['observations']], [self.cutoff, self.now])
        self.assertEqual(result['history'], before['history'])
        for key in ('state', 'statistics'):
            self.assertEqual(result['accounts']['baseline'][key], before['accounts']['baseline'][key])
        self.assertEqual(result['accounts']['baseline']['events'], [])
        self.assertEqual(self.rows()['events'], before['events'])
        self.assertEqual(self.rows()['errors'], before['errors'])
        self.assertEqual(result['archive_files'], ['2026-09-07.json'])
        directory = self.root / retention.DIRECTORY
        self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual((directory / result['archive_files'][0]).stat().st_mode & 0o777, 0o600)

    def test_midnight_rollover_archives_newly_completed_cutoff_day(self):
        first = self.archive()
        second = self.archive(first, timestamp('2026-09-12'))
        self.assertEqual([row['time'] for row in second['observations']], [self.now])
        self.assertEqual(second['archive_files'], ['2026-09-07.json', '2026-09-08.json'])
        self.assertEqual(len(self.rows()['observations']), 2)

    def test_replay_before_or_after_hot_commit_is_idempotent(self):
        first = self.archive()
        archive_path = self.root / retention.DIRECTORY / first['archive_files'][0]
        original_bytes = archive_path.read_bytes()
        self.assertEqual(self.archive(), first)  # crash before hot commit
        self.assertEqual(self.archive(first), first)  # successful hot commit
        self.assertEqual(archive_path.read_bytes(), original_bytes)
        self.assertEqual(len(self.rows()['events']), 1)

    def test_merge_preserves_distinct_events_at_identical_time(self):
        self.archive()
        updated = deepcopy(self.doc)
        updated['events'].append(dict(updated['events'][0], net_pnl=-3))
        self.archive(updated)
        self.assertEqual(len(self.rows()['events']), 2)

    def test_observation_conflict_refuses_and_preserves_archive_and_hot_doc(self):
        first = self.archive()
        path = self.root / retention.DIRECTORY / first['archive_files'][0]
        original_bytes = path.read_bytes()
        changed = deepcopy(self.doc)
        changed['observations'][0]['market'] = {'changed': True}
        before = deepcopy(changed)
        with self.assertRaisesRegex(ValueError, 'conflicting observations'):
            self.archive(changed)
        self.assertEqual(changed, before)
        self.assertEqual(path.read_bytes(), original_bytes)

    def test_archive_failure_never_mutates_original_document(self):
        before = deepcopy(self.doc)
        with patch.object(cli, 'atomic_json', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.archive()
        self.assertEqual(self.doc, before)
        self.assertEqual(len(self.archive()['archive_files']), 1)

    def test_period_read_is_start_exclusive_and_end_inclusive(self):
        self.archive()
        self.assertEqual(self.rows(self.old, self.now)['observations'], [])
        self.assertEqual(len(self.rows(self.old - 1, self.old)['observations']), 1)
        self.assertEqual(self.rows(self.old, self.old), dict(observations=[], events=[], errors=[]))

    def test_archives_inherited_account_events_even_if_not_top_level(self):
        self.doc['events'] = []
        self.archive()
        self.assertEqual(self.rows()['events'][0]['account'], 'baseline')
        self.assertEqual(self.rows()['events'][0]['net_pnl'], -2)

    def test_rejects_invalid_records_without_pruning_anything(self):
        for invalid in (None, True, float('nan'), -1):
            with self.subTest(timestamp=invalid):
                doc = deepcopy(self.doc)
                doc['errors'][0]['time'] = invalid
                with self.assertRaises(ValueError):
                    self.archive(doc)
        self.assertFalse((self.root / retention.DIRECTORY).exists())

    def test_rejects_unsafe_manifest_and_symlink_archives(self):
        doc = deepcopy(self.doc)
        doc['archive_files'] = ['../account.json']
        with self.assertRaises(ValueError):
            self.archive(doc)
        directory = self.root / retention.DIRECTORY
        directory.mkdir()
        target = self.root / 'secret.json'
        target.write_text('{}')
        (directory / '2026-09-07.json').symlink_to(target)
        with self.assertRaises(ValueError):
            self.archive()
        with self.assertRaises(ValueError):
            self.rows()
        self.assertEqual(target.read_text(), '{}')

    def test_read_rejects_wrong_day_and_corrupt_json(self):
        self.archive()
        path = self.root / retention.DIRECTORY / '2026-09-07.json'
        data = json.loads(path.read_text())
        data['observations'][0]['time'] = self.now
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'wrong UTC day'):
            self.rows()
        path.write_text('{broken')
        with self.assertRaises(ValueError):
            self.rows()

    def test_no_old_records_leaves_recent_history_unchanged(self):
        doc = deepcopy(self.doc)
        for kind in retention.RECORDS:
            doc[kind] = [dict(time=self.cutoff, payload=kind)]
        doc['accounts']['baseline']['events'] = [dict(time=self.now, type='open')]
        result = self.archive(doc)
        result.pop('archive_files')
        self.assertEqual(result, doc)
        self.assertFalse((self.root / retention.DIRECTORY).exists())


if __name__ == '__main__':
    unittest.main()
