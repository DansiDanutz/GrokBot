from copy import deepcopy
import unittest
from unittest.mock import patch
from paper_grid import analytics, audits, engine, coinglass, public_snapshot, telemetry_metrics
from paper_grid import test_analytics as fixtures
START = fixtures.START
from paper_grid.test_public_snapshot import fixture


class TelemetryReportingTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.AnalyticsTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.doc=self.fixture.doc
        self.observation=self.fixture.observation
        self.build=self.fixture.build

    def test_rejections_have_coverage_and_never_increase_trade_costs(self):
        self.observation(START)
        obs=self.observation(START+300);obs['telemetry_schema']=1
        self.doc['events'].append(dict(type='buy_rejected',account='baseline',symbol='TESTUSDTM',
            time=START+300,action='open',stage='execution',reason='insufficient_ask_depth',context={'fee':999}))
        account=self.build()['windows']['all']['accounts']['baseline']
        self.assertEqual(account['buy_rejections']['recorded'],1)
        self.assertEqual(account['buy_rejections']['coverage'],'partial')
        self.assertEqual(account['buy_rejections']['legacy_checks'],1)
        self.assertEqual((account['fills'],account['fees_paid'],account['net_closed_pnl']),(0,0,0))

    def test_per_tick_equity_catches_drawdown_between_publications(self):
        self.doc['history']=[dict(time=START,baseline_equity=1000,liquidation_filter_equity=1000),
            dict(time=START+1800,baseline_equity=1000,liquidation_filter_equity=1000)]
        for at,equity in [(START+300,990),(START+600,1000)]:
            obs=self.observation(at);obs.update(telemetry_schema=1,equity={'baseline':{'equity':equity,'equity_is_estimate':False}})
        a=self.build(START+1800)['windows']['all']['accounts']['baseline']
        self.assertEqual(a['observed_max_drawdown'],10)
        self.assertEqual(a['equity_sampling']['resolution'],'mixed')
        self.assertEqual(a['equity_sampling']['tick_samples'],2)

    def test_uninstrumented_zero_is_not_claimed_complete(self):
        self.observation(START)
        value=self.build()['windows']['all']['accounts']['baseline']['buy_rejections']
        self.assertEqual(value['coverage'],'unavailable')
        self.assertEqual(value['instrumented_checks'],0)
        self.assertEqual(value['legacy_checks'],1)

    def test_audit_filter_uses_engine_function(self):
        self.observation(START+1)
        with patch.object(coinglass,'apply_filter',wraps=coinglass.apply_filter) as apply:
            data=audits._data_metrics(self.doc,START,START+2)
        apply.assert_called_once()
        self.assertEqual(data['coins']['TESTUSDTM']['filtered_signal_eligible'],1)


class PublicRejectionTests(unittest.TestCase):
    def test_roadmap_reason_and_lot_context_are_public_but_extra_fields_are_not(self):
        source = fixture()
        at = source['experiment']['report_at']
        source['events'] = [dict(type='buy_rejected', account='baseline', symbol='TESTUSDTM',
            time=at, reason='lots_lt_1', action='open', stage='execution',
            context={'lots': 0, 'budget': 50, 'unit_cost': 100, 'private': 'omit'})]
        row = public_snapshot._report(source, at)['events'][0]
        self.assertEqual(row['reason'], 'lots_lt_1')
        self.assertEqual(row['context']['lots'], 0)
        self.assertNotIn('private', row['context'])

    def test_rejection_context_is_explicitly_allowlisted(self):
        source=fixture();at=source['experiment']['report_at']
        source['events']=[dict(type='buy_rejected',account='baseline',symbol='TESTUSDTM',time=at,
            reason='insufficient_ask_depth',action='open',stage='execution',
            context={'required_contracts':5,'ask_size':2,'private':'do-not-publish'})]
        result=public_snapshot._report(source,at)['events'][0]
        self.assertEqual(result['context']['required_contracts'],5)
        self.assertNotIn('private',result['context'])
        self.assertEqual(result['reason'],'insufficient_ask_depth')


if __name__=='__main__':unittest.main()
