import unittest
from trader.strategies.kucoin_grid import create_bot, advance, stop, preview, net_equity
from trader.strategies.grid_types import GridConfig


class GridTests(unittest.TestCase):
    def config(self, **kw):
        return GridConfig(pair='TESTUSDTM', low=90, high=110, grids=20,
                          quantity=1, **kw)

    def test_neutral_initial_half_gross_seed_is_not_grid_profit(self):
        state = create_bot(self.config(), 100, 0)
        self.assertEqual(sum(p.quantity for p in state.positions), 10)
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
        self.assertEqual(data['order_count'], 20)
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

    def test_missing_neutral_seed_quarters_for_tiny_grid_is_explicit(self):
        config = GridConfig(pair='T', low=90, high=110, grids=2, quantity=1)
        state = create_bot(config, 100, 0)
        self.assertEqual(state.positions, ())

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

    def test_range_exit_cancels_orders(self):
        state = create_bot(self.config(), 100, 0)
        self.assertEqual(advance(state, 111, 60_000).orders, ())

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
        self.assertEqual(sum(p.side == -1 and not p.seed for p in rising.positions), 5)
        returned = advance(rising, 100, 120_000)
        self.assertEqual(returned.completed_grids, 5)
        self.assertAlmostEqual(returned.grid_profit, 5)
        stopped = stop(returned, 100, 180_000)
        self.assertAlmostEqual(net_equity(stopped, 100), 1000 + stopped.grid_profit
                               + stopped.seed_pnl + stopped.close_pnl - stopped.fees)

    def test_neutral_lower_outer_slots_mirror_upper_slots(self):
        state = create_bot(self.config(), 100, 0)
        falling = advance(state, 90, 60_000)
        self.assertEqual(falling.completed_grids, 0)
        self.assertEqual(sum(p.side == 1 and not p.seed for p in falling.positions), 5)
        returned = advance(falling, 100, 120_000)
        self.assertEqual(returned.completed_grids, 5)
        self.assertAlmostEqual(returned.grid_profit, 5)

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

    def test_range_exit_remains_pending_replacement_after_reentry(self):
        state = create_bot(self.config(direction='long'), 100, 0)
        outside = advance(state, 89, 60_000)
        returned = advance(outside, 100, 8 * 3_600_000, funding_rate=.001)
        self.assertEqual(returned.status, 'out_of_range')
        self.assertEqual(returned.orders, ())
        self.assertEqual(returned.range_exits, 1)
        self.assertLess(returned.funding, outside.funding)
        self.assertEqual(returned.completed_grids, outside.completed_grids)

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
