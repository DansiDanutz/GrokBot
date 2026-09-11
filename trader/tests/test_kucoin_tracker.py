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
        self.assertEqual(result['state'].price, 85)
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
        running = replace(state(investment=200, leverage=10), orders=())
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
        running = replace(state(investment=200, leverage=10), orders=())
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
