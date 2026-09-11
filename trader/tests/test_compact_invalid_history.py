"""Compact invalid preparations preserve all causal rejection evidence."""
import unittest

from trader.research.kucoin_radar import radar
from trader.research.kucoin_replay import run_window
from trader.research.kucoin_snapshot import HistoricalSnapshot
from trader.strategies.candle_coverage import is_prepared_history, prepare_validated, validate_history
from trader.strategies.grid_features import features
from trader.tests.test_snapshot_retention import MemorySource, NOW, DAY, HOUR, MINUTE


def candle(index):
    return dict(timestamp_ms=NOW-7*DAY+index*MINUTE, open=100, high=101,
                low=99, close=100, volume=1, turnover=100)


class CompactInvalidPreparationTests(unittest.TestCase):
    def compare(self, rows, *, seed=None, expected_compact=True, at=NOW):
        history = validate_history(rows)
        normal = prepare_validated(history, at, prior_seed=seed)
        compact = prepare_validated(history, at, prior_seed=seed, omit_invalid_bars=True)
        self.assertTrue(is_prepared_history(compact))
        self.assertEqual(compact['coverage'], normal['coverage'])
        self.assertEqual(compact['reason'], normal['reason'])
        self.assertEqual(compact['valid'], normal['valid'])
        self.assertEqual(compact.observed_bars, normal.observed_bars)
        self.assertEqual(features(compact), features(normal))
        if expected_compact:
            self.assertFalse(compact['valid'])
            self.assertEqual(compact['bars'], [])
            self.assertTrue(compact['dense_omitted'])
        else:
            self.assertEqual(compact, normal)
            self.assertNotIn('dense_omitted', compact)
        return normal, compact

    def test_sparse_seeded_unseeded_and_empty_evidence_match(self):
        for rows, seed in (([candle(i) for i in range(0,10080,15)], None),
                           ([candle(i) for i in range(1,10080,15)], None),
                           ([candle(i) for i in range(1,10080,15)], candle(-1)),
                           ([], None), ([], candle(-1))):
            with self.subTest(count=len(rows), seeded=seed is not None):
                normal, compact = self.compare(rows, seed=seed)
                self.assertEqual(len(normal['bars']), 10080)
                with self.assertRaises(TypeError):
                    compact['valid'] = True
                with self.assertRaises(TypeError):
                    compact['bars'].append(candle(0))

    def test_exact_95_percent_boundary_and_valid_dense_output_unchanged(self):
        for count in (9575, 9576, 10080):
            with self.subTest(count=count):
                self.compare([candle(i) for i in range(count)], expected_compact=count < 9576)
        self.compare([candle(i) for i in range(1,9577)], expected_compact=True)

    def test_duplicates_preserve_counts_and_conflicts_preserve_reference_errors(self):
        sparse = [candle(i) for i in range(0,10080,15)]
        self.compare(sparse+[candle(15)])
        self.compare(sparse+[dict(candle(15), close=100.5)], expected_compact=False)

    def test_malformed_and_invalid_seed_fallback_order_is_unchanged(self):
        sparse = [candle(i) for i in range(0,10080,15)]
        cases = [(sparse+[dict(candle(10), close=float('nan'))], None),
                 (sparse+[dict(candle(10080), close=float('nan'))], None),
                 (sparse, candle(0)), (sparse, dict(candle(-1), synthetic=True)),
                 ([candle(-1)]+sparse, dict(candle(-1), close=100.5))]
        for rows, seed in cases:
            with self.subTest(seed=seed, count=len(rows)):
                self.compare(rows, seed=seed, expected_compact=False)

    def test_invalid_previous_never_supplies_dense_reuse_to_valid_transition(self):
        rows = [candle(i) for i in range(600,10080+1440)]
        history = validate_history(rows)
        invalid = prepare_validated(history, NOW, omit_invalid_bars=True)
        self.assertTrue(invalid['dense_omitted'])
        self.assertIsNone(invalid._membership)
        valid = prepare_validated(history, NOW+DAY, previous=invalid, omit_invalid_bars=True)
        reference = prepare_validated(history, NOW+DAY)
        self.assertTrue(valid['valid'])
        self.assertEqual(valid, reference)

    def test_valid_previous_can_transition_to_compact_invalid(self):
        history = validate_history([candle(i) for i in range(10080)])
        valid = prepare_validated(history, NOW)
        invalid = prepare_validated(history, NOW+DAY, previous=valid, omit_invalid_bars=True)
        reference = prepare_validated(history, NOW+DAY, previous=valid)
        self.assertTrue(invalid['dense_omitted'])
        self.assertEqual(invalid['coverage'], reference['coverage'])
        self.assertEqual(invalid['reason'], reference['reason'])

    def test_opt_in_requires_boolean(self):
        with self.assertRaises(ValueError):
            prepare_validated(validate_history([]), NOW, omit_invalid_bars=1)


class CompactHistoricalSnapshotTests(unittest.TestCase):
    def source(self, compact=True, strategy='income_chart_v3'):
        ranges = {'BADUSDTM': (NOW-8*DAY, NOW+10*DAY, 15*MINUTE),
                  'LATEUSDTM': (NOW-DAY, NOW+10*DAY, MINUTE)}
        options = dict(strategy=strategy, omit_invalid_bars=compact)
        return HistoricalSnapshot(MemorySource(ranges), options)

    def test_only_income_chart_snapshot_opts_in_and_explicit_opt_out_is_dense(self):
        fast = self.source().records(NOW, ['BADUSDTM'])[0]
        self.assertTrue(fast['prepared']['dense_omitted'])
        for source in (self.source(False), self.source(True, 'legacy'),
                       HistoricalSnapshot(self.source().source)):
            with self.subTest(parameters=source.parameters):
                record = source.records(NOW, ['BADUSDTM'])[0]
                self.assertEqual(len(record['prepared']['bars']), 10080)
                self.assertNotIn('dense_omitted', record['prepared'])

    def test_full_radar_evidence_matches_opt_out_including_valid_transitions(self):
        compact, dense = self.source(), self.source(False)
        for at in (NOW, NOW+HOUR, NOW+7*DAY):
            with self.subTest(at=at):
                actual = radar(compact.iter_records(at), at, parameters={'strategy':'income_chart_v3'})
                expected = radar(dense.iter_records(at), at, parameters={'strategy':'income_chart_v3'})
                self.assertEqual(actual, expected)
        self.assertTrue(compact._record_cache['LATEUSDTM'][1]['prepared']['valid'])

    def test_chart_replay_results_match_opt_out(self):
        options = dict(strategy='income_chart_v3', historical_candle_only=True)
        actual = run_window(self.source(), NOW, NOW+2*HOUR, options)
        expected = run_window(self.source(False), NOW, NOW+2*HOUR, options)
        actual.pop('performance', None)
        expected.pop('performance', None)
        self.assertEqual(actual, expected)
        self.assertEqual(actual['completed_hours'], 2)
        self.assertEqual(len(actual['hourly_radar']), 2)

    def test_chart_replay_valid_transition_keeps_full_volatility_and_reason_evidence(self):
        class MissingReferences(MemorySource):
            def candles(self, pair, start, end):
                return super().candles(pair, start, end) if pair in self.ranges else []

        results = []
        options = dict(strategy='income_chart_v3', historical_candle_only=True)
        start = NOW+6*DAY-HOUR
        for compact in (False, True):
            original = self.source(compact)
            source = HistoricalSnapshot(MissingReferences(original.source.ranges), original.parameters)
            result = run_window(source, start, start+2*HOUR, options)
            result.pop('performance', None)
            results.append(result)
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[1]['completed_hours'], 2)
        scans = results[1]['hourly_radar']
        first = next(row for row in scans[0]['rejected'] if row['pair'] == 'LATEUSDTM')
        later = next(row for row in scans[1]['rejected']+scans[1]['radar'] if row['pair'] == 'LATEUSDTM')
        self.assertFalse(first['candle_coverage']['indicators_valid'])
        self.assertTrue(later['candle_coverage']['indicators_valid'])
        self.assertEqual(later['candle_coverage']['fraction'], 1.)


if __name__ == '__main__':
    unittest.main()
