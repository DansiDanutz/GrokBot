import unittest

from trader.research.grid_replay import deployment_decision, choose_parameters


class ReplayPolicyTests(unittest.TestCase):
    def test_two_complete_positive_disjoint_months_and_drawdown_required(self):
        a = dict(start_ms=0, end_ms=31*86400000, net=1,
                 max_drawdown=.09, coverage_complete=True, calendar_month=True)
        b = dict(a, start_ms=31*86400000, end_ms=59*86400000)
        self.assertEqual(deployment_decision([a, b], parity_verified=True)['decision'],
                         'eligible_for_separate_paper_deployment_review')
        self.assertEqual(deployment_decision([a, b])['decision'], 'shelve')
        for changed in (dict(b, net=0), dict(b, max_drawdown=.1),
                        dict(b, coverage_complete=False), dict(b, start_ms=0)):
            self.assertEqual(deployment_decision([a, changed], parity_verified=True)['decision'], 'shelve')

    def test_training_selection_uses_grid_rate_then_net_deterministically(self):
        trials = [dict(lookback_hours=4, margin_gph=.5, completed_grids_per_hour=5, net=10),
                  dict(lookback_hours=1, margin_gph=0, completed_grids_per_hour=6, net=-1)]
        self.assertEqual(choose_parameters(trials)['lookback_hours'], 1)

    def test_nonfinite_metrics_cannot_pass_deployment_gate(self):
        import math
        a = dict(start_ms=0, end_ms=31*86400000, net=math.inf,
                 max_drawdown=.01, coverage_complete=True)
        b = dict(a, start_ms=31*86400000, end_ms=59*86400000)
        self.assertEqual(deployment_decision([a, b], parity_verified=True)['decision'], 'shelve')

    def test_walk_forward_freezes_parameters_selected_only_on_prior_training(self):
        from trader.research.grid_replay import sweep, timestamp
        calls = []
        class Snapshot:
            manifest = {'source': 'synthetic test'}
        def runner(snapshot, start, end, parameters, seed=None):
            calls.append((start, end, parameters.copy(), seed))
            return dict(start_ms=start, end_ms=end, net=1, max_drawdown=.01,
                        completed_grids_per_hour=parameters['lookback_hours'],
                        coverage_complete=True)
        result = sweep(Snapshot(), runner=runner)
        self.assertEqual(len(calls), 22)
        for offset, start in ((0, timestamp('2026-07-01T00:00:00Z')),
                              (11, timestamp('2026-08-01T00:00:00Z'))):
            self.assertTrue(all(call[1] == start and call[0] < start for call in calls[offset:offset+9]))
            self.assertEqual(calls[offset+9][2], calls[offset+10][2])
            self.assertEqual(calls[offset+9][2]['lookback_hours'], 8)
            self.assertIsNotNone(calls[offset+10][3])
        self.assertEqual(result['decision'], 'shelve')

    def test_uncommitted_registration_cannot_start_a_sweep(self):
        from unittest.mock import patch
        from trader.research.grid_replay import registered_document
        with patch('trader.research.grid_replay.subprocess.check_output', return_value=b'{}'):
            with self.assertRaisesRegex(ValueError, 'committed'):
                registered_document()
