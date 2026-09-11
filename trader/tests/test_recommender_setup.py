import copy
import unittest
from unittest.mock import patch

from trader.strategies.recommender_setup import build_recommender_setup
from trader.strategies.grid_setup import grid_cash_profit
from trader.tests.test_grid_count_search import bars, ASOF, FUNDING
from trader.tests.test_chart_read import entry_cells

MARKET = dict(price=100.5,tick_size=.01,lot_size=.001,multiplier=1.)


def chart(direction='long', ready=True):
    cells = entry_cells(direction)
    cells['1h'].update(fresh=True,indicator_observed_fraction=1., support_resistance=dict(support=98.,resistance=102.,estimated=False,
        support_basis='confirmed swing',resistance_basis='confirmed swing'))
    if not ready:
        cells['5m']['entry_context'] = None
    return dict(direction=direction,bias_reason='higher-timeframe agreement',five_cells=cells,
                bias_mode='1d+4h',asof_ms=ASOF)


def safe_preview(config):
    step=(config.high-config.low)/config.grids
    return dict(liquidation_price_long=60.,liquidation_price_short=140.,
                profit_per_grid_min=float(grid_cash_profit(config.quantity,config.high-step,config.high)),
                profit_per_grid_max=float(grid_cash_profit(config.quantity,config.low,config.low+step)))


def build(data=None, rows=None, **kwargs):
    params=dict(min_grids=4,max_grids=8,preview_fn=safe_preview)
    params.update(kwargs.pop('parameters',{}))
    with patch('trader.strategies.recommender_setup.read_chart',return_value=data or chart()):
        return build_recommender_setup('TESTUSDT',bars() if rows is None else rows,MARKET,{},ASOF,
                                       params,funding=kwargs.pop('funding',FUNDING),**kwargs)


class RecommenderSetupTests(unittest.TestCase):
    def test_income_sweep_returns_three_exact_safe_fixed_budget_forms(self):
        result=build()
        self.assertTrue(result['offered'],result)
        self.assertEqual(len(result['candidates']),3)
        self.assertTrue(result['entry_eligible'])
        self.assertTrue(result['waiting_for_trigger'])
        self.assertTrue(result['funded_entry_eligible'])
        self.assertTrue(result['can_arm'])
        self.assertFalse(result['market_entry_ready'])
        for row in result['candidates']:
            self.assertEqual((row['leverage'],row['used_margin'],row['reserved_margin'],row['total_margin']),
                             (5,1000,200,1200))
            self.assertEqual(row['range_exit_stop_pct'],0)
            self.assertFalse(row['adaptive_range_stops'])
            self.assertEqual((row['hard_stop_low'],row['hard_stop_high']),(row['low'],row['high']))
            self.assertAlmostEqual(row['high'],row['low']+row['grids']*row['step'])
            self.assertLess(row['low'],row['trigger'])
            self.assertLess(row['trigger'],row['high'])
            self.assertIn('kucoin_profit_pct_min',row['preview'])
            self.assertGreater(row['preview']['profit_per_grid_min'],0)
            self.assertFalse(row['quantity_calibrated'])
            self.assertTrue(row['liquidation_estimated'])
            self.assertNotIn('take_profit',row['config'])
        self.assertEqual([r['selection_income_per_hour'] for r in result['candidates']],
                         sorted([r['selection_income_per_hour'] for r in result['candidates']],reverse=True))

    def test_reached_directional_trigger_starts_at_current_entry_without_waiting(self):
        from trader.research.kucoin_replay import _config
        from trader.strategies.kucoin_grid import create_bot
        for direction,price,trigger in (('long',101.5,101.),('short',98.5,99.)):
            with self.subTest(direction=direction), \
                    patch('trader.strategies.recommender_setup.read_chart',return_value=chart(direction)):
                result=build_recommender_setup('TESTUSDT',bars(),dict(MARKET,price=price),{},ASOF,
                    dict(min_grids=4,max_grids=8),funding=FUNDING)
            self.assertTrue(result['can_arm'],result)
            self.assertTrue(result['market_entry_ready'])
            self.assertFalse(result['waiting_for_trigger'])
            self.assertEqual(result['entry_signal']['trigger'],trigger)
            for form in result['candidates']:
                self.assertIsNone(form['trigger'])
                self.assertIsNone(form['config']['trigger'])
                self.assertEqual(form['entry'],price)
                self.assertEqual(form['config']['entry_price'],price)
                state=create_bot(_config(form),price,ASOF)
                self.assertEqual(state.status,'running')
                from trader.strategies.grid_setup import _round
                expected=_round(5000/(form['grids']*price*(1+5*.0006)),.001)
                self.assertEqual(form['quantity'],expected)

    def test_immediate_entry_rounds_market_tick_without_crossing_range_edge(self):
        for direction,price,expected in (('long',101.503,101.51),('short',98.507,98.50)):
            with patch('trader.strategies.recommender_setup.read_chart',return_value=chart(direction)):
                form=build_recommender_setup('TESTUSDT',bars(),dict(MARKET,price=price),{},ASOF,
                    dict(min_grids=4,max_grids=8,preview_fn=safe_preview),funding=FUNDING)
            self.assertTrue(form['can_arm'])
            self.assertEqual(form['entry'],expected)
            self.assertIsNone(form['trigger'])
        with patch('trader.strategies.recommender_setup.read_chart',return_value=chart()):
            form=build_recommender_setup('TESTUSDT',bars(),dict(MARKET,price=101.999),{},ASOF,
                dict(min_grids=4,max_grids=8,preview_fn=safe_preview),funding=FUNDING)
        self.assertFalse(form['can_arm'])
        self.assertFalse(form['entry_eligible'])
        self.assertIn('tick',form['entry_signal']['reason'])

    def test_unreached_directional_trigger_stays_pending_in_engine(self):
        from trader.research.kucoin_replay import _config
        from trader.strategies.kucoin_grid import create_bot
        for direction,trigger in (('long',101.),('short',99.)):
            with self.subTest(direction=direction):
                form=build(chart(direction))
                self.assertTrue(form['can_arm'])
                self.assertTrue(form['waiting_for_trigger'])
                self.assertFalse(form['market_entry_ready'])
                self.assertEqual(form['trigger'],trigger)
                self.assertEqual(form['entry'],trigger)
                self.assertEqual(create_bot(_config(form),MARKET['price'],ASOF).status,'waiting')

    def test_forbidden_long_only_falls_back_neutral_with_fresh_entry_check(self):
        result=build(regime=dict(pair='TESTUSDT',asof_ms=ASOF,allowed_directions=['short','neutral']))
        self.assertEqual(result['direction'],'neutral')
        self.assertEqual(result['original_direction'],'long')
        self.assertTrue(result['entry_eligible'])
        self.assertIsNone(result['trigger'])
        bad=chart()
        bad['five_cells']['5m']['entry_context']['last_low']=94.
        waiting=build(bad,regime=dict(pair='TESTUSDT',asof_ms=ASOF,allowed_directions=['short','neutral']))
        self.assertTrue(waiting['offered'])
        self.assertFalse(waiting['entry_eligible'])
        forbidden=build(regime=dict(pair='TESTUSDT',asof_ms=ASOF,allowed_directions=['short']))
        self.assertFalse(forbidden['offered'])
        self.assertNotEqual(forbidden.get('direction'),'short')

    def test_missing_funding_and_entry_context_keep_provisional_offer(self):
        result=build(chart(ready=False),funding=None)
        self.assertTrue(result['offered'])
        self.assertFalse(result['entry_eligible'])
        self.assertFalse(result['funded_economics_eligible'])
        self.assertFalse(result['funded_entry_eligible'])
        self.assertTrue(result['provisional'])
        self.assertIsNone(result['funding_adjusted_income_per_hour'])
        self.assertIn('unknown',result['status'])

    def test_liquidation_safety_uses_each_count_exact_trimmed_range(self):
        seen=[]
        def preview(config):
            seen.append(config)
            result=safe_preview(config)
            if config.grids!=5:
                result['liquidation_price_long']=config.low*.95
            return result
        result=build(parameters=dict(preview_fn=preview))
        self.assertEqual([r['grids'] for r in result['candidates']],[5])
        for config in seen:
            expected=98+config.grids*(int((4/config.grids)/.01+1e-8)*.01)
            self.assertAlmostEqual(config.high,expected)
        self.assertTrue(any('liquidation' in row['reason'].lower() for row in result['search']['rejected_counts']))

    def test_one_hour_observed_extrema_fallback_and_missing_range_waits(self):
        data=chart('neutral')
        data['five_cells']['1h'].update(available=False,fresh=True,support_resistance=dict(
            support=98.,resistance=102.,estimated=False,support_basis='trailing twenty-bar low fallback',
            resistance_basis='trailing twenty-bar high fallback'))
        result=build(data)
        self.assertTrue(result['offered'],result)
        self.assertEqual(result['range_evidence']['basis'],'one_hour_observed_extrema_fallback')
        self.assertFalse(result['chart']['five_cells']['1h']['available'])
        self.assertTrue(result['range_evidence']['fallback'])
        self.assertTrue(result['entry_eligible'])
        data['five_cells']['1h'].update(support_resistance={},fresh=False)
        rows=bars()+[dict(timestamp_ms=ASOF,open=100,low=1,high=1000,close=100)]
        waiting=build(data,rows)
        self.assertFalse(waiting['offered'])
        self.assertIn('one-hour',waiting['reason'])

    def test_causally_filled_hourly_range_above_95_percent_stays_estimated(self):
        from trader.features.chart_read import read_timeframe, interpret_entry
        from trader.tests.test_chart_read import bars as frame_bars, ASOF as frame_asof
        hourly=frame_bars('1h',0)
        for row in hourly:
            row.update(timestamp_ms=row['timestamp_ms']+ASOF-frame_asof,
                       synthetic=True,indicator_only=True,observed=False,
                       observed_minutes=59,expected_minutes=60)
        data=chart('neutral')
        data['five_cells']['1h']=read_timeframe(hourly,'1h',ASOF)
        self.assertTrue(data['five_cells']['1h']['support_resistance']['estimated'])
        with patch('trader.strategies.recommender_setup.interpret_entry',wraps=interpret_entry) as entry_read:
            result=build(data)
        self.assertTrue(result['offered'],result)
        self.assertTrue(result['range_evidence']['estimated'])
        self.assertAlmostEqual(result['range_evidence']['indicator_observed_fraction'],59/60)
        copied=entry_read.call_args.args[0]['1h']
        self.assertTrue(copied['support_resistance']['estimated'])
        self.assertAlmostEqual(copied['indicator_observed_fraction'],59/60)
        self.assertTrue(result['chart']['five_cells']['1h']['support_resistance']['estimated'])

    def test_sparse_or_unknown_hourly_indicator_coverage_waits(self):
        for fraction in (.949, None, float('nan')):
            data=chart('neutral')
            data['five_cells']['1h']['indicator_observed_fraction']=fraction
            data['five_cells']['1h']['support_resistance']['estimated']=True
            result=build(data)
            self.assertFalse(result['offered'])
            self.assertIn('coverage',result['reason'])
        exact=chart('neutral')
        exact['five_cells']['1h']['indicator_observed_fraction']=.95
        exact['five_cells']['1h']['support_resistance']['estimated']=True
        self.assertTrue(build(exact)['offered'])

    def test_future_gate_conflicting_policy_and_trigger_at_edge_reject(self):
        result=build(regime=dict(pair='TESTUSDT',asof_ms=ASOF+1,allowed_directions=['long','neutral']))
        self.assertFalse(result['offered'])
        with patch('trader.strategies.recommender_setup.read_chart',return_value=chart()):
            future_quote=build_recommender_setup('TESTUSDT',bars(),dict(MARKET,observed_at_ms=ASOF+1),
                {},ASOF,dict(min_grids=4,max_grids=4,preview_fn=safe_preview),funding=FUNDING)
        self.assertFalse(future_quote['offered'])
        for params in (dict(leverage=4),dict(used_margin=900),dict(range_exit_stop_pct=.05)):
            self.assertFalse(build(parameters=params)['offered'])
        data=chart()
        for tf in ('5m','15m'):
            data['five_cells'][tf]['entry_context']['last_high']=102.
        result=build(data)
        self.assertFalse(result['entry_eligible'])
        self.assertIsNone(result['trigger'])

    def test_real_core_preview_all_modes_valid_lots_and_fee_budget(self):
        from trader.strategies.kucoin_grid import GridConfig, preview
        for direction in ('long','short','neutral'):
            with patch('trader.strategies.recommender_setup.read_chart',return_value=chart(direction)):
                result=build_recommender_setup('TESTUSDT',bars(),MARKET,{},ASOF,
                    dict(min_grids=4,max_grids=8),funding=FUNDING)
            self.assertTrue(result['offered'],result)
            legs=2 if direction=='neutral' else 1
            for offer in result['candidates']:
                actual=preview(GridConfig(**offer['config']))
                self.assertAlmostEqual(actual['profit_per_grid_min'],offer['preview']['profit_per_grid_min'])
                self.assertAlmostEqual(offer['quantity']/.001,round(offer['quantity']/.001))
                self.assertLessEqual(legs*offer['quantity']*offer['grids']*offer['entry']/5+
                                     offer['opening_fee_budget'],1000)
                for buffer in offer['buffers'].values():
                    self.assertGreaterEqual(buffer['edge_fraction'],.1)
                    self.assertGreaterEqual(buffer['range_fraction'],.1)

    def test_neutral_short_side_unsafe_cannot_be_hidden_by_long_safety(self):
        def unsafe(config):
            value=safe_preview(config)
            value['liquidation_price_short']=config.high*1.05
            return value
        result=build(chart('neutral'),parameters=dict(preview_fn=unsafe))
        self.assertFalse(result['offered'])
        self.assertTrue(all('liquidation' in row['reason'].lower() for row in result['search']['rejected_counts']))

    def test_current_quote_outside_chart_band_is_not_an_entry_signal(self):
        with patch('trader.strategies.recommender_setup.read_chart',return_value=chart()):
            result=build_recommender_setup('TESTUSDT',bars(),dict(MARKET,price=103),{},ASOF,
                dict(min_grids=4,max_grids=8,preview_fn=safe_preview),funding=FUNDING)
        self.assertTrue(result['offered'])
        self.assertFalse(result['entry_eligible'])
        self.assertFalse(result['entry_signal']['eligible'])
        self.assertFalse(result['can_arm'])
        self.assertIsNone(result['trigger'])

    def test_real_chart_api_uses_declared_bias_variant_without_future_frames(self):
        from trader.tests.test_chart_read import bars as frame_bars, ASOF as frame_asof, DURATIONS
        frames={tf:frame_bars(tf,.45) for tf in DURATIONS}
        frames['1d']=frame_bars('1d',-.45)
        rows=[dict(r,timestamp_ms=r['timestamp_ms']+frame_asof-ASOF) for r in bars()]
        params=dict(min_grids=4,max_grids=4,preview_fn=safe_preview,bias_mode='4h-only')
        result=build_recommender_setup('TESTUSDT',rows,MARKET,frames,frame_asof,params,funding=None)
        self.assertEqual(result['original_direction'],'long')
        self.assertEqual(result['chart']['bias_mode'],'4h-only')
        before=copy.deepcopy(result['chart'])
        frames['4h'].append(dict(timestamp_ms=frame_asof,open=1,high=1,low=1,close=1,volume=0))
        again=build_recommender_setup('TESTUSDT',rows,MARKET,frames,frame_asof,params,funding=None)
        self.assertEqual(again['chart'],before)


if __name__=='__main__':
    unittest.main()
