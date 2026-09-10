"""Offline evidence and accounting regression tests for the learning dashboard."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from paper_grid import analytics, cli, retention

START = 1789081915.0
ARMS = analytics.ARMS
SYMBOL = 'TESTUSDTM'


class AnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.doc = dict(schema=1, mode='paper', start_at=START, last_tick_at=START,
            tick_seconds=300, accounts={a: dict(statistics={'initial_equity':1000}, events=[]) for a in ARMS},
            history=[], observations=[], events=[], errors=[])

    def save(self):
        cli.atomic_json(self.root/'experiment.json', self.doc)

    def build(self, at=START+1000):
        self.save()
        return analytics.build(self.root, at)

    def event(self, kind, at, arm='baseline', symbol=SYMBOL, **fields):
        result = dict(type=kind, time=at, account=arm, symbol=symbol)
        if kind in ('open','add'):
            result.update(notional=50, fee=.03)
        if kind == 'close':
            result.update(net_pnl=1, entry_fees=.03, exit_fee=.031, funding_model_cost=.01,
                          reason='net_profit_target')
        result.update(fields)
        self.doc['events'].append(result)
        return result

    def observation(self, at, scanned=None, skipped=None):
        value = dict(time=at, scan=dict(scanned_at=START-100 if scanned is None else scanned,
            top5=[SYMBOL], liquid_contracts=147), market={SYMBOL:dict(symbol=SYMBOL,eligible=True)},
            coinglass=dict(fetched_at=at, symbols={SYMBOL: dict(eligible=True,
                latest_hour=(int(at)//3600-1)*3600, burst_ratio=4, long_share=.7, total_usd=100)}))
        if skipped:
            value['skipped'] = skipped
        self.doc['observations'].append(value)
        return value

    def archive(self, records):
        directory = self.root/retention.DIRECTORY
        directory.mkdir(exist_ok=True)
        grouped = {}
        for kind, rows in records.items():
            for row in rows:
                day = retention._day(row['time'])
                grouped.setdefault(day, dict(schema=1, day=day, observations=[], events=[], errors=[]))[kind].append(row)
        for day, value in grouped.items():
            cli.atomic_json(directory/(day+'.json'), value)
        self.doc['archive_files'] = [day+'.json' for day in grouped]

    def test_zero_results_are_explicit_and_source_is_unchanged(self):
        self.save()
        before = (self.root/'experiment.json').read_bytes()
        report = analytics.build(self.root, START)
        window = report['windows']['all']
        self.assertEqual(window['checks']['successful'],0)
        account = window['accounts']['baseline']
        self.assertEqual(account['entries'],0)
        self.assertIsNone(account['win_rate'])
        self.assertIsNone(account['profit_factor'])
        self.assertEqual(account['profit_factor_state'],'no_closes')
        self.assertEqual(account['journal'],[])
        self.assertEqual((self.root/'experiment.json').read_bytes(),before)

    def test_entries_adds_close_outcomes_and_net_pnl_not_double_charged(self):
        self.event('open',START)
        self.event('add',START+10,notional=65,fee=.039)
        self.event('add',START+20,notional=85,fee=.051)
        self.event('close',START+30,net_pnl=2,entry_fees=.12,exit_fee=.13,funding_model_cost=.5)
        self.event('open',START+40)
        self.event('close',START+50,net_pnl=-1,reason='price_stop')
        self.event('open',START+60)
        self.event('close',START+70,net_pnl=0)
        account = self.build()['windows']['all']['accounts']['baseline']
        self.assertEqual((account['entries'],account['adds'],account['closes'],account['fills']),(3,2,3,8))
        self.assertEqual((account['wins'],account['losses'],account['breakeven']),(1,1,1))
        self.assertEqual(account['net_closed_pnl'],1)
        self.assertEqual(account['positive_profit'],2)
        self.assertEqual(account['negative_loss'],-1)
        self.assertEqual(account['profit_factor'],2)
        self.assertAlmostEqual(account['fees_paid'],.03+.039+.051+.13+.03+.031+.03+.031)
        self.assertAlmostEqual(account['modeled_funding_on_closes'],.52)
        self.assertAlmostEqual(account['win_rate'],1/3)
        two = next(c for c in account['add_cohorts'] if c['adds']==2)
        self.assertEqual(two,dict(adds=2,closes=1,wins=1,losses=0,net_pnl=2))
        journal = account['journal'][-1]
        self.assertEqual(journal['entry_notional'],200)
        self.assertEqual(journal['hold_seconds'],30)
        loss = next(r for r in account['close_reasons'] if r['reason']=='price_stop')
        self.assertEqual(loss['losses'],1)

    def test_lifecycle_reconstructed_before_window_and_window_fees_only(self):
        self.event('open',START)
        self.event('add',START+10,notional=65)
        self.event('close',START+2*analytics.DAY,entry_fees=.06,exit_fee=.09)
        window = self.build(START+2*analytics.DAY)['windows']['24h']
        account = window['accounts']['baseline']
        self.assertEqual(account['entries'],0)
        self.assertEqual(account['adds'],0)
        self.assertEqual(account['closes'],1)
        self.assertEqual(account['fees_paid'],.09)
        self.assertEqual(account['journal'][0]['adds'],1)
        self.assertEqual(account['journal'][0]['entry_notional'],115)
        self.assertEqual(account['journal'][0]['opened_at'],START)

    def test_inherited_history_known_and_unknown_lifecycles(self):
        inherited = dict(type='open',time=START-100,symbol=SYMBOL,notional=50,fee=.03)
        self.doc['accounts']['baseline']['events'] = [inherited]
        self.event('close',START+1)
        self.event('close',START+2,symbol='OTHERUSDTM',net_pnl=-2)
        account = self.build()['windows']['all']['accounts']['baseline']
        self.assertEqual(account['entries'],0)
        self.assertEqual(account['journal'][1]['opened_at'],START-100)
        self.assertIsNone(account['journal'][0]['adds'])
        self.assertIsNone(account['journal'][0]['entry_notional'])
        self.assertEqual(account['add_cohorts'][-1]['closes'],1)

    def test_cached_scan_is_not_new_discovery_and_filter_matches_engine(self):
        self.observation(START)
        second = self.observation(START+300)
        second['coinglass']['symbols'][SYMBOL]['eligible'] = False
        self.observation(START+600,skipped='cycle_error')
        window = self.build()['windows']['all']
        self.assertEqual(window['checks']['attempts'],3)
        self.assertEqual(window['checks']['successful'],2)
        self.assertEqual(window['checks']['skipped'],1)
        discovery = window['discovery']
        self.assertEqual(discovery['distinct_shortlist_coins'],1)
        self.assertEqual(discovery['distinct_scan_snapshots'],1)
        self.assertEqual(discovery['new_discovery_scans'],0)
        self.assertEqual(discovery['latest_liquid_contracts'],147)
        self.assertEqual(discovery['symbol_observations'],2)
        self.assertEqual(discovery['baseline_eligible_observations'],2)
        self.assertEqual(discovery['filtered_eligible_observations'],1)

    def test_new_scan_and_union_of_shortlist_symbols(self):
        self.observation(START,scanned=START)
        row=self.observation(START+300,scanned=START+300)
        row['scan']['top5']=['OTHERUSDTM']
        window=self.build()['windows']['all']
        self.assertEqual(window['discovery']['new_discovery_scans'],2)
        self.assertEqual(window['discovery']['distinct_shortlist_coins'],2)
        self.assertEqual(window['discovery']['symbol_observations'],3)

    def test_duplicate_archive_hot_and_account_records_count_once(self):
        event=self.event('open',START)
        obs=self.observation(START)
        self.doc['accounts']['baseline']['events']=[{k:v for k,v in event.items() if k!='account'}]
        self.archive(dict(events=[event],observations=[obs]))
        window=self.build()['windows']['all']
        self.assertEqual(window['accounts']['baseline']['entries'],1)
        self.assertEqual(window['checks']['successful'],1)

    def test_conflicting_archived_observation_fails(self):
        obs=self.observation(START)
        changed=deepcopy(obs)
        changed['scan']['liquid_contracts']=200
        self.archive(dict(observations=[changed]))
        with self.assertRaises(ValueError):
            self.build()

    def test_conflicting_event_fails(self):
        event=self.event('close',START)
        self.archive(dict(events=[dict(event,net_pnl=999)]))
        with self.assertRaises(ValueError):
            self.build()

    def test_missing_declared_archive_fails_instead_of_partial_totals(self):
        self.doc['archive_files']=['2026-09-10.json']
        with self.assertRaises(ValueError):
            self.build()

    def test_symlinks_and_file_size_bounds(self):
        self.save()
        actual=self.root/'real.json'
        (self.root/'experiment.json').rename(actual)
        (self.root/'experiment.json').symlink_to(actual)
        with self.assertRaises(ValueError):
            analytics.build(self.root,START+1)
        (self.root/'experiment.json').unlink()
        actual.rename(self.root/'experiment.json')
        with patch.object(analytics,'MAX_BYTES',1), self.assertRaises(ValueError):
            analytics.build(self.root,START+1)
        with self.assertRaises(ValueError):
            analytics.build(self.root,START+401*analytics.DAY)

    def test_archive_wrong_day_and_nonfinite_fail(self):
        event=self.event('close',START)
        self.archive(dict(events=[event]))
        archive=self.root/retention.DIRECTORY/self.doc['archive_files'][0]
        body=json.loads(archive.read_text())
        body['events'][0]['time']+=analytics.DAY
        archive.write_text(json.dumps(body))
        with self.assertRaises(ValueError):
            self.build()
        archive.unlink()
        self.doc['archive_files']=[]
        self.doc['events'][0]['net_pnl']=float('nan')
        (self.root/'experiment.json').write_text(json.dumps(self.doc))
        with self.assertRaises(ValueError):
            analytics.build(self.root,START+1)

    def test_secret_text_does_not_enter_dto(self):
        secret='DO_NOT_PUBLISH_private-key-123'
        row=self.observation(START)
        row['scan']['errors']=[secret]
        row['market'][SYMBOL]['reasons']=[secret]
        row['coinglass']['secret']=secret
        self.doc['errors']=[dict(time=START,type=secret,detail=secret)]
        self.event('close',START,reason=secret,detail=secret)
        report=self.build()
        self.assertNotIn(secret,json.dumps(report))
        self.assertEqual(report['windows']['all']['accounts']['baseline']['journal'][0]['reason'],'unknown')
        self.doc['events'][0]['symbol']=secret
        with self.assertRaises(ValueError):
            self.build()

    def test_journal_limit_does_not_limit_aggregates(self):
        for index in range(220):
            self.event('open',START+index*2)
            self.event('close',START+index*2+1)
        account=self.build()['windows']['all']['accounts']['baseline']
        self.assertEqual(len(account['journal']),200)
        self.assertEqual(account['journal_total'],220)
        self.assertEqual(account['closes'],220)
        self.assertEqual(account['net_closed_pnl'],220)
        self.assertEqual(account['journal'][0]['closed_at'],START+439)

    def test_marked_pnl_drawdown_separate_from_closed_pnl(self):
        self.doc['history']=[dict(time=START+10,baseline_equity=1010,liquidation_filter_equity=1000),
            dict(time=START+20,baseline_equity=995,liquidation_filter_equity=1000)]
        self.event('close',START+20,net_pnl=2)
        account=self.build()['windows']['all']['accounts']['baseline']
        self.assertEqual(account['marked_equity_change'],-5)
        self.assertEqual(account['net_closed_pnl'],2)
        self.assertEqual(account['observed_max_drawdown'],15)
        self.assertAlmostEqual(account['observed_max_drawdown_pct'],1500/1010)
        self.assertEqual(account['end_mark_age_seconds'],980)

    def test_daily_and_per_coin_totals_match_account(self):
        self.observation(START)
        self.event('open',START)
        self.event('add',START+1)
        self.event('close',START+2,net_pnl=-3,reason='rotation')
        window=self.build()['windows']['all']
        daily=window['daily'][0]['baseline']
        coin=window['coins'][0]['baseline']
        for name in ('entries','adds','closes','wins','losses','net_closed_pnl'):
            self.assertEqual(daily[name],window['accounts']['baseline'][name])
            self.assertEqual(coin[name],window['accounts']['baseline'][name])
        self.assertEqual(window['daily'][0]['checks'],1)

    def test_missing_costs_interval_and_nonpaper_account_fail(self):
        event=self.event('close',START)
        del event['funding_model_cost']
        with self.assertRaises(ValueError):
            self.build()
        event['funding_model_cost']=0
        self.doc['tick_seconds']=0
        with self.assertRaises(ValueError):
            self.build()
        self.doc['tick_seconds']=300
        self.doc['accounts']['baseline']['state']={'mode':'live'}
        with self.assertRaises(ValueError):
            self.build()

    def test_accounts_remain_independent(self):
        self.event('open',START)
        self.event('close',START+1,net_pnl=2)
        self.event('open',START,arm='liquidation_filter')
        self.event('close',START+1,arm='liquidation_filter',net_pnl=-3)
        accounts=self.build()['windows']['all']['accounts']
        self.assertEqual(accounts['baseline']['net_closed_pnl'],2)
        self.assertEqual(accounts['liquidation_filter']['net_closed_pnl'],-3)


if __name__ == '__main__':
    unittest.main()
