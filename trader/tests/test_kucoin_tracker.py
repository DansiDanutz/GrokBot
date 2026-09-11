import unittest
from dataclasses import replace
from trader.strategies.grid_types import GridConfig, Position
from trader.strategies.kucoin_grid import create_bot, advance
from trader.research.kucoin_tracker import track_bars, track_summary

HOUR = 3_600_000


def state(**kwargs):
    config = GridConfig(pair='T', low=90, high=110, grids=20,
                        quantity=1, direction='long', **kwargs)
    return create_bot(config, 100, 0)


def bar(time=0, **changes):
    return dict(timestamp_ms=time, open=100, high=101, low=99, close=100, **changes)


class TrackerTests(unittest.TestCase):
    def test_collects_seed_and_all_intrabar_fills_and_is_pure(self):
        initial = state()
        result = track_bars(initial, [bar()], start_ms=0)
        events = result['ledger']
        self.assertEqual(initial.completed_grids, 0)
        self.assertGreater(result['state'].completed_grids, 0)
        self.assertAlmostEqual(sum(row['fee'] for row in events), result['state'].fees)
        self.assertEqual(len({row['event_id'] for row in events}), len(events))
        self.assertEqual(sum(row.get('completed_grid', False) for row in events),
                         result['state'].completed_grids)
        self.assertEqual(result['summary']['asof_ms'], 60_000)

    def test_rates_use_elapsed_exposure_not_full_window_for_new_bot(self):
        initial = state()
        running = replace(initial, timestamp_ms=HOUR, completion_times=(HOUR//2, HOUR))
        summary = track_summary(running, 0, HOUR, 10)
        self.assertEqual(summary['realized_gph_1h'], 2)
        self.assertEqual(summary['realized_gph_6h'], 2)
        self.assertEqual(summary['expected_start_gph'], 10)
        self.assertEqual(summary['positions']['long']['average_entry'], 100)
        self.assertEqual(summary['positions']['short']['quantity'], 0)

    def test_emergency_within_five_percent_liquidation(self):
        running = state(investment=200, leverage=10)
        # Fixed inventory risk: liq=(1000-199.4)/(10*.995)=80.46.
        running = replace(running, price=83)
        summary = track_summary(running, 0, HOUR)
        self.assertTrue(summary['emergency'])
        self.assertEqual(summary['emergency_action'], 'stop or add reserve before liquidation')
        self.assertLess(summary['distance_liquidation_pct'], 5)

    def test_stop_loss_is_reported_and_remaining_bar_does_not_trade(self):
        result = track_bars(state(stop_loss=85),
                            [dict(timestamp_ms=0, open=100, high=101, low=80, close=100)])
        self.assertTrue(result['summary']['stop_loss_hit'])
        self.assertEqual(result['state'].price, 85.5)
        self.assertEqual(result['summary']['positions']['long']['quantity'], 0)
        self.assertGreater(result['summary']['switch_close_fee_paid'], 0)

    def test_funding_boundary_requires_actual_event(self):
        initial = replace(state(), timestamp_ms=8*HOUR-60_000)
        candle = bar(8*HOUR-60_000)
        with self.assertRaisesRegex(ValueError, 'funding'):
            track_bars(initial, [candle], start_ms=0)
        result = track_bars(initial, [candle], start_ms=0,
                            funding_events=[dict(timestamp_ms=8*HOUR, rate=.001)])
        self.assertLess(result['state'].funding, 0)
        self.assertEqual(len([row for row in result['ledger'] if row['kind'] == 'funding']), 1)

    def test_incomplete_candle_gap_duplicate_and_invalid_ohlc_rejected(self):
        for bars, kwargs in [([bar()], {'asof_ms': 30_000}),
                             ([bar(60_000)], {}),
                             ([bar(), bar()], {}),
                             ([dict(timestamp_ms=0, open=100, high=90, low=99, close=100)], {})]:
            with self.subTest(bars=bars):
                with self.assertRaises(ValueError):
                    track_bars(state(), bars, **kwargs)

    def test_summary_contains_signed_distances_and_funding(self):
        running = advance(state(), 89, 60_000)
        result = track_summary(running, 0, 60_000)
        self.assertLess(result['distance_low_pct'], 0)
        self.assertEqual(result['funding'], running.funding)
        self.assertEqual(result['grid_profit'], running.grid_profit)
        self.assertEqual(result['grid_net_profit'], running.grid_net_profit)

    def test_intrahour_emergency_survives_price_recovery(self):
        running = replace(state(investment=200, leverage=10,
                                range_exit_stop_pct=None), orders=())
        candle = dict(timestamp_ms=0, open=100, high=101, low=83, close=100)
        summary = track_bars(running, [candle])['summary']
        self.assertTrue(summary['emergency'])
        self.assertLess(summary['closest_liquidation_pct'], 5)
        self.assertGreater(summary['distance_liquidation_pct'], 5)

    def test_hourly_ledgers_do_not_repeat_previous_fills(self):
        first = track_bars(state(), [bar()], start_ms=0)
        second = track_bars(first['state'], [bar(60_000)], start_ms=0)
        combined = first['ledger'] + second['ledger']
        self.assertEqual(len(combined), len({row['event_id'] for row in combined}))
        self.assertAlmostEqual(sum(row['fee'] for row in combined), second['state'].fees)

    def test_equity_path_retains_every_intralevel_mark_across_all_bars(self):
        initial = state()
        bars = [dict(timestamp_ms=t, open=100, low=95, high=109, close=100)
                for t in (0, 60_000)]
        expected, running = list(initial.equity_marks), initial
        for candle in bars:
            for price, timestamp, gap in [(100, candle['timestamp_ms'], True),
                     (95, candle['timestamp_ms']+20_000, False),
                     (109, candle['timestamp_ms']+40_000, False),
                     (100, candle['timestamp_ms']+60_000, False)]:
                running = advance(running, price, timestamp, gap=gap)
                expected.extend(running.equity_marks)
        result = track_bars(initial, bars)
        self.assertEqual(result['equity_path'], expected)
        self.assertGreater(len(expected), 20)
        self.assertGreater(max(expected), result['summary']['net_equity'])

    def test_range_risk_keeps_worst_intrahour_distance_after_recovery(self):
        running = replace(state(investment=200, leverage=10,
                                range_exit_stop_pct=None), orders=())
        candle = dict(timestamp_ms=0, open=100, high=101, low=83, close=100)
        summary = track_bars(running, [candle])['summary']
        self.assertLess(summary['closest_liquidation_range_pct'],
                        summary['distance_liquidation_range_pct'])
        self.assertGreater(summary['closest_liquidation_range_pct'], 0)

    def test_terminal_stop_retains_preclose_liquidation_distance(self):
        result = track_bars(state(stop_loss=85),
                 [dict(timestamp_ms=0, open=100, high=100, low=80, close=90)])
        summary = result['summary']
        self.assertIsNotNone(summary['closest_liquidation_range_pct'])
        self.assertIsNotNone(summary['distance_liquidation_range_pct'])
        self.assertEqual(summary['distance_stop_loss_pct'], 0)
        self.assertLessEqual(summary['closest_liquidation_range_pct'],
                             summary['distance_liquidation_range_pct'])

    def test_liquidation_forces_zero_closest_distance(self):
        result = track_bars(state(investment=200, leverage=10),
                 [dict(timestamp_ms=0, open=100, high=100, low=1, close=90)])
        self.assertTrue(result['summary']['liquidated'])
        self.assertEqual(result['summary']['closest_liquidation_range_pct'], 0)
        self.assertEqual(result['summary']['closest_liquidation_pct'], 0)
        self.assertEqual(result['equity_path'][-1], result['summary']['net_equity'])

    def test_equity_timeline_preserves_values_order_and_model_time(self):
        result = track_bars(state(), [bar(), bar(60_000)])
        timeline = result['equity_timeline']
        self.assertEqual([row['equity'] for row in timeline], result['equity_path'])
        times = [row['timestamp_ms'] for row in timeline]
        self.assertEqual(times, sorted(times))
        self.assertEqual(times[0], 0)
        self.assertEqual(times[-1], 120_000)
        self.assertEqual(len(timeline), len(result['equity_path']))
        self.assertEqual(result['equity_timeline_time_basis'],
                         'modeled OHLC vertex time; preserve mark order within timestamp')

    def test_floating_timeline_retains_intralevel_losses_before_recovery(self):
        initial = state()
        candle = dict(timestamp_ms=0, open=100, low=95, high=109, close=100)
        expected, running = [], initial
        for price, timestamp, gap in [(100, 0, True), (95, 20000, False),
                                       (109, 40000, False), (100, 60000, False)]:
            running = advance(running, price, timestamp, gap=gap)
            self.assertEqual(len(running.floating_pnl_marks), len(running.equity_marks))
            expected.extend(running.floating_pnl_marks)
        result = track_bars(initial, [candle])
        self.assertEqual([row['floating_pnl'] for row in result['equity_timeline']], expected)
        self.assertLess(min(expected), result['summary']['floating_pnl'])

    def test_all_modes_expose_both_five_percent_hard_stops_and_distances(self):
        for direction in ('long', 'short', 'neutral'):
            with self.subTest(direction=direction):
                config = GridConfig(pair='T', low=90, high=110, grids=20,
                                    quantity=1, direction=direction)
                summary = track_summary(create_bot(config, 100, 0), 0, HOUR)
                self.assertEqual(summary['range_exit_stop_low'], 85.5)
                self.assertEqual(summary['range_exit_stop_high'], 115.5)
                self.assertEqual(summary['effective_stop_loss_low'], 85.5)
                self.assertEqual(summary['effective_stop_loss_high'], 115.5)
                self.assertAlmostEqual(summary['distance_range_exit_stop_low_pct'], 14.5/85.5*100)
                self.assertAlmostEqual(summary['distance_range_exit_stop_high_pct'], 15.5/115.5*100)

    def test_closest_stop_distance_uses_effective_nearer_custom_threshold(self):
        summary = track_summary(state(stop_loss=88), 0, HOUR)
        self.assertEqual(summary['effective_stop_loss_low'], 88)
        self.assertEqual(summary['range_exit_stop_low'], 85.5)
        self.assertAlmostEqual(summary['distance_stop_loss_pct'], 12/88*100)

    def test_upper_hard_stop_is_reported_for_long_without_a_custom_upper_stop(self):
        result = track_bars(state(), [dict(timestamp_ms=0, open=100, low=99,
                                          high=120, close=100)])
        summary = result['summary']
        self.assertTrue(summary['stop_loss_hit'])
        self.assertEqual(summary['price'], 115.5)
        self.assertEqual(summary['distance_stop_loss_pct'], 0)
        self.assertEqual(summary['distance_range_exit_stop_high_pct'], 0)

    def test_latched_low_stop_is_reported_instead_of_static_five_percent_preview(self):
        initial = state(adaptive_range_stops=True)
        event = (60000, 'low', .01, 'insufficient_liquidation_clearance')
        latched = replace(initial, effective_range_exit_stop_pct_low=.01,
                          protective_reason='risk_protection', adaptive_stop_events=(event,))
        summary = track_summary(latched, 0, HOUR)
        self.assertEqual(summary['range_exit_stop_low'], 89.1)
        self.assertEqual(summary['range_exit_stop_high'], 115.5)
        self.assertEqual(summary['effective_range_exit_stop_pct_low'], .01)
        self.assertEqual(summary['protective_reason'], 'risk_protection')
        self.assertEqual(summary['adaptive_stop_events'], [event])
        self.assertEqual(summary['full_inventory_preview']['range_exit_stop_low'], 85.5)
        self.assertAlmostEqual(summary['distance_range_exit_stop_low_pct'], 10.9/89.1*100)

    def test_latched_upper_stop_retains_custom_nearer_stop_and_reported_reason(self):
        config = GridConfig(pair='T', low=90, high=110, grids=20, quantity=1,
                            direction='short', stop_loss=110.5, adaptive_range_stops=True)
        initial = create_bot(config, 100, 0)
        latched = replace(initial, effective_range_exit_stop_pct_high=.01,
                          protective_reason='risk_protection')
        summary = track_summary(latched, 0, HOUR)
        self.assertEqual(summary['range_exit_stop_high'], 111.1)
        self.assertEqual(summary['effective_stop_loss_high'], 110.5)
        self.assertEqual(summary['protective_reason'], 'risk_protection')
        self.assertEqual(summary['effective_range_exit_stop_pct_high'], .01)

    def test_tracking_carries_adaptive_latch_from_live_model_advance_to_close(self):
        initial = state(investment=200, leverage=10, adaptive_range_stops=True)
        result = track_bars(initial, [dict(timestamp_ms=0, open=100,
                            low=88, high=101, close=100)])
        summary = result['summary']
        self.assertTrue(summary['adaptive_stop_latched_low'])
        self.assertEqual(summary['effective_range_exit_stop_pct_low'], .01)
        self.assertEqual(summary['price'], 89.1)
        self.assertEqual(summary['distance_stop_loss_pct'], 0)
        self.assertEqual(summary['adaptive_stop_events'][0][1:],
                         ('low', .01, 'insufficient_liquidation_clearance'))
        self.assertFalse(summary['liquidated'])
        self.assertAlmostEqual(sum(row['fee'] for row in result['ledger']), summary['fees'])

    def test_adaptive_early_warning_does_not_offer_more_than_fixed_reserve(self):
        initial = state(investment=200, leverage=10, adaptive_range_stops=True)
        warning = track_summary(replace(initial, price=83), 0, HOUR)
        self.assertTrue(warning['emergency'])
        self.assertGreater(warning['distance_liquidation_pct'], 1)
        self.assertLess(warning['distance_liquidation_pct'], 5)
        self.assertEqual(warning['emergency_action'],
            'verify tightened 1% range protection or stop; no additional margin beyond fixed reserve')
        self.assertNotIn('add reserve', warning['emergency_action'])

    def test_historical_gap_does_not_invent_crossings_or_missing_minute_fills(self):
        candle = dict(timestamp_ms=120_000, open=99, high=99, low=99, close=99)
        result = track_bars(state(), [candle], historical_candle_only=True)
        self.assertEqual(result['state'].completed_grids, 0)
        self.assertTrue(all(row['kind'] == 'seed' for row in result['ledger']))
        self.assertEqual(result['summary']['missing_minutes'], 2)
        self.assertEqual(result['summary']['observed_minutes'], 1)
        self.assertAlmostEqual(result['summary']['execution_coverage_pct'], 100/3)
        self.assertEqual(result['summary']['asof_ms'], 180_000)
        self.assertTrue(all(row['timestamp_ms'] >= 120_000 for row in result['equity_timeline']))
        with self.assertRaises(ValueError):
            track_bars(state(), [candle])

    def test_historical_unknown_funding_is_not_reported_as_measured_zero(self):
        candle = dict(timestamp_ms=24*HOUR+60_000, open=100, high=100, low=100, close=100)
        result = track_bars(state(), [candle], historical_candle_only=True)
        unknown = [row for row in result['ledger'] if row['kind'] == 'funding_unknown']
        self.assertEqual([row['timestamp_ms'] for row in unknown], [8*HOUR, 16*HOUR, 24*HOUR])
        self.assertTrue(all(row['cash'] is None and row['modeled_cash'] == 0 for row in unknown))
        self.assertIsNone(result['summary']['funding'])
        self.assertEqual(result['summary']['funding_modeled_cash'], 0)
        self.assertEqual(result['summary']['unknown_funding_settlements'], [8*HOUR, 16*HOUR, 24*HOUR])
        self.assertEqual(result['summary']['net_before_unknown_funding'],
                         result['summary']['net_modeled'])
        self.assertFalse(result['summary']['spread_observed'])

    def test_historical_known_gap_funding_uses_labeled_carried_mark_without_fills(self):
        initial = replace(state(), timestamp_ms=8*HOUR-60_000)
        candle = dict(timestamp_ms=8*HOUR+60_000, open=99, high=99, low=99, close=99)
        result = track_bars(initial, [candle], historical_candle_only=True,
                           funding_events=[dict(timestamp_ms=8*HOUR, rate=.001)])
        estimated = [row for row in result['ledger'] if row['kind'] == 'funding_estimated']
        self.assertEqual(len(estimated), 1)
        self.assertTrue(estimated[0]['mark_carried_forward'])
        self.assertEqual(estimated[0]['mark'], 100)
        self.assertAlmostEqual(estimated[0]['modeled_cash'], -1)
        self.assertFalse(any(row['kind'].startswith('grid_') for row in result['ledger']))
        self.assertAlmostEqual(result['summary']['funding_modeled_cash'], -1)

    def test_historical_asof_counts_unobserved_exposure_without_synthetic_bars(self):
        result = track_bars(state(), [bar()], asof_ms=2*HOUR, historical_candle_only=True)
        self.assertEqual(result['summary']['asof_ms'], 2*HOUR)
        self.assertEqual(result['summary']['missing_minutes'], 119)
        self.assertEqual(result['last_observed_ms'], 60_000)
        self.assertAlmostEqual(result['summary']['realized_gph_6h'],
                               result['state'].completed_grids / 2)
        self.assertLessEqual(max(row['timestamp_ms'] for row in result['equity_timeline']), 60_000)

    def test_historical_gap_stop_uses_actual_open_and_missing_extremes_remain_unknown(self):
        candle = dict(timestamp_ms=120_000, open=80, high=80, low=80, close=80)
        result = track_bars(state(), [candle], historical_candle_only=True)
        self.assertEqual(result['state'].price, 80)
        self.assertEqual(result['state'].stop_reason, 'stop_loss')
        closes = [row for row in result['ledger'] if row['kind'] == 'stop_loss']
        self.assertTrue(all(row['price'] == 80 and row['timestamp_ms'] == 120_000 for row in closes))
        self.assertFalse(result['summary']['missing_extremes_observed'])

    def test_historical_gap_liquidation_uses_observed_open_without_interpolated_threshold(self):
        candle = dict(timestamp_ms=120_000, open=1, high=1, low=1, close=1)
        result = track_bars(state(investment=200, leverage=10, range_exit_stop_pct=None),
                           [candle], historical_candle_only=True)
        self.assertTrue(result['state'].liquidated)
        self.assertEqual(result['state'].price, 1)

    def test_historical_mode_still_rejects_duplicate_invalid_or_incomplete_bars(self):
        for candles, extra in [([bar(), bar()], {}),
                                ([dict(timestamp_ms=60_000, open=100, high=90, low=99, close=100)], {}),
                                ([bar(60_000)], {'asof_ms': 90_000})]:
            with self.assertRaises(ValueError):
                track_bars(state(), candles, historical_candle_only=True, **extra)

    def test_historical_actual_open_retains_adaptive_future_inventory_check(self):
        initial = state(investment=200, leverage=10, adaptive_range_stops=True)
        result = track_bars(initial, [bar()], historical_candle_only=True)
        self.assertEqual(result['state'].adaptive_stop_events[0][0], 0)
        self.assertEqual(result['state'].effective_range_exit_stop_pct_low, .01)

    def test_historical_empty_hour_has_no_claimed_observed_mark_or_new_fills(self):
        result = track_bars(state(), [], asof_ms=HOUR, historical_candle_only=True)
        self.assertIsNone(result['last_observed_ms'])
        self.assertEqual(result['summary']['observed_minutes'], 0)
        self.assertEqual(result['summary']['missing_minutes'], 60)
        self.assertEqual(result['equity_timeline'], [])
        self.assertTrue(all(row['kind'] == 'seed' for row in result['ledger']))

    def test_synthetic_and_indicator_rows_rejected_before_any_execution_in_both_modes(self):
        from unittest.mock import patch
        for historical in (False, True):
            for marker in ('synthetic', 'indicator_only'):
                with self.subTest(historical=historical, marker=marker):
                    candles = [bar(), bar(60_000, **{marker: True})]
                    with patch('trader.research.kucoin_tracker.advance', wraps=advance) as execute:
                        with self.assertRaisesRegex(ValueError, 'synthetic or indicator-only'):
                            track_bars(state(), candles, historical_candle_only=historical)
                        execute.assert_not_called()

    def test_explicitly_observed_rows_with_false_synthetic_flags_still_execute(self):
        for historical in (False, True):
            result = track_bars(state(), [bar(synthetic=False, indicator_only=False)],
                                historical_candle_only=historical)
            self.assertGreater(result['state'].completed_grids, 0)
            if historical:
                self.assertEqual(result['summary']['observed_minutes'], 1)
