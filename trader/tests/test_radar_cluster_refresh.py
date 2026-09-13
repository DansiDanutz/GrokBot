"""The radar keeps its own advisory cluster file fresh.

A second scheduled job would annotate zeros forever on a machine where nobody
installed it, so the hourly radar rebuilds the file itself. Deriving clusters
sits on the entry path, so every failure here must be survivable.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from trader.radar import liquidity_levels as levels

NOW = 1789300000000
MIN = 60_000


def report(generated):
    return {'schema_version': 1, 'method': 'oi_delta_implied_v1',
            'generated_at_ms': generated, 'window_hours': 168,
            'symbols': {'DOTUSDTM': {'price': 1.0, 'clusters': [],
                                     'nearest_below': None, 'nearest_above': None,
                                     'total_usd': 0.0, 'snapshots_used': 9}}}


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = str(Path(self.dir.name) / 'clusters.json')

    def write(self, generated):
        Path(self.path).write_text(json.dumps(report(generated)))

    def test_a_fresh_file_is_left_alone(self):
        self.write(NOW - 10 * MIN)
        with patch('trader.data.liquidation_clusters.build') as build:
            levels.refresh(self.path, 'db.sqlite3', ['DOTUSDTM'], NOW)
        build.assert_not_called()

    def test_a_stale_file_is_rebuilt(self):
        self.write(NOW - 100 * MIN)
        with patch('trader.data.liquidation_clusters.build',
                   return_value=report(NOW)) as build:
            levels.refresh(self.path, 'db.sqlite3', ['DOTUSDTM'], NOW)
        build.assert_called_once()
        self.assertEqual(json.loads(Path(self.path).read_text())
                         ['generated_at_ms'], NOW)

    def test_a_missing_file_is_built(self):
        with patch('trader.data.liquidation_clusters.build',
                   return_value=report(NOW)) as build:
            levels.refresh(self.path, 'db.sqlite3', ['DOTUSDTM'], NOW)
        build.assert_called_once()
        self.assertTrue(Path(self.path).is_file())

    def test_a_file_from_the_future_is_rebuilt(self):
        self.write(NOW + 5 * MIN)
        with patch('trader.data.liquidation_clusters.build',
                   return_value=report(NOW)) as build:
            levels.refresh(self.path, 'db.sqlite3', ['DOTUSDTM'], NOW)
        build.assert_called_once()

    def test_a_build_failure_never_reaches_the_scan(self):
        with patch('trader.data.liquidation_clusters.build',
                   side_effect=RuntimeError('boom')):
            self.assertEqual(
                levels.refresh(self.path, 'db.sqlite3', ['DOTUSDTM'], NOW),
                self.path)
        self.assertFalse(Path(self.path).exists())

    def test_a_corrupt_existing_file_is_rebuilt_not_trusted(self):
        Path(self.path).write_text('{not json')
        with patch('trader.data.liquidation_clusters.build',
                   return_value=report(NOW)) as build:
            levels.refresh(self.path, 'db.sqlite3', ['DOTUSDTM'], NOW)
        build.assert_called_once()

    def test_no_symbols_means_no_work(self):
        with patch('trader.data.liquidation_clusters.build') as build:
            levels.refresh(self.path, 'db.sqlite3', [], NOW)
        build.assert_not_called()

    def test_symbols_are_deduplicated_and_capped(self):
        wanted = ['A%d' % i for i in range(80)] + ['A0']
        with patch('trader.data.liquidation_clusters.build',
                   return_value=report(NOW)) as build:
            levels.refresh(self.path, 'db.sqlite3', wanted, NOW, limit=60)
        asked = build.call_args[0][1]
        self.assertEqual(len(asked), 60)
        self.assertEqual(len(set(asked)), 60)


if __name__ == '__main__':
    unittest.main()
