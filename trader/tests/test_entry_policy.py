"""New-entry rules use closed evidence; never migrate the active book."""
from copy import deepcopy
import unittest
from trader.radar.entry import VERSION, funding_window, retest, admission
from trader.radar.layout import select_range
from trader.tests.test_autopilot_policy import row, radar, HOUR
from trader.autopilot import policy

NOW = 100 * HOUR

def candidate():
    r = row('TEST', funding_pct=.01, funding_interval_ms=8*HOUR,
            funding_asof_ms=NOW, atr_1h_pct=2)
    return r

def candles():
    rows = [(NOW-(25-i)*HOUR, 101, 104, 99, 101, 1) for i in range(25)]
    rows[-1] = (NOW-HOUR, 99.8, 100.4, 99.3, 100, 1)
    return rows

def basis():
    return dict(supports=[dict(price=99.4, touches=2,
                pivot_times_ms=[NOW-12*HOUR,NOW-7*HOUR],
                confirmed_at_ms=[NOW-9*HOUR,NOW-4*HOUR])], resistances=[])

class EntryTests(unittest.TestCase):
    def test_four_hour_funding_budget_and_metadata_fail_closed(self):
        r=candidate()
        self.assertEqual(funding_window(r,NOW)['settlements'],1)
        self.assertEqual(funding_window(dict(r,funding_interval_ms=HOUR),NOW)['settlements'],4)
        for extra in ({'funding_pct':None},{'funding_interval_ms':None},
                      {'funding_asof_ms':NOW-3*HOUR},{'funding_asof_ms':NOW+1}):
            self.assertEqual(funding_window(dict(r,**extra),NOW)['status'],'REJECTED')

    def test_confirmed_long_retest_and_no_chasing(self):
        proof=retest(candles(),basis(),candidate(),'LONG',NOW)
        self.assertEqual(proof['status'],'CONFIRMED')
        self.assertEqual(retest(candles(),basis(),dict(candidate(),price=102),'LONG',NOW)['status'],'REJECTED')
        self.assertEqual(retest(candles(),basis(),candidate(),'LONG',NOW+3*HOUR)['status'],'REJECTED')

    def test_open_candle_gaps_and_unconfirmed_level_cannot_qualify(self):
        for rows in (candles()[:-1], candles()[:-2]+candles()[-1:],
                     candles()[:-1]+[(NOW,99.8,100.4,99.3,100,1)]):
            self.assertEqual(retest(rows,basis(),candidate(),'LONG',NOW)['status'],'REJECTED')
        b=basis();b['supports'][0]['confirmed_at_ms']=[NOW,NOW]
        self.assertEqual(retest(candles(),b,candidate(),'LONG',NOW)['status'],'REJECTED')

    def test_short_mirror_and_neutral_rejection(self):
        rows=[(t,200-o,200-l,200-h,200-c,v) for t,o,h,l,c,v in candles()]
        b={'supports':[], 'resistances':[dict(basis()['supports'][0],price=100.6)]}
        self.assertEqual(retest(rows,b,candidate(),'SHORT',NOW)['status'],'CONFIRMED')
        self.assertEqual(retest(rows,b,candidate(),'NEUTRAL',NOW)['status'],'CONFIRMED')

    def test_funded_selection_uses_strict_split_and_seventy_floor(self):
        r=candidate()
        selected,reason=select_range([(80,2)],[(130,2)],r,'LONG',entry_policy=VERSION,asof_ms=NOW)
        self.assertEqual(reason,'')
        self.assertGreaterEqual(selected['grids'],70)
        self.assertTrue(selected['range_evidence']['funding_stress']['passes_floor'])
        self.assertEqual(selected['range_evidence']['split_mode'],'strict')
        self.assertIsNone(select_range([(90,2)],[(115,2)],r,'LONG',entry_policy=VERSION,asof_ms=NOW)[0])
        self.assertEqual(select_range([(80,2)],[(130,2)],dict(r,funding_pct=None),'LONG',entry_policy=VERSION,asof_ms=NOW)[1],'UNKNOWN_FUNDING_METADATA')

    def test_production_admission_rejects_old_radar_without_rewriting_open_bot(self):
        s,_=policy.decide(policy.new_state(0),radar(long=[row('OLD')]),{},0,'a')
        self.assertEqual(len(s['open_bots']),1)
        old=deepcopy(s['open_bots'][0])
        updated,_=policy.decide(s,radar(long=[row('OLD'),row('NEW')]),{},10000,'b',entry_policy=VERSION)
        self.assertEqual(updated['open_bots'],[old])
        self.assertEqual(updated['started_ms'],s['started_ms'])
        self.assertEqual(admission(candidate(),'LONG',NOW,required_version=VERSION),'ENTRY_POLICY_MISMATCH')

    def test_receipt_rechecked_against_live_price_funding_and_expiry(self):
        r=candidate();r.update(entry_policy_version=VERSION,entry_retests={
            'LONG':retest(candles(),basis(),r,'LONG',NOW)})
        self.assertEqual(admission(r,'LONG',NOW,required_version=VERSION),'')
        for extra in ({'price':102},{'funding_pct':5},{'grids':20}):
            self.assertNotEqual(admission(dict(r,**extra),'LONG',NOW,required_version=VERSION),'')
        self.assertNotEqual(admission(r,'LONG',NOW+3*HOUR,required_version=VERSION),'')

    def test_open_freezes_policy_inside_verified_dossier(self):
        from trader.autopilot.setup_evidence import evidence_id
        r=candidate()
        proof=r['range_evidence']
        for k in ('window_start_ms','window_end_ms','analysis_asof_ms','candle_asof_ms'):
            proof[k]+=NOW
        for k in ('selected_support','selected_resistance'):
            for field in ('pivot_times_ms','confirmed_at_ms'):
                proof[k][field]=[t+NOW for t in proof[k][field]]
        r.update(entry_policy_version=VERSION,entry_retests={
            'LONG':retest(candles(),basis(),r,'LONG',NOW)})
        state,events=policy.decide(policy.new_state(NOW),radar(long=[r]),{'TEST':100},NOW,'new',entry_policy=VERSION)
        self.assertEqual(len(state['open_bots']),1)
        dossier=state['open_bots'][0]['setup_evidence']
        self.assertEqual(dossier['entry_policy']['version'],VERSION)
        self.assertEqual(dossier['entry_policy']['funding']['horizon_hours'],4)
        self.assertEqual(dossier['evidence_id'],evidence_id(dossier))
        frozen=deepcopy(dossier)
        r['funding_pct']=2
        self.assertEqual(state['open_bots'][0]['setup_evidence'],frozen)

    def test_radar_cli_enables_version_and_blocks_missing_metadata(self):
        import json,tempfile
        from pathlib import Path
        from trader.tests.test_radar import fixture, NOW as RADAR_NOW
        from trader.radar.cli import main
        with tempfile.TemporaryDirectory() as root:
            database=Path(root)/'market.sqlite3';fixture(database)
            out=Path(root)/'radar.json'
            main(['--database',str(database),'--json',str(out),'--asof-ms',str(RADAR_NOW),
                  '--no-liquidation-clusters'],printer=lambda *_:None)
            report=json.loads(out.read_text())
            self.assertEqual(report['entry_policy_version'],VERSION)
            self.assertTrue(all(r['entry_policy_version']==VERSION for r in report['rows']))
            self.assertFalse(any(r['entry_viable'] for r in report['rows']))

    def test_daemon_cli_enables_policy_without_replacing_state(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from trader.autopilot.cli import main
        with tempfile.TemporaryDirectory() as root:
            paths=[str(Path(root).resolve()/name) for name in ('db','state','radar','snapshot')]
            Path(paths[0]).touch()
            args=['once']
            for flag,path in zip(('database','state','radar','snapshot'),paths):
                args+=['--'+flag,path]
            with patch('trader.autopilot.cli.Runner') as runner:
                self.assertEqual(main(args),0)
                self.assertEqual(runner.call_args.kwargs['entry_policy'],VERSION)
