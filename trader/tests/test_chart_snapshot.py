import copy
import math
import unittest
from unittest.mock import patch

from trader.features.timeframes import TIMEFRAME_MINUTES, aggregate_candles
from trader.research.chart_snapshot import ChartSnapshot
from trader.research.kucoin_snapshot import HistoricalSnapshot
from trader.research.kucoin_radar import radar
from trader.strategies.candle_coverage import prepare_validated, validate_history


MINUTE, HOUR, DAY = 60000, 3600000, 86400000
BASE = 100 * DAY
PAIR = 'TESTUSDTM'


def bar(timestamp):
    price = 100 + math.sin(timestamp / HOUR)
    return dict(time_ms=timestamp, open=price, high=price + 1, low=price - 1,
                close=price + .1, volume=2.)


class FakeSnapshot:
    historical_candle_only = True
    manifest = {'offline_copy': True, 'source': 'kucoin-public'}

    def __init__(self, first=BASE-7*DAY, missing=None):
        self.first = first
        self.calls = []
        self.missing = missing or set()

    def candles(self, pair, start_ms, end_ms):
        self.calls.append((pair, start_ms, end_ms))
        return [bar(t) for t in range(max(start_ms, self.first), end_ms-MINUTE+1, MINUTE)
                if t not in self.missing]

    def iter_records(self, at_ms, pairs=None):
        for pair in pairs or [PAIR]:
            yield dict(pair=pair, market={'filter_mode': 'candle-only filters'},
                       bars=self.candles(pair, at_ms-7*DAY, at_ms))

    def bounds(self):
        return self.first, BASE + DAY


class ChartSnapshotTests(unittest.TestCase):
    def test_fifty_five_closed_daily_buckets_and_all_timeframes(self):
        source = FakeSnapshot(first=BASE-56*DAY)
        adapter = ChartSnapshot(source)
        frames = adapter.frames(PAIR, BASE + HOUR)
        self.assertEqual(set(frames), set(TIMEFRAME_MINUTES))
        for name, rows in frames.items():
            with self.subTest(name=name):
                self.assertEqual(len(rows), 55)
                self.assertTrue(all(row['end_ms'] <= BASE + HOUR for row in rows))
                self.assertTrue(all(row['close'] is not None for row in rows))
        self.assertEqual(frames['1d'][-1]['end_ms'], BASE)
        self.assertEqual(frames['1d'][0]['timestamp_ms'], BASE-55*DAY)

    def test_incremental_only_reads_new_minutes_and_matches_pure_aggregation(self):
        source = FakeSnapshot()
        adapter = ChartSnapshot(source)
        adapter.frames(PAIR, BASE)
        source.calls.clear()
        frames = adapter.frames(PAIR, BASE + HOUR)
        self.assertEqual(source.calls, [(PAIR, BASE, BASE + HOUR)])
        for name, duration in TIMEFRAME_MINUTES.items():
            end = (BASE+HOUR)//(duration*MINUTE)*(duration*MINUTE)
            start = end-55*duration*MINUTE
            original = source.candles(PAIR, start-MINUTE, BASE+HOUR)
            expected = aggregate_candles(original, name, BASE+HOUR, start_ms=start,
                                         indicator_fill=True)
            self.assertEqual([{k:v for k,v in row.items() if k != 'crossing_eligible'} for row in frames[name]],
                             [{k:v for k,v in row.items() if k != 'crossing_eligible'} for row in expected])
        source.calls.clear()
        adapter.frames(PAIR, BASE + HOUR)
        self.assertEqual(source.calls, [])

    def test_rewind_discards_future_cache_and_public_results_cannot_mutate_cache(self):
        adapter = ChartSnapshot(FakeSnapshot())
        adapter.frames(PAIR, BASE + DAY)
        rewound = adapter.frames(PAIR, BASE)
        fresh = ChartSnapshot(FakeSnapshot()).frames(PAIR, BASE)
        self.assertEqual(rewound, fresh)
        rewound['5m'][-1]['close'] = 999999
        self.assertEqual(adapter.frames(PAIR, BASE), fresh)

    def test_cache_limits_symbol_count_and_current_day_raw_minutes(self):
        adapter = ChartSnapshot(FakeSnapshot(), cache_symbols=2)
        for pair in ('AAAUSDTM', 'BBBUSDTM', 'CCCUSDTM'):
            adapter.frames(pair, BASE + 23*HOUR)
        info = adapter.cache_info()
        self.assertEqual(info['cached_symbols'], 2)
        self.assertLessEqual(info['cached_minute_rows'], 2*1440)
        self.assertEqual(info['cache_evictions'], 1)

    def test_large_forward_jump_rewarms_with_a_bounded_read(self):
        source = FakeSnapshot()
        adapter = ChartSnapshot(source)
        adapter.frames(PAIR, BASE)
        source.calls.clear()
        adapter.frames(PAIR, BASE+100*DAY)
        self.assertEqual(len(source.calls), 1)
        self.assertLessEqual(source.calls[0][2]-source.calls[0][1], 57*DAY)

    def test_low_coverage_record_skips_long_warmup_but_reports_reason(self):
        source = FakeSnapshot(first=BASE-DAY)
        adapter = ChartSnapshot(source)
        records = adapter.records(BASE, [PAIR])
        self.assertEqual(len(source.calls), 1)
        self.assertFalse(records[0]['chart_data_status']['eligible'])
        self.assertIn('95%', records[0]['chart_data_status']['reason'])
        self.assertEqual(records[0]['frames'], {name: [] for name in TIMEFRAME_MINUTES})
        self.assertIsNone(records[0]['regime'])

    def test_enrichment_preserves_actual_crossing_bars_and_passes_macro_reads(self):
        source = FakeSnapshot()
        source_record = next(source.iter_records(BASE, [PAIR]))
        original = copy.deepcopy(source_record)
        adapter = ChartSnapshot(source)
        chart = {'asof_ms': BASE, 'five_cells': {}}
        with patch.object(source, 'iter_records', return_value=iter([source_record])), \
                patch('trader.research.chart_snapshot.read_chart', return_value=chart), \
                patch('trader.research.chart_snapshot.gate_entry', return_value={'allowed_directions': ['neutral']}) as gate:
            record = adapter.records(BASE, [PAIR])[0]
        self.assertEqual(source_record, original)
        self.assertEqual(record['bars'], original['bars'])
        self.assertEqual(set(gate.call_args.args[1]), {'XBTUSDTM', 'ETHUSDTM', 'SOLUSDTM'})
        self.assertTrue(record['chart_data_status']['eligible'])
        self.assertEqual(record['regime']['allowed_directions'], ['neutral'])
        self.assertEqual(adapter.bounds(), source.bounds())

    def test_disabled_regime_keeps_counterfactual_evidence_and_allows_all_directions(self):
        source = FakeSnapshot()
        adapter = ChartSnapshot(source, {'regime_gate': False})
        evidence = {'allowed_directions': ['neutral'], 'macro_gate': {'direction': 'unknown'},
                    'unknown_reasons': ['missing evidence']}
        original = copy.deepcopy(evidence)
        with patch.object(adapter, 'frames', return_value={}), \
                patch.object(adapter, 'chart_reads', return_value={}), \
                patch('trader.research.chart_snapshot.gate_entry', return_value=evidence):
            result = adapter.records(BASE, [PAIR])[0]['regime']
        self.assertFalse(result['enabled'])
        self.assertEqual(result['allowed_directions'], ['long', 'short', 'neutral'])
        self.assertEqual(result['would_allow'], ['neutral'])
        self.assertEqual(result['macro_gate'], original['macro_gate'])
        self.assertEqual(result['unknown_reasons'], original['unknown_reasons'])
        self.assertEqual(evidence, original)

    def test_default_regime_remains_enabled(self):
        adapter = ChartSnapshot(FakeSnapshot())
        with patch.object(adapter, 'frames', return_value={}), \
                patch.object(adapter, 'chart_reads', return_value={}), \
                patch('trader.research.chart_snapshot.gate_entry', return_value={'allowed_directions': ['short']}):
            result = adapter.records(BASE, [PAIR])[0]['regime']
        self.assertTrue(result['enabled'])
        self.assertEqual(result['allowed_directions'], ['short'])

    def test_flat_chart_parameters_reach_reads_and_gate(self):
        adapter = ChartSnapshot(FakeSnapshot(), {'bias_mode': '4h-only',
                                'minimum_confidence': .61, 'neutral_band': [.3, .7]})
        with patch.object(adapter, 'frames', return_value={}), \
                patch('trader.research.chart_snapshot.read_chart', return_value={}) as reader, \
                patch('trader.research.chart_snapshot.gate_entry', return_value={'allowed_directions': ['neutral']}) as gate:
            adapter.records(BASE, [PAIR])
        self.assertEqual(reader.call_args.kwargs, {'bias_mode': '4h-only',
                          'minimum_confidence': .61, 'neutral_band': [.3, .7]})
        self.assertEqual(gate.call_args.kwargs['minimum_confidence'], .61)

    def test_nested_chart_options_merge_but_conflicts_and_nonboolean_gate_reject(self):
        adapter = ChartSnapshot(FakeSnapshot(), {'minimum_confidence': .6,
                                'chart': {'bias_mode': '4h-only', 'minimum_confidence': .6}})
        with patch.object(adapter, 'frames', return_value={}), \
                patch('trader.research.chart_snapshot.read_chart', return_value={}) as reader:
            adapter.chart_reads(BASE)
        self.assertEqual(reader.call_args.kwargs, {'minimum_confidence': .6, 'bias_mode': '4h-only'})
        for options in ({'minimum_confidence': .6, 'chart': {'minimum_confidence': .8}},
                        {'regime_gate': 'false'}, {'chart': {'unexpected': True}}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                ChartSnapshot(FakeSnapshot(), options)

    def test_records_only_source_is_supported(self):
        backing = FakeSnapshot(first=BASE-DAY)

        class RecordsOnly:
            manifest = backing.manifest
            candles = backing.candles

            def records(self, at_ms, pairs=None):
                return list(backing.iter_records(at_ms, pairs))

        record = ChartSnapshot(RecordsOnly()).records(BASE, [PAIR])[0]
        self.assertEqual(record['pair'], PAIR)
        self.assertFalse(record['chart_data_status']['eligible'])

    def test_equivalent_parameters_reuse_wrapper(self):
        adapter = ChartSnapshot(FakeSnapshot(), {'regime_gate': False})
        self.assertIs(adapter.for_parameters({'strategy': 'income_chart_v3', 'regime_gate': False}), adapter)
        default = ChartSnapshot(FakeSnapshot())
        self.assertIs(default.for_parameters({}), default)

    def test_variant_parameters_change_gate_and_bias_without_mutating_old_wrapper(self):
        source = FakeSnapshot()
        history = [{'symbol': PAIR, 'timestamp_ms': BASE-4*HOUR, 'rate': .0001},
                   {'symbol': PAIR, 'timestamp_ms': BASE, 'rate': .0002}]
        member = {'schema_version': 1, 'members': {}, 'non_members': {}}
        old = ChartSnapshot(source, {'bias_mode': '1d+4h'}, funding_histories={
            PAIR: {'records': history, 'complete': True}}, membership=member, cache_symbols=7)
        changed = old.for_parameters({'bias_mode': '4h-only', 'regime_gate': False})
        self.assertIsNot(changed, old)
        self.assertIs(changed.source, old.source)
        self.assertEqual(changed.funding(PAIR, BASE-HOUR, BASE), old.funding(PAIR, BASE-HOUR, BASE))
        self.assertEqual(changed.funding_coverage(PAIR, BASE-HOUR, BASE)['traversal_complete'], True)
        self.assertEqual(changed.membership, old.membership)
        self.assertEqual(changed.cache_info()['capacity'], 7)
        with patch.object(ChartSnapshot, 'frames', return_value={}), \
                patch('trader.research.chart_snapshot.read_chart',
                      side_effect=lambda frames, at, **options: {'bias_mode': options.get('bias_mode', '1d+4h')}), \
                patch('trader.research.chart_snapshot.gate_entry', return_value={'allowed_directions': ['neutral']}):
            old_record = old.records(BASE, [PAIR])[0]
            new_record = changed.records(BASE, [PAIR])[0]
            self.assertEqual(old.chart_reads(BASE)['XBTUSDTM']['bias_mode'], '1d+4h')
            self.assertEqual(changed.chart_reads(BASE)['XBTUSDTM']['bias_mode'], '4h-only')
        self.assertTrue(old_record['regime']['enabled'])
        self.assertFalse(new_record['regime']['enabled'])
        self.assertEqual(old_record['regime']['allowed_directions'], ['neutral'])
        self.assertEqual(new_record['regime']['allowed_directions'], ['long', 'short', 'neutral'])
        self.assertEqual(old.parameters, {'strategy': 'income_chart_v3', 'bias_mode': '1d+4h'})

    def test_changed_nonchart_parameters_also_create_independent_wrapper(self):
        original = ChartSnapshot(FakeSnapshot(), {'setup': {'target': 1}})
        options = {'setup': {'target': 2}}
        changed = original.for_parameters(options)
        options['setup']['target'] = 99
        self.assertEqual(changed.parameters['setup']['target'], 2)
        self.assertEqual(original.parameters['setup']['target'], 1)

    def test_membership_manifest_digest_binds_content_and_ignores_dict_order(self):
        first = {'schema_version': 1, 'members': {PAIR: {'basis': 'test', 'sources': ['source']}},
                 'non_members': {}}
        reordered = {'non_members': {}, 'members': {PAIR: {'sources': ['source'], 'basis': 'test'}},
                     'schema_version': 1}
        adapter = ChartSnapshot(FakeSnapshot(), membership=first)
        digest = adapter.manifest['chart_membership_sha256']
        self.assertEqual(len(digest), 64)
        self.assertEqual(digest, ChartSnapshot(FakeSnapshot(), membership=reordered).manifest['chart_membership_sha256'])
        changed = ChartSnapshot(FakeSnapshot(), membership=dict(first, schema_version=2))
        self.assertNotEqual(digest, changed.manifest['chart_membership_sha256'])
        self.assertNotEqual(ChartSnapshot(FakeSnapshot(), membership=None).manifest['chart_membership_sha256'],
                            ChartSnapshot(FakeSnapshot(), membership={}).manifest['chart_membership_sha256'])
        self.assertEqual(digest, adapter.for_parameters({'regime_gate': False}).manifest['chart_membership_sha256'])

    def test_manifest_rejects_nonfinite_and_unserializable_membership(self):
        for member in ({'weight': float('nan')}, {'weight': float('inf')}, {'members': {PAIR}}):
            with self.subTest(member=member), self.assertRaises(ValueError):
                ChartSnapshot(FakeSnapshot(), membership=member)

    def test_funding_manifest_digest_binds_records_and_traversal_provenance(self):
        rows = [{'symbol': PAIR, 'timestamp_ms': BASE, 'rate': .001},
                {'symbol': PAIR, 'timestamp_ms': BASE+HOUR, 'rate': .002}]
        initial = ChartSnapshot(FakeSnapshot(), funding_histories={PAIR: {'records': rows, 'complete': True}})
        digest = initial.manifest['chart_funding_sha256']
        reordered = ChartSnapshot(FakeSnapshot(), funding_histories={PAIR: {'complete': True, 'records': rows[::-1]}})
        self.assertEqual(digest, reordered.manifest['chart_funding_sha256'])
        changed = copy.deepcopy(rows)
        changed[0]['rate'] = .003
        altered = ChartSnapshot(FakeSnapshot(), funding_histories={PAIR: {'records': changed, 'complete': True}})
        self.assertNotEqual(digest, altered.manifest['chart_funding_sha256'])
        incomplete = ChartSnapshot(FakeSnapshot(), funding_histories={PAIR: {'records': rows, 'complete': False}})
        self.assertNotEqual(digest, incomplete.manifest['chart_funding_sha256'])
        self.assertEqual(digest, initial.for_parameters({'bias_mode': '4h-only'}).manifest['chart_funding_sha256'])

    def test_indicator_source_rows_do_not_become_crossing_observations(self):
        source = FakeSnapshot()
        record = next(source.iter_records(BASE, [PAIR]))
        record['bars'][0]['indicator_only'] = True
        with patch.object(source, 'iter_records', return_value=iter([record])):
            actual = ChartSnapshot(source).records(BASE, [PAIR])[0]['bars']
        self.assertEqual(len(actual), 10079)
        self.assertTrue(all(not row.get('indicator_only') for row in actual))

    def test_causal_missing_daily_buckets_do_not_backfill_from_later_days(self):
        frames = ChartSnapshot(FakeSnapshot()).frames(PAIR, BASE)
        self.assertIsNone(frames['1d'][0]['close'])
        self.assertFalse(frames['1d'][0]['observed'])
        self.assertEqual(frames['1d'][-1]['observed_minutes'], 1440)

    def test_historical_wrapper_uses_raw_source_for_long_warmup(self):
        raw = FakeSnapshot(first=BASE-56*DAY)
        historical = object.__new__(HistoricalSnapshot)
        historical.source = raw
        historical.manifest = dict(raw.manifest)
        historical.candles = lambda *args: self.fail('Nine-day historical candle cache cannot supply warmup')
        adapter = ChartSnapshot(historical)
        self.assertTrue(adapter.income_chart_v3)
        self.assertTrue(adapter.historical_candle_only)
        self.assertTrue(all(row['observed'] for row in adapter.frames(PAIR, BASE)['1d']))

    def test_incremental_day_boundary_preserves_gap_coverage_and_past_seed(self):
        source = FakeSnapshot(missing={BASE+DAY-2*MINUTE})
        adapter = ChartSnapshot(source)
        adapter.frames(PAIR, BASE+23*HOUR)
        frames = adapter.frames(PAIR, BASE+DAY)
        daily = frames['1d'][-1]
        self.assertEqual(daily['observed_minutes'], 1439)
        self.assertEqual(daily['missing_minutes'], 1)
        self.assertTrue(daily['indicator_only'])
        self.assertIsNotNone(daily['close'])
        self.assertEqual(adapter.cache_info()['cached_minute_rows'], 0)
        self.assertEqual(frames, ChartSnapshot(source).frames(PAIR, BASE+DAY))


class ChartFundingTests(unittest.TestCase):
    def history(self):
        return [{'symbol': PAIR, 'timestamp_ms': BASE+i*HOUR, 'rate': .001+i*.0001}
                for i in (-8, -4, 0, 4, 8)]

    def test_funding_cash_keeps_actual_time_and_forecast_uses_lag(self):
        adapter = ChartSnapshot(FakeSnapshot(first=BASE), funding_histories={PAIR:self.history()})
        row = adapter.records(BASE, [PAIR])[0]
        self.assertEqual(row['funding_terms']['observed_at_ms'], BASE-4*HOUR+MINUTE)
        self.assertEqual(row['funding_terms']['interval_ms'], 4*HOUR)
        events = adapter.funding(PAIR, BASE-4*HOUR, BASE)
        self.assertEqual([event['timestamp_ms'] for event in events], [BASE])
        self.assertEqual(events[0]['rate'], .001)

    def test_missing_history_is_not_zero_or_verified_coverage(self):
        adapter = ChartSnapshot(FakeSnapshot(first=BASE))
        row = adapter.records(BASE, [PAIR])[0]
        self.assertIsNone(row['funding_terms'])
        self.assertEqual(row['funding_diagnostics']['coverage'], 'missing')
        coverage = adapter.funding_coverage(PAIR, BASE-HOUR, BASE)
        self.assertFalse(coverage['modeled_complete'])
        self.assertFalse(coverage['known'])
        self.assertFalse(coverage['verified'])
        self.assertEqual(coverage['records_available'], 0)

    def test_modeled_coverage_checks_due_times_without_future_bracket(self):
        history = self.history()
        adapter = ChartSnapshot(FakeSnapshot(), funding_histories={PAIR:history})
        coverage = adapter.funding_coverage(PAIR, BASE-3*HOUR, BASE+HOUR)
        self.assertTrue(coverage['modeled_complete'])
        self.assertFalse(coverage['verified'])
        self.assertEqual(coverage['missing_expected_settlements'], 0)
        past_only = [row for row in history if row['timestamp_ms'] <= BASE+HOUR]
        another = ChartSnapshot(FakeSnapshot(), funding_histories={PAIR:past_only})
        self.assertEqual(coverage, another.funding_coverage(PAIR, BASE-3*HOUR, BASE+HOUR))
        missing = [row for row in history if row['timestamp_ms'] != BASE]
        incomplete = ChartSnapshot(FakeSnapshot(), funding_histories={PAIR:missing})
        self.assertFalse(incomplete.funding_coverage(PAIR, BASE-3*HOUR, BASE+HOUR)['modeled_complete'])


class ChartPreflightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bars = [bar(timestamp) for timestamp in range(BASE-7*DAY, BASE, MINUTE)]
        cls.prepared = prepare_validated(validate_history(cls.bars), BASE)
        cls.unseeded = prepare_validated(validate_history(cls.bars[1:]), BASE)

    def record(self, changes=None, prepared=None):
        market = dict(active=True, perpetual=True, quote_currency='USDT', asset_class='crypto',
                      quote_turnover_24h=500000, bid=999.5, ask=1000.5,
                      observed_at_ms=BASE, listed_at_ms=BASE-7*DAY,
                      bid_depth_usdt=10000, ask_depth_usdt=10000,
                      funding_rate=.0001, funding_interval_hours=8)
        market.update(changes or {})
        return dict(pair=PAIR, bars=self.bars, prepared=prepared or self.prepared, market=market)

    def run_radar(self, record, *, eager=False, rate=.0005):
        source = FakeSnapshot()
        adapter = ChartSnapshot(source)
        terms = dict(rate=rate, interval_ms=4*HOUR, observed_at_ms=BASE-MINUTE)
        frames = {name: [{'example': True}] for name in TIMEFRAME_MINUTES}

        def setup(pair, bars, market, chart_frames, *args, **kwargs):
            return {'offered': False, 'reason': 'chart setup exercised',
                    'test_frame_keys': sorted(chart_frames)}

        with patch.object(source, 'iter_records', side_effect=lambda *args: iter([record])), \
                patch.object(adapter, 'frames', return_value=frames) as frame_reader, \
                patch.object(adapter, 'chart_reads', return_value={}) as reference_reader, \
                patch.object(adapter, '_terms', return_value=(terms, {'coverage': 'inferred_regular'})), \
                patch('trader.research.chart_snapshot.gate_entry', return_value={'allowed_directions': ['neutral']}), \
                patch('trader.research.kucoin_radar.build_recommender_setup', side_effect=setup):
            if eager:
                with patch('trader.research.chart_snapshot._chart_preflight_reason', return_value=None, create=True):
                    enriched = adapter.records(BASE, [PAIR])
            else:
                enriched = adapter.records(BASE, [PAIR])
            result = radar(enriched, BASE, parameters={'strategy': 'income_chart_v3'})
        return result, frame_reader.call_count, reference_reader.call_count, enriched[0]

    def test_known_filter_rejections_skip_warmup_with_identical_entire_radar_output(self):
        cases = [({'quote_turnover_24h': 499999}, .0005),
                 ({'ask': 1000.5001}, .0005),
                 ({'listed_at_ms': BASE-7*DAY+1}, .0005),
                 ({}, .0005001), ({}, -.0005001),
                 ({'observed_at_ms': BASE-2*HOUR}, .0005),
                 ({'asset_class': 'stock'}, .0005)]
        for changes, rate in cases:
            with self.subTest(changes=changes, rate=rate):
                record = self.record(changes)
                eager = self.run_radar(record, eager=True, rate=rate)
                lazy = self.run_radar(record, rate=rate)
                self.assertEqual(eager[0], lazy[0])
                self.assertEqual(lazy[1:3], (0, 0))
                self.assertEqual(eager[1:3], (1, 1))
                self.assertEqual(lazy[3]['chart_preflight_reason'], lazy[0]['rejected'][0]['reason'])

    def test_exact_threshold_passes_still_warm_charts_and_preserve_output(self):
        for rate in (.0005, -.0005):
            with self.subTest(rate=rate):
                eager = self.run_radar(self.record(), eager=True, rate=rate)
                lazy = self.run_radar(self.record(), rate=rate)
                self.assertEqual(eager[0], lazy[0])
                self.assertEqual(lazy[1:3], (1, 1))
                self.assertEqual(lazy[0]['rejected'][0]['reason'], 'chart setup exercised')

    def test_unseeded_prepared_history_and_market_reasons_keep_radar_order(self):
        for changes in ({}, {'quote_turnover_24h': 1}):
            with self.subTest(changes=changes):
                record = self.record(changes, prepared=self.unseeded)
                lazy = self.run_radar(record)
                self.assertEqual(lazy[0], self.run_radar(record, eager=True)[0])
                self.assertEqual(lazy[1:3], (0, 0))

    def test_raw_untrusted_and_wrong_time_prepared_keep_eager_path(self):
        for prepared in (dict(self.prepared),
                         prepare_validated(validate_history(self.bars), BASE+MINUTE)):
            with self.subTest(kind=type(prepared)):
                result = self.run_radar(self.record({'quote_turnover_24h': 1}, prepared=prepared))
                self.assertEqual(result[1:3], (1, 1))

    def test_candle_only_normalization_and_skipped_filters_are_preserved(self):
        for turnover, expected_reads in ((499999, 0), (500000, 1)):
            with self.subTest(turnover=turnover):
                record = self.record({'filter_mode': 'candle-only filters',
                    'turnover_basis': 'recorded quote turnover', 'quote_turnover_24h': turnover,
                    'membership_basis': 'observed_candles', 'listed_at_ms': BASE-HOUR,
                    'ask': 2000})
                eager = self.run_radar(record, eager=True, rate=.1)
                lazy = self.run_radar(record, rate=.1)
                self.assertEqual(eager[0], lazy[0])
                self.assertEqual(lazy[1:3], (expected_reads, expected_reads))


if __name__ == '__main__':
    unittest.main()
