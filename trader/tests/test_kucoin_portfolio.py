import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from trader.research.kucoin_portfolio import sweep, decision


class ValidationTests(unittest.TestCase):
    def test_failed_calibration_blocks_profitable_results(self):
        metrics = dict(net=100, max_drawdown_fraction_of_margin=.01,
                       liquidations=0, closest_liquidation_range_pct=20)
        windows = [dict(coverage={'complete': True}, metrics=metrics)]*2
        self.assertEqual(decision(windows, {'calibrated': False}, {'validated': True})['decision'], 'shelve')

    def test_missing_metrics_cannot_pass(self):
        windows = [dict(coverage={'complete': False}, metrics={'net': None})]*2
        self.assertEqual(decision(windows, {'calibrated': True}, {'validated': True})['decision'], 'shelve')

    def test_sweep_rejects_uncommitted_registration_before_runner(self):
        runner = unittest.mock.Mock()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'registration.json'
            path.write_text('{}')
            with self.assertRaises(ValueError):
                sweep(object(), path, {}, {}, runner=runner)
        runner.assert_not_called()

    @patch('trader.research.kucoin_portfolio._registered')
    def test_missing_months_skip_every_combination(self, registered):
        document = json.loads((Path(__file__).parents[2]/'research/preregistration/grid-kucoin.json').read_text())
        registered.return_value = document
        snapshot = unittest.mock.Mock()
        snapshot.market_bounds.return_value = (1, 2)
        runner = unittest.mock.Mock()
        result = sweep(snapshot, 'ignored', {}, {}, runner=runner)
        self.assertEqual(result['decision']['decision'], 'shelve')
        self.assertEqual(len(result['training_trials']), 64)
        self.assertTrue(all(row['status'] == 'not_run_coverage' for row in result['training_trials']))
        runner.assert_not_called()

    @patch('trader.research.kucoin_portfolio._registered')
    def test_walk_forward_freezes_prior_week_winner_and_never_trains_on_holdout(self, registered):
        from trader.research.kucoin_portfolio import _time, DAY_MS
        document = json.loads((Path(__file__).parents[2]/'research/preregistration/grid-kucoin.json').read_text())
        registered.return_value = document
        snapshot = unittest.mock.Mock()
        snapshot.market_bounds.return_value = (0, 9999999999999)
        calls = []
        def runner(snapshot, start, end, parameters=None, **options):
            calls.append((start, end, dict(parameters), options))
            return dict(status='complete', coverage={'complete': True}, metrics=dict(
                completed_grids_per_hour_at_net_1_usdt=parameters['minimum_rate_factor'],
                net=parameters['leverage_cap'], liquidations=0,
                completed_at_target=10, completed_below_target=0,
                max_drawdown_fraction_of_margin=.01, closest_liquidation_range_pct=20))
        result = sweep(snapshot, 'ignored', {'calibrated': True, 'neutral_calibrated': True}, {'validated': True}, runner=runner)
        self.assertEqual(len(result['training_trials']), 64)
        for span in document['holdouts']:
            start, end = _time(span['start']), _time(span['end'])
            trained = [call for call in calls if call[0] == start-7*DAY_MS and call[1] == start]
            self.assertEqual(len(trained), 32)
            evaluated = [call for call in calls if call[0] == start and call[1] == end]
            self.assertTrue(all(call[2]['minimum_rate_factor'] == .75 and call[2]['leverage_cap'] == 6
                                for call in evaluated))
        self.assertEqual(result['decision']['decision'], 'eligible_for_separate_hourly_recommender_review')

    def test_profitable_partial_month_is_not_two_complete_months(self):
        from trader.research.kucoin_portfolio import _time
        metrics = dict(net=100, max_drawdown_fraction_of_margin=.01,
                       liquidations=0, closest_liquidation_range_pct=20)
        windows = [dict(start_ms=_time('2026-07-02T00:00:00Z'),
            end_ms=_time('2026-08-01T00:00:00Z'), coverage={'complete': True}, metrics=metrics),
            dict(start_ms=_time('2026-08-01T00:00:00Z'), end_ms=_time('2026-09-01T00:00:00Z'),
                 coverage={'complete': True}, metrics=metrics)]
        self.assertEqual(decision(windows, {'calibrated': True}, {'validated': True})['decision'], 'shelve')

    def test_interleaved_portfolio_equity_counts_open_drawdown(self):
        from trader.research.kucoin_portfolio import _drawdown
        report = dict(initial_capital=2000, equity_timeline=[
            dict(bot_id='A', timestamp_ms=1, equity=1100, margin=1000),
            dict(bot_id='B', timestamp_ms=2, equity=900, margin=1000),
            dict(bot_id='A', timestamp_ms=3, equity=800, margin=1000),
            dict(bot_id='A', timestamp_ms=4, equity=1200, margin=1000)])
        self.assertEqual(_drawdown(report, [dict(bot_id='A'), dict(bot_id='B')]), 400)

    def test_shared_timestamp_risk_bound_prevents_optimistic_gate(self):
        from trader.research.kucoin_portfolio import _risk_bound
        report = dict(equity_timeline=[
            dict(bot_id='A', timestamp_ms=1, equity=1200, margin=1000, floating_pnl=200),
            dict(bot_id='A', timestamp_ms=1, equity=1000, margin=1000, floating_pnl=-100),
            dict(bot_id='B', timestamp_ms=1, equity=1200, margin=1000, floating_pnl=200),
            dict(bot_id='B', timestamp_ms=1, equity=1000, margin=1000, floating_pnl=-100)])
        bots = [dict(bot_id='A'), dict(bot_id='B')]
        self.assertEqual(_risk_bound(report, bots), 400)
        self.assertEqual(_risk_bound(report, bots, True), 200)


    def test_monthly_grid_target_gate_rejects_subtarget_seed_only_and_unknown_counts(self):
        from trader.research.kucoin_portfolio import _time
        valid = dict(net=100, seed_pnl=100, max_drawdown_fraction_of_margin=.01,
                     liquidations=0, closest_liquidation_range_pct=20,
                     completed_at_target=10, completed_below_target=0)
        boundaries = ['2026-07-01T00:00:00Z', '2026-08-01T00:00:00Z', '2026-09-01T00:00:00Z']
        calibration = {'calibrated': True, 'neutral_calibrated': True}
        for changes in ({'completed_below_target': 1}, {'completed_at_target': 0},
                        {'completed_below_target': None}, {'completed_at_target': None}):
            with self.subTest(changes=changes):
                windows = [dict(start_ms=_time(boundaries[i]), end_ms=_time(boundaries[i+1]),
                    coverage={'complete': True}, metrics=dict(valid, **(changes if i == 1 else {})))
                    for i in range(2)]
                outcome = decision(windows, calibration, {'validated': True})
                self.assertEqual(outcome['decision'], 'shelve')
                self.assertTrue(any('month 2:' in reason for reason in outcome['reasons']))
        windows = [dict(start_ms=_time(boundaries[i]), end_ms=_time(boundaries[i+1]),
                   coverage={'complete': True}, metrics=dict(valid)) for i in range(2)]
        self.assertEqual(decision(windows, calibration, {'validated': True})['decision'],
                         'eligible_for_separate_hourly_recommender_review')

    @patch('trader.research.kucoin_portfolio._registered')
    def test_archived_registration_cannot_run_new_policy(self, registered):
        registered.return_value = {'id': 'grid-kucoin'}
        with self.assertRaisesRegex(ValueError, 'archived registration'):
            sweep(object(), 'archived', {}, {})

    @patch('trader.research.kucoin_portfolio._registered')
    def test_new_policy_registration_has_twelve_trials_per_month_and_fixed_capital(self, registered):
        path = Path(__file__).parents[2]/'research/preregistration/grid-kucoin-policy-v2.json'
        document = json.loads(path.read_text())
        registered.return_value = document
        snapshot = unittest.mock.Mock()
        snapshot.market_bounds.return_value = (1, 2)
        result = sweep(snapshot, path, {}, {}, runner=unittest.mock.Mock())
        self.assertEqual(len(result['training_trials']), 24)
        self.assertEqual(result['registration']['total_bankroll_usdt'], 2400)
        self.assertTrue(all('leverage_cap' not in row['parameters'] for row in result['training_trials']))
        self.assertEqual(result['decision']['decision'], 'shelve')


    @patch('trader.research.kucoin_portfolio._registered')
    def test_v3_runs_only_two_prespecified_months_and_keeps_verification_blocked(self, registered):
        document = dict(id='grid-kucoin-v3', fixed_parameters=dict(historical_spread_bps=10),
            holdouts=[dict(start='2026-07-01T00:00:00Z', end='2026-08-01T00:00:00Z'),
                      dict(start='2026-08-01T00:00:00Z', end='2026-09-01T00:00:00Z')])
        registered.return_value = document
        runner = unittest.mock.Mock(side_effect=lambda *args, **kwargs: dict(
            coverage={'complete': False}, metrics={'net': 5}, modeled_metrics={'net': 5}))
        result = sweep(object(), 'v3', {}, {}, runner=runner)
        self.assertEqual(runner.call_count, 2)
        self.assertEqual(result['training_trials'], [])
        self.assertFalse(result['parameter_search_performed'])
        self.assertTrue(all(call.kwargs['parameters']['historical_candle_only'] for call in runner.call_args_list))
        self.assertEqual(result['holdouts'][0]['modeled_metrics']['net'], 5)
        self.assertEqual(result['decision']['decision'], 'shelve')


class PositiveCyclePolicyTests(unittest.TestCase):
    def windows(self, **changes):
        from trader.research.kucoin_portfolio import _time
        boundaries = ['2026-07-01T00:00:00Z', '2026-08-01T00:00:00Z', '2026-09-01T00:00:00Z']
        metrics = dict(net=20, completed_positive_net=100, completed_nonpositive_net=0,
            completed_at_target=0, completed_below_target=100, liquidations=0,
            closest_liquidation_range_pct=20, max_drawdown_fraction_of_margin=.01)
        return [dict(start_ms=_time(boundaries[i]), end_ms=_time(boundaries[i+1]),
            coverage={'complete': True}, metrics=dict(metrics, **changes)) for i in range(2)]

    def outcome(self, windows, registration=None):
        registration = registration or dict(id='grid-kucoin-v3-positive',
                                             fixed_parameters={'minimum_grid_net_usdt': 0})
        return decision(windows, {'calibrated': True, 'neutral_calibrated': True},
                        {'validated': True}, registration)

    def test_profitable_subdollar_cycles_pass_zero_floor_but_not_legacy_one(self):
        self.assertEqual(self.outcome(self.windows())['decision'],
                         'eligible_for_separate_hourly_recommender_review')
        legacy = dict(id='grid-kucoin-v3', minimum_grid_net_usdt=1)
        self.assertEqual(self.outcome(self.windows(), legacy)['decision'], 'shelve')

    def test_positive_policy_keeps_nonpositive_unknown_net_and_risk_gates(self):
        for changes in ({'completed_nonpositive_net': 1}, {'completed_nonpositive_net': None},
                        {'completed_positive_net': 0}, {'completed_positive_net': None},
                        {'net': -1}, {'liquidations': 1},
                        {'max_drawdown_fraction_of_margin': .15},
                        {'closest_liquidation_range_pct': 9}):
            with self.subTest(changes=changes):
                self.assertEqual(self.outcome(self.windows(**changes))['decision'], 'shelve')
        windows = self.windows()
        windows[0]['coverage'] = {'complete': False, 'reasons': ['unknown funding']}
        self.assertEqual(self.outcome(windows)['decision'], 'shelve')
        self.assertEqual(decision(self.windows(), {}, {'validated': True},
            {'minimum_grid_net_usdt': 0})['decision'], 'shelve')

    @patch('trader.research.kucoin_portfolio._registered')
    def test_fixed_runner_propagates_zero_and_archived_one_without_search(self, registered):
        for identifier, minimum in [('grid-kucoin-v3-positive', 0), ('grid-kucoin-v3', 1)]:
            registered.return_value = dict(id=identifier, minimum_grid_net_usdt=minimum,
                fixed_parameters={'historical_spread_bps': 10}, holdouts=[
                    dict(start='2026-07-01T00:00:00Z', end='2026-08-01T00:00:00Z'),
                    dict(start='2026-08-01T00:00:00Z', end='2026-09-01T00:00:00Z')])
            runner = unittest.mock.Mock(side_effect=lambda *args, **kwargs: dict(
                coverage={'complete': False}, metrics={}))
            result = sweep(object(), 'ignored', {}, {}, runner=runner)
            self.assertEqual(runner.call_count, 2)
            self.assertFalse(result['parameter_search_performed'])
            self.assertTrue(all(call.kwargs['parameters']['minimum_grid_net_usdt'] == minimum
                                for call in runner.call_args_list))

    @patch('trader.research.kucoin_portfolio._registered')
    def test_conflicting_registered_minimum_fails_before_execution(self, registered):
        registered.return_value = dict(id='grid-kucoin-v3-positive', minimum_grid_net_usdt=0,
            fixed_parameters={'minimum_grid_net_usdt': 1}, holdouts=[])
        runner = unittest.mock.Mock()
        with self.assertRaisesRegex(ValueError, 'conflict'):
            sweep(object(), 'ignored', {}, {}, runner=runner)
        runner.assert_not_called()

    def test_portfolio_metrics_preserve_income_and_expose_positive_partition(self):
        from trader.research.kucoin_portfolio import _metrics
        from trader.strategies.grid_types import GridConfig
        from trader.strategies.kucoin_grid import create_bot
        state = create_bot(GridConfig(pair='T', low=90, high=110, grids=20,
                                      quantity=1, direction='long'), 100, 0)
        fills = [dict(bot_id='a', completed_grid=True, price=price, gross_pnl=gross,
                      quantity=1, side=-1, kind='grid_close')
                 for price, gross in [(101, 1), (103, 3), (5003, 6), (100, 0)]]
        report = dict(ledger=fills, start_ms=0, end_ms=7200000, equity_timeline=[],
                      initial_capital=2400, switches=[])
        values = _metrics(report, [dict(bot_id='a', state=state, closest=20, outside_ms=0)])
        self.assertEqual(values['completed_positive_net'], 2)
        self.assertEqual(values['completed_nonpositive_net'], 2)
        self.assertEqual(values['completed_positive_net_per_hour'], 1)
        self.assertEqual(values['completed_nonpositive_net_per_day'], 24)
        self.assertEqual(values['completed_at_target'], 1)
        self.assertEqual(values['legacy_completed_at_net_1_usdt'], 1)
        self.assertEqual(values['grid_income_per_hour'], state.grid_net_profit/2)

    def test_new_registration_identifier_keeps_committed_byte_requirement(self):
        from trader.research.kucoin_portfolio import _registered
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            path = root/'positive.json'
            raw = json.dumps(dict(id='grid-kucoin-v3-positive',
                                  fixed_parameters={'minimum_grid_net_usdt': 0})).encode()
            path.write_bytes(raw)
            committed = unittest.mock.Mock(stdout=raw)
            with patch('trader.research.kucoin_portfolio.ROOT', root), patch(
                    'trader.research.kucoin_portfolio.subprocess.run', return_value=committed):
                self.assertEqual(_registered(path)['id'], 'grid-kucoin-v3-positive')
                path.write_bytes(raw+b' ')
                with self.assertRaisesRegex(ValueError, 'differs from committed'):
                    _registered(path)
