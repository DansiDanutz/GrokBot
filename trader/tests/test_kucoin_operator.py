import unittest
from unittest.mock import patch
from trader.research.kucoin_operator import operator_report, validate_running

HOUR = 3_600_000


def running(**changes):
    bot = dict(bot_id='test-bot', pair='TESTUSDTM', direction='long', leverage=5,
               used_margin=1000, reserved_margin=200, entry=100, low=90, high=110,
               grids=20, stop_loss=85, start_ms=0, expected_start_gph=10,
               quantity=1)
    return dict(schema_version=1, bots=[dict(bot, **changes)])


class MemorySnapshot:
    manifest = {'offline_copy': True, 'source': 'kucoin-public'}

    def records(self, asof, pairs=None):
        return []

    def candles(self, pair, start, end):
        return [dict(time_ms=t, open=100, high=100, low=100, close=100)
                for t in range(start, end, 60000)]

    def funding(self, pair, start, end):
        return [dict(time_ms=t, rate=.0001) for t in range(8*HOUR, end+1, 8*HOUR)
                if t > start]

    def market(self, pair, asof):
        return dict(bid=99.99, ask=100.01, observed_at_ms=asof,
                    book_observed_at_ms=asof)


def funded(document, asof_ms, cash=10):
    return dict(document, capital=dict(available_cash_usdt=cash, asof_ms=asof_ms))


def candidate(pair='NEW', score=10):
    return dict(pair=pair, direction='long', score=score, reason='fixture',
                setup=dict(running()['bots'][0], pair=pair, eligible=True,
                           preview={'profit_per_grid_min': 1}))


class OperatorTests(unittest.TestCase):
    def test_operator_range_edges_close_all_modes_and_explicit_legacy_buffer_survives(self):
        for direction in ('long', 'short', 'neutral'):
            for edge in (90, 110):
                for buffer in (0, .05):
                    with self.subTest(direction=direction, edge=edge, buffer=buffer):
                        snapshot = MemorySnapshot()
                        snapshot.candles = lambda pair, start, end: [dict(time_ms=t,
                            open=100, high=max(100, edge), low=min(100, edge), close=100)
                            for t in range(start, end, 60000)]
                        document = running(direction=direction, stop_loss=85 if direction != 'short' else 115)
                        if direction == 'neutral':
                            document['bots'][0]['stop_loss_high'] = 115
                        with patch('trader.research.kucoin_operator.radar', return_value=
                                dict(radar=[], rejected=[], coverage={'observed': 1})) as mocked:
                            result = operator_report(snapshot, HOUR, document, {'range_exit_stop_pct': buffer, 'adaptive_range_stops': False})
                        bot = result['running'][0]
                        self.assertEqual(bot['tracker']['status'], 'stopped' if buffer == 0 else 'running')
                        stops = [event for event in bot['ledger'] if event['kind'] == 'stop_loss']
                        self.assertEqual(bool(stops), buffer == 0)
                        self.assertTrue(all(event['price'] == edge for event in stops))
                        self.assertEqual(mocked.call_args.args[3]['setup']['range_exit_stop_pct'], buffer)
                        self.assertFalse(mocked.call_args.args[3]['setup']['adaptive_range_stops'])

    def test_operator_validation_and_preview_share_declared_range_policy(self):
        from trader.research.kucoin_operator import _config
        document = running(entry=90)
        with self.assertRaisesRegex(ValueError, 'strictly inside'):
            validate_running(document, HOUR)
        validate_running(document, HOUR, {'range_exit_stop_pct': .05})
        default = _config(running()['bots'][0])
        self.assertEqual(default.range_exit_stop_pct, 0)
        self.assertFalse(default.adaptive_range_stops)
        legacy = _config(running()['bots'][0], {'range_exit_stop_pct': .05, 'adaptive_range_stops': True})
        self.assertEqual(legacy.range_exit_stop_pct, .05)
        self.assertTrue(legacy.adaptive_range_stops)

    def test_direction_setup_and_reported_preview_use_same_range_policy(self):
        snapshot = MemorySnapshot()
        snapshot.records = lambda at, pairs=None: [dict(pair='TESTUSDTM',
            bars=[{'close': 100}]*10080, market={})]
        for options, fraction, adaptive in (({}, 0, False), ({'range_exit_stop_pct': .05}, .05, True)):
            with self.subTest(options=options), \
                    patch('trader.research.kucoin_operator.radar', return_value=dict(radar=[], coverage={})), \
                    patch('trader.research.kucoin_operator.normalize_market', return_value=
                          dict(observed_at_ms=HOUR, funding_rate_8h=0)), \
                    patch('trader.research.kucoin_operator.features', return_value={}), \
                    patch('trader.research.kucoin_operator.build_setup', return_value=
                          dict(direction='long', eligible=False)) as builder:
                report = operator_report(snapshot, HOUR, running(), options)
                self.assertEqual(builder.call_args.args[3]['range_exit_stop_pct'], fraction)
                self.assertEqual(builder.call_args.args[3]['adaptive_range_stops'], adaptive)
                estimate = report['running'][0]['preview']
                self.assertAlmostEqual(estimate['range_exit_stop_low'], 90*(1-fraction))
                self.assertAlmostEqual(estimate['range_exit_stop_high'], 110*(1+fraction))
                self.assertEqual(report['range_policy'], dict(range_exit_stop_pct=fraction,
                                                            adaptive_range_stops=adaptive))

    def test_flat_cash_floor_reaches_radar_and_funded_entries(self):
        for minimum, expected in ((0, 1), (1, 0)):
            with self.subTest(minimum=minimum):
                row = candidate()
                row['setup']['preview']['profit_per_grid_min'] = .25
                with patch('trader.research.kucoin_operator.radar', return_value=
                           dict(radar=[row], rejected=[], coverage={'observed': 1})) as mocked:
                    result = operator_report(MemorySnapshot(), HOUR,
                        funded(dict(schema_version=1, bots=[]), HOUR, 1200),
                        {'minimum_grid_net_usdt': minimum})
                self.assertEqual(mocked.call_args.args[3]['setup']['minimum_grid_net_usdt'], minimum)
                self.assertEqual(len(result['recommended_forms']), expected)

    def test_zero_history_operator_uses_actual_subunit_setup_cash(self):
        from trader.research.kucoin_operator import _track_bot
        bot = running()['bots'][0]
        tracked = _track_bot(MemorySnapshot(), bot, HOUR, [bot['pair']], {})
        self.assertEqual(tracked['tracker']['completed_grids'], 0)
        self.assertAlmostEqual(tracked['_decision']['actual_net_usdt_per_grid'], .8686)
        self.assertEqual(tracked['_decision']['cash_estimate_basis'], 'modeled setup cash after both fill fees')

    def test_running_document_rejects_unknown_fields_credentials_and_bad_margin(self):
        for document in [dict(running(), api_key='not-a-credential'),
                         running(used_margin=999), running(start_ms=-1),
                         running(direction='up'), running(bot_id='../bad'),
                         running(secret='not-a-credential')]:
            with self.subTest(document=document):
                with self.assertRaises(ValueError):
                    validate_running(document, 2*HOUR)

    @patch('trader.research.kucoin_operator.radar')
    def test_hourly_tracking_has_full_ledger_and_held_replacement(self, mocked):
        mocked.return_value = dict(radar=[candidate()], rejected=[], coverage={})
        report = operator_report(MemorySnapshot(), 2*HOUR, funded(running(), 2*HOUR))
        self.assertFalse(report['live_use']['actionable'])
        self.assertIn('calibration', report['live_use']['status'])
        bot = report['running'][0]
        self.assertEqual([row['asof_ms'] for row in bot['hourly_history']], [HOUR, 2*HOUR])
        self.assertEqual(bot['verdict']['action'], 'replace')
        self.assertEqual(bot['verdict']['replacement']['pair'], 'NEW')
        self.assertGreater(len(bot['ledger']), 0)
        self.assertAlmostEqual(sum(row['fee'] for row in bot['ledger']), bot['tracker']['fees'])
        self.assertEqual(len(report['recommended_forms']), 1)
        self.assertEqual(report['recommended_forms'][0]['pair'], 'NEW')

    @patch('trader.research.kucoin_operator.radar')
    def test_canonical_form_preview_and_running_exclusion_are_preserved(self, mocked):
        mocked.return_value = dict(radar=[candidate('A'), candidate('B'), candidate('C')], rejected=[], coverage={})
        report = operator_report(MemorySnapshot(), HOUR, funded(running(), HOUR))
        self.assertEqual(len(report['recommended_forms']), 1)
        self.assertEqual(report['recommended_forms'][0]['preview']['profit_per_grid_min'], 1)
        self.assertEqual(mocked.call_args.args[2], ['TESTUSDTM'])

    def test_missing_candles_and_funding_fail_closed(self):
        snapshot = MemorySnapshot()
        snapshot.candles = lambda *args: []
        with self.assertRaisesRegex(ValueError, 'candles'):
            operator_report(snapshot, HOUR, running())
        snapshot = MemorySnapshot()
        snapshot.funding = lambda *args: []
        with self.assertRaisesRegex(ValueError, 'funding'):
            operator_report(snapshot, 8*HOUR, running())

    def test_invalid_asof_and_future_bot_fail_before_reading(self):
        for at, document in [(1, running()), (HOUR, running(start_ms=2*HOUR))]:
            with self.assertRaises(ValueError):
                operator_report(None, at, document)

    def test_just_started_bot_retains_initial_seed_events(self):
        report = operator_report(MemorySnapshot(), 0, running())
        bot = report['running'][0]
        self.assertGreater(len(bot['ledger']), 0)
        self.assertAlmostEqual(sum(row['fee'] for row in bot['ledger']),
                               bot['tracker']['fees'])

    def test_running_form_requires_fixed_five_times_leverage(self):
        for leverage in (2, 3, 4, 6, 7):
            with self.assertRaises(ValueError):
                validate_running(running(leverage=leverage), HOUR)

    def test_no_radar_replacement_after_stop_remains_waiting(self):
        snapshot = MemorySnapshot()
        snapshot.candles = lambda pair, start, end: [
            dict(time_ms=t, open=100, high=100, low=80, close=90)
            for t in range(start, end, 60000)]
        report = operator_report(snapshot, 2*HOUR, running())
        bot = report['running'][0]
        self.assertEqual(bot['verdict']['action'], 'waiting')
        self.assertTrue(bot['tracker']['stop_loss_hit'])
        self.assertLess(bot['verdict']['switch_cost']['net_realized_on_close'], 0)

    def test_switch_cost_uses_executable_quote_while_tracker_keeps_mark(self):
        snapshot = MemorySnapshot()
        snapshot.market = lambda pair, at: dict(bid=99.95, ask=100.05,
            observed_at_ms=at-1000, book_observed_at_ms=at-500)
        report = operator_report(snapshot, HOUR, running(grids=10))
        bot = report['running'][0]
        self.assertEqual(bot['tracker']['floating_pnl'], 0)
        cost = bot['verdict']['switch_cost']
        self.assertAlmostEqual(cost['floating_pnl'], -.25)
        self.assertAlmostEqual(cost['close_fees'], .29985)
        self.assertAlmostEqual(cost['net_realized_on_close'], -.54985)
        self.assertEqual(cost['close_cost_basis'], 'historical_bid_ask')
        self.assertEqual(cost['close_quote_bid'], 99.95)
        self.assertEqual(cost['quote_observation_age_ms'], 1000)

    def test_intrabar_stop_cost_coverage_is_explicitly_incomplete(self):
        snapshot = MemorySnapshot()
        snapshot.candles = lambda pair, start, end: [
            dict(time_ms=t, open=100, high=100, low=80, close=90)
            for t in range(start, end, 60000)]
        report = operator_report(snapshot, HOUR, running())
        self.assertFalse(report['coverage']['execution_costs_complete'])
        self.assertFalse(report['running'][0]['execution_cost_coverage']['complete'])
        self.assertIn('intrabar bid/ask', report['running'][0]['execution_cost_coverage']['reason'])

    def test_fixed_policy_requires_used_1000_plus_reserve_200(self):
        validate_running(running(), HOUR)
        for changes in ({'used_margin': 800, 'reserved_margin': 200},
                        {'used_margin': 1000, 'reserved_margin': 0},
                        {'used_margin': 900, 'reserved_margin': 300}):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    validate_running(running(**changes), HOUR)

    @patch('trader.research.kucoin_operator.radar')
    def test_two_running_bots_emit_only_one_portfolio_switch(self, mocked):
        mocked.return_value = dict(radar=[candidate('A'), candidate('B')], rejected=[], coverage={})
        first = running()['bots'][0]
        second = dict(first, bot_id='second-bot', pair='SECONDUSDTM')
        report = operator_report(MemorySnapshot(), 2*HOUR,
                                 funded(dict(schema_version=1, bots=[first, second]), 2*HOUR))
        replacements = [bot for bot in report['running'] if bot['verdict']['action'] == 'replace']
        self.assertEqual(len(replacements), 1)
        self.assertEqual(report['portfolio_decision']['worst_bot_id'], replacements[0]['bot_id'])
        self.assertEqual(len(report['recommended_forms']), 1)
        self.assertEqual(report['capital_policy']['total_for_two_bots'], 2400)

    def test_operator_disables_adaptive_safety_under_boundary_policy(self):
        from trader.research.kucoin_operator import _config
        config = _config(running()['bots'][0])
        self.assertFalse(config.adaptive_range_stops)
        self.assertEqual(config.adaptive_tight_stop_pct, .01)
        self.assertEqual(config.adaptive_liquidation_clearance_pct, .01)

    def test_capital_snapshot_is_strict_finite_and_exactly_asof(self):
        validate_running(funded(running(), HOUR, 0), HOUR)
        malformed = [dict(available_cash_usdt=-1, asof_ms=HOUR),
                     dict(available_cash_usdt=float('nan'), asof_ms=HOUR),
                     dict(available_cash_usdt=True, asof_ms=HOUR),
                     dict(available_cash_usdt=1200, asof_ms=0),
                     dict(available_cash_usdt=1200, asof_ms=2*HOUR),
                     dict(available_cash_usdt=1200, asof_ms=HOUR, deposit=1)]
        for capital in malformed:
            with self.subTest(capital=capital):
                with self.assertRaises(ValueError):
                    validate_running(dict(running(), capital=capital), HOUR)

    @patch('trader.research.kucoin_operator.radar')
    def test_missing_capital_withholds_funded_forms_but_keeps_research_radar(self, mocked):
        mocked.return_value = dict(radar=[candidate()], rejected=[], coverage={})
        for document in (running(), dict(schema_version=1, bots=[])):
            report = operator_report(MemorySnapshot(), HOUR, document)
            self.assertEqual(report['recommended_forms'], [])
            self.assertFalse(report['capital']['known'])
            self.assertIn('unknown', report['capital']['reason'])
            self.assertEqual(len(report['radar']['radar']), 1)
            self.assertNotEqual(report['portfolio_decision']['action'], 'replace')

    @patch('trader.research.kucoin_operator.radar')
    def test_two_allocated_bots_zero_cash_cannot_fund_losing_replacement(self, mocked):
        mocked.return_value = dict(radar=[candidate()], rejected=[], coverage={})
        first = running()['bots'][0]
        second = dict(first, bot_id='second', pair='SECONDUSDTM')
        document = funded(dict(schema_version=1, bots=[first, second]), HOUR, 0)
        report = operator_report(MemorySnapshot(), HOUR, document)
        self.assertEqual(report['recommended_forms'], [])
        self.assertNotEqual(report['portfolio_decision']['action'], 'replace')
        self.assertTrue(any('capital cannot fund' in row['reason']
                           for row in report['portfolio_decision']['rejected_candidates']))

    @patch('trader.research.kucoin_operator.radar')
    def test_one_kept_bot_can_fill_a_funded_second_slot_without_double_spending(self, mocked):
        mocked.return_value = dict(radar=[candidate('A'), candidate('B')], rejected=[], coverage={})
        report = operator_report(MemorySnapshot(), HOUR, funded(running(), HOUR, 1200))
        self.assertEqual(report['running'][0]['verdict']['action'], 'keep')
        self.assertEqual([row['pair'] for row in report['entry_decision']['selected']], ['A'])
        self.assertEqual([row['pair'] for row in report['recommended_forms']], ['A'])
        self.assertEqual(report['entry_decision']['remaining_cash'], 0)
        self.assertNotEqual(report['portfolio_decision']['action'], 'replace')

    @patch('trader.research.kucoin_operator.radar')
    def test_empty_portfolio_uses_same_cost_adjusted_entry_ranking_as_replay(self, mocked):
        fast, profitable = candidate('FAST', 20), candidate('PROFITABLE', 5)
        fast['setup']['opening_fee_budget'] = 119
        profitable['setup']['opening_fee_budget'] = .1
        mocked.return_value = dict(radar=[fast, profitable], rejected=[], coverage={})
        document = funded(dict(schema_version=1, bots=[]), HOUR, 1200)
        report = operator_report(MemorySnapshot(), HOUR, document)
        self.assertEqual([row['pair'] for row in report['recommended_forms']], ['PROFITABLE'])
        self.assertEqual(report['entry_decision']['required_cash'], 1200)

    @patch('trader.research.kucoin_operator.radar')
    def test_asof_cash_does_not_add_already_released_modeled_equity_twice(self, mocked):
        mocked.return_value = dict(radar=[candidate()], rejected=[], coverage={})
        snapshot = MemorySnapshot()
        snapshot.candles = lambda pair, start, end: [
            dict(time_ms=t, open=100, high=100, low=80, close=90)
            for t in range(start, end, 60000)]
        first = running()['bots'][0]
        second = dict(first, bot_id='second', pair='SECONDUSDTM')
        document = funded(dict(schema_version=1, bots=[first, second]), HOUR, 300)
        report = operator_report(snapshot, HOUR, document)
        self.assertTrue(all(bot['tracker']['status'] == 'stopped' for bot in report['running']))
        self.assertEqual(report['recommended_forms'], [])
        self.assertNotEqual(report['portfolio_decision']['action'], 'replace')

    @patch('trader.research.kucoin_operator.radar')
    def test_operator_propagates_requested_radar_size_and_defaults_to_ten(self, mocked):
        mocked.return_value = dict(radar=[], rejected=[], coverage={})
        document = funded(dict(schema_version=1, bots=[]), HOUR, 2400)
        for parameters, expected in [({}, 10), ({'radar_size': 5}, 5)]:
            with self.subTest(parameters=parameters):
                operator_report(MemorySnapshot(), HOUR, document, parameters)
                self.assertEqual(mocked.call_args.args[3]['radar_size'], expected)
