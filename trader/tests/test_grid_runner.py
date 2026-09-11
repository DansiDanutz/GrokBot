from dataclasses import asdict
import unittest
from unittest.mock import patch

from trader.strategies.grid_sizing import size_grid
from trader.research.grid_runner import run_window

START = 10*86400000 + 3600000


def candidate(pair='AAA', rate=10, investment=1000):
    sizing = size_grid(centre=100, investment=investment, multiplier=.001)
    return dict(pair=pair, expected_grids_per_hour=rate, rate_4h=rate, rate_24h=rate,
                form=sizing.form_values(pair), sizing=asdict(sizing),
                bid=99.99, ask=100.01, multiplier=.001, lot_size=1)


class Data:
    manifest = {'offline_copy': True, 'source': 'synthetic'}

    def market(self, pair, at_ms):
        return dict(bid=99.99, ask=100.01, mark=100,
                    book_time_ms=at_ms, book_observed_at_ms=at_ms,
                    ticker_observed_at_ms=at_ms)

    def candles(self, pair, start_ms, end_ms):
        return [dict(time_ms=start_ms, open=100, high=101, low=99, close=100)]

    def funding(self, *args):
        return []


def scanner(data, at_ms, investment=1000, grids=20):
    choices = [candidate(investment=investment), candidate('BBB', 9, investment)]
    return dict(candidates=choices, eligible_candidates=choices,
                coverage=dict(unknown=0, asof_symbols=2))


class RunnerTests(unittest.TestCase):
    def parameters(self, **kwargs):
        return dict(lookback_hours=1, margin_gph=0, tick_ms=60000, **kwargs)

    @patch('trader.research.grid_runner.scan', scanner)
    def test_continuous_profit_does_not_trigger_stop_and_net_reconciles(self):
        result = run_window(Data(), START, START+600000, self.parameters())
        self.assertGreater(result['completed_grids'], 0)
        self.assertEqual(result['switches'], [])
        self.assertTrue(result['coverage_complete'])
        self.assertAlmostEqual(result['net'], result['grid_profit']+result['seed_pnl']
                               +result['floating_pnl_realized']+result['floating_pnl']
                               -result['fees']+result['funding'])

    @patch('trader.research.grid_runner.scan', scanner)
    def test_random_null_is_reproducible(self):
        a = run_window(Data(), START, START+600000, self.parameters(), seed=7)
        b = run_window(Data(), START, START+600000, self.parameters(), seed=7)
        self.assertEqual(a, b)

    @patch('trader.research.grid_runner.scan', scanner)
    def test_missing_candle_invalidates_without_fake_close(self):
        data = Data()
        data.candles = lambda *args: []
        result = run_window(data, START, START+600000, self.parameters())
        self.assertFalse(result['coverage_complete'])
        self.assertEqual(result['completed_grids'], 0)
        self.assertEqual(result['switches'], [])
        self.assertIn('missing', result['errors'][0]['reason'])

    @patch('trader.research.grid_runner.scan', scanner)
    def test_missing_funding_is_not_zero(self):
        boundary = 11*86400000
        result = run_window(Data(), boundary-60000, boundary, self.parameters())
        self.assertFalse(result['coverage_complete'])
        self.assertIn('funding', result['errors'][0]['reason'])

    def test_rotation_does_not_borrow_to_pay_close_fees(self):
        def rotating(data, at_ms, investment=1000, grids=20):
            pair = 'AAA' if at_ms == START else 'BBB'
            choices = [candidate(pair, 10000, investment)]
            return dict(candidates=choices, eligible_candidates=choices,
                        coverage=dict(unknown=0, asof_symbols=1))
        with patch('trader.research.grid_runner.scan', rotating):
            result = run_window(Data(), START, START+120000, self.parameters())
        self.assertEqual(len(result['switches']), 1)
        self.assertGreaterEqual(result['cash_unallocated'], 0)
        self.assertLessEqual(result['per_coin']['BBB']['initial_investment'], 1000)

    def test_range_exit_funding_covers_inventory_until_recorded_stop(self):
        from trader.research.grid_runner import minute_paths
        from trader.strategies.kucoin_grid import create_bot
        from trader.strategies.grid_types import GridConfig
        boundary = 11*86400000
        state = create_bot(GridConfig('AAA', 90, 110, direction='long', quantity=1),
                           100, boundary-60000)
        data = Data()
        data.candles = lambda *args: [dict(time_ms=boundary-60000,
                                          open=100, high=100, low=80, close=80)]
        data.funding = lambda *args: [dict(time_ms=boundary, rate=.0001)]
        result, _, _ = minute_paths(data, state, boundary)
        self.assertEqual(result.last_funding_ms, boundary)
        self.assertLess(result.funding, 0)

    @patch('trader.research.grid_runner.scan', scanner)
    def test_per_coin_risk_and_end_close_spread_are_reported(self):
        result = run_window(Data(), START, START+120000, self.parameters())
        self.assertGreater(result['end_close_spread_reserve'], 0)
        for row in result['per_coin'].values():
            self.assertIn('max_drawdown', row)
            self.assertIn('max_floating_loss', row)
            self.assertIn('switches_per_day', row)

    @patch('trader.research.grid_runner.scan', scanner)
    def test_ending_close_reserve_uses_actual_quote_not_only_half_spread(self):
        data = Data()
        original = data.market
        def shifted(pair, at_ms):
            row = original(pair, at_ms)
            if at_ms == START+120000:
                row.update(bid=89.99, ask=90.01)
            return row
        data.market = shifted
        result = run_window(data, START, START+120000, self.parameters())
        self.assertGreaterEqual(result['end_close_spread_reserve'], 0)
        self.assertIn('hypothetical_end_liquidation_net', result)
