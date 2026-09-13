"""Offline accounting examples for the immutable paper grid engine."""
import copy
import json
import math
from pathlib import Path
import unittest

from trader.papergrid import close_bot, open_bot, step
from trader.papergrid.engine import FEE_RATE_MAKER, FEE_RATE_TAKER


FIXTURES = Path(__file__).resolve().parents[2] / "tests/fixtures/papergrid"
BOUNDARY = 8 * 60 * 60 * 1000
FEE = FEE_RATE_TAKER  # seed/close-out fills stay taker; grid fills are maker


def specification(direction="NEUTRAL", **changes):
    result = dict(bot_id="fixture", symbol="RAYUSDTM", direction=direction,
                  range_low=81.0, range_high=121.0, step_pct=10.0, grids=4,
                  notional_usdt=1000.0, leverage=3.0, funding_pct=0.01)
    result.update(changes)
    return result


class PaperGridTests(unittest.TestCase):
    def check_identity(self, bot):
        net = (bot["realized_pnl"] + bot["unrealized_pnl"]
               - bot["fees_paid"] - bot["funding_paid"])
        self.assertAlmostEqual(bot["equity"], bot["notional_usdt"] + net)
        self.assertGreaterEqual(bot["peak_equity"], bot["equity"])
        self.assertGreaterEqual(bot["max_drawdown_pct"], 0)

    def check_orders(self, bot):
        self.assertEqual(len(bot["orders"]), bot["grids"])
        occupied = {order["line"] for order in bot["orders"]}
        self.assertEqual(len(occupied), bot["grids"])
        self.assertEqual(occupied | {bot["empty_line"]}, set(range(bot["grids"] + 1)))

    def test_geometric_lines_and_directional_seed(self):
        for direction in ("LONG", "SHORT", "NEUTRAL"):
            with self.subTest(direction=direction):
                spec = specification(direction)
                before = copy.deepcopy(spec)
                bot = open_bot(spec, 100, 0)
                self.assertEqual(spec, before)
                self.assertAlmostEqual(bot["lines"][0], 81)
                self.assertAlmostEqual(bot["lines"][-1], 121)
                ratios = [b / a for a, b in zip(bot["lines"], bot["lines"][1:])]
                self.assertTrue(all(math.isclose(ratios[0], value) for value in ratios))
                self.assertEqual(bot["empty_line"], 2)
                self.assertAlmostEqual(bot["contracts_per_line"], 7.5)
                expected = {"LONG": 15, "SHORT": -15, "NEUTRAL": 0}[direction]
                self.assertAlmostEqual(bot["position_contracts"], expected)
                self.assertAlmostEqual(bot["fees_paid"], abs(expected) * 100 * FEE)
                self.check_identity(bot)
                self.check_orders(bot)

    def test_seed_counts_seeded_orders_excluding_nearest_empty_line(self):
        for direction, price, sign in (("LONG", 98, 1), ("SHORT", 100, -1)):
            with self.subTest(direction=direction):
                bot = open_bot(specification(direction), price, 0)
                self.assertEqual(bot["empty_line"], 2)
                seeded = [order for order in bot['orders'] if order['paired_line'] is not None]
                self.assertEqual(len(seeded), 2)
                self.assertEqual(bot["position_contracts"], sign * len(seeded) * bot["contracts_per_line"])
                self.assertNotIn(2, [order["line"] for order in bot["orders"]])
                exact = open_bot(specification(direction), bot["lines"][2], 0)
                self.assertEqual(exact["position_contracts"], sign * 2 * exact["contracts_per_line"])
                finished, _ = step(bot, dict(ts_ms=1, price=bot['range_high'] if sign == 1 else bot['range_low']))
                self.assertEqual(finished['position_contracts'], 0)

    def test_explicit_legacy_maker_override_pays_one_third_of_bot_fee(self):
        # Two identical LONG bots walk the same one-grid path; the control
        # bot is forced to pay the taker rate on its grid fills. Same seed
        # fee (taker) on both, so the fee delta is purely the grid fills.
        maker_bot = open_bot(specification("LONG", fee_rate_maker=FEE_RATE_MAKER), 100, 0)
        taker_bot = open_bot(specification("LONG", fee_rate_maker=FEE_RATE_TAKER),
                             100, 0)
        seed_fees = maker_bot["fees_paid"]
        target = maker_bot["lines"][3]  # one adjacent sell line above the empty
        maker_bot, _ = step(maker_bot, {"ts_ms": 1000, "price": target})
        taker_bot, _ = step(taker_bot, {"ts_ms": 1000, "price": target})
        self.assertGreater(maker_bot["completed_grids"], 0)
        maker_grid_fees = maker_bot["fees_paid"] - seed_fees
        taker_grid_fees = taker_bot["fees_paid"] - seed_fees
        self.assertAlmostEqual(maker_grid_fees * 3, taker_grid_fees, places=10)
        self.assertAlmostEqual(maker_grid_fees, taker_grid_fees / 3)
        self.assertAlmostEqual(maker_bot["fee_rate_maker"], FEE_RATE_MAKER)
        self.assertAlmostEqual(maker_bot["fee_rate_taker"], FEE_RATE_TAKER)

    def test_multiline_tick_fills_at_each_limit_in_price_order(self):
        bot = open_bot(specification(), 100, 0)
        before = copy.deepcopy(bot)
        update = {"ts_ms": 1, "price": 80}
        result, events = step(bot, update)
        fills = [event for event in events if event["type"] == "FILL"]
        self.assertEqual([event["price"] for event in fills], [bot["lines"][1], 81])
        self.assertEqual(bot, before)
        self.assertEqual(update, {"ts_ms": 1, "price": 80})
        self.assertEqual(result["fills"], 2)
        self.assertEqual(result["position_contracts"], 15)
        self.assertAlmostEqual(result["avg_entry"], (bot["lines"][1] + 81) / 2)
        self.assertEqual(result["completed_grids"], 0)
        self.check_orders(result)
        self.check_identity(result)

    def test_seeded_grid_profit_and_realized_use_distinct_prices(self):
        bot = open_bot(specification("LONG"), 100, 0)
        target = bot["lines"][3]
        result, _ = step(bot, {"ts_ms": 1, "price": target})
        self.assertEqual(result["completed_grids"], 1)
        self.assertAlmostEqual(result["grid_profit"], 7.5 * (target - bot["lines"][2]))
        self.assertAlmostEqual(result["realized_pnl"], 7.5 * (target - 100))
        self.assertNotAlmostEqual(result["grid_profit"], result["realized_pnl"])
        self.check_identity(result)

    def test_neutral_pair_closes_and_position_reverses_without_double_count(self):
        bot = open_bot(specification(), 100, 0)
        bot, _ = step(bot, {"ts_ms": 1, "price": bot["lines"][1]})
        bot, _ = step(bot, {"ts_ms": 2, "price": bot["lines"][2]})
        self.assertEqual(bot["completed_grids"], 1)
        self.assertAlmostEqual(bot["position_contracts"], 0)
        self.assertAlmostEqual(bot["realized_pnl"], bot["grid_profit"])
        self.assertGreater(bot["realized_pnl"] - bot["fees_paid"], 0)
        self.check_identity(bot)

    def test_short_seeded_pair_reduces_inventory_at_open_cost(self):
        bot = open_bot(specification("SHORT"), 100, 0)
        target = bot["lines"][1]
        result, _ = step(bot, {"ts_ms": 1, "price": target})
        self.assertEqual(result["position_contracts"], -7.5)
        self.assertEqual(result["completed_grids"], 1)
        self.assertAlmostEqual(result["realized_pnl"], 7.5 * (100 - target))
        self.assertAlmostEqual(result["grid_profit"], 7.5 * (bot["lines"][2] - target))
        self.assertAlmostEqual(result["avg_entry"], 100)
        self.check_identity(result)

    def test_net_position_reverses_using_volume_weighted_cost(self):
        bot = open_bot(specification(), 100, 0)
        bot, _ = step(bot, {"ts_ms": 1, "price": 122})
        entry = (bot["lines"][3] + bot["lines"][4]) / 2
        self.assertAlmostEqual(bot["avg_entry"], entry)
        bot, _ = step(bot, {"ts_ms": 2, "price": 80})
        self.assertEqual(bot["position_contracts"], 15)
        self.assertAlmostEqual(bot["avg_entry"], (bot["lines"][1] + bot["lines"][0]) / 2)
        self.assertAlmostEqual(bot["realized_pnl"],
                               7.5 * (2 * entry - bot["lines"][3] - bot["lines"][2]))
        self.check_identity(bot)

    def test_candle_nearest_extreme_path_matches_tick_segments(self):
        for opening, high, low, close in ((100, 113, 95, 104), (100, 104, 85, 96)):
            with self.subTest(low=low):
                original = open_bot(specification(), 100, 0)
                candle = dict(ts_ms=100, open=opening, high=high, low=low, close=close)
                actual, _ = step(original, candle)
                first, second = (low, high) if opening - low < high - opening else (high, low)
                expected = original
                for ts, price in enumerate((opening, first, second, close), 1):
                    expected, _ = step(expected, dict(ts_ms=ts, price=price))
                for field in ("position_contracts", "avg_entry", "realized_pnl", "fees_paid",
                              "grid_profit", "fills", "completed_grids", "equity"):
                    self.assertAlmostEqual(actual[field], expected[field], msg=field)

    def test_funding_missing_boundaries_charged_once_and_override(self):
        bot = open_bot(specification("LONG"), 100, BOUNDARY - 1)
        bot, _ = step(bot, {"ts_ms": 2 * BOUNDARY, "price": 100}, funding_pct=0.02)
        self.assertAlmostEqual(bot["funding_paid"], 2 * 15 * 100 * .02 / 100)
        self.assertEqual(bot["last_funding_ts_ms"], 2 * BOUNDARY)
        before = copy.deepcopy(bot)
        again, events = step(bot, {"ts_ms": 2 * BOUNDARY, "price": 80})
        self.assertEqual(again, before)
        self.assertEqual(events, [])
        later, _ = step(bot, {"ts_ms": 2 * BOUNDARY + 1, "price": 100})
        self.assertEqual(later["funding_paid"], bot["funding_paid"])

    def test_funding_backfill_uses_pre_boundary_position_and_close(self):
        bot = open_bot(specification("SHORT"), 100, BOUNDARY - 120000)
        candle = dict(ts_ms=BOUNDARY - 60000, open=100, high=101, low=100, close=101)
        bot, _ = step(bot, candle)
        position = bot["position_contracts"]
        candle = dict(ts_ms=BOUNDARY, open=101, high=115, low=101, close=115)
        bot, _ = step(bot, candle)
        self.assertAlmostEqual(bot["funding_paid"], position * 101 * .01 / 100)
        restored = json.loads(json.dumps(bot))
        actual, events = step(restored, candle)
        self.assertEqual(actual, restored)
        self.assertEqual(events, [])

    def test_funding_before_fills_and_stop_evaluation(self):
        bot = open_bot(specification("LONG", funding_pct=20), 100, BOUNDARY - 1)
        result, events = step(bot, {"ts_ms": BOUNDARY, "price": bot["lines"][3]})
        self.assertAlmostEqual(result["funding_paid"], 15 * 100 * .2)
        kinds = [event["type"] for event in events]
        self.assertIn("FILL", kinds)
        self.assertIn("STOP_LOSS", kinds)
        self.assertLess(kinds.index("FILL"), kinds.index("STOP_LOSS"))
        self.assertIsNone(result["closed_ms"])
        self.check_identity(result)

    def test_range_break_requires_three_consecutive_updates_and_resets(self):
        bot = open_bot(specification(), 100, 0)
        for ts, price in enumerate((134, 134, 121, 134, 134, 134), 1):
            bot, events = step(bot, {"ts_ms": ts, "price": price})
            self.assertEqual(any(event["type"] == "RANGE_BREAK" for event in events), ts == 6)
        self.assertIsNone(bot["closed_ms"])
        self.assertEqual(bot["range_break_count"], 3)

    def test_close_flattens_with_fee_without_counting_a_grid_and_freezes(self):
        bot = open_bot(specification("LONG"), 100, 0)
        before = copy.deepcopy(bot)
        closed, events = close_bot(bot, 110, 1000, "MANUAL")
        self.assertEqual(bot, before)
        self.assertEqual(closed["position_contracts"], 0)
        self.assertEqual(closed["unrealized_pnl"], 0)
        self.assertEqual(closed["orders"], [])
        self.assertEqual(closed["completed_grids"], 0)
        self.assertAlmostEqual(closed["realized_pnl"], 150)
        self.assertAlmostEqual(closed["fees_paid"], 15 * (100 + 110) * FEE)
        self.assertEqual(closed["reason"], "MANUAL")
        self.assertIn("CLOSE", [event["type"] for event in events])
        self.check_identity(closed)
        unchanged, events = step(closed, {"ts_ms": BOUNDARY, "price": 80})
        self.assertEqual(unchanged, closed)
        self.assertEqual(events, [])

    def test_fixture_candles_are_deterministic_and_directional(self):
        for name, direction in (("oscillation", "NEUTRAL"), ("trend_up", "LONG"),
                                ("trend_down", "SHORT")):
            with self.subTest(name=name):
                candles = json.loads((FIXTURES / (name + ".json")).read_text())
                self.assertEqual(len(candles), 200)
                spec = specification(direction, range_low=80, range_high=125,
                                     grids=40, step_pct=1)
                results = []
                for _ in range(2):
                    bot = open_bot(spec, 100, 0)
                    for candle in candles:
                        source = bot
                        before, update = copy.deepcopy(source), copy.deepcopy(candle)
                        bot, _ = step(bot, candle)
                        self.assertEqual(source, before)
                        self.assertEqual(candle, update)
                        self.check_identity(bot)
                        self.check_orders(bot)
                        self.assertIsNot(bot, source)
                    results.append(bot)
                self.assertEqual(*results)
                self.assertGreater(bot["completed_grids"], 0)
                self.assertGreater(bot["equity"], bot["notional_usdt"])

    def test_tick_fixture_is_repeatable(self):
        ticks = json.loads((FIXTURES / "ticks.json").read_text())
        bots = []
        for _ in range(2):
            bot = open_bot(specification(grids=20), 100, 0)
            for tick in ticks:
                bot, _ = step(bot, tick)
            bots.append(bot)
        self.assertEqual(*bots)
        self.assertGreater(bot["completed_grids"], 0)

    def test_paired_opening_fill_does_not_count_a_grid(self):
        alternating = open_bot(specification(), 100, 0)
        for at, line in enumerate((1, 2, 1, 2), 1):
            alternating, _ = step(alternating, dict(ts_ms=at, price=alternating['lines'][line]))
        self.assertEqual(alternating['fills'], 4)
        self.assertEqual(alternating['completed_grids'], 2)
        for direction in ('LONG', 'SHORT', 'NEUTRAL'):
            with self.subTest(direction=direction):
                bot = open_bot(specification(direction), 100, 0)
                # Repeated traversal includes seeded closes, paired openings and closes.
                for at, price in enumerate((80, 122, 80, 122), 1):
                    before = copy.deepcopy(bot)
                    bot, events = step(bot, dict(ts_ms=at, price=price))
                    position, count, profit = before['position_contracts'], 0, 0
                    orders = {o['line']: o for o in before['orders']}
                    for event in events:
                        if event['type'] != 'FILL':
                            continue
                        order = orders[event['line']]
                        after = position + event['side'] * event['contracts']
                        if order['paired_line'] is not None and abs(after) < abs(position) - 1e-9:
                            count += 1
                            profit += event['contracts'] * abs(event['price'] - before['lines'][order['paired_line']])
                        position = after
                    self.assertEqual(bot['completed_grids'] - before['completed_grids'], count)
                    self.assertAlmostEqual(bot['grid_profit'] - before['grid_profit'], profit)

    def test_ray_recorded_window_matches_conditional_gate_bounds(self):
        candles = json.loads((FIXTURES / 'ray_20260911.json').read_text())
        self.assertEqual(len(candles), 418)
        bot = open_bot(specification('NEUTRAL', range_low=1.1, range_high=2.0,
                       grids=140, leverage=5, step_pct=0.43, funding_pct=0),
                       candles[0]['open'], candles[0]['ts_ms'] - 1)
        for candle in candles:
            bot, _ = step(bot, candle)
        self.assertGreaterEqual(bot['completed_grids'], 130)
        self.assertLessEqual(bot['completed_grids'], 200)
        self.assertGreaterEqual(bot['grid_profit'], 20)
        self.assertLessEqual(bot['grid_profit'], 32)
        self.check_identity(bot)

    def test_funding_receipt_and_boundary_close_are_idempotent(self):
        bot = open_bot(specification('LONG', funding_pct=-0.01), 100, BOUNDARY - 1)
        closed, events = close_bot(bot, 100, BOUNDARY, 'MANUAL')
        self.assertAlmostEqual(closed['funding_paid'], -0.15)
        self.assertAlmostEqual(closed['fees_paid'], 1.8)
        self.assertAlmostEqual(closed['equity'], 998.35)
        self.assertEqual(closed['last_funding_ts_ms'], BOUNDARY)
        again, repeated = close_bot(closed, 120, 2 * BOUNDARY, 'MANUAL')
        self.assertEqual(again, closed)
        self.assertEqual(repeated, [])


if __name__ == "__main__":
    unittest.main()
