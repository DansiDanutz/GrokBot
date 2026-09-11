"""Synthetic lifecycle metrics; no credentials, providers or runtime imports."""
from copy import deepcopy
import unittest
from paper_grid import engine, trade_metrics
from paper_grid.test_engine import quote

START = 1800000000.0
A, B = 'AAAUSDTM', 'BBBUSDTM'


def event(kind, at, symbol=A, **extra):
    row = dict(type=kind, time=START+at, symbol=symbol, account='baseline')
    if kind in ('open', 'add'):
        row.update(notional=100, fill_price=100, contracts=1000, fee=.06)
    if kind == 'close':
        row.update(net_pnl=2, entry_fees=.06, exit_fee=.06,
                   funding_model_cost=.01, reason='net_profit_target')
    return dict(row, **extra)


def document(events, observations=None):
    return dict(start_at=START, config=engine.default_config(), tick_seconds=300,
                events=events, observations=observations or [],
                accounts={a: dict(events=[], state=dict(positions={}))
                          for a in ('baseline', 'liquidation_filter')})


class TradeMetricTests(unittest.TestCase):
    def metric(self, doc, start=START, end=START+1000):
        return trade_metrics.report(doc, 'baseline', start, end, include_start=True)

    def test_profit_hold_symbol_and_union_exposure(self):
        doc = document([event('open',0),event('open',100,B),event('close',400),
                        event('close',600,B,net_pnl=-1)])
        before = deepcopy(doc)
        row = self.metric(doc)
        self.assertEqual(row['profit_factor'],2)
        self.assertEqual(row['average_hold_seconds'],450)
        self.assertEqual(row['exposure']['observed_seconds'],600)
        self.assertEqual(row['exposure']['fraction'],.6)
        self.assertEqual(row['per_symbol_pnl'],[
            dict(symbol=A,closes=1,net_pnl=2),dict(symbol=B,closes=1,net_pnl=-1)])
        self.assertEqual(doc,before)

    def test_unknown_open_does_not_become_zero_exposure_or_hold(self):
        row = self.metric(document([event('close',400)]))
        self.assertIsNone(row['exposure']['fraction'])
        self.assertEqual(row['exposure']['coverage'],'partial')
        self.assertIsNone(row['average_hold_seconds'])
        self.assertEqual(row['unknown_hold_trades'],1)
        self.assertIsNone(row['trades'][0]['observed_mae_net_usdt'])

    def test_new_experiment_without_last_tick_has_known_flat_exposure(self):
        doc=document([]);doc['last_tick_at']=None
        row=self.metric(doc)
        self.assertEqual(row['exposure']['observed_seconds'],0)
        self.assertEqual(row['exposure']['fraction'],0)

    def test_inherited_current_position_overlapping_historical_window_is_unknown(self):
        doc=document([]);doc['last_tick_at']=START+2000
        doc['accounts']['baseline']['state']['positions']={A:dict(opened_at=START-100)}
        row=self.metric(doc)
        self.assertIsNone(row['exposure']['fraction'])
        self.assertEqual(row['exposure']['unknown_lifecycles'],1)
        doc['accounts']['baseline']['state']['positions'][A]['opened_at']=START+1500
        self.assertEqual(self.metric(doc)['exposure']['fraction'],0)

    def test_absent_filter_inputs_are_unknown_but_recorded_empty_is_blocked(self):
        obs=dict(time=START,market={A:quote(A,now=START)})
        doc=document([event('open',0),event('close',400)],[obs])
        row=self.metric(doc)['baseline_filter_blocked']
        self.assertEqual(row['unknown_trades'],1)
        self.assertEqual(row['blocked_trades'],0)
        obs['coinglass']={}
        row=self.metric(doc)['baseline_filter_blocked']
        self.assertEqual(row['unknown_trades'],0)
        self.assertEqual(row['blocked_trades'],1)

    def test_orphan_add_marks_open_exposure_unknown(self):
        row=self.metric(document([event('add',100)]))
        self.assertIsNone(row['exposure']['fraction'])
        self.assertEqual(row['exposure']['unknown_lifecycles'],1)

    def test_open_position_and_window_clipping(self):
        doc = document([event('open',0),event('open',100,B),event('close',400)])
        row = self.metric(doc,START+200,START+500)
        self.assertEqual(row['exposure']['observed_seconds'],300)
        self.assertEqual(row['exposure']['fraction'],1)
        self.assertEqual(row['average_hold_seconds'],400)

    def test_window_boundary_and_no_loss_factor(self):
        doc = document([event('open',0),event('close',400)])
        row = trade_metrics.report(doc,'baseline',START+400,START+900)
        self.assertEqual(row['closed_trades'],0)
        self.assertEqual(row['profit_factor_state'],'no_closes')
        self.assertEqual(self.metric(doc)['profit_factor_state'],'no_losses')

    def test_filter_cohort_only_initial_entry_with_known_evidence(self):
        obs = dict(time=START,market={A:quote(A,now=START)},coinglass={})
        doc = document([event('open',0),event('add',100),event('close',400,net_pnl=3),
                        event('open',500),event('close',800,net_pnl=-2)],[obs])
        row = self.metric(doc)['baseline_filter_blocked']
        self.assertEqual(row['blocked_trades'],1)
        self.assertEqual(row['net_pnl'],3)
        self.assertEqual(row['unknown_trades'],1)
        self.assertEqual(row['coverage'],'partial')

    def test_excursions_match_saved_accounting_with_add_and_funding(self):
        c = engine.default_config()
        state = engine.initial_state(c,START)
        events, observations, nets = [], [], []
        for seconds, price, add in [(0,100,False),(300,98,True),(600,99,False)]:
            at=START+seconds; market={A:quote(A,price,at,add_eligible=add,funding_rate=.01)}
            state, rows = engine.step(state,market,at,c)
            events.extend(dict(e,account='baseline') for e in rows)
            observations.append(dict(time=at,market=market,coinglass={}))
            nets.append(engine._net(state['positions'][A],market[A],c))
        closing=[]; engine._close(state,A,market[A],at,c,'rotation',closing)
        events.extend(dict(e,account='baseline') for e in closing)
        doc=document(events,observations);doc['config']=c
        row=self.metric(doc)['trades'][0]
        self.assertAlmostEqual(row['observed_mae_net_usdt'],min(nets))
        self.assertAlmostEqual(row['observed_mfe_net_usdt'],max(nets))
        self.assertEqual(row['excursion_coverage'],'complete_observations')
        self.assertGreaterEqual(row['excursion_samples'],3)

    def test_tick_jitter_does_not_invent_a_missing_observation(self):
        doc=document([event('open',0),event('close',301)],
            [dict(time=START,market={A:quote(A,now=START)}),
             dict(time=START+301,market={A:quote(A,now=START+301)})])
        row=self.metric(doc)['trades'][0]
        self.assertEqual(row['excursion_missing_samples'],0)
        self.assertEqual(row['excursion_coverage'],'complete_observations')

    def test_missing_quote_and_missing_config_have_honest_coverage(self):
        doc=document([event('open',0),event('close',600)],
            [dict(time=START,market={A:quote(A,now=START)}),dict(time=START+300,market={})])
        row=self.metric(doc)['trades'][0]
        self.assertEqual(row['excursion_coverage'],'partial')
        doc.pop('config')
        row=self.metric(doc)['trades'][0]
        self.assertEqual(row['excursion_coverage'],'unavailable')
        self.assertIsNone(row['observed_mae_net_usdt'])




class MetricIntegrationTests(unittest.TestCase):
    def test_synthetic_audit_and_analytics_share_fields(self):
        import json
        import tempfile
        from pathlib import Path
        from paper_grid import analytics, audits, metric_sample
        doc=metric_sample.document();report=metric_sample.report()
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary).resolve()/'experiment.json';path.write_text(json.dumps(doc))
            learning=analytics.build(path.parent,doc['last_tick_at'])
        for arm in trade_metrics.ARMS:
            self.assertEqual(report['accounts'][arm]['performance'],
                learning['windows']['all']['accounts'][arm]['performance'])
        page,_=audits._render(report)
        self.assertIn('SYNTHETIC FIXTURE ONLY',page)
        self.assertIn('observed_mae_net_usdt',page)
        self.assertIn('baseline_filter_blocked',page)
        self.assertNotIn('<script src=',page)
        self.assertNotIn('style=',page)

    def test_sample_report_is_compact_and_reproducibly_writable(self):
        import json
        import tempfile
        from pathlib import Path
        from paper_grid import audits,metric_sample
        page,_=audits._render(metric_sample.report())
        self.assertLess(len(page.splitlines()),800)
        self.assertIn('Time in market · seconds',page)
        self.assertIn('Baseline blocked-entry cohort · net USDT',page)
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve()
            metric_sample.write_sample(root)
            self.assertEqual((root/'phase-1-sample.html').read_text(),page)
            self.assertEqual(json.loads((root/'phase-1-sample.json').read_text()),metric_sample.report())

    def test_public_metric_projection_drops_arbitrary_fields(self):
        import json
        from paper_grid import metric_sample,public_snapshot
        report=metric_sample.report();marker='PRIVATE_VALUE_NOT_TO_EXPORT'
        performance=report['accounts']['baseline']['performance']
        performance['private']=marker
        performance['trades'][0]['private']=marker
        performance['baseline_filter_blocked']['interpretation']=marker
        safe=public_snapshot._audit(report,report['id'],report['generated_at'])
        result=safe['accounts']['baseline']['performance']
        self.assertNotIn(marker,json.dumps(safe))
        self.assertIn('observed_mae_net_usdt',result['trades'][0])
        self.assertEqual(result['profit_factor'],performance['profit_factor'])

    def test_report_unknown_entry_preserves_known_close_pnl(self):
        from paper_grid import audits,metric_sample
        doc=metric_sample.document()
        doc['events']=[e for e in doc['events'] if e['type']=='close']
        result=audits._arm_metrics(doc,'baseline',START,doc['last_tick_at'])['performance']
        self.assertGreater(result['unknown_hold_trades'],0)
        self.assertIsNone(result['exposure']['fraction'])
        self.assertEqual(result['baseline_filter_blocked']['unknown_trades'],result['closed_trades'])


if __name__ == '__main__':
    unittest.main()
