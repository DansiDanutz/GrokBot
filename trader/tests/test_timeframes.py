import copy
import unittest

from trader.features.timeframes import (
    TIMEFRAME_MINUTES, aggregate_candles, build_timeframes, compare_kucoin_1h,
)


MINUTE = 60000
BASE = 20000 * 1440 * MINUTE


def candle(index, **changes):
    value = {'timestamp_ms': BASE + index * MINUTE, 'open': 100 + index,
             'high': 102 + index, 'low': 99 + index, 'close': 101 + index,
             'volume': .1}
    value.update(changes)
    return value


class TimeframesTests(unittest.TestCase):
    def test_all_intervals_exact_utc_ohlcv(self):
        bars = [candle(i) for i in range(1440)]
        original = copy.deepcopy(bars)
        frames = build_timeframes(bars, BASE + 1440 * MINUTE)
        self.assertEqual(set(frames), {'5m', '15m', '1h', '4h', '1d'})
        for name, minutes in TIMEFRAME_MINUTES.items():
            with self.subTest(name=name):
                rows = frames[name]
                self.assertEqual(len(rows), 1440 // minutes)
                row = rows[0]
                self.assertEqual(row['timestamp_ms'] % (minutes * MINUTE), 0)
                self.assertEqual([row[k] for k in ('open', 'high', 'low', 'close')],
                                 [100, 101 + minutes, 99, 100 + minutes])
                self.assertEqual(row['volume'], minutes / 10)
                self.assertTrue(row['observed'])
                self.assertFalse(row['synthetic'])
                self.assertEqual(row['missing_minutes'], 0)
        self.assertEqual(bars, original)

    def test_future_and_unfinished_buckets_never_change_output(self):
        bars = [candle(i) for i in range(60)]
        asof = BASE + 59 * MINUTE + 30000
        frames = build_timeframes(bars, asof)
        self.assertEqual(len(frames['5m']), 11)
        self.assertEqual(frames['1h'], [])
        self.assertEqual(frames, build_timeframes(bars + [candle(60, close=float('nan'))], asof))
        self.assertTrue(all(row['end_ms'] <= asof for rows in frames.values() for row in rows))

    def test_missing_minutes_and_whole_buckets_remain_explicit_gaps(self):
        rows = aggregate_candles([candle(i) for i in [0, 1, 3, 4, 10, 11, 12, 13, 14]],
                                 '5m', BASE + 15 * MINUTE)
        self.assertEqual([r['missing_minutes'] for r in rows], [1, 5, 0])
        self.assertIsNone(rows[0]['close'])
        self.assertIsNone(rows[0]['volume'])
        self.assertEqual(rows[0]['observed_volume'], .4)
        self.assertFalse(rows[0]['observed'])
        self.assertFalse(rows[2]['crossing_eligible'])

    def test_indicator_fills_use_only_past_close_zero_volume(self):
        rows = aggregate_candles([candle(0), candle(4)], '5m', BASE + 5 * MINUTE,
                                 indicator_fill=True)
        row = rows[0]
        self.assertEqual([row[k] for k in ('open', 'high', 'low', 'close', 'volume')],
                         [100, 106, 99, 105, .2])
        self.assertTrue(row['synthetic'])
        self.assertTrue(row['indicator_only'])
        self.assertFalse(row['observed'])
        self.assertFalse(row['crossing_eligible'])
        self.assertEqual(row['coverage_fraction'], .4)

    def test_leading_gap_never_backfills_from_future(self):
        args = ([candle(i) for i in range(1, 5)], '5m', BASE + 5 * MINUTE)
        row = aggregate_candles(*args, indicator_fill=True)[0]
        self.assertIsNone(row['open'])
        self.assertTrue(row['unfillable'])
        seeded = aggregate_candles(*args, indicator_fill=True,
                                   prior_seed=candle(-1, close=100))[0]
        self.assertEqual(seeded['open'], 100)
        self.assertEqual(seeded['observed_minutes'], 4)
        with self.assertRaises(ValueError):
            aggregate_candles(*args, prior_seed=candle(0))

    def test_explicit_start_and_earlier_seed(self):
        rows = aggregate_candles([candle(0), candle(10)], '5m', BASE + 15 * MINUTE,
                                 start_ms=BASE + 5 * MINUTE, indicator_fill=True)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['close'], 101)
        self.assertEqual(rows[0]['volume'], 0)
        self.assertEqual(rows[0]['observed_minutes'], 0)

    def test_supplied_synthetic_or_indicator_rows_never_count_as_observed(self):
        for flag in ('synthetic', 'indicator_only'):
            with self.subTest(flag=flag):
                bars = [candle(i) for i in range(5)]
                bars[2][flag] = True
                row = aggregate_candles(bars, '5m', BASE + 5 * MINUTE)[0]
                self.assertEqual(row['observed_minutes'], 4)
                self.assertIsNone(row['close'])

    def test_duplicates_deduplicate_or_reject_conflicts(self):
        bars = [candle(i) for i in range(5)]
        row = aggregate_candles(bars + [candle(0)], '5m', BASE + 5 * MINUTE)[0]
        self.assertEqual(row['volume'], .5)
        with self.assertRaisesRegex(ValueError, 'Conflicting duplicate'):
            aggregate_candles(bars + [candle(0, volume=9)], '5m', BASE + 5 * MINUTE)

    def test_malformed_inputs_fail_explicitly(self):
        for changes in ({'volume': -1}, {'high': 99}, {'close': float('nan')},
                        {'synthetic': 'false'}, {'indicator_only': 1},
                        {'timestamp_ms': BASE + 1}, {'time_ms': BASE + MINUTE},
                        {'observed': False}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                aggregate_candles([candle(0, **changes)], '5m', BASE + 5 * MINUTE)
        missing = candle(0)
        del missing['volume']
        with self.assertRaises(ValueError):
            aggregate_candles([missing], '5m', BASE + 5 * MINUTE)

    def test_empty_window_and_invalid_options(self):
        self.assertEqual(build_timeframes([], BASE), {k: [] for k in TIMEFRAME_MINUTES})
        rows = aggregate_candles([], '5m', BASE + 5 * MINUTE, start_ms=BASE)
        self.assertEqual(rows[0]['missing_minutes'], 5)
        for kwargs in ({'asof_ms': True}, {'asof_ms': BASE, 'start_ms': BASE + MINUTE},
                       {'asof_ms': BASE, 'indicator_fill': 1}):
            with self.assertRaises(ValueError):
                build_timeframes([], **kwargs)
        with self.assertRaises(ValueError):
            aggregate_candles([], '2h', BASE)


class HourlyComparisonTests(unittest.TestCase):
    def test_match_and_mismatch_fields_with_exact_default(self):
        minutes = [candle(i) for i in range(120)]
        stored = [{'timestamp_ms': BASE, 'open': 100, 'high': 161, 'low': 99,
                   'close': 160, 'volume': 6},
                  {'timestamp_ms': BASE + 60 * MINUTE, 'open': 160, 'high': 221,
                   'low': 159, 'close': 220, 'volume': 6.1}]
        report = compare_kucoin_1h(minutes, stored, BASE + 120 * MINUTE)
        self.assertEqual((report['matches'], report['mismatches'], report['gaps']), (1, 1, 0))
        self.assertFalse(report['all_match'])
        self.assertEqual(set(report['rows'][1]['differences']), {'volume'})
        tolerated = compare_kucoin_1h(minutes, stored, BASE + 120 * MINUTE,
                                     volume_tolerance=.11)
        self.assertEqual(tolerated['matches'], 2)
        self.assertEqual(tolerated['volume_tolerance'], .11)

    def test_missing_source_or_stored_candles_are_gaps(self):
        minutes = [candle(i) for i in range(120) if i != 1]
        stored = [{'timestamp_ms': BASE, 'open': 100, 'high': 161, 'low': 99,
                   'close': 160, 'volume': 6}]
        report = compare_kucoin_1h(minutes, stored, BASE + 120 * MINUTE)
        self.assertEqual(report['gaps'], 2)
        self.assertEqual(report['matches'], 0)
        self.assertIn('missing_source_minutes', report['rows'][0]['reasons'])
        self.assertIn('missing_stored_candle', report['rows'][1]['reasons'])

    def test_comparison_rejects_unaligned_hour_and_invalid_tolerance(self):
        with self.assertRaises(ValueError):
            compare_kucoin_1h([], [candle(1)], BASE + 120 * MINUTE)
        with self.assertRaises(ValueError):
            compare_kucoin_1h([], [], BASE, price_tolerance=-1)


if __name__ == '__main__':
    unittest.main()
