"""Acceptance evidence from independently observed RAY order-history cycles.

The 17-contract quantity and four-decimal profits were read from KuCoin, not
inferred from the engine. Long and Short are counterfactual direction checks
using those same fills; the actual observed bot was Neutral. Tests should pass
on the existing implementation: this is additional evidence, not a RED-first
feature or a claim that automatic allocation is calibrated.
"""
from dataclasses import asdict
from decimal import Decimal
import unittest

from trader.research.kucoin_portfolio import summarize
from trader.research.kucoin_tracker import track_summary
from trader.strategies.grid_order_observations import net_cycle
from trader.strategies.grid_setup import build_setup
from trader.strategies.grid_types import GridConfig
from trader.strategies.kucoin_grid import advance, create_bot, preview

# buy, sell, observed displayed net, independently calculated unrounded net.
CYCLES = (
    (1.5992, 1.6120, '0.1848', '0.18484576'),
    (1.5864, 1.5992, '0.1851', '0.18510688'),
    (1.6248, 1.6376, '0.1843', '0.18432352'),
)
DIRECTIONS = ('long', 'short', 'neutral')
HOUR_MS = 3_600_000


def observed_config(direction, leverage=5):
    return GridConfig(pair='RAYUSDTM', low=1.1, high=2, grids=70,
                      investment=1000, reserved_margin=200, leverage=leverage,
                      direction=direction, entry_price=1.574, quantity=17,
                      multiplier=1, lot_size=1, tick_size=.0001)


def execute_cycle(direction, buy, sell, leverage=5):
    state = create_bot(observed_config(direction, leverage), 1.574, 0)
    ledger = [asdict(event) for event in state.fill_events]
    # Clear the Long seed first; a seed close must never count as arbitrage.
    # Neutral's down/up return completes one Short and one Long cycle.
    prices = (sell, buy) if direction == 'short' else (sell, buy, sell)
    for index, price in enumerate(prices, 1):
        state = advance(state, price, index * 60_000)
        ledger.extend(asdict(event) for event in state.fill_events)
    return state, ledger


def portfolio_report(state, ledger):
    bot_id = 'observed-ray-cycle'
    bot = dict(bot_id=bot_id, state=state, closest=None, outside_ms=0, start_ms=0,
               form={}, expected=0, exposure_hours=state.timestamp_ms/HOUR_MS)
    report = dict(start_ms=0, end_ms=state.timestamp_ms, initial_capital=1200,
                  ledger=[dict(event, bot_id=bot_id) for event in ledger],
                  bots=[bot], equity_timeline=[], switches=[],
                  coverage={'complete': True}, per_coin={}, direction_mix={})
    return summarize(report)


class ObservedProfitAllModesTests(unittest.TestCase):
    def test_observed_cycle_profit_matches_completed_fill_ledger_in_every_mode(self):
        for direction in DIRECTIONS:
            for buy, sell, displayed, exact in CYCLES:
                with self.subTest(direction=direction, buy=buy, sell=sell):
                    state, ledger = execute_cycle(direction, buy, sell)
                    closes = [event for event in ledger if event['completed_grid']]
                    count = 2 if direction == 'neutral' else 1
                    self.assertEqual(len(closes), count)
                    self.assertEqual(state.completed_grids, count)
                    self.assertAlmostEqual(state.grid_net_profit, count * float(exact), places=11)
                    for close in closes:
                        opened = [event for event in ledger if event['slot'] == close['slot']
                                  and event['kind'] == 'grid_open'
                                  and event['event_id'] < close['event_id']][-1]
                        self.assertEqual(opened['side'], -close['side'])
                        actual_buy = opened['price'] if opened['side'] == 1 else close['price']
                        actual_sell = close['price'] if close['side'] == -1 else opened['price']
                        self.assertAlmostEqual(actual_buy, buy)
                        self.assertAlmostEqual(actual_sell, sell)
                        self.assertEqual(opened['quantity'], 17)
                        realized = close['gross_pnl'] - opened['fee'] - close['fee']
                        self.assertAlmostEqual(realized, float(exact), places=11)
                        self.assertEqual(Decimal(str(realized)).quantize(Decimal('.0001')),
                                         Decimal(displayed))
                    self.assertAlmostEqual(sum(event['fee'] for event in ledger), state.fees)
                    if direction == 'neutral':
                        self.assertEqual({event['side'] for event in closes}, {-1, 1})
                    self.assertTrue(all(not event['completed_grid'] for event in ledger
                                        if event['kind'] == 'seed_close'))

    def test_tracker_and_portfolio_report_actual_net_and_reject_subdollar_cycles(self):
        for direction in DIRECTIONS:
            for buy, sell, _, exact in CYCLES:
                with self.subTest(direction=direction, buy=buy):
                    state, ledger = execute_cycle(direction, buy, sell)
                    expected = float(exact) * (2 if direction == 'neutral' else 1)
                    tracker = track_summary(state, 0, state.timestamp_ms)
                    report = portfolio_report(state, ledger)
                    metrics = report['metrics']
                    self.assertAlmostEqual(tracker['grid_net_profit'], expected, places=11)
                    self.assertAlmostEqual(metrics['grid_net_profit'], expected, places=11)
                    self.assertEqual(metrics['completed_at_target'], 0)
                    self.assertEqual(metrics['completed_below_target'], state.completed_grids)
                    self.assertAlmostEqual(metrics['grid_income_per_hour'],
                                           expected / (state.timestamp_ms/HOUR_MS), places=9)
                    self.assertAlmostEqual(metrics['net'], tracker['net_equity'] - 1200)
                    self.assertAlmostEqual(metrics['net'], state.grid_profit + state.seed_pnl
                        + state.close_pnl + state.funding + tracker['floating_pnl'] - state.fees)
                    self.assertAlmostEqual(report['per_coin']['RAYUSDTM']['grid_net_profit'], expected)
                    self.assertAlmostEqual(report['bots'][0]['grid_net_profit'], expected)

    def test_actual_preview_range_is_identical_across_modes_for_observed_quantity(self):
        for direction in DIRECTIONS:
            estimate = preview(observed_config(direction))
            self.assertEqual(estimate['quantity'], 17)
            self.assertAlmostEqual(estimate['profit_per_grid_min'], .17701216, places=11)
            self.assertAlmostEqual(estimate['profit_per_grid_max'], .19502944, places=11)
            self.assertAlmostEqual(estimate['profit_per_grid'], .1860208, places=11)
            self.assertEqual(estimate['order_count'], 140 if direction == 'neutral' else 70)
            self.assertNotAlmostEqual(estimate['profit_per_grid_min'],
                                      estimate['kucoin_profit_usdt_min'])

    def test_leverage_never_multiplies_profit_again_when_fill_quantity_is_fixed(self):
        for direction in DIRECTIONS:
            states = [execute_cycle(direction, 1.5992, 1.6120, leverage)[0]
                      for leverage in (4, 5, 6)]
            for state in states[1:]:
                self.assertAlmostEqual(state.grid_net_profit, states[0].grid_net_profit)
                self.assertAlmostEqual(state.fees, states[0].fees)
            estimates = [preview(observed_config(direction, leverage)) for leverage in (4, 6)]
            self.assertAlmostEqual(estimates[0]['profit_per_grid_min'], estimates[1]['profit_per_grid_min'])
            self.assertNotAlmostEqual(estimates[0]['kucoin_profit_usdt_min'],
                                      estimates[1]['kucoin_profit_usdt_min'])

    def test_observation_calculator_preserves_same_exact_fee_formula(self):
        for buy, sell, displayed, exact in CYCLES:
            calculated = net_cycle(17, 1, buy, sell)
            self.assertAlmostEqual(calculated['net'], float(exact), places=12)
            self.assertEqual(Decimal(str(calculated['net'])).quantize(Decimal('.0001')),
                             Decimal(displayed))

    def test_generated_setup_actual_preview_uses_same_two_fill_formula_in_every_mode(self):
        common = dict(valid=True, price=100., low_7d=50., high_7d=160., low_24h=80.,
                      high_24h=120., atr_1m=2., typical_movement_1m=5.,
                      fresh_high=False, fresh_low=False)
        market = dict(price=100., tick_size=.01, lot_size=.001, multiplier=1., funding_rate=0.)
        for direction, position, trend in (('long', .2, 1), ('short', .8, -1), ('neutral', .5, 0)):
            features = dict(common, position_24h=position, position_7d=position,
                            ema_slope_4h=trend*.5, ema_slope_24h=trend*.5,
                            structure_4h=trend, structure_24h=trend, funding_sign=-trend)
            result = build_setup('TESTUSDTM', features, market)
            self.assertTrue(result['eligible'], result['reason'])
            self.assertEqual(result['direction'], direction)
            q = Decimal(str(result['quantity']))
            levels = [Decimal(str(price)) for price in result['levels']]
            profits = [q*((sell-buy)-Decimal('.0006')*(buy+sell))
                       for buy, sell in zip(levels, levels[1:])]
            self.assertAlmostEqual(result['preview']['profit_per_grid_min'], float(min(profits)), places=8)
            self.assertAlmostEqual(result['preview']['profit_per_grid_max'], float(max(profits)), places=8)
            self.assertGreater(result['preview']['profit_per_grid_min'], 0)
