"""Storage compaction preserves decision evidence without changing execution inputs."""
import copy
import gzip
import json
import unittest

from trader.research.replay_evidence import compact_scan, compact_form


def raw_read():
    return dict(timeframe='4h', direction='up', confidence=.9,
                structure={'highs':[{'price':100+i,'estimated':False} for i in range(4)]},
                support_resistance={'support':98,'resistance':105},
                explanation='closed causal indicator evidence '*100)


def regime():
    value = raw_read()
    cell = dict(symbol='SOLUSDTM', known=True, direction='up', raw_read=value)
    return dict(allowed_directions=['long','neutral'], enabled=True,
                membership={'status':'member','sources':['fixture://maintained']},
                macro_gate={'assets':[{'reads':[copy.deepcopy(cell)]} for _ in range(2)]},
                sol_gate={'applied':True,'reads':[copy.deepcopy(cell)]},
                reads=[copy.deepcopy(cell) for _ in range(3)], unknown=False, warnings=[])


def candidate(count):
    return dict(grids=count,low=98.,high=102.,step=4/count,quantity=100/count,
        net_usdt_per_grid=.04,kucoin_profit_pct_min=.02,kucoin_profit_pct_max=.03,
        expected_gph=10,grid_income_per_hour=.4,selection_income_per_hour=.4,
        funded_economics_eligible=True,provisional=False,quantity_calibrated=False,
        windows={name:dict(median_hold_ms={'long':1000},completed_grids_per_hour=10,
            funding_adjusted_income_per_hour=.4,expected_funding_cost_usdt=.01,
            completed_by_side={'long':240},denominator_hours=hours) for name,hours in [('24h',24),('7d',168)]},
        economics={name:{'long':dict(fee_net_usdt=.05,admission_net_usdt=.04,
            median_hold_ms=1000,expected_funding_cost_usdt=.01,funding_status='known_rate_estimate')}
            for name in ('24h','7d')},
        config={'quantity':100/count,'grids':count,'range_exit_stop_pct':0},
        preview={'profit_per_grid_min':.05,'kucoin_profit_pct_min':.02},
        chart={'five_cells':{tf:raw_read() for tf in ('1d','4h','1h','15m','5m')}},
        range_evidence={'low':98,'high':102},entry_signal={'estimated':False,'trigger':100.5})


def form():
    best = candidate(80)
    offers = [candidate(n) for n in (80,79,78)]
    return dict(best,candidates=offers,search=dict(candidates=copy.deepcopy(offers),evaluated_count=199,
        count_limits={'min':2,'max':200},coverage={'actual':10080,'expected':10080},
        rejected_counts=[dict(grids=n,reason='fee floor' if n%2 else 'risk',economics=best['economics']) for n in range(2,201)],
        trials=[candidate(n) for n in range(2,201)]),
        entry_eligible=True,can_arm=True,market_entry_ready=False,waiting_for_trigger=True,
        fees_estimated=.12,range_exit_stop_pct=0,stop_loss=98,stop_loss_high=None,
        trigger=100.5,entry=100.5,regime=regime())


def scan():
    chosen = form()
    row = dict(pair='RAYUSDTM',setup=chosen,five_cells=chosen['chart']['five_cells'],
               top_grid_counts=chosen['candidates'],regime=regime(),score=10.,expected_gph=10.,
               grid_income_per_hour=.4,entry_eligible=True,can_arm=True,
               funding_diagnostics={'usable_for_projection':True},candle_coverage={'fraction':1})
    return dict(strategy='income_chart_v3',asof_ms=1000,radar=[row],
        rejected=[dict(copy.deepcopy(row),pair='JUPUSDTM',reason='entry unavailable',coverage_issue=False)],
        coverage={'observed':2,'eligible':1,'rejected':1},volatility_universe=[{'pair':'RAYUSDTM','volatility_proxy_pct':4}],
        custom_stat={'scans':1})


def expand(value, pool):
    if isinstance(value, dict):
        if set(value) == {'read_ref'}:
            return copy.deepcopy(pool[value['read_ref']])
        return {key:expand(item,pool) for key,item in value.items()}
    if isinstance(value,list):
        return [expand(item,pool) for item in value]
    return value


class ReplayEvidenceTests(unittest.TestCase):
    def test_form_keeps_chosen_configuration_entry_economics_and_chart_once(self):
        original = form()
        before = copy.deepcopy(original)
        compact = compact_form(original)
        self.assertEqual(original,before)
        for key in ('config','preview','range_evidence','entry_signal','entry','trigger','stop_loss',
                    'quantity','grids','low','high','step','windows','economics','entry_eligible',
                    'can_arm','market_entry_ready','waiting_for_trigger','grid_income_per_hour','expected_gph'):
            self.assertEqual(compact[key],original[key],key)
        self.assertEqual(compact['chart'],original['chart'])
        self.assertNotIn('candidates',compact)
        self.assertNotIn('trials',compact['search'])
        self.assertNotIn('candidates',compact['search'])
        self.assertEqual(compact['search']['evaluated_count'],199)
        self.assertEqual(compact['search']['count_limits'],{'min':2,'max':200})
        self.assertEqual(compact['search']['rejected_reason_counts'],{'risk':100,'fee floor':99})
        self.assertEqual(len(compact['search']['rejected_examples']),3)
        self.assertEqual(compact['search']['rejected_count'],199)

    def test_top_three_preserves_count_tradeoff_values_and_hold_funding_windows(self):
        original = form()
        compact = compact_form(original)
        self.assertEqual(len(compact['top_grid_counts']),3)
        for old,new in zip(original['candidates'],compact['top_grid_counts']):
            for key in ('grids','low','high','step','quantity','net_usdt_per_grid','kucoin_profit_pct_min',
                        'kucoin_profit_pct_max','expected_gph','grid_income_per_hour','windows','economics'):
                self.assertEqual(new[key],old[key],key)
            self.assertNotIn('chart',new)
            self.assertNotIn('config',new)

    def test_scan_preserves_every_row_stats_rejection_and_one_copy_of_cells(self):
        original = scan()
        before = copy.deepcopy(original)
        compact = compact_scan(original)
        self.assertEqual(original,before)
        for key in ('coverage','volatility_universe','asof_ms','custom_stat'):
            self.assertEqual(compact[key],original[key])
        for group in ('radar','rejected'):
            self.assertEqual(len(compact[group]),len(original[group]))
            for old,new in zip(original[group],compact[group]):
                if group == 'radar':
                    self.assertEqual(new['five_cells'],old['five_cells'])
                    self.assertNotIn('five_cells',new['setup']['chart'])
                    self.assertNotIn('top_grid_counts',new['setup'])
                    self.assertEqual(new['setup']['config'],old['setup']['config'])
                else:
                    self.assertNotIn('five_cells',new)
                    self.assertNotIn('top_grid_counts',new)
                    self.assertNotIn('setup',new)
                    self.assertEqual(new['setup_summary']['config'],old['setup']['config'])
                for key in ('entry_eligible','can_arm','expected_gph','grid_income_per_hour','candle_coverage'):
                    self.assertEqual(new[key],old[key])
        self.assertEqual(compact['rejected'][0]['reason'],'entry unavailable')
        self.assertFalse(compact['rejected'][0]['coverage_issue'])

    def test_shared_regime_refs_expand_to_exact_original_full_evidence(self):
        original = scan()
        compact = compact_scan(original)
        pool = compact['regime_read_pool']
        self.assertEqual(len(pool),1)
        self.assertTrue(all(len(key)==64 for key in pool))
        for group in ('radar','rejected'):
            old,new = original[group][0],compact[group][0]
            self.assertEqual(expand(new['regime'],pool),old['regime'])
            form_key = 'setup' if group == 'radar' else 'setup_summary'
            self.assertEqual(expand(new[form_key]['regime'],pool),old['setup']['regime'])
        self.assertEqual(compact_scan(compact),compact)

    def test_200_count_duplicate_fixture_has_bounded_storage_and_detached_output(self):
        original = scan()
        compact = compact_scan(original)
        self.assertLess(len(json.dumps(compact)),len(json.dumps(original))*.08)
        encoded = lambda value: json.dumps(value,sort_keys=True).encode()
        self.assertLess(len(gzip.compress(encoded(compact),mtime=0)),
                        len(gzip.compress(encoded(original),mtime=0))*.1)
        compact['radar'][0]['setup']['config']['quantity'] = 999
        self.assertNotEqual(original['radar'][0]['setup']['config']['quantity'],999)

    def test_unknown_empty_and_legacy_values_remain_explicit(self):
        self.assertIsNone(compact_form(None))
        self.assertEqual(compact_form({}),{})
        self.assertEqual(compact_scan(None),None)
        self.assertEqual(compact_scan({}),{})
        legacy = dict(strategy='legacy',radar=[{'setup':{'candidates':[{'quantity':1}]}}])
        self.assertEqual(compact_scan(legacy),legacy)
        result = compact_scan(dict(strategy='income_chart_v3',radar=[{'setup':None,'regime':{'reads':[{'raw_read':None}]}}],rejected=[]))
        self.assertIsNone(result['radar'][0]['setup'])
        self.assertIsNone(result['radar'][0]['regime']['reads'][0]['raw_read'])
        self.assertEqual(result['regime_read_pool'],{})

    def test_distinct_raw_reads_keep_distinct_references_without_losing_gate_fields(self):
        original = scan()
        original['rejected'][0]['regime']['reads'][0]['raw_read']['confidence'] = .5
        result = compact_scan(original)
        self.assertEqual(len(result['regime_read_pool']),2)
        self.assertEqual(expand(result['rejected'][0]['regime'],result['regime_read_pool']),
                         original['rejected'][0]['regime'])

    def test_real_synthetic_setup_shape_keeps_every_chosen_nonnested_field(self):
        from trader.tests.test_recommender_setup import build
        original = build()
        compact = compact_form(original)
        for key,value in original.items():
            if key not in ('candidates','search'):
                self.assertEqual(compact[key],value,key)
        self.assertEqual(compact_form(compact),compact)
        for old,new in zip(original['candidates'],compact['top_grid_counts']):
            for key in ('net_usdt_per_grid','funding_adjusted_net_usdt_per_grid',
                        'estimated_fee_only_net_usdt_per_grid','kucoin_profit_pct_min',
                        'kucoin_profit_pct_max','expected_gph','grid_income_per_hour','windows','economics'):
                self.assertEqual(new[key],old[key],key)

    def test_unknown_search_counts_are_not_invented_as_zero(self):
        original = dict(eligible=False,reason='unknown',search={'rejected_counts':None},candidates=None)
        result = compact_form(original)
        self.assertIsNone(result['search']['rejected_count'])
        self.assertIsNone(result['search']['rejected_reason_counts'])
        self.assertIsNone(result['top_grid_counts'])
        self.assertEqual(result['reason'],'unknown')

    def test_rejected_storage_preserves_causes_negative_economics_and_brief_chart_reads(self):
        original = scan()
        row = original['rejected'][0]
        row['chart_reasons'] = {'bias':'up','entry':'not ready','setup':'funding fails'}
        row['timeframe_coverage'] = {'1d':{'indicator_observed_fraction':.99}}
        row['setup']['net_usdt_per_grid'] = -.02
        row['setup']['preview']['profit_per_grid_min'] = -.03
        row['setup']['entry_signal'] = {'eligible':False,'reason':'fresh high','estimated':True}
        compact = compact_scan(original)['rejected'][0]
        for key in ('pair','reason','coverage_issue','candle_coverage','chart_reasons','timeframe_coverage'):
            self.assertEqual(compact[key],row[key])
        summary = compact['setup_summary']
        for key in ('entry_signal','config','windows','economics'):
            self.assertEqual(summary[key],row['setup'][key])
        self.assertEqual(summary['net_usdt_per_grid'],-.02)
        self.assertEqual(summary['preview']['profit_per_grid_min'],-.03)
        self.assertEqual(summary['search']['rejected_reason_counts'],{'risk':100,'fee floor':99})
        self.assertEqual(len(summary['search']['rejected_examples']),3)
        for tf, value in row['five_cells'].items():
            self.assertEqual(compact['timeframe_reads'][tf]['direction'],value['direction'])
            self.assertEqual(compact['timeframe_reads'][tf]['confidence'],value['confidence'])
            self.assertNotIn('structure',compact['timeframe_reads'][tf])
        self.assertTrue({'five_cells','setup','top_grid_counts'} <= set(compact['omitted_reproducible_fields']))

    def test_twenty_six_rejections_are_much_smaller_than_rich_storage_with_radar_unchanged(self):
        original = scan()
        row = original['rejected'][0]
        original['rejected'] = [dict(copy.deepcopy(row),pair='REJECT'+str(i)) for i in range(26)]
        before = copy.deepcopy(original)
        result = compact_scan(original)
        rich = compact_scan(dict(strategy='income_chart_v3',radar=original['rejected']))
        encoded = lambda value: json.dumps(value,sort_keys=True).encode()
        self.assertLess(len(encoded(result['rejected'])),len(encoded(rich['radar']))*.45)
        self.assertEqual(result['radar'][0]['five_cells'],original['radar'][0]['five_cells'])
        self.assertEqual(len(result['radar'][0]['top_grid_counts']),3)
        self.assertEqual(original,before)
        self.assertEqual(compact_scan(result),result)

    def test_rejected_missing_setup_and_unknown_cells_remain_explicit(self):
        original = dict(strategy='income_chart_v3',radar=[],rejected=[
            {'pair':'UNKNOWN','reason':'no history','coverage_issue':True,'setup':None,'five_cells':None}])
        row = compact_scan(original)['rejected'][0]
        self.assertIsNone(row['setup_summary'])
        self.assertIsNone(row['timeframe_reads'])
        self.assertTrue(row['coverage_issue'])
        self.assertEqual(row['reason'],'no history')
