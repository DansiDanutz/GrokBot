"""Observed depth is a per-count feasibility constraint, before income ranking."""
import copy
import unittest
from unittest.mock import patch

from trader.research.kucoin_radar import radar
from trader.tests.test_chart_radar import record, STRATEGY, ASOF
from trader.tests.test_recommender_setup import chart, safe_preview


class RecommenderDepthTests(unittest.TestCase):
    def scan(self, depth, maximum=8, ask=None, candle_only=False):
        source = record()
        source['market'].update(bid_depth_usdt=depth, ask_depth_usdt=depth if ask is None else ask)
        if candle_only:
            source['market'].update(filter_mode='candle-only filters',turnover_basis='observed quote turnover')
        before = copy.deepcopy(source)
        with patch('trader.strategies.recommender_setup.read_chart', return_value=chart()):
            result = radar([source], ASOF, parameters={'strategy':STRATEGY,
                'setup':dict(min_grids=4,max_grids=maximum,preview_fn=safe_preview)})
        self.assertEqual(source,before)
        return result

    def test_depth_700_selects_eight_instead_of_rejecting_five_count_winner(self):
        result = self.scan(700)
        self.assertEqual(len(result['radar']),1)
        chosen = result['radar'][0]['setup']
        self.assertEqual(chosen['grids'],8)
        self.assertAlmostEqual(chosen['quantity']*chosen['entry'],623.069)
        self.assertAlmostEqual(chosen['grid_income_per_hour'],140.44166421737938)
        self.assertTrue(all(row['quantity']*row['entry'] <= 700 for row in chosen['candidates']))
        rejected = {row['grids']:row['reason'] for row in chosen['search']['rejected_counts']}
        self.assertIn('depth',rejected[5])
        self.assertIn('depth',rejected[7])

    def test_feasible_count_outside_unconstrained_top_three_remains_discoverable(self):
        rich = self.scan(10000,maximum=20)['radar'][0]['setup']
        previous_top_three = {row['grids'] for row in rich['candidates']}
        self.assertEqual(previous_top_three,{5,7,8})
        result = self.scan(600,maximum=20)
        self.assertEqual(len(result['radar']),1)
        chosen = result['radar'][0]['setup']
        self.assertNotIn(chosen['grids'],previous_top_three)
        self.assertTrue(all(row['quantity']*row['entry'] <= 600 for row in chosen['candidates']))
        self.assertEqual(chosen['search']['evaluated_count'],17)

    def test_candle_only_keeps_explicit_depth_exemption(self):
        result = self.scan(1,candle_only=True)
        self.assertEqual(len(result['radar']),1)
        row = result['radar'][0]
        self.assertIn('depth',row['skipped_filters'])
        self.assertGreater(row['setup']['quantity']*row['setup']['entry'],1)
        self.assertFalse(any('depth' in item['reason'] for item in row['setup']['search']['rejected_counts']))

    def test_both_sides_and_known_finite_depth_are_required_for_quoted_counts(self):
        for depth,ask in ((700,100),(None,None),(True,True),(float('nan'),float('nan'))):
            with self.subTest(depth=depth,ask=ask):
                result = self.scan(depth,ask=ask)
                self.assertEqual(result['radar'],[])
                rejected = result['rejected'][0]['setup']['search']['rejected_counts']
                self.assertTrue(rejected)
                self.assertTrue(all('depth' in item['reason'] for item in rejected))
