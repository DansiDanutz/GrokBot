import json
from pathlib import Path
import unittest

from trader.research import kucoin_portfolio as portfolio


class IncomeChartRegistrationTests(unittest.TestCase):
    def test_registered_four_variants_and_identical_random_controls_are_all_reported(self):
        document=json.loads((Path(__file__).resolve().parents[2]/'research/preregistration/grid-kucoin-v3-income-chart.json').read_text())
        calls=[]
        def runner(snapshot,start,end,parameters=None,mode='system',**kwargs):
            calls.append((start,end,dict(parameters),mode))
            return dict(coverage={'complete':False},metrics={'net':None},status='partial_coverage')
        result=portfolio._chart_registered(object(),document,{}, {},runner)
        self.assertEqual(len(result['variants']),4)
        self.assertEqual(len(calls),16)
        combinations={(row['parameters']['regime_gate'],row['parameters']['bias_mode']) for row in result['variants']}
        self.assertEqual(combinations,{(gate,bias) for gate in (False,True) for bias in ('4h-only','1d+4h')})
        for row in result['variants']:
            self.assertEqual(len(row['holdouts']),2)
            self.assertEqual(row['decision']['decision'],'shelve')
            for window in row['holdouts']:
                self.assertIn('random_radar_identical_rules',window['baselines'])
        self.assertFalse(result['holdout_selected_variant'])
        self.assertEqual(result['primary_metric'],'grid_income_per_hour')

    def test_new_registration_has_zero_cash_floor_and_exact_edge_stops(self):
        self.assertEqual(portfolio._minimum_grid_net({'id':'grid-kucoin-v3-income-chart'}),0)
        self.assertEqual(portfolio._registered_range_exit({'id':'grid-kucoin-v3-income-chart'}),0)
