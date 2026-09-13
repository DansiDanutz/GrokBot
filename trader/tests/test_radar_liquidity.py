"""The liquidation-cluster annotation is additive, advisory and fail-open."""
import json
from pathlib import Path
import tempfile
import unittest

from trader.radar.cli import main
from trader.radar.liquidity_levels import FIELDS, MAX_AGE_MS, load
from trader.radar.radar import analyse
from trader.tests.test_radar import NOW, fixture

SYMBOL = 'XBTUSDTM'
BELOW = dict(price=120.0, side='long', usd=250000.0, distance_pct=2.5)
ABOVE = dict(price=135.0, side='short', usd=90000.0, distance_pct=4.75)


def clusters(generated_at_ms, symbols=None):
    return dict(schema_version=1, method='oi_delta_implied_v1',
                generated_at_ms=generated_at_ms, window_hours=168,
                symbols=symbols if symbols is not None else {SYMBOL: dict(
                    price=128.0, total_usd=1e6, snapshots_used=700,
                    clusters=[], nearest_below=dict(BELOW),
                    nearest_above=dict(ABOVE))})


class AnnotationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.database = self.root / 'market.sqlite3'
        fixture(self.database)
        self.plain = analyse(self.database, NOW)

    def write(self, report, name='clusters.json'):
        path = self.root / name
        path.write_text(json.dumps(report))
        return path

    def test_absent_option_leaves_the_report_byte_identical(self):
        again = analyse(self.database, NOW)
        self.assertNotIn('liq_clusters_generated_at_ms', again)
        for row in again['rows']:
            self.assertEqual([key for key in row if key.startswith('liq_')], [])
        stamp = dict(generated_at_ms=0)  # wall clock, not part of the analysis
        self.assertEqual(json.dumps({**again, **stamp}, sort_keys=True,
                                    allow_nan=False),
                         json.dumps({**self.plain, **stamp}, sort_keys=True,
                                    allow_nan=False))

    def test_fresh_file_annotates_rows_and_sections_without_changing_scores(self):
        path = self.write(clusters(NOW - MAX_AGE_MS + 1))
        report = analyse(self.database, NOW, path)
        self.assertEqual(report['liq_clusters_generated_at_ms'],
                         NOW - MAX_AGE_MS + 1)
        rows = {row['symbol']: row for row in report['rows']}
        self.assertEqual(rows[SYMBOL]['liq_below_pct'], BELOW['distance_pct'])
        self.assertEqual(rows[SYMBOL]['liq_below_usd'], BELOW['usd'])
        self.assertEqual(rows[SYMBOL]['liq_above_pct'], ABOVE['distance_pct'])
        self.assertEqual(rows[SYMBOL]['liq_above_usd'], ABOVE['usd'])
        for symbol, row in rows.items():
            if symbol != SYMBOL:
                self.assertEqual([row[name] for name in FIELDS], [0.0] * 4)
        self.assertEqual([{key: value for key, value in row.items()
                           if not key.startswith('liq_')}
                          for row in report['rows']], self.plain['rows'])
        self.assertEqual(
            {name: [row['symbol'] for row in items]
             for name, items in report['sections'].items()},
            {name: [row['symbol'] for row in items]
             for name, items in self.plain['sections'].items()})
        self.assertEqual(report['sections']['majors'][0]['liq_below_usd'],
                         BELOW['usd'])

    def test_stale_missing_and_malformed_files_annotate_zeros(self):
        cases = {
            'stale': self.write(clusters(NOW - MAX_AGE_MS - 1), 'stale.json'),
            'future': self.write(clusters(NOW + 1), 'future.json'),
            'missing': self.root / 'absent.json',
            'schema': self.write(dict(clusters(NOW), schema_version=2),
                                 'schema.json'),
            'invalid': self.write('not a report', 'invalid.json'),
            'unstamped': self.write(dict(clusters(NOW), generated_at_ms='now'),
                                    'unstamped.json'),
        }
        cases['broken'] = self.root / 'broken.json'
        cases['broken'].write_text('{not json')
        for name, path in cases.items():
            with self.subTest(case=name):
                self.assertEqual(load(path, NOW), ({}, None))
                report = analyse(self.database, NOW, path)
                self.assertIsNone(report['liq_clusters_generated_at_ms'])
                for row in report['rows']:
                    self.assertEqual([row[field] for field in FIELDS],
                                     [0.0] * 4)

    def test_unusable_symbol_entries_annotate_zeros_rather_than_nulls(self):
        cases = ((None, [0.0] * 4), ({}, [0.0] * 4),
                 (dict(nearest_below=None, nearest_above=None), [0.0] * 4),
                 (dict(nearest_below='low', nearest_above=7), [0.0] * 4),
                 (dict(nearest_below=dict(BELOW, usd=float('inf')),
                       nearest_above=dict(ABOVE, distance_pct='wide')),
                  [BELOW['distance_pct'], 0.0, 0.0, ABOVE['usd']]))
        for entry, expected in cases:
            with self.subTest(entry=entry):
                path = self.write(clusters(NOW, {SYMBOL: entry}), 'entry.json')
                report = analyse(self.database, NOW, path)
                rows = {row['symbol']: row for row in report['rows']}
                self.assertEqual([rows[SYMBOL][field] for field in FIELDS],
                                 expected)

    def test_cli_option_writes_the_annotated_report(self):
        path = self.write(clusters(NOW))
        destination = self.root / 'radar.json'
        main(['--database', str(self.database), '--json', str(destination),
              '--asof-ms', str(NOW), '--liquidation-clusters', str(path)],
             printer=lambda *_: None)
        report = json.loads(destination.read_text())
        self.assertEqual(report['liq_clusters_generated_at_ms'], NOW)
        rows = {row['symbol']: row for row in report['rows']}
        self.assertEqual(rows[SYMBOL]['liq_above_usd'], ABOVE['usd'])


if __name__ == '__main__':
    unittest.main()
