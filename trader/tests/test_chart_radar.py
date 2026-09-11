import unittest
from unittest.mock import patch

from trader.research.kucoin_radar import radar
from trader.research.kucoin_operator import operator_report, validate_running
from trader.tests.test_kucoin_radar import market
from trader.tests.test_grid_count_search import ASOF, FUNDING, bars
from trader.tests.test_recommender_setup import chart, safe_preview

STRATEGY='income_chart_v3'


def record(pair='TESTUSDT', **overrides):
    value=dict(pair=pair,bars=bars(),market=market(observed_at_ms=ASOF,listed_at_ms=0,
        tick_size=.01,lot_size=.001,multiplier=1.,bid_depth_usdt=10000,ask_depth_usdt=10000),
        frames={tf:[] for tf in ('1d','4h','1h','15m','5m')},funding_terms=FUNDING,
        funding_diagnostics={'usable_for_projection':True},regime=None)
    value.update(overrides)
    return value


def offer(pair='TESTUSDT', income=10., can_arm=True):
    return dict(pair=pair,offered=True,eligible=True,direction='long',reason='income offer',
        chart=dict(five_cells={tf:{'direction':'up'} for tf in ('1d','4h','1h','15m','5m')},bias_reason='agreement'),
        candidates=[{'grids':n} for n in (4,5,6)],entry_eligible=True,can_arm=can_arm,
        funded_entry_eligible=can_arm,funded_economics_eligible=can_arm,provisional=not can_arm,
        low=98.,high=102.,grids=4,entry=100.,quantity=1.,trigger=101.,stop_loss=98.,stop_loss_high=None,
        leverage=5,used_margin=1000.,reserved_margin=200.,total_margin=1200.,opening_fee_budget=.24,
        expected_gph=3.,grid_income_per_hour=income,selection_income_per_hour=income,
        funding_adjusted_income_per_hour=income if can_arm else None,fee_net_income_per_hour=income,
        ranking_basis='funding_adjusted_estimate' if can_arm else 'fee_only_provisional_estimate',
        preview={'profit_per_grid_min':.5,'profit_per_grid_max':.6,'kucoin_profit_pct_min':1.},
        quantity_calibrated=False,liquidation_estimated=True,
        range_exit_stop_pct=0,adaptive_range_stops=False,waiting_for_trigger=True,
        market_entry_ready=False,entry_signal={'eligible':True},windows={})


class ChartRadarTests(unittest.TestCase):
    def test_explicit_strategy_uses_caller_frames_terms_gate_and_income_ranking(self):
        frames={'1h':[{'sentinel':True}]}
        gate={'asof_ms':ASOF,'pair':'A','allowed_directions':['neutral']}
        records=[record('A',frames=frames,regime=gate),record('B')]
        def builder(pair,*args,**kwargs):
            return offer(pair,1. if pair=='A' else 5.)
        with patch('trader.research.kucoin_radar.build_recommender_setup',side_effect=builder) as mocked, \
             patch('trader.research.kucoin_radar.build_setup',side_effect=AssertionError('archived setup used')), \
             patch('trader.research.kucoin_radar.crossing_score',side_effect=AssertionError('one-way score used')):
            result=radar(records,ASOF,parameters={'strategy':STRATEGY,'setup':{'max_grids':20}})
        self.assertEqual(result['strategy'],STRATEGY)
        self.assertEqual([r['pair'] for r in result['radar']],['B','A'])
        call=mocked.call_args_list[0]
        self.assertEqual(call.args[3],frames)
        self.assertEqual(call.kwargs['funding'],FUNDING)
        self.assertEqual(call.kwargs['regime'],gate)
        row=result['radar'][0]
        self.assertEqual(len(row['five_cells']),5)
        self.assertEqual(len(row['top_grid_counts']),3)
        self.assertEqual(row['grid_income_per_hour'],5.)
        self.assertEqual(row['actual_net_usdt_per_grid'],.5)
        self.assertEqual(row['kind'],'paired_completed_grid_income_estimate')
        self.assertTrue(row['offered'])
        self.assertTrue(row['can_arm'])
        self.assertFalse(row['quantity_calibrated'])
        self.assertTrue(row['liquidation_estimated'])

    def test_unknown_funding_skips_filter_but_keeps_provisional_offer(self):
        with patch('trader.research.kucoin_radar.build_recommender_setup',return_value=offer(can_arm=False)):
            result=radar([record(funding_terms=None)],ASOF,parameters={'strategy':STRATEGY})
        row=result['radar'][0]
        self.assertIn('funding_window',row['skipped_filters'])
        self.assertTrue(row['provisional'])
        self.assertFalse(row['can_arm'])
        self.assertIsNone(row['funding_adjusted_income_per_hour'])

    def test_known_funding_005_percent_limit_and_candle_only_exception(self):
        with patch('trader.research.kucoin_radar.build_recommender_setup',return_value=offer()) as builder:
            for rate,interval,accepted in ((.00025,4*3600000,True),(.000251,4*3600000,False),
                                          (-.000501,8*3600000,False)):
                with self.subTest(rate=rate):
                    row=record(funding_terms=dict(FUNDING,rate=rate,interval_ms=interval))
                    result=radar([row],ASOF,parameters={'strategy':STRATEGY})
                    self.assertEqual(bool(result['radar']),accepted)
            row=record(funding_terms=dict(FUNDING,rate=.02))
            row['market'].update(filter_mode='candle-only filters',turnover_basis='observed quote turnover')
            result=radar([row],ASOF,parameters={'strategy':STRATEGY})
            self.assertTrue(result['radar'])
            self.assertIn('funding_window',result['radar'][0]['skipped_filters'])
        self.assertGreater(builder.call_count,0)

    def test_missing_chart_range_keeps_rejection_evidence_and_no_legacy_fallback(self):
        result=radar([record()],ASOF,parameters={'strategy':STRATEGY})
        self.assertEqual(result['radar'],[])
        rejected=result['rejected'][0]
        self.assertEqual(len(rejected['five_cells']),5)
        self.assertFalse(rejected['offered'])
        self.assertIn('one-hour',rejected['reason'])
        self.assertEqual(rejected['candle_coverage']['actual'],10080)
        self.assertTrue(rejected['coverage_issue'])

    def test_real_adapter_path_shows_income_sweep_and_pending_arm(self):
        with patch('trader.strategies.recommender_setup.read_chart',return_value=chart()):
            result=radar([record()],ASOF,parameters={'strategy':STRATEGY,
                'setup':dict(min_grids=4,max_grids=8,preview_fn=safe_preview)})
        row=result['radar'][0]
        self.assertTrue(row['can_arm'])
        self.assertTrue(row['setup']['waiting_for_trigger'])
        self.assertEqual(row['grid_income_per_hour'],row['setup']['selection_income_per_hour'])
        self.assertIn('kucoin_profit_pct_min',row['setup']['preview'])
        self.assertEqual(len(row['top_grid_counts']),3)

    def test_chosen_and_top_counts_expose_conservative_cash_and_display_percent(self):
        for funding in (FUNDING,None):
            with patch('trader.strategies.recommender_setup.read_chart',return_value=chart()):
                scan=radar([record(funding_terms=funding)],ASOF,parameters={'strategy':STRATEGY,
                    'setup':dict(min_grids=4,max_grids=8,preview_fn=safe_preview)})
            row=scan['radar'][0]
            for form in [row['setup']]+row['top_grid_counts']:
                economics=[item for by_side in form['economics'].values() for item in by_side.values()]
                fee_only=min(item['fee_net_usdt'] for item in economics)
                self.assertAlmostEqual(form['estimated_fee_only_net_usdt_per_grid'],fee_only)
                self.assertEqual(form['kucoin_profit_pct_min'],form['preview']['kucoin_profit_pct_min'])
                if funding is None:
                    self.assertIsNone(form['funding_adjusted_net_usdt_per_grid'])
                    self.assertAlmostEqual(form['net_usdt_per_grid'],fee_only)
                else:
                    conservative=min(item['admission_net_usdt'] for item in economics)
                    self.assertAlmostEqual(form['funding_adjusted_net_usdt_per_grid'],conservative)
                    self.assertAlmostEqual(form['net_usdt_per_grid'],conservative)
            self.assertEqual(row['net_usdt_per_grid'],row['setup']['net_usdt_per_grid'])
            self.assertEqual(row['kucoin_profit_pct_min'],row['setup']['kucoin_profit_pct_min'])

    def test_existing_market_coverage_and_running_filters_still_apply(self):
        rows=[record('RUN'),record('LOW'),record('GAP')]
        rows[1]['market']['quote_turnover_24h']=1
        rows[2]['bars']=bars()[:9575]
        with patch('trader.research.kucoin_radar.build_recommender_setup',return_value=offer()) as builder:
            result=radar(rows,ASOF,running_pairs=['RUN'],parameters={'strategy':STRATEGY})
        self.assertEqual(result['radar'],[])
        self.assertEqual(len(result['rejected']),3)
        builder.assert_not_called()

    def test_operator_arms_known_pending_form_using_income_not_cash_proxy(self):
        class Snapshot:
            def records(self,at,pairs=None):
                return [record()]
        document=dict(schema_version=1,bots=[],capital=dict(available_cash_usdt=2400,asof_ms=ASOF))
        with patch('trader.research.kucoin_radar.build_recommender_setup',return_value=offer(income=.1)):
            result=operator_report(Snapshot(),ASOF,document,{'strategy':STRATEGY})
        self.assertEqual(len(result['recommended_forms']),1)
        self.assertTrue(result['recommended_forms'][0]['waiting_for_trigger'])
        self.assertEqual(result['entry_decision']['remaining_cash'],1200)
        economics=result['entry_decision']['selected'][0]['economics']
        self.assertAlmostEqual(economics['projected_grid_income'],.6)
        self.assertNotAlmostEqual(economics['projected_grid_income'],3*.5*6)

    def test_operator_direction_uses_chart_path_and_arm_gate(self):
        from trader.research.kucoin_operator import _direction
        class Snapshot:
            def records(self,at,pairs=None):
                return [record()]
        form=offer()
        form['chart'].update(bias_mode='1d+4h',minimum_confidence=.75)
        for cell in form['chart']['five_cells'].values():
            cell.update(available=True,confidence=1.)
        with patch('trader.research.kucoin_radar.build_recommender_setup',return_value=form), \
             patch('trader.research.kucoin_operator.build_setup',side_effect=AssertionError('archived builder called')):
            direction=_direction(Snapshot(),{'pair':'TESTUSDT','direction':'short'},ASOF,{'strategy':STRATEGY})
        self.assertTrue(direction['known'])
        self.assertTrue(direction['flipped'])
        self.assertIsNotNone(direction['candidate'])

    def test_invalid_future_funding_rejects_before_adapter(self):
        for terms in (dict(FUNDING,observed_at_ms=ASOF+1),dict(FUNDING,interval_ms=0),dict(FUNDING,rate=True)):
            with patch('trader.research.kucoin_radar.build_recommender_setup') as builder:
                result=radar([record(funding_terms=terms)],ASOF,parameters={'strategy':STRATEGY})
            self.assertEqual(result['radar'],[])
            self.assertTrue(result['rejected'][0]['coverage_issue'])
            builder.assert_not_called()

    def test_running_start_income_is_declared_immutable_and_unverified_six_hours_stays_unknown(self):
        from trader.tests.test_kucoin_operator import running
        document=running(start_ms=ASOF,expected_start_income_per_hour=2.5)
        class Snapshot:
            def records(self,at,pairs=None):
                return []
            def market(self,pair,at):
                return dict(bid=99.99,ask=100.01,observed_at_ms=at,book_observed_at_ms=at)
        report=operator_report(Snapshot(),ASOF,document,{'strategy':STRATEGY})
        summary=report['running'][0]['tracker']
        self.assertEqual(summary['expected_start_income_per_hour'],2.5)
        self.assertIn('immutable',summary['starting_income_basis'])
        self.assertEqual(summary['start_ms'],ASOF)
        self.assertEqual(summary['asof_ms'],ASOF)
        self.assertFalse(summary['income_coverage_6h_known'])
        self.assertIsNone(summary['realized_grid_income_per_hour_6h'])
        for value in (-1,float('nan'),True,None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_running(running(start_ms=ASOF,expected_start_income_per_hour=value),ASOF,{'strategy':STRATEGY})
        validate_running(running(start_ms=ASOF,expected_start_income_per_hour=0),ASOF,{'strategy':STRATEGY})

    def test_operator_preserves_offers_but_withholds_unarmed_funding_and_live_use(self):
        class Snapshot:
            def records(self,at,pairs=None):
                return [record()]
        document=dict(schema_version=1,bots=[],capital=dict(available_cash_usdt=2400,asof_ms=ASOF))
        with patch('trader.research.kucoin_radar.build_recommender_setup',return_value=offer(can_arm=False)):
            result=operator_report(Snapshot(),ASOF,document,{'strategy':STRATEGY})
        self.assertEqual(len(result['offered_forms']),1)
        self.assertEqual(result['recommended_forms'],[])
        self.assertEqual(result['capital']['available_cash_usdt'],2400)
        self.assertFalse(result['live_use']['actionable'])
        self.assertEqual(result['live_use']['status'],'research_only_live_execution_unavailable')
        self.assertEqual(result['strategy'],STRATEGY)
        self.assertTrue(result['funding_withheld_offers'])


class RecordedOperatorIncomeTests(unittest.TestCase):
    class Snapshot:
        def __init__(self, complete=True):
            self.complete=complete
            self.coverage_calls=[]

        def records(self,at,pairs=None):
            return []

        def candles(self,pair,start,end):
            # One paired grid opens at99 after start, holds through funding,
            # and closes at100 at7h. Initial above-entry seed positions persist.
            return [dict(time_ms=t,open=100 if t==0 or t>=7*3600000 else 99,
                         high=100 if t==0 or t>=7*3600000 else 99,
                         low=100 if t==0 or t>=7*3600000 else 99,
                         close=100 if t==0 or t>=7*3600000 else 99)
                    for t in range(start,end,60000)]

        def funding(self,pair,start,end):
            when=90*60000
            return [dict(symbol=pair,timestamp_ms=when,rate=.001)] if start<when<=end else []

        def funding_coverage(self,pair,start,end):
            self.coverage_calls.append((pair,start,end))
            return dict(pair=pair,start_ms=start,end_ms=end,modeled_complete=self.complete,
                        known=False,verified=False,basis='offline supplied settlement calendar model')

        def market(self,pair,at):
            return dict(bid=99.99,ask=100.01,observed_at_ms=at,book_observed_at_ms=at)

    def test_recorded_funding_full_history_is_attributed_before_six_hour_window(self):
        import json
        from trader.tests.test_kucoin_operator import running
        from trader.research.funded_cycles import funded_cycles
        snapshot=self.Snapshot()
        with patch('trader.research.kucoin_operator.track_bars',wraps=__import__(
                'trader.research.kucoin_tracker',fromlist=['track_bars']).track_bars) as tracking:
            result=operator_report(snapshot,8*3600000,running(expected_start_income_per_hour=1.5),
                                   {'strategy':STRATEGY})
        bot=result['running'][0]
        summary=bot['tracker']
        ledger=json.loads(json.dumps(bot['ledger']))
        cycles=funded_cycles(ledger,2*3600000,8*3600000,coverage_known=False)
        self.assertEqual(cycles['summary']['completed_grids'],1)
        cycle=cycles['completed'][0]
        self.assertLess(cycle['opening_timestamp_ms'],2*3600000)
        self.assertAlmostEqual(cycle['allocated_funding'],-.099)
        self.assertAlmostEqual(cycle['modeled_net'],1-.0594-.06-.099)
        self.assertIsNone(cycle['net'])
        self.assertTrue(summary['income_coverage_6h_known'])
        self.assertTrue(summary['income_6h_estimated'])
        self.assertAlmostEqual(summary['realized_grid_income_per_hour_6h'],cycle['modeled_net']/6)
        self.assertAlmostEqual(summary['realized_gph_6h'],1/6)
        self.assertEqual(summary['expected_start_income_per_hour'],1.5)
        self.assertIsNone(summary['funding'])
        self.assertTrue(any(row['kind']=='seed' and row['timestamp_ms']==0 for row in ledger))
        self.assertEqual([row['timestamp_ms'] for row in ledger if row['kind']=='funding'],[90*60000])
        self.assertTrue(all(call.kwargs['funding_mode']=='recorded_events' for call in tracking.call_args_list))
        self.assertTrue(all(call.kwargs['funding_coverage']['start_ms']==0 for call in tracking.call_args_list))
        self.assertIn(('TESTUSDTM',0,8*3600000),snapshot.coverage_calls)
        self.assertFalse(bot['hourly_history'][4]['income_coverage_6h_known'])
        self.assertTrue(bot['hourly_history'][5]['income_coverage_6h_known'])

    def test_terminal_summary_does_not_relabel_modeled_funding_as_verified(self):
        from trader.tests.test_kucoin_operator import running
        class Stopped(self.Snapshot):
            def candles(self,pair,start,end):
                rows=super().candles(pair,start,end)
                for row in rows:
                    if row['time_ms']>=2*3600000:
                        row.update(open=110,high=110,low=110,close=110)
                return rows
        report=operator_report(Stopped(),8*3600000,running(expected_start_income_per_hour=1.5),
                               {'strategy':STRATEGY})
        summary=report['running'][0]['tracker']
        self.assertEqual(summary['status'],'stopped')
        self.assertIsNone(summary['funding'])
        self.assertIsNotNone(summary['funding_modeled_cash'])
        self.assertEqual(summary['funding_mode'],'recorded_events')

    def test_coverage_must_span_original_hold_not_only_six_hour_cut(self):
        from trader.tests.test_kucoin_operator import running
        class Partial(self.Snapshot):
            def funding_coverage(self,pair,start,end):
                return dict(super().funding_coverage(pair,start,end),start_ms=2*3600000)
        report=operator_report(Partial(),8*3600000,running(expected_start_income_per_hour=1.5),
                               {'strategy':STRATEGY})
        summary=report['running'][0]['tracker']
        self.assertFalse(summary['income_coverage_6h_known'])
        self.assertIsNone(summary['realized_grid_income_per_hour_6h'])

    def test_unknown_recorded_coverage_and_warmup_do_not_invent_income(self):
        from trader.tests.test_kucoin_operator import running
        for end,complete in ((5*3600000,True),(8*3600000,False)):
            with self.subTest(end=end,complete=complete):
                result=operator_report(self.Snapshot(complete),end,running(expected_start_income_per_hour=1.5),
                                       {'strategy':STRATEGY})
                summary=result['running'][0]['tracker']
                self.assertFalse(summary['income_coverage_6h_known'])
                self.assertIsNone(summary['realized_grid_income_per_hour_6h'])
                self.assertIsNone(summary['realized_gph_6h'])
                self.assertTrue(summary['income_coverage_reason'])


if __name__=='__main__':
    unittest.main()
