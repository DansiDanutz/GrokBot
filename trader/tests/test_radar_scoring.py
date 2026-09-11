import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from trader.radar.radar import analyse
from trader.radar.scoring import score_row
from trader.tests.test_radar import fixture, NOW


def row(**changes):
    data = dict(symbol='RAYUSDTM', direction='LONG', price=100,
                expected_grids_per_hour=10, turnover_24h_usdt=16_500_000,
                spread_pct=0.10, range_low=90, range_high=120,
                atr_1h_pct=5, atr_4h_pct=10, position_7d=0.5,
                funding_pct=-0.02, change_24h_pct=10, listing_age_days=20,
                snapshot_age_min=10)
    data.update(changes)
    return data


def parts(data):
    return {part['code']: part for part in score_row(data)['score_parts']}


class RadarScoringTests(unittest.TestCase):
    def test_exact_parts_numeric_measurements_and_no_mutation(self):
        original = row()
        before = copy.deepcopy(original)
        result = score_row(original)
        expected = {
            'OSCILLATION': (10, 15), 'TREND_CLARITY': (0.5, 20),
            'LIQUIDITY_TURNOVER': (16_500_000, 5),
            'LIQUIDITY_SPREAD': (0.10, 2.5), 'ROOM': (1, 7.5),
            'FUNDING': (-0.02, 10), 'STABILITY': (1, 10),
        }
        self.assertEqual(set(parts(original)), set(expected))
        for code, (value, points) in expected.items():
            self.assertAlmostEqual(parts(original)[code]['value'], value)
            self.assertAlmostEqual(parts(original)[code]['points'], points)
        self.assertAlmostEqual(result['score'], 70)
        self.assertEqual(original, before)
        json.dumps(result, allow_nan=False)

    def test_component_clamps_and_penalty_boundaries(self):
        data = row(expected_grids_per_hour=30, turnover_24h_usdt=60_000_000,
                   spread_pct=0.01, range_low=70, range_high=130)
        self.assertEqual(score_row(data)['score'], 100)
        for spread, expected in [(0.05, 5), (0.10, 2.5), (0.15, 0), (0.2, 0)]:
            self.assertAlmostEqual(parts(row(spread_pct=spread))['LIQUIDITY_SPREAD']['points'], expected)
        for turnover, expected in [(0, 0), (3_000_000, 0), (30_000_000, 10)]:
            self.assertEqual(parts(row(turnover_24h_usdt=turnover))['LIQUIDITY_TURNOVER']['points'], expected)
        self.assertNotIn('MOVER_RISK', parts(row(change_24h_pct=30)))
        self.assertNotIn('YOUNG_LISTING', parts(row(listing_age_days=14)))
        self.assertNotIn('STALE_DATA', parts(row(snapshot_age_min=60)))
        penalties = parts(row(symbol='XBTUSDTM', change_24h_pct=-31,
                             listing_age_days=13, snapshot_age_min=61))
        for code, value, points in [('MOVER_RISK', -31, -15), ('YOUNG_LISTING', 13, -10),
                                    ('STALE_DATA', 61, -20), ('MAJOR_LOW_YIELD', 1, -10)]:
            self.assertEqual(penalties[code], dict(code=code, value=value, points=points))
        self.assertEqual(score_row(row(symbol='SOLUSDTM', expected_grids_per_hour=0,
                         direction='NEUTRAL', position_7d=0, funding_pct=1,
                         spread_pct=1, turnover_24h_usdt=0, range_low=100,
                         atr_1h_pct=0, change_24h_pct=40, listing_age_days=1,
                         snapshot_age_min=100))['score'], 0)

    def test_trend_funding_and_stability_rules(self):
        for direction, position, points in [('LONG', 0, 20), ('SHORT', 1, 20),
                ('TURNING-UP', 0, 14), ('TURNING-DOWN', 1, 14),
                ('NEUTRAL', .25, 16), ('NEUTRAL', .75, 16), ('NEUTRAL', .24, 6)]:
            self.assertEqual(parts(row(direction=direction, position_7d=position))['TREND_CLARITY']['points'], points)
        for direction, funding, expected in [('LONG', -.001, 10), ('LONG', .001, 5),
                ('LONG', .01, 0), ('SHORT', .02, 10), ('SHORT', -.02, 0),
                ('TURNING-UP', -.02, 10), ('TURNING-DOWN', .02, 10),
                ('NEUTRAL', .001, 5), ('NEUTRAL', .01, 0)]:
            self.assertEqual(parts(row(direction=direction, funding_pct=funding))['FUNDING']['points'], expected)
        for atr, expected in [(3, 10), (7, 10), (2.99, 4), (7.01, 4)]:
            self.assertEqual(parts(row(atr_1h_pct=atr))['STABILITY']['points'], expected)

    def test_invalid_inputs_fail_without_nonfinite_output(self):
        for name in row():
            if name in ('symbol', 'direction'):
                continue
            for value in (float('nan'), float('inf'), True, '1'):
                with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                    score_row(row(**{name: value}))
        for changes in ({'price': 0}, {'atr_4h_pct': 0}, {'range_low': 121},
                        {'spread_pct': -1}, {'direction': 'UNKNOWN'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                score_row(row(**changes))

    def test_analyse_scores_all_rows_preserving_old_ranking(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'market.sqlite3'
            fixture(database)
            report = analyse(database, NOW)
            for item in report['rows']:
                self.assertEqual(item['score'], score_row(item)['score'])
                self.assertEqual(item['score_parts'], score_row(item)['score_parts'])
            old_ranks = [item['rank_score'] for item in report['rows']]
            self.assertEqual(old_ranks, sorted(old_ranks, reverse=True))
            with sqlite3.connect(database) as connection:
                connection.execute("UPDATE top_of_book SET bid=0 WHERE symbol='UPUSDTM'")
            report = analyse(database, NOW)
            self.assertNotIn('UPUSDTM', [item['symbol'] for item in report['rows']])
            json.dumps(report, allow_nan=False)
