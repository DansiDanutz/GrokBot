import unittest
import copy
import json
from unittest.mock import patch

from trader.research.kucoin_replay import run_window
from trader.tests.test_kucoin_replay import MemorySnapshot, candidate, START, HOUR


class ChartSnapshot(MemorySnapshot):
    income_chart_v3 = True

    def candles(self,pair,start,end):
        return [dict(timestamp_ms=t,open=100,high=103,low=97,close=100)
                for t in range(start,end,60000)]

    def funding(self,pair,start,end):
        return [dict(symbol=pair,timestamp_ms=t,rate=.0001)
                for t in range(start+HOUR,end+1,HOUR)]

    def funding_coverage(self,pair,start,end):
        return dict(start_ms=start,end_ms=end,modeled_complete=True,
                    known=False,verified=False,coverage_verified=False)


def scan(records,asof,running_pairs=(),parameters=None):
    rows=[]
    for record in records:
        if record['pair'] in running_pairs:
            continue
        row=candidate(record['pair'])
        row.update(score=30,grid_income_per_hour=30,expected_gph=15)
        row['setup'].update(can_arm=True,funded_entry_eligible=True,
            funded_economics_eligible=True,grid_income_per_hour=30,expected_gph=15,
            stop_loss=90,range_exit_stop_pct=0,adaptive_range_stops=False)
        rows.append(row)
    return dict(radar=rows,rejected=[],coverage={'observed':len(rows) or 1})


class IncomeChartReplayTests(unittest.TestCase):
    def test_compact_research_storage_preserves_execution_and_checkpoint_semantics(self):
        from trader.research.replay_evidence import compact_scan, compact_form
        def rich_scan(*args,**kwargs):
            result=scan(*args,**kwargs)
            result['strategy']='income_chart_v3'
            for row in result['radar']:
                row['setup']['search']={'evaluated_count':199,'rejected_counts':[
                    {'grids':count,'reason':'fixture rejection','diagnostics':'unused detail '*300}
                    for count in range(2,201)]}
            return result
        with patch('trader.research.kucoin_replay.radar',side_effect=rich_scan), \
             patch('trader.research.kucoin_replay.compact_scan',wraps=compact_scan,create=True) as scan_storage, \
             patch('trader.research.kucoin_replay.compact_form',wraps=compact_form,create=True) as form_storage:
            small=run_window(ChartSnapshot(),START,START+2*HOUR,dict(strategy='income_chart_v3'))
        self.assertTrue(scan_storage.called)
        self.assertTrue(form_storage.called)
        with patch('trader.research.kucoin_replay.radar',side_effect=rich_scan), \
             patch('trader.research.kucoin_replay.compact_scan',side_effect=copy.deepcopy,create=True), \
             patch('trader.research.kucoin_replay.compact_form',side_effect=copy.deepcopy,create=True):
            full=run_window(ChartSnapshot(),START,START+2*HOUR,dict(strategy='income_chart_v3'))
        for key in ('ledger','partial_metrics','equity_timeline','capital_events','switches','entry_decisions','hourly_tracker'):
            self.assertEqual(small[key],full[key],key)
        self.assertLess(len(json.dumps(small['hourly_radar'])),len(json.dumps(full['hourly_radar']))/3)

    def test_missing_challenger_observation_cannot_close_the_incumbent(self):
        from trader.research.kucoin_replay import _execute_portfolio_switch, _config
        from trader.strategies.kucoin_grid import create_bot
        source=ChartSnapshot()
        source.market=lambda *args:{}
        state=create_bot(_config(candidate('A')['setup']),100,START)
        report=dict(parameters={'strategy':'income_chart_v3'},bots=[{'bot_id':'old','state':state}],
                    capital_events=[],ledger=[],switches=[])
        decision=dict(worst_bot_id='old',replacement=candidate('B'),switch_cost={'net_realized_on_close':0})
        with patch('trader.research.kucoin_replay._close',return_value=0) as close, \
             patch('trader.research.kucoin_replay._open',return_value=1200):
            cash=_execute_portfolio_switch(source,report,START,1200,decision,{'old':{}})
        close.assert_not_called()
        self.assertEqual(cash,1200)
        self.assertEqual(report['capital_events'][0]['action'],'await_observed_switch_price')

    def test_a_setup_reference_price_cannot_replace_a_missing_entry_observation(self):
        source=ChartSnapshot()
        original=source.market
        source.market=lambda pair,at:{} if at==START else original(pair,at)
        with patch('trader.research.kucoin_replay.radar',side_effect=scan):
            result=run_window(source,START,START+HOUR,dict(strategy='income_chart_v3'))
        self.assertEqual(result['bots'],[])
        self.assertTrue(any(row['action']=='await_observed_price' for row in result['capital_events']))

    def test_recorded_funding_income_and_checkpoint_resume_match(self):
        from trader.research.kucoin_replay import run_chunk
        options=dict(strategy='income_chart_v3',bias_mode='4h-only',regime_gate=False)
        identity=dict(source='test-source',data='test-data',registration='test-registration')
        with patch('trader.research.kucoin_replay.radar',side_effect=scan):
            full=run_window(ChartSnapshot(),START,START+7*HOUR,options)
            packet=None
            for _ in range(7):
                part=run_chunk(ChartSnapshot(),START,START+7*HOUR,options,
                    max_hours=1,checkpoint=packet,identity=identity)
                packet=json.loads(json.dumps(part['checkpoint']))
            resumed=part['result']
        for result in (full,resumed):
            result.pop('performance',None)
        self.assertEqual(full,resumed)

    def test_strategy_and_distinct_start_income_and_grid_rate_reach_execution(self):
        with patch('trader.research.kucoin_replay.radar',side_effect=scan) as scanner:
            result=run_window(ChartSnapshot(),START,START+HOUR,dict(strategy='income_chart_v3'))
        self.assertEqual(scanner.call_args.args[3]['strategy'],'income_chart_v3')
        self.assertEqual(len(result['bots']),2)
        for bot in result['bots']:
            self.assertEqual(bot['expected_start_gph'],15)
            self.assertEqual(bot['expected_start_income_per_hour'],30)
        funding=[row for row in result['ledger'] if row['kind'].startswith('funding')]
        self.assertEqual(len(funding),2)
        self.assertTrue(all(row['timestamp_ms']==START+HOUR for row in funding))

    def test_six_hour_income_uses_full_ledger_and_keeps_estimation_explicit(self):
        with patch('trader.research.kucoin_replay.radar',side_effect=scan):
            result=run_window(ChartSnapshot(),START,START+7*HOUR,dict(strategy='income_chart_v3'))
        rows=result['hourly_tracker']
        mature=[row for row in rows if row['asof_ms']-row['start_ms']>=6*HOUR]
        self.assertTrue(mature)
        self.assertTrue(all(row['income_coverage_6h_known'] for row in mature))
        self.assertTrue(all(row['income_6h_estimated'] for row in mature))
        self.assertTrue(all(row['realized_grid_income_per_hour_6h'] is not None for row in mature))
        young=[row for row in rows if row['asof_ms']-row['start_ms']<6*HOUR]
        self.assertTrue(all(row['realized_grid_income_per_hour_6h'] is None for row in young))
        self.assertFalse(result['coverage']['complete'])
        self.assertIsNone(result['verified_metrics']['grid_income_per_hour'])
        self.assertIn('funded_cycle_diagnostics',result)

    def test_unknown_funding_never_becomes_known_income_or_verified_zero(self):
        source=ChartSnapshot()
        source.funding=lambda *args:[]
        source.funding_coverage=lambda *args:dict(modeled_complete=False,verified=False)
        with patch('trader.research.kucoin_replay.radar',side_effect=scan):
            result=run_window(source,START,START+7*HOUR,dict(strategy='income_chart_v3'))
        mature=[r for r in result['hourly_tracker'] if r['asof_ms']-r['start_ms']>=6*HOUR]
        self.assertTrue(mature)
        self.assertTrue(all(r['realized_grid_income_per_hour_6h'] is None for r in mature))
        self.assertTrue(all(r['income_coverage_6h_known'] is False for r in mature))

    def test_missing_execution_history_cannot_be_measured_as_zero_income(self):
        source=ChartSnapshot()
        source.historical_candle_only=True
        source.bounds=lambda:(START,START+10*HOUR)
        source.candles=lambda *args:[]
        with patch('trader.research.kucoin_replay.radar',side_effect=scan):
            result=run_window(source,START,START+7*HOUR,
                dict(strategy='income_chart_v3',historical_candle_only=True))
        mature=[r for r in result['hourly_tracker'] if r['asof_ms']-r['start_ms']>=6*HOUR]
        self.assertTrue(mature)
        for row in mature:
            self.assertFalse(row['income_coverage_6h_known'])
            self.assertIsNone(row['realized_grid_income_per_hour_6h'])
            self.assertEqual(row['income_execution_coverage_6h']['observed_minutes'],0)

    def test_boundary_exit_remains_prior_to_an_order_at_the_edge(self):
        source=ChartSnapshot()
        source.candles=lambda pair,start,end:[dict(timestamp_ms=t,open=100,high=100,low=90,close=95)
                                             for t in range(start,end,60000)]
        with patch('trader.research.kucoin_replay.radar',side_effect=scan):
            result=run_window(source,START,START+HOUR,dict(strategy='income_chart_v3'))
        self.assertEqual(len(result['bots']),2)
        self.assertTrue(all(bot['status']=='stopped' for bot in result['bots']))
        stops=[row for row in result['ledger'] if row['kind']=='stop_loss']
        self.assertTrue(stops)
        self.assertTrue(all(row['price']==90 for row in stops))
        self.assertFalse(any(row.get('completed_grid') and row['price']==90 for row in result['ledger']))
