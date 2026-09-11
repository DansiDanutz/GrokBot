"""Retained metric indexes use synthetic source data only."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from paper_grid import metric_evidence, retention
from paper_grid.test_trade_metrics import START, document, event

DAY = 86400


class MetricScalingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.directory = self.root / retention.DIRECTORY
        self.directory.mkdir()

    def archive(self, at, events=None, padding=0):
        day = retention._day(at)
        path = self.directory / (day + '.json')
        record = dict(time=at, market={}, coinglass={})
        path.write_text(json.dumps(dict(schema=1, day=day, events=events or [],
                                        observations=[record], errors=[],
                                        padding='x' * padding)))
        return path

    def test_400_days_exceed_old_aggregate_limit_but_warm_report_reads_one_day(self):
        files = [self.archive(START + n * DAY,
                              [event('open', n * DAY), event('close', n * DAY + 60)],
                              padding=525000) for n in range(400)]
        self.assertGreater(sum(p.stat().st_size for p in files), 200 * 1024 * 1024)
        end = START + 399 * DAY
        doc = document([])
        windows = [(end, end + 300)]
        with patch.object(metric_evidence, '_read_archive', wraps=metric_evidence._read_archive) as read:
            result = metric_evidence.load(self.root, doc, end + 300, windows=windows)
            self.assertLessEqual(read.call_count, 401)
        self.assertEqual([o['time'] for o in result['observations']], [end])
        self.assertEqual(len(result['events']), 800)
        with patch.object(metric_evidence, '_read_archive', wraps=metric_evidence._read_archive) as read:
            again = metric_evidence.load(self.root, doc, end + 300, windows=windows)
            self.assertEqual(read.call_count, 1)
        self.assertEqual(again, result)
        metric_evidence.load(self.root, doc, START + 500 * DAY,
                             windows=[(START + 499 * DAY, START + 500 * DAY)])

    def test_lifetime_events_and_selected_trade_open_are_kept(self):
        opening = event('open', 0)
        closing = event('close', 10 * DAY)
        self.archive(START, [opening])
        self.archive(START + 5 * DAY)
        self.archive(closing['time'], [closing])
        end = closing['time'] + 300
        result = metric_evidence.load(self.root, document([]), end,
                                      windows=[(closing['time'] - 1, end)])
        self.assertEqual(result['events'], [opening, closing])
        self.assertEqual(len(result['observations']), 3)
        result = metric_evidence.load(self.root, document([]), end + DAY,
                                      windows=[(end, end + DAY)])
        self.assertEqual(result['events'], [opening, closing])
        self.assertEqual(result['observations'], [])

    def test_changed_archive_revalidated_even_when_outside_window(self):
        path = self.archive(START, [event('open', 0)])
        end = START + 5 * DAY
        metric_evidence.load(self.root, document([]), end, windows=[(end - 300, end)])
        payload = json.loads(path.read_text())
        payload['schema'] = 999
        path.write_text(json.dumps(payload))
        with self.assertRaisesRegex(ValueError, 'schema/day'):
            metric_evidence.load(self.root, document([]), end, windows=[(end - 300, end)])

    def test_warm_cache_does_not_hide_archive_symlinks_or_conflicts(self):
        path = self.archive(START, [event('open', 0)])
        end = START + DAY
        metric_evidence.load(self.root, document([]), end, windows=[(end - 300, end)])
        with self.assertRaisesRegex(ValueError, 'conflicting metric events'):
            metric_evidence.load(self.root, document([event('open', 0, fee=.9)]), end,
                                 windows=[(end - 300, end)])
        actual = self.root / 'actual.json'
        path.rename(actual)
        path.symlink_to(actual)
        with self.assertRaisesRegex(ValueError, 'unsafe'):
            metric_evidence.load(self.root, document([]), end, windows=[(end - 300, end)])

    def test_cache_symlink_is_rejected(self):
        self.archive(START)
        (self.root / '.metric-event-index').symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'unsafe'):
            metric_evidence.load(self.root, document([]), START + 300,
                                 windows=[(START, START + 300)])

    def test_prior_equity_boundary_survives_long_observation_gap(self):
        path = self.archive(START)
        payload = json.loads(path.read_text())
        payload['observations'][0].update(telemetry_schema=1,
            equity={arm: dict(equity=999) for arm in ('baseline', 'liquidation_filter')})
        path.write_text(json.dumps(payload))
        end = START + 30 * DAY
        result = metric_evidence.load(self.root, document([]), end,
                                      windows=[(end - DAY, end)])
        self.assertEqual(result['observations'][0]['time'], START)
        with patch.object(metric_evidence, '_read_archive', wraps=metric_evidence._read_archive) as read:
            metric_evidence.load(self.root, document([]), end, windows=[(end - DAY, end)])
            self.assertEqual(read.call_count, 1)

    def test_same_size_rewrite_invalidates_event_index(self):
        import os
        path = self.archive(START, [event('open', 0)])
        end = START + DAY
        metric_evidence.load(self.root, document([]), end, windows=[(end - 300, end)])
        before = path.stat()
        path.write_text(path.read_text().replace('"fee": 0.06', '"fee": 0.09'))
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        result = metric_evidence.load(self.root, document([]), end,
                                      windows=[(end - 300, end)])
        self.assertEqual(result['events'][0]['fee'], .09)

    def test_changed_archive_conflicting_observations_still_fail(self):
        path = self.archive(START)
        end = START + DAY
        metric_evidence.load(self.root, document([]), end, windows=[(end - 300, end)])
        payload = json.loads(path.read_text())
        payload['observations'].append(dict(payload['observations'][0], skipped=True))
        path.write_text(json.dumps(payload))
        with self.assertRaisesRegex(ValueError, 'conflicting observations'):
            metric_evidence.load(self.root, document([]), end, windows=[(end - 300, end)])

    def test_cache_file_symlink_and_corrupted_schema_rejected(self):
        path = self.archive(START)
        end = START + DAY
        metric_evidence.load(self.root, document([]), end, windows=[(end - 300, end)])
        cached = self.root / '.metric-event-index' / path.name
        actual = self.root / 'index.json'
        cached.rename(actual)
        cached.symlink_to(actual)
        with self.assertRaisesRegex(ValueError, 'index file is unsafe'):
            metric_evidence.load(self.root, document([]), end, windows=[(end - 300, end)])
        cached.unlink()
        actual.rename(cached)
        payload = json.loads(cached.read_text())
        payload['schema'] = 7
        cached.write_text(json.dumps(payload))
        with self.assertRaisesRegex(ValueError, 'index schema'):
            metric_evidence.load(self.root, document([]), end, windows=[(end - 300, end)])


if __name__ == '__main__':
    unittest.main()
