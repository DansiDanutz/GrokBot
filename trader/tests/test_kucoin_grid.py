import unittest
from trader.strategies.kucoin_grid import create_bot, advance, stop, preview, net_equity, floating_pnl
from trader.strategies.grid_types import GridConfig


class GridTests(unittest.TestCase):
    def config(self, **kw):
        return GridConfig(pair='TESTUSDTM', low=90, high=110, grids=20,
                          quantity=1, **kw)

    def test_neutral_initial_dual_leg_seed_is_not_grid_profit(self):
        state = create_bot(self.config(), 100, 0)
        self.assertEqual(sum(p.quantity for p in state.positions), 20)
        state = advance(state, 101, 60_000)
        self.assertEqual(state.completed_grids, 0)
        self.assertGreater(state.seed_pnl, 0)

    def test_roundtrip_and_no_profit_stop(self):
        state = create_bot(self.config(direction='long'), 100, 0)
        state = advance(state, 99, 60_000)
        state = advance(state, 100, 120_000)
        self.assertGreaterEqual(state.completed_grids, 1)
        self.assertEqual(state.status, 'running')
        self.assertEqual(state.completion_times[-1], 120_000)

    def test_optional_trigger_and_outside_range(self):
        state = create_bot(self.config(trigger=102), 100, 0)
        self.assertEqual(state.status, 'waiting')
        state = advance(state, 102, 60_000)
        self.assertEqual(state.status, 'running')
        outside = advance(state, 112, 120_000)
        self.assertEqual(outside.status, 'out_of_range')
        unchanged = advance(outside, 111, 180_000)
        self.assertEqual(outside.orders, unchanged.orders)

    def test_stop_markets_and_cash_reconciles(self):
        state = create_bot(self.config(direction='long'), 100, 0)
        closed = stop(state, 100, 60_000, bid=99.9, ask=100.1)
        self.assertEqual(closed.positions, ())
        self.assertEqual(closed.status, 'stopped')
        self.assertAlmostEqual(net_equity(closed, 100), 1000 + closed.close_pnl - closed.fees)

    def test_funding_boundaries_once(self):
        state = create_bot(self.config(direction='long'), 100, 0)
        funded = advance(state, 100, 8 * 3_600_000, funding_rate=.001)
        repeated = advance(funded, 100, 8 * 3_600_000, funding_rate=.001)
        self.assertAlmostEqual(funded.funding, -1)
        self.assertEqual(funded.funding, repeated.funding)

    def test_bad_numbers_and_time(self):
        for kwargs in ({'leverage': 11}, {'quantity': float('nan')}):
            with self.assertRaises(ValueError):
                create_bot(GridConfig(pair='T', low=90, high=110, **kwargs), 100, 0)
        state = create_bot(self.config(), 100, 10)
        with self.assertRaises(ValueError):
            advance(state, 100, 9)

    def test_preview_is_estimate_and_fee_aware(self):
        data = preview(self.config())
        self.assertEqual(data['order_count'], 40)
        self.assertGreater(data['profit_per_grid_min'], 0)
        self.assertTrue(data['liquidation_estimate'])

    def test_large_loss_liquidates(self):
        state = create_bot(self.config(direction='long', investment=200, leverage=10), 100, 0)
        state = advance(state, 1, 60_000)
        self.assertTrue(state.liquidated)
        self.assertEqual(state.status, 'liquidated')

    def test_reference_notional_cap(self):
        with self.assertRaises(ValueError):
            create_bot(self.config(investment=50, leverage=10), 100, 0)

    def test_short_roundtrip_profit_fees_and_floating(self):
        state = create_bot(self.config(direction='short'), 100, 0)
        state = advance(state, 101, 60_000)
        state = advance(state, 100, 120_000)
        self.assertEqual(state.completed_grids, 1)
        self.assertAlmostEqual(state.grid_profit, 1)
        self.assertAlmostEqual(state.grid_net_profit, 1 - .0006 * 201)
        final = stop(state, 100, 180_000)
        self.assertAlmostEqual(net_equity(final, 100),
                               1000 + final.grid_profit + final.seed_pnl
                               + final.close_pnl + final.funding - final.fees)

    def test_liquidation_occurs_at_threshold_inside_jump(self):
        state = create_bot(self.config(direction='long', investment=200, leverage=10), 100, 0)
        state = advance(state, 1, 60_000)
        self.assertTrue(state.liquidated)
        self.assertGreater(state.price, 1)
        self.assertGreater(net_equity(state, state.price), 0)

    def test_small_neutral_grid_still_has_both_legs(self):
        config = GridConfig(pair='T', low=90, high=110, grids=2, quantity=1)
        state = create_bot(config, 100, 0)
        self.assertEqual(len(state.positions), 2)

    def test_preview_uses_full_inventory_cost_and_fees(self):
        cfg = self.config(direction='long')
        estimate = preview(cfg)
        # Ten seed units at100; lower ten entries90..99 =1945 entryUSDT.
        cost = 1000 + sum(range(90, 100))
        expected = (cost + cost * .0006 - 1000) / (20 * (1 - .005))
        self.assertAlmostEqual(estimate['liquidation_price_long'], expected)
        self.assertEqual(estimate['liquidation_scenario'], 'all_directional_grid_entries_filled')

    def test_explicit_quantity_respects_contract_lots(self):
        cfg = GridConfig(pair='T', low=90, high=110, quantity=1.05, multiplier=.1)
        with self.assertRaises(ValueError):
            create_bot(cfg, 100, 0)

    def test_terminal_stop_idempotent_preserves_liquidation(self):
        state = create_bot(self.config(direction='long', investment=200, leverage=10), 100, 0)
        liquidated = advance(state, 1, 60_000)
        self.assertEqual(stop(liquidated, 2, 120_000), liquidated)

    def test_range_exit_keeps_ladder_for_reentry(self):
        state = create_bot(self.config(), 100, 0)
        self.assertEqual(len(advance(state, 111, 60_000).orders), 40)

    def test_neutral_initial_orders_are_resting_not_marketable(self):
        state = create_bot(self.config(), 100, 0)
        for order in state.orders:
            if order.side == 1:
                self.assertLessEqual(order.price, state.price)
            else:
                self.assertGreaterEqual(order.price, state.price)

    def test_neutral_outer_slots_open_on_outward_path_and_close_on_return(self):
        state = create_bot(self.config(), 100, 0)
        rising = advance(state, 110, 60_000)
        self.assertEqual(rising.completed_grids, 0)
        self.assertEqual(sum(p.side == -1 and not p.seed for p in rising.positions), 10)
        returned = advance(rising, 100, 120_000)
        self.assertEqual(returned.completed_grids, 10)
        self.assertAlmostEqual(returned.grid_profit, 10)
        stopped = stop(returned, 100, 180_000)
        self.assertAlmostEqual(net_equity(stopped, 100), 1000 + stopped.grid_profit
                               + stopped.seed_pnl + stopped.close_pnl - stopped.fees)

    def test_neutral_lower_outer_slots_mirror_upper_slots(self):
        state = create_bot(self.config(), 100, 0)
        falling = advance(state, 90, 60_000)
        self.assertEqual(falling.completed_grids, 0)
        self.assertEqual(sum(p.side == 1 and not p.seed for p in falling.positions), 10)
        returned = advance(falling, 100, 120_000)
        self.assertEqual(returned.completed_grids, 10)
        self.assertAlmostEqual(returned.grid_profit, 10)

    def test_floating_loss_survives_first_jump_liquidation(self):
        state = create_bot(self.config(direction='long', investment=200, leverage=10), 100, 0)
        closed = advance(state, 1, 60_000)
        self.assertTrue(closed.liquidated)
        self.assertGreater(closed.max_floating_loss, 100)
        self.assertLess(closed.max_floating_loss, 200)
        self.assertEqual(stop(closed, 1, 120_000).max_floating_loss,
                         closed.max_floating_loss)

    def test_max_floating_loss_is_monotonic_after_recovery_and_close(self):
        state = create_bot(self.config(direction='long'), 100, 0)
        loss = advance(state, 95, 60_000)
        recovery = advance(loss, 101, 120_000)
        self.assertGreater(loss.max_floating_loss, 0)
        self.assertGreaterEqual(recovery.max_floating_loss, loss.max_floating_loss)
        self.assertGreaterEqual(stop(recovery, 100, 180_000).max_floating_loss,
                                recovery.max_floating_loss)

    def test_range_reentry_resumes_trading(self):
        state = create_bot(self.config(direction='long'), 100, 0)
        outside = advance(state, 89, 60_000)
        returned = advance(outside, 100, 120_000)
        self.assertEqual(returned.status, 'running')
        self.assertEqual(returned.range_exits, 1)
        self.assertEqual(returned.completed_grids, 10)

    def test_interior_equity_marks_match_grid_level_split_peak_drawdown(self):
        state = advance(create_bot(self.config(), 100, 0), 95, 1_000)
        coarse = advance(state, 109, 30_000)
        split, split_marks = state, []
        for index, price in enumerate(range(96, 110), 1):
            split = advance(split, price, 1_000 + index * 1_000)
            split_marks.extend(split.equity_marks)
        def stats(marks):
            peak, drawdown = marks[0], 0
            for value in marks:
                peak = max(peak, value)
                drawdown = max(drawdown, (peak - value) / peak)
            return peak, drawdown
        self.assertAlmostEqual(stats(coarse.equity_marks)[0], stats(split_marks)[0])
        self.assertAlmostEqual(stats(coarse.equity_marks)[1], stats(split_marks)[1])
        self.assertGreater(max(coarse.equity_marks), net_equity(coarse, 109))
        self.assertAlmostEqual(net_equity(coarse, 109), net_equity(split, 109))

    def test_equity_marks_are_ephemeral_and_include_liquidation_fee(self):
        state = create_bot(self.config(direction='long', investment=200, leverage=10), 100, 0)
        closed = advance(state, 1, 60_000)
        self.assertAlmostEqual(closed.equity_marks[-1], net_equity(closed, closed.price))
        self.assertGreater(closed.equity_marks[-2], closed.equity_marks[-1])
        self.assertGreater(min(closed.equity_marks), 0)
        same = advance(create_bot(self.config(), 100, 0), 100, 1_000)
        later = advance(same, 100, 2_000)
        self.assertEqual(len(same.equity_marks), len(later.equity_marks))

    def test_equity_marks_include_funding_cash_change(self):
        state = create_bot(self.config(direction='long'), 100, 0)
        funded = advance(state, 100, 8 * 3_600_000, funding_rate=.001)
        self.assertAlmostEqual(funded.equity_marks[-2] - funded.equity_marks[-1], 1)
        self.assertAlmostEqual(funded.equity_marks[-1], net_equity(funded, 100))

    def test_boundary_price_fills_precede_funding_for_closed_inventory(self):
        state = create_bot(self.config(direction='long'), 100, 0)
        boundary = advance(state, 110, 8 * 3_600_000, funding_rate=.001)
        self.assertEqual(boundary.positions, ())
        self.assertEqual(boundary.funding, 0)

    def test_boundary_funding_charges_inventory_opened_along_path(self):
        state = create_bot(self.config(direction='long'), 100, 0)
        boundary = advance(state, 90, 8 * 3_600_000, funding_rate=.001)
        self.assertEqual(sum(p.quantity for p in boundary.positions), 20)
        self.assertAlmostEqual(boundary.funding, -1.8)

    def test_funding_can_force_liquidation_after_boundary_fills(self):
        state = create_bot(self.config(direction='long', investment=200, leverage=10), 100, 0)
        boundary = advance(state, 100, 8 * 3_600_000, funding_rate=.3)
        self.assertTrue(boundary.liquidated)
        self.assertEqual(boundary.price, 100)
        self.assertAlmostEqual(boundary.funding, -300)
        self.assertAlmostEqual(boundary.equity_marks[-1], net_equity(boundary, 100))

    def test_reserve_is_equity_but_does_not_increase_quantity(self):
        from dataclasses import replace
        cfg = self.config(direction='long', investment=200, leverage=10)
        reserved = replace(cfg, reserved_margin=300)
        self.assertEqual(preview(cfg)['quantity'], preview(reserved)['quantity'])
        self.assertLess(preview(reserved)['liquidation_price_long'],
                        preview(cfg)['liquidation_price_long'])
        self.assertEqual(reserved.total_margin, 500)
        self.assertAlmostEqual(net_equity(create_bot(reserved, 100, 0), 100), 499.4)

    def test_entry_changes_full_inventory_liquidation_and_initial_share(self):
        from dataclasses import replace
        cfg = replace(self.config(direction='long'), entry_price=95)
        started = create_bot(cfg, 95, 0)
        self.assertEqual(len(started.positions), 15)
        cost = 15 * 95 + sum(range(90, 95))
        expected = (cost * 1.0006 - 1000) / (20 * .995)
        self.assertAlmostEqual(preview(cfg)['liquidation_price_long'], expected)

    def test_stop_loss_is_outside_range_and_neutral_requires_both(self):
        for kwargs in ({'direction': 'long', 'stop_loss': 91},
                       {'direction': 'short', 'stop_loss': 109},
                       {'direction': 'neutral', 'stop_loss': 85}):
            with self.assertRaises(ValueError):
                create_bot(self.config(**kwargs), 100, 0)

    def test_continuous_stop_loss_closes_at_threshold_before_future_price(self):
        state = create_bot(self.config(direction='long', stop_loss=86), 100, 0)
        stopped = advance(state, 70, 60_000)
        self.assertEqual(stopped.stop_reason, 'stop_loss')
        self.assertEqual(stopped.price, 86)
        self.assertEqual(stopped.positions, ())
        self.assertGreaterEqual(min(stopped.equity_marks), net_equity(stopped, 86))

    def test_gap_stop_does_not_promise_threshold_price(self):
        state = create_bot(self.config(direction='long', stop_loss=85), 100, 0)
        stopped = advance(state, 70, 60_000, gap=True)
        self.assertEqual(stopped.stop_reason, 'stop_loss')
        self.assertEqual(stopped.price, 70)
        self.assertTrue(all(fill.price == 70 for fill in stopped.fill_events))

    def test_short_and_neutral_upper_stop(self):
        for direction, stops in [('short', {'stop_loss': 115}),
                                 ('neutral', {'stop_loss': 85, 'stop_loss_high': 115})]:
            state = create_bot(self.config(direction=direction, **stops), 100, 0)
            self.assertEqual(advance(state, 120, 60_000).price, 115)

    def test_every_fill_has_immutable_event_and_fees_reconcile(self):
        from dataclasses import FrozenInstanceError
        state = create_bot(self.config(direction='long'), 100, 0)
        events = list(state.fill_events)
        for index, price in enumerate([99, 100, 111, 100], 1):
            state = advance(state, price, index * 60_000)
            events.extend(state.fill_events)
        state = stop(state, 100, 300_000)
        events.extend(state.fill_events)
        self.assertEqual(len({event.event_id for event in events}), len(events))
        self.assertAlmostEqual(sum(event.fee for event in events), state.fees)
        self.assertEqual(sum(event.completed_grid for event in events), state.completed_grids)
        with self.assertRaises(FrozenInstanceError):
            events[0].price = 1

    def test_moving_path_crossing_unsplit_funding_is_rejected(self):
        state = create_bot(self.config(direction='long'), 100, 0)
        with self.assertRaises(ValueError):
            advance(state, 90, 9 * 3_600_000, funding_rate=.001)

    def test_ray_observed_dual_leg_inventory_and_order_split(self):
        cfg = GridConfig(pair='RAYUSDT', low=1.1, high=2, grids=70, leverage=5,
                         investment=1000, reserved_margin=200, direction='neutral',
                         entry_price=1.574, quantity=17, tick_size=.0001,
                         maintenance_rate=.015)
        state = create_bot(cfg, 1.574, 0)
        self.assertEqual(len(state.orders), 140)
        self.assertEqual(len(state.fill_events), 69)
        self.assertEqual(sum(p.quantity for p in state.positions if p.side == 1), 544)
        self.assertEqual(sum(p.quantity for p in state.positions if p.side == -1), 629)
        self.assertEqual(sum(o.side == 1 for o in state.orders), 75)
        self.assertEqual(sum(o.side == -1 for o in state.orders), 65)
        self.assertAlmostEqual(max(o.price for o in state.orders), 1.996)
        self.assertEqual(len({o.slot for o in state.orders}), 140)
        self.assertEqual(floating_pnl(state, 1.574), 0)

    def test_ray_user_percentage_formula_and_actual_profit_are_separate(self):
        from trader.strategies.kucoin_grid import margin_profit_per_grid
        cfg = GridConfig(pair='RAYUSDT', low=1.1, high=2, grids=70, leverage=5,
                         investment=1000, reserved_margin=200, direction='neutral',
                         entry_price=1.574, quantity=17, tick_size=.0001)
        result = preview(cfg)
        self.assertAlmostEqual(result['grid_step'], .0128)
        self.assertAlmostEqual(result['kucoin_profit_pct_min'], 2.60)
        self.assertLess(abs(result['kucoin_profit_pct_max'] - 5.21), .3)
        self.assertAlmostEqual(result['kucoin_profit_usdt_min'], 1000 / 70 * .026)
        self.assertAlmostEqual(margin_profit_per_grid(cfg, 1.574)['usdt'],
                               1000 / 70 * (.0128 / 1.574 * 5 - .006))
        self.assertLess(result['profit_per_grid_min'], result['kucoin_profit_usdt_min'])

    def test_neutral_long_and_short_slots_survive_same_interval_roundtrip(self):
        state = create_bot(self.config(), 100, 0)
        for index, price in enumerate([99, 101, 99, 101], 1):
            state = advance(state, price, index * 1000)
        self.assertEqual(len(state.orders), 40)
        self.assertEqual(len({p.slot for p in state.positions}), len(state.positions))
        self.assertGreater(state.completed_grids, 3)
        self.assertEqual(len(state.equity_marks), len(state.floating_pnl_marks))
        self.assertAlmostEqual(state.floating_pnl_marks[-1], floating_pnl(state, 101))

    def test_five_percent_outside_range_stops_all_modes_both_edges(self):
        for direction in ('long', 'short', 'neutral'):
            for edge, before, beyond in ((85.5, 85.5001, 80), (115.5, 115.4999, 120)):
                with self.subTest(direction=direction, edge=edge):
                    state = create_bot(self.config(direction=direction), 100, 0)
                    before_state = advance(state, before, 1_000)
                    self.assertEqual(before_state.status, 'out_of_range')
                    at_edge = advance(before_state, edge, 2_000)
                    self.assertEqual(at_edge.status, 'stopped')
                    self.assertEqual(at_edge.stop_reason, 'stop_loss')
                    self.assertEqual(at_edge.price, edge)
                    beyond_state = advance(state, beyond, 1_000)
                    self.assertEqual(beyond_state.price, edge)

    def test_historical_baseline_can_explicitly_disable_new_range_stop(self):
        state = create_bot(self.config(direction='long', range_exit_stop_pct=None), 100, 0)
        outside = advance(state, 80, 1_000)
        self.assertEqual(outside.status, 'out_of_range')
        self.assertIsNone(preview(state.config)['range_exit_stop_low'])
        self.assertEqual(advance(outside, 100, 2_000).status, 'running')

    def test_range_stop_gap_uses_observed_execution_price(self):
        state = create_bot(self.config(direction='long'), 100, 0)
        outside = advance(state, 80, 1_000, bid=79.9, ask=80.1, gap=True)
        self.assertEqual(outside.price, 80)
        self.assertEqual(outside.stop_reason, 'stop_loss')
        self.assertTrue(all(event.price == 79.9 for event in outside.fill_events))

    def test_stop_preview_shows_both_edges_and_nearer_custom_stop(self):
        result = preview(self.config(direction='long', stop_loss=87))
        self.assertEqual(result['range_exit_stop_low'], 85.5)
        self.assertEqual(result['range_exit_stop_high'], 115.5)
        self.assertEqual(result['effective_stop_loss_low'], 87)
        self.assertEqual(result['effective_stop_loss_high'], 115.5)
        for invalid in (-.1, 0, 1, float('nan')):
            with self.assertRaises(ValueError):
                preview(self.config(range_exit_stop_pct=invalid))
