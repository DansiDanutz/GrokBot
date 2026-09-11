import json
from pathlib import Path
import unittest

from trader.strategies.grid_setup import build_setup, setup, grid_profit_preview, grid_cash_profit
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
PARAMS = dict(preview_fn=safe_preview, minimum_grid_net_usdt=1.)


class SetupTests(unittest.TestCase):
    def test_exact_form_total_fees_integer_leverage_and_ticks(self):
        result = build_setup('TESTUSDT', feature_set(), MARKET, PARAMS)
        self.assertTrue(result['eligible'], result)
        self.assertEqual(result['direction'], 'long')
        self.assertEqual(result['total_margin'], 1200)
        self.assertEqual(result['used_margin'], 1000)
        self.assertEqual(result['reserved_margin'], 200)
        self.assertEqual(result['used_margin'] + result['reserved_margin'], 1200)
        self.assertEqual(result['leverage'], 5)
        self.assertGreaterEqual(result['preview']['profit_per_grid_min'], 1.)
        self.assertGreater(result['stop_loss'], 10)
        self.assertLess(result['stop_loss'], result['low'])
        self.assertAlmostEqual(result['quantity'] / .001, round(result['quantity'] / .001))
        self.assertTrue(all(abs(price/.01-round(price/.01)) < 1e-7 for price in result['levels']))
        self.assertLessEqual(result['quantity'] * result['multiplier'] * result['grids'] * result['entry'], result['used_margin'] * result['leverage'])

    def test_preview_reports_tick_valid_stops_on_both_sides(self):
        long = feature_set(price=100.13)
        short = feature_set(price=100.13, position_24h=.8, position_7d=.8,
                            ema_slope_4h=-.5, ema_slope_24h=-.5,
                            structure_4h=-1., structure_24h=-1., funding_sign=1.)
        for data in (long, short):
            result = build_setup('TESTUSDT', data, dict(MARKET, price=100.13), PARAMS)
            self.assertTrue(result['eligible'], result['reason'])
            for side in ('low', 'high'):
                self.assertEqual(result['preview']['effective_stop_loss_'+side],
                                 result['hard_stop_'+side])

    def test_unsafe_setup_narrows_without_changing_budget_or_leverage(self):
        def needs_narrower(config):
            safe = config.low >= 88.
            return dict(estimated_liquidation_price=10. if safe else 99.)
        result = build_setup('TESTUSDT', feature_set(), MARKET, dict(preview_fn=needs_narrower))
        self.assertTrue(result['eligible'],result['reason'])
        self.assertEqual([row['range_stage'] for row in result['attempts']],[0,1])
        for row in result['attempts']:
            self.assertEqual(row['leverage'],5)
            self.assertEqual(row['used_margin'],1000)
            self.assertEqual(row['reserved_margin'],200)

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
        self.assertEqual(first_narrow,1)
        self.assertEqual(len(attempts),4)
        for row in attempts:
            self.assertEqual((row['leverage'],row['used_margin'],row['reserved_margin']),(5,1000,200))

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

    def test_explicit_cash_floor_preserves_fixed_reserve_and_buffers(self):
        result = build_setup('TESTUSDT', feature_set(), MARKET,dict(minimum_grid_net_usdt=1.))
        self.assertTrue(result['eligible'], result)
        self.assertEqual(result['leverage'], 5)
        self.assertEqual(result['reserved_margin'],200)
        self.assertEqual(result['used_margin'],1000)
        self.assertTrue(all(row['leverage'] == 5 for row in result['attempts']))
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
        self.assertAlmostEqual(neutral['entry']-neutral['low'],neutral['high']-neutral['entry'],delta=MARKET['tick_size']+1e-9)

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

    def test_nominal_preview_is_separate_from_explicit_actual_cash_floor(self):
        result = build_setup('TESTUSDT',feature_set(),MARKET,PARAMS)
        self.assertTrue(result['eligible'],result['reason'])
        preview = result['preview']
        self.assertLess(preview['kucoin_profit_usdt_min'],1.)
        self.assertGreaterEqual(preview['profit_per_grid_min'],1.)
        expected = grid_profit_preview(result['low'],result['high'],result['grids'],result['leverage'],result['used_margin'],result['entry'])
        for key in ('kucoin_profit_pct_min','kucoin_profit_pct_max','kucoin_profit_usdt_min','kucoin_profit_usdt_max'):
            self.assertAlmostEqual(preview[key], expected[key])
        self.assertEqual(result['interval'],result['step'])
        self.assertEqual(result['total_margin'],1200)
        self.assertEqual(result['used_margin']+result['reserved_margin'],1200)

    def test_neutral_default_preview_accounts_for_both_order_legs(self):
        neutral = feature_set(position_24h=.5,position_7d=.5,ema_slope_4h=0.,ema_slope_24h=0.,structure_4h=0.,structure_24h=0.,funding_sign=0.)
        result = build_setup('TESTUSDT',neutral,MARKET,dict(minimum_grid_net_usdt=1.))
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

    def test_conflicting_policy_overrides_rejected_instead_of_silent_resizing(self):
        for key,value in (('leverage_cap',6),('leverage',4),('used_margin',800),
                          ('reserved_margin',300),('total_margin',1000)):
            with self.subTest(parameter=key):
                result = build_setup('TESTUSDT',feature_set(),MARKET,dict(PARAMS,**{key:value}))
                self.assertFalse(result['eligible'])
                self.assertIn('fixed policy',result['reason'])
                self.assertEqual(result['total_margin'],1200)
        result = build_setup('TESTUSDT',feature_set(),MARKET,dict(PARAMS,leverage_cap=5,used_margin=1000,reserved_margin=200,total_margin=1200))
        self.assertTrue(result['eligible'],result['reason'])

    def test_new_setup_enables_adaptive_stops_without_changing_historical_defaults(self):
        observed = []
        def record_config(config):
            observed.append(config)
            return safe_preview(config)
        result = build_setup('TESTUSDT',feature_set(),MARKET,dict(preview_fn=record_config))
        self.assertTrue(result['eligible'],result['reason'])
        self.assertTrue(result['adaptive_range_stops'])
        self.assertEqual(result['adaptive_tight_stop_pct'],.01)
        self.assertEqual(result['adaptive_liquidation_clearance_pct'],.01)
        self.assertTrue(observed[-1].adaptive_range_stops)
        self.assertEqual(observed[-1].adaptive_tight_stop_pct,.01)
        self.assertEqual(observed[-1].adaptive_liquidation_clearance_pct,.01)
        from trader.strategies.kucoin_grid import GridConfig
        self.assertFalse(GridConfig(pair='HISTORICAL',low=80.,high=120.).adaptive_range_stops)

    def test_default_accepts_positive_sub_dollar_cash_grids_and_reports_floor(self):
        result = build_setup('TESTUSDT',feature_set(),MARKET,dict(preview_fn=safe_preview))
        historical = build_setup('TESTUSDT',feature_set(),MARKET,PARAMS)
        self.assertTrue(result['eligible'],result['reason'])
        self.assertGreater(result['preview']['profit_per_grid_min'],0.)
        self.assertLess(result['preview']['profit_per_grid_min'],1.)
        self.assertGreater(result['grids'],historical['grids'])
        self.assertEqual(result['minimum_grid_net_usdt'],0.)
        self.assertEqual(result['target_profit_per_grid'],0.)
        self.assertEqual(historical['minimum_grid_net_usdt'],1.)
        self.assertGreaterEqual(historical['preview']['profit_per_grid_min'],1.)
        for buy,sell in zip(result['levels'],result['levels'][1:]):
            self.assertGreater(grid_cash_profit(result['quantity'],buy,sell),0)
        self.assertIn('display',result['preview']['nominal_profit_basis'])

    def test_exact_break_even_rejected_but_small_genuine_cash_profit_allowed(self):
        self.assertEqual(grid_cash_profit(1,4997,5003),0)
        self.assertGreater(grid_cash_profit(1,4996,5002),0)
        self.assertLess(grid_cash_profit(1,4998,5004),0)
        def boundary_setup(low,high,entry):
            data = feature_set(price=entry,low_7d=low,high_7d=high,low_24h=low,high_24h=high,
                               atr_1m=1.,position_24h=.8,position_7d=.8,ema_slope_4h=-.5,
                               ema_slope_24h=-.5,structure_4h=-1.,structure_24h=-1.,funding_sign=1.)
            return build_setup('BOUNDARY',data,dict(MARKET,price=entry,tick_size=1.),
                               dict(min_grids=2,max_grids=2,preview_fn=lambda config:dict(liquidation_price_short=10000.)))
        self.assertFalse(boundary_setup(4991,5003,5000)['eligible'])
        positive = boundary_setup(4990,5002,4999)
        self.assertTrue(positive['eligible'],positive['reason'])
        self.assertGreater(positive['preview']['profit_per_grid_min'],0.)
        self.assertLess(positive['preview']['profit_per_grid_min'],.01)

    def test_minimum_grid_net_parameter_rejects_invalid_values(self):
        for value in (-1.,float('nan'),float('inf'),True,False,None,'1'):
            with self.subTest(value=value):
                result = build_setup('TESTUSDT',feature_set(),MARKET,dict(preview_fn=safe_preview,minimum_grid_net_usdt=value))
                self.assertFalse(result['eligible'])
                self.assertIn('minimum_grid_net_usdt',result['reason'])
