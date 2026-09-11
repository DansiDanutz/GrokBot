"""Observed RAY hedge counts and independent paper pair accounting."""
import unittest
from trader.papergrid.neutral import open_neutral, step_neutral, close_neutral


def spec():
    return dict(bot_id=1, symbol='RAYUSDTM', direction='NEUTRAL', range_low=1.1,
                range_high=2.0, grid_interval=.0128, grids=70, step_pct=.8,
                quantity_per_grid=17, notional_usdt=1000, leverage=5, funding_pct=.01)


class NeutralBooksTest(unittest.TestCase):
    def test_ray_seed_counts_and_orders(self):
        bot = open_neutral(spec(), 1.574, 0)
        self.assertEqual((bot['long_contracts'], bot['short_contracts']), (544, -629))
        self.assertEqual(len(bot['orders']), 140)
        self.assertEqual(sum(o['side'] == 'buy' for o in bot['orders']), 75)
        self.assertEqual(bot['completed_grids'], 0)
        self.assertAlmostEqual(bot['fees_paid'], 1173 * 1.574 * .0006)

    def test_open_fill_does_not_complete_and_close_uses_actual_pair(self):
        bot = open_neutral(spec(), 1.574, 0)
        after, events = step_neutral(bot, 1.5736, 1)
        # Long opens; neither book has closed a pair yet.
        self.assertEqual(after['completed_grids'], 0)
        self.assertEqual(sum(e['type'] == 'GRID' for e in events), 0)
        later, events = step_neutral(after, 1.5864, 2)
        self.assertEqual(later['completed_grids'], 1)
        self.assertAlmostEqual(later['grid_profit'] - after['grid_profit'], 17 * .0128)
        self.assertEqual(bot['completed_grids'], 0)

    def test_funding_and_flatten_both_legs(self):
        bot = open_neutral(spec(), 1.574, 0)
        after, _ = step_neutral(bot, 1.574, 8 * 3600000)
        self.assertAlmostEqual(after['funding_paid'], (544 - 629) * 1.574 * .0001)
        closed, events = close_neutral(after, 1.6, 8 * 3600000 + 1)
        self.assertEqual((closed['long_contracts'], closed['short_contracts']), (0, 0))
        self.assertEqual(closed['orders'], [])
        self.assertEqual(sum(e['type'] == 'FILL' for e in events), 2)
        self.assertAlmostEqual(closed['realized_pnl'], (544 - 629) * (1.6 - 1.574))
        self.assertEqual(closed['completed_grids'], 0)
        again, events = close_neutral(closed, 1.6, 8 * 3600000 + 2)
        self.assertEqual(events, [])
        self.assertEqual(again, closed)

    def test_invalid_quantity_and_duplicate_tick(self):
        bad = spec(); bad['quantity_per_grid'] = 0
        with self.assertRaises(ValueError):
            open_neutral(bad, 1.574, 0)
        bot = open_neutral(spec(), 1.574, 100)
        self.assertEqual(step_neutral(bot, 1.5, 100), (bot, []))

    def test_managed_funding_preserves_external_ledger(self):
        settings = dict(spec(), funding_managed=True)
        bot = open_neutral(settings, 1.574, 0)
        bot['funding_paid'] = 3.5
        after, _ = step_neutral(bot, 1.574, 8 * 3600000)
        self.assertEqual(after['funding_paid'], 3.5)
        self.assertAlmostEqual(after['equity'], 1000 - after['fees_paid'] - 3.5)

    def test_seed_pair_uses_actual_entry_and_books_never_flip(self):
        bot = open_neutral(spec(), 1.574, 0)
        after, _ = step_neutral(bot, 1.5992, 1)
        self.assertEqual(after['completed_grids'], 1)
        self.assertAlmostEqual(after['grid_profit'], 17 * (1.5992 - 1.574))
        for at, price in enumerate((1.1, 1.996, 1.1, 1.996), 2):
            after, _ = step_neutral(after, price, at)
            self.assertGreaterEqual(after['long_contracts'], 0)
            self.assertLessEqual(after['short_contracts'], 0)
            self.assertEqual(len(after['orders']), 140)
            self.assertAlmostEqual(after['unrealized_pnl'],
                after['long_unrealized_pnl'] + after['short_unrealized_pnl'])

    def test_added_reserve_is_counted_in_mark_without_changing_positions(self):
        bot = open_neutral(spec(), 1.574, 0)
        bot['reserve_added_usdt'] = 200
        after, _ = step_neutral(bot, 1.574, 1)
        self.assertAlmostEqual(after['equity'], bot['equity'] + 200)
        self.assertEqual(after['long_avg_entry'], bot['long_avg_entry'])
        self.assertEqual(after['short_avg_entry'], bot['short_avg_entry'])
