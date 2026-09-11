import json
from pathlib import Path
import unittest

from trader.strategies.grid_setup import build_setup, setup, grid_profit_preview
from trader.tests.test_grid_features import candles


def feature_set(**overrides):
    value = dict(valid=True, price=100., low_7d=50., high_7d=160., low_24h=80., high_24h=120.,
                 atr_1m=2., typical_movement_1m=5., position_24h=.2, position_7d=.2,
                 ema_slope_4h=.5, ema_slope_24h=.5, structure_4h=1., structure_24h=1.,
                 funding_sign=-1., fresh_high=False, fresh_low=False)
    value.update(overrides)
    return value


def safe_preview(config):
    return dict(estimated_liquidation_price=10., estimated_liquidation_price_high=1000.)


MARKET = dict(price=100., tick_size=.01, lot_size=.001, multiplier=1., funding_rate=0.)
PARAMS = dict(preview_fn=safe_preview)


class SetupTests(unittest.TestCase):
    def test_exact_form_total_fees_integer_leverage_and_ticks(self):
        result = build_setup('TESTUSDT', feature_set(), MARKET, PARAMS)
        self.assertTrue(result['eligible'], result)
        self.assertEqual(result['direction'], 'long')
        self.assertEqual(result['total_margin'], 1000)
        self.assertEqual(result['used_margin'] + result['reserved_margin'], 1000)
        self.assertEqual(result['leverage'], 6)
        self.assertGreaterEqual(result['preview']['profit_per_grid_min'], 1.)
        self.assertGreater(result['stop_loss'], 10)
        self.assertLess(result['stop_loss'], result['low'])
        self.assertAlmostEqual(result['quantity'] / .001, round(result['quantity'] / .001))
        self.assertTrue(all(abs(price/.01-round(price/.01)) < 1e-7 for price in result['levels']))
        self.assertLessEqual(result['quantity'] * result['multiplier'] * result['grids'] * result['entry'], result['used_margin'] * result['leverage'])

    def test_reserve_exhausted_before_lowering_then_narrowing(self):
        def needs_lower(config):
            safe = config.leverage <= 5
            return dict(estimated_liquidation_price=10. if safe else 99.)
        result = build_setup('TESTUSDT', feature_set(), MARKET, dict(preview_fn=needs_lower))
        self.assertTrue(result['eligible'], result)
        attempts = result['attempts']
        at_six = [row for row in attempts if row['leverage'] == 6]
        self.assertEqual([row['reserved_margin'] for row in at_six], list(range(0,1000,100)))
        self.assertEqual(result['leverage'], 5)
        self.assertEqual(attempts[len(at_six)]['reserved_margin'], 0)
        self.assertTrue(all(row['range_stage'] == 0 for row in attempts))

    def test_trigger_for_fresh_high_and_neutral_middle(self):
        result = build_setup('TESTUSDT', feature_set(fresh_high=True), MARKET, PARAMS)
        self.assertIsNotNone(result['trigger'])
        self.assertLess(result['entry'], MARKET['price'])
        neutral = feature_set(position_24h=.5, position_7d=.5, ema_slope_4h=0., ema_slope_24h=0., structure_4h=0., structure_24h=0., funding_sign=0.)
        result = build_setup('TESTUSDT', neutral, MARKET, PARAMS)
        self.assertEqual(result['direction'], 'neutral')
        self.assertIsNone(result['trigger'])
        self.assertLess(result['stop_loss'], result['low'])
        self.assertGreater(result['stop_loss_high'], result['high'])

    def test_short_mirror(self):
        short = feature_set(position_24h=.8, position_7d=.8, ema_slope_4h=-.5, ema_slope_24h=-.5, structure_4h=-1., structure_24h=-1., funding_sign=1.)
        result = build_setup('TESTUSDT', short, MARKET, PARAMS)
        self.assertTrue(result['eligible'], result)
        self.assertEqual(result['direction'], 'short')
        self.assertGreater(result['stop_loss'], result['high'])
        self.assertLess(result['stop_loss'], 1000)

    def test_infeasible_quantity_and_movement_explained(self):
        result = build_setup('BADUSDT', feature_set(), dict(MARKET, lot_size=1000000), PARAMS)
        self.assertFalse(result['eligible'])
        result = build_setup('SLOWUSDT', feature_set(typical_movement_1m=.000001), MARKET, PARAMS)
        self.assertTrue(result['eligible'], result)
        self.assertIn('reducing grid count widens', result['warnings'][0])
        self.assertGreaterEqual(result['preview']['profit_per_grid_min'], 1.)
        flat = build_setup('SLOWUSDT', feature_set(typical_movement_1m=0.), MARKET, PARAMS)
        self.assertTrue(flat['eligible'], flat['reason'])
        self.assertTrue(flat['warnings'])

    def test_all_unsafe_rejected_with_ordered_narrowing(self):
        result = build_setup('BADUSDT', feature_set(), MARKET, dict(preview_fn=lambda config: dict(estimated_liquidation_price=100.)))
        self.assertFalse(result['eligible'])
        attempts = result['attempts']
        first_narrow = next(i for i,row in enumerate(attempts) if row['range_stage'] == 1)
        self.assertEqual(first_narrow, 40)
        self.assertEqual(attempts[39]['leverage'], 3)
        self.assertEqual(attempts[40]['leverage'], 6)

    def test_wrapper_and_invalid_market(self):
        self.assertFalse(setup('BADUSDT', candles(10), MARKET, PARAMS)['eligible'])
        self.assertFalse(build_setup('BADUSDT', feature_set(), dict(MARKET, multiplier=0), PARAMS)['eligible'])

    def test_observed_hemi_btr_calibration_facts_are_not_setup_targets(self):
        path = Path(__file__).resolve().parents[2] / 'tests/fixtures/kucoin-grid-bots-20260911.json'
        bots = json.loads(path.read_text())['running_bots'][:2]
        for bot in bots:
            n = bot['grids_buy'] + bot['grids_sell']
            step_pct = (bot['range_high']-bot['range_low']) / n / bot['entry_price']*100
            self.assertAlmostEqual(step_pct, .87, delta=.02)
            self.assertAlmostEqual(bot['grid_profit']/bot['arbitrage_total'], .89, delta=.01)

    def test_default_replica_adds_reserve_and_checks_both_buffers(self):
        result = build_setup('TESTUSDT', feature_set(), MARKET)
        self.assertTrue(result['eligible'], result)
        self.assertEqual(result['leverage'], 6)
        self.assertGreater(result['reserved_margin'], 0)
        self.assertTrue(all(row['leverage'] == 6 for row in result['attempts']))
        self.assertGreaterEqual(result['preview']['buffer_range_percent']['long'], 10.)
        self.assertGreaterEqual(result['preview']['buffer_edge_percent']['long'], 10.)
        fees = .0006
        for a,b in zip(result['levels'],result['levels'][1:]):
            self.assertGreaterEqual(result['quantity']*((b-a)-fees*(a+b)), 1.)
        self.assertLessEqual(result['quantity']*result['grids']*result['entry']/result['leverage'] + result['opening_fee_budget'], result['used_margin'])

    def test_multiplier_units_and_configurable_small_margin_neutral(self):
        result = build_setup('TESTUSDT', feature_set(), dict(MARKET,multiplier=.1,lot_size=3), PARAMS)
        self.assertTrue(result['eligible'], result)
        self.assertAlmostEqual(result['quantity']/.3,round(result['quantity']/.3))
        self.assertAlmostEqual(result['contracts_per_grid']/3,round(result['contracts_per_grid']/3))
        neutral = build_setup('TESTUSDT',feature_set(),MARKET,dict(PARAMS,direction_threshold=.99))
        self.assertEqual(neutral['direction'],'neutral')
        self.assertAlmostEqual(neutral['entry']-neutral['low'],neutral['high']-neutral['entry'],delta=MARKET['tick_size'])

    def test_invalid_direction_weight_or_nonfinite_feature_rejected(self):
        for params in (dict(PARAMS,direction_weights={'typo':2}), dict(PARAMS,direction_weights={'ema_slope_4h':-1})):
            self.assertFalse(build_setup('TESTUSDT',feature_set(),MARKET,params)['eligible'])
        self.assertFalse(build_setup('TESTUSDT',feature_set(ema_slope_4h=float('nan')),MARKET,PARAMS)['eligible'])

    def test_ray_seventy_grid_percent_is_not_usdt_profit(self):
        result = grid_profit_preview(1.1, 2., 70, 5, 1000, 1.58)
        self.assertAlmostEqual(result['step'], .9/70)
        self.assertAlmostEqual(result['kucoin_profit_pct_min'], 2.614285714285714)
        self.assertAlmostEqual(result['kucoin_profit_pct_max'], 5.244155844155844)
        self.assertAlmostEqual(result['kucoin_profit_usdt_min'], .373469387755102)
        self.assertLess(result['kucoin_profit_usdt_max'], 1.)
        self.assertEqual(result['used_margin'], 1000)

    def test_ray_fifty_grids_pay_one_near_entry_but_not_at_top(self):
        fifty = grid_profit_preview(1.1, 2., 50, 5, 1000, 1.58)
        self.assertAlmostEqual(fifty['step'], .018)
        self.assertAlmostEqual(fifty['kucoin_profit_usdt_at_price'], 1.019240506329114)
        self.assertAlmostEqual(fifty['kucoin_profit_usdt_min'], .78)
        passing = [n for n in range(2,301)
                   if grid_profit_preview(1.1,2.,n,5,1000)['kucoin_profit_usdt_min'] >= 1.]
        self.assertEqual(max(passing), 44)

    def test_setup_reports_and_enforces_nominal_percent_and_usdt_floor(self):
        result = build_setup('TESTUSDT',feature_set(),MARKET,PARAMS)
        self.assertTrue(result['eligible'],result['reason'])
        preview = result['preview']
        self.assertGreaterEqual(preview['kucoin_profit_usdt_min'],1.)
        self.assertGreaterEqual(preview['profit_per_grid_min'],1.)
        expected = grid_profit_preview(result['low'],result['high'],result['grids'],result['leverage'],result['used_margin'],result['entry'])
        for key in ('kucoin_profit_pct_min','kucoin_profit_pct_max','kucoin_profit_usdt_min','kucoin_profit_usdt_max'):
            self.assertAlmostEqual(preview[key], expected[key])
        self.assertEqual(result['interval'],result['step'])
        self.assertEqual(result['total_margin'],1000)
        self.assertEqual(result['used_margin']+result['reserved_margin'],1000)

    def test_neutral_default_preview_accounts_for_both_order_legs(self):
        neutral = feature_set(position_24h=.5,position_7d=.5,ema_slope_4h=0.,ema_slope_24h=0.,structure_4h=0.,structure_24h=0.,funding_sign=0.)
        result = build_setup('TESTUSDT',neutral,MARKET)
        self.assertTrue(result['eligible'],result['reason'])
        self.assertEqual(result['preview']['order_count'],2*result['grids'])
        self.assertGreaterEqual(result['preview']['profit_per_grid_min'],1.)
        self.assertGreaterEqual(result['preview']['kucoin_profit_usdt_min'],1.)
        used_with_fees = 2*result['quantity']*result['grids']*result['entry']/result['leverage'] + result['opening_fee_budget']
        self.assertLessEqual(used_with_fees,result['used_margin'])

    def test_ray_tick_preview_remains_percent_and_uses_only_used_margin(self):
        result = grid_profit_preview(1.1,2.,70,5,1000,1.58,.0001)
        self.assertAlmostEqual(result['step'],.0128)
        self.assertAlmostEqual(result['kucoin_profit_pct_min'],2.61,delta=.02)
        self.assertAlmostEqual(result['kucoin_profit_pct_max'],5.21,delta=.02)
        self.assertAlmostEqual(result['kucoin_profit_usdt_min'],1000/70*result['kucoin_profit_pct_min']/100)
        self.assertLess(result['kucoin_profit_usdt_max'],1.)

    def test_five_percent_both_edge_stops_are_price_based_for_all_modes(self):
        for threshold,trend in ((0.,1),(.99,0),(0.,-1)):
            data = feature_set(ema_slope_4h=.5*trend,ema_slope_24h=.5*trend,
                               structure_4h=trend,structure_24h=trend,
                               position_24h=.5-.3*trend,position_7d=.5-.3*trend,
                               funding_sign=-trend)
            result = build_setup('TESTUSDT',data,MARKET,dict(PARAMS,direction_threshold=threshold))
            self.assertTrue(result['eligible'],result['reason'])
            self.assertAlmostEqual(result['range_exit_stop_low'],.95*result['low'])
            self.assertAlmostEqual(result['range_exit_stop_high'],1.05*result['high'])
            self.assertGreaterEqual(result['hard_stop_low'],result['range_exit_stop_low'])
            self.assertLessEqual(result['hard_stop_high'],result['range_exit_stop_high'])
            self.assertLess(result['hard_stop_low']-result['range_exit_stop_low'],MARKET['tick_size']+1e-9)
            self.assertLess(result['range_exit_stop_high']-result['hard_stop_high'],MARKET['tick_size']+1e-9)
            self.assertIn('one tick',result['stop_tick_adjustment'])

    def test_range_uses_confirmed_support_resistance_and_explains_fallback(self):
        evidence = dict(method='confirmed five-bar swing pivots',confirmation_bars=2,lookback_minutes=10080,
                        supports=[dict(price=92.,count=3,last_confirmed_index=10070)],
                        resistances=[dict(price=130.,count=2,last_confirmed_index=10071)],
                        support_pivot_count=3,resistance_pivot_count=2)
        result = build_setup('TESTUSDT',feature_set(support_resistance=evidence),MARKET,PARAMS)
        self.assertTrue(result['eligible'],result['reason'])
        self.assertGreaterEqual(result['low'],92.)
        self.assertLessEqual(result['high'],130.)
        self.assertEqual(result['range_evidence']['support']['price'],92.)
        self.assertEqual(result['range_evidence']['support']['count'],3)
        self.assertEqual(result['range_evidence']['resistance']['price'],130.)
        self.assertEqual(result['range_evidence']['confirmation_bars'],2)
        fallback = build_setup('TESTUSDT',feature_set(),MARKET,PARAMS)
        self.assertEqual(fallback['range_evidence']['support_source'],'7d low fallback; no confirmed eligible pivot')
