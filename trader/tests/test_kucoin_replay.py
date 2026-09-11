import unittest
from unittest.mock import patch
from trader.research.kucoin_replay import run_window

HOUR = 3600000
START = 20 * 24 * HOUR


def candidate(pair='A', direction='long'):
    form = dict(pair=pair, direction=direction, low=90, high=110, entry=100,
                grids=10, leverage=5, used_margin=1000, reserved_margin=200,
                quantity=1, multiplier=.001, lot_size=1, stop_loss=80 if direction == 'long' else 120,
                trigger=None, eligible=True, reason='fixture', preview={'profit_per_grid_min': 1}, opening_fee_budget=1)
    return dict(pair=pair, direction=direction, score=5, setup=form)


class MemorySnapshot:
    manifest = {'offline_copy': True}

    def market_bounds(self):
        return START, START + 10*HOUR

    def records(self, at_ms, pairs=None):
        return [dict(pair=p, bars=[], market={}) for p in ['A', 'B', 'C'] if not pairs or p in pairs]

    def candles(self, pair, start, end):
        return [dict(timestamp_ms=t, open=100, high=101, low=99, close=100)
                for t in range(start, end, 60000)]

    def market(self, pair, at):
        return dict(bid=99.99, ask=100.01, observed_at_ms=at)

    def funding(self, pair, start, end):
        return [dict(timestamp_ms=t, rate=0) for t in range((start//(8*HOUR)+1)*8*HOUR, end+1, 8*HOUR)]


def scan(records, asof, running_pairs=(), parameters=None):
    return dict(asof_ms=asof, radar=[candidate(r['pair']) for r in records
                if r['pair'] not in running_pairs][:5], rejected=[], coverage={'observed': len(records)})


class ReplayTests(unittest.TestCase):
    def test_missing_history_is_not_zero_profit(self):
        result = run_window(MemorySnapshot(), 0, HOUR)
        self.assertFalse(result['coverage']['complete'])
        self.assertIsNone(result['metrics']['net'])
        self.assertEqual(result['ledger'], [])

    def test_replay_range_edges_close_all_modes_and_preserve_explicit_legacy_buffer(self):
        from trader.research.kucoin_replay import _config
        for direction in ('long', 'short', 'neutral'):
            for edge in (90, 110):
                for buffer in (0, .05):
                    with self.subTest(direction=direction, edge=edge, buffer=buffer):
                        row = candidate('A', direction)
                        row['setup'].update(stop_loss=None, range_exit_stop_pct=buffer,
                                            adaptive_range_stops=False)
                        snapshot = MemorySnapshot()
                        snapshot.candles = lambda pair, start, end: [dict(timestamp_ms=t,
                            open=100, high=max(100, edge), low=min(100, edge), close=100)
                            for t in range(start, end, 60000)]
                        with patch('trader.research.kucoin_replay.radar', return_value=
                                dict(radar=[row], rejected=[], coverage={'observed': 1})):
                            result = run_window(snapshot, START, START+HOUR)
                        self.assertEqual(result['bots'][0]['status'], 'stopped' if buffer == 0 else 'running')
                        stops = [event for event in result['ledger'] if event['kind'] == 'stop_loss']
                        self.assertEqual(bool(stops), buffer == 0)
                        self.assertTrue(all(event['price'] == edge for event in stops))
                        self.assertEqual(_config(row['setup']).range_exit_stop_pct, buffer)
        default = _config(candidate()['setup'])
        self.assertEqual(default.range_exit_stop_pct, 0)
        self.assertFalse(default.adaptive_range_stops)

    def test_flat_range_policy_reaches_radar_setup(self):
        for options, expected in (({}, 0), ({'range_exit_stop_pct': .05}, .05)):
            with patch('trader.research.kucoin_replay.radar', return_value=
                    dict(radar=[], rejected=[], coverage={'observed': 1})) as mocked:
                run_window(MemorySnapshot(), START, START+HOUR, options)
            self.assertEqual(mocked.call_args.args[3]['setup']['range_exit_stop_pct'], expected)
            self.assertEqual(mocked.call_args.args[3]['setup']['adaptive_range_stops'], expected >= .01)

    def test_flat_cash_floor_reaches_radar_and_funded_entries_in_both_reader_paths(self):
        for streamed in (False, True):
            for minimum, expected in ((0, 1), (1, 0)):
                with self.subTest(streamed=streamed, minimum=minimum):
                    snapshot = MemorySnapshot()
                    if streamed:
                        snapshot.iter_records = snapshot.records
                    row = candidate()
                    row['setup']['preview']['profit_per_grid_min'] = .25
                    with patch('trader.research.kucoin_replay.radar', return_value=
                            dict(radar=[row], rejected=[], coverage={'observed': 1})) as mocked:
                        result = run_window(snapshot, START, START+HOUR, {'minimum_grid_net_usdt': minimum})
                    self.assertEqual(mocked.call_args.args[3]['setup']['minimum_grid_net_usdt'], minimum)
                    self.assertEqual(len(result['bots']), expected)

    def test_zero_history_decision_uses_actual_subunit_setup_cash(self):
        from trader.research.kucoin_replay import _decision_summaries
        from trader.research.kucoin_tracker import track_summary
        from trader.strategies.kucoin_grid import GridConfig, create_bot
        state = create_bot(GridConfig(pair='A', low=90, high=110, grids=10,
                           direction='long', entry_price=100, quantity=.1, multiplier=.1), 100, START)
        bot = dict(bot_id='A', active=True, state=state,
                   latest=track_summary(state, START, START+HOUR, 1))
        rows, _ = _decision_summaries(MemorySnapshot(), {'bots': [bot]}, START+HOUR)
        self.assertEqual(rows[0]['completed_grids'], 0)
        self.assertAlmostEqual(rows[0]['actual_net_usdt_per_grid'], .18692)
        self.assertEqual(rows[0]['cash_estimate_basis'], 'modeled setup cash after both fill fees')

    def test_fixed_four_missing_start_price_has_explicit_reason_and_no_entries(self):
        fixtures = [dict(symbol=pair+'USDT', mode='long', range_low=90, range_high=110,
                         grids_buy=5, grids_sell=5, leverage=5, margin_usdt=1200,
                         reserved_margin=200, entry_price=100) for pair in 'ABCD']
        missing_markets = ({}, dict(assumed=True, candle_observed_at_ms=START-60000,
                                   bid=99.95, ask=100.05))
        for missing in missing_markets:
            with self.subTest(missing=missing):
                snapshot = MemorySnapshot()
                snapshot.market = lambda pair, at: (missing if pair == 'DUSDTM' else
                                                    dict(bid=99.99, ask=100.01, observed_at_ms=at))
                result = run_window(snapshot, START, START+HOUR,
                    mode='four_observed_long_forms_unchanged', fixtures=fixtures)
                self.assertFalse(result['coverage']['complete'])
                self.assertIn('baseline historical starting price unavailable for DUSDTM at '+str(START),
                              result['coverage']['reasons'])
                self.assertEqual(result['bots'], [])
                self.assertEqual(result['ledger'], [])
                self.assertIsNone(result['metrics']['net'])
                self.assertEqual(result['completed_hours'], 0)

    @patch('trader.research.kucoin_replay.radar', side_effect=scan)
    def test_two_slots_total_margin_and_fill_identity(self, unused):
        result = run_window(MemorySnapshot(), START, START+HOUR)
        self.assertEqual(result['initial_capital'], 2400)
        self.assertEqual(len(result['bots']), 2)
        self.assertTrue(all(b['form']['used_margin']+b['form']['reserved_margin'] == 1200 for b in result['bots']))
        ids = [(row['bot_id'], row['event_id']) for row in result['ledger']]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreater(len(ids), 0)
        self.assertEqual(result['switches'], [])
        self.assertTrue(result['coverage']['complete'])

    @patch('trader.research.kucoin_replay.radar', side_effect=scan)
    def test_minute_gap_invalidates_window(self, unused):
        snapshot = MemorySnapshot()
        snapshot.candles = lambda pair, start, end: []
        result = run_window(snapshot, START, START+HOUR)
        self.assertFalse(result['coverage']['complete'])
        self.assertIsNone(result['metrics']['net'])

    def test_known_empty_radar_has_zero_completed_diagnostic(self):
        with patch('trader.research.kucoin_replay.radar', return_value=dict(radar=[], rejected=[], coverage={'observed': 3})):
            result = run_window(MemorySnapshot(), START, START+HOUR)
        self.assertEqual(result['metrics']['completed_grids_per_hour_at_net_1_usdt'], 0)
        self.assertEqual(result['metrics']['net'], 0)
        self.assertEqual(result['bots'], [])

    @patch('trader.research.kucoin_replay.radar', side_effect=scan)
    def test_loss_prevents_unfunded_thousand_dollar_restart(self, unused):
        snapshot = MemorySnapshot()
        original = snapshot.candles
        def candles(pair, start, end):
            rows = original(pair, start, end)
            if pair == 'A' and start == START:
                rows[0]['low'] = 79
            return rows
        snapshot.candles = candles
        result = run_window(snapshot, START, START+2*HOUR, {'consecutive_hours': 100})
        self.assertFalse(result['coverage']['complete'])
        self.assertEqual(len(result['bots']), 2)
        self.assertLess(result['ending_available_cash'], 1200)
        self.assertEqual(result['partial_metrics']['stop_loss_hits'], 1)
        stopped = [row for row in result['ledger'] if row['kind'] == 'stop_loss']
        self.assertGreater(len(stopped), 0)
        self.assertFalse(any(row['kind'] == 'replacement' and row['bot_id'] == 'bot-1'
                             for row in result['ledger']))

    def test_stopped_slot_waits_without_resurrection_or_duplicate_fills(self):
        snapshot = MemorySnapshot()
        original = snapshot.candles
        def candles(pair, start, end):
            rows = original(pair, start, end)
            if pair == 'A' and start == START:
                rows[0]['low'] = 79
            return rows
        snapshot.candles = candles
        def initial_only(records, at, running_pairs=(), parameters=None):
            result = scan(records, at, running_pairs, parameters)
            if at > START:
                result['radar'] = []
            return result
        with patch('trader.research.kucoin_replay.radar', side_effect=initial_only):
            result = run_window(snapshot, START, START+3*HOUR, {'consecutive_hours': 100})
        self.assertEqual(len(result['bots']), 2)
        self.assertEqual(result['bots'][0]['status'], 'stopped')
        self.assertEqual(result['switches'], [])
        waits = [row for row in result['hourly_tracker'] if row['bot_id'] == 'bot-1' and 'verdict' in row]
        self.assertTrue(all(row['verdict']['action'] == 'waiting' for row in waits))
        ids = [(row['bot_id'], row['event_id']) for row in result['ledger']]
        self.assertEqual(len(ids), len(set(ids)))

    @patch('trader.research.kucoin_replay.radar', side_effect=scan)
    def test_same_coin_direction_flip_uses_held_signal(self, unused):
        snapshot = MemorySnapshot()
        original = snapshot.candles
        def candles(pair, start, end):
            return [dict(row, low=97, high=103) for row in original(pair, start, end)]
        snapshot.candles = candles
        def flip(records, bot, at, options):
            return dict(candidate(bot['state'].config.pair, 'short'), score=10000)
        with patch('trader.research.kucoin_replay._flip', side_effect=flip):
            result = run_window(snapshot, START, START+2*HOUR, {'consecutive_hours': 1, 'replacement_policy': 'legacy_held'})
        self.assertTrue(result['coverage']['complete'])
        self.assertEqual(len(result['switches']), 2)
        self.assertTrue(all('direction_flip' in row['triggers'] for row in result['switches']))
        self.assertTrue(all(row['pair'] == row['replacement']['pair'] for row in result['switches']))
        self.assertEqual([b['direction'] for b in result['bots'][-2:]], ['short', 'short'])

    @patch('trader.research.kucoin_replay.radar', side_effect=scan)
    def test_liquidation_fails_and_terminates_trial(self, unused):
        snapshot = MemorySnapshot()
        snapshot.candles = lambda pair, start, end: [dict(timestamp_ms=t,
            open=1, high=100, low=1, close=100) for t in range(start, end, 60000)]
        def heavily_loaded(records, at, running_pairs=(), parameters=None):
            result = scan(records, at, running_pairs, parameters)
            for row in result['radar']:
                row['setup'].update(quantity=3)
            return result
        with patch('trader.research.kucoin_replay.radar', side_effect=heavily_loaded):
            result = run_window(snapshot, START, START+3*HOUR)
        self.assertEqual(result['status'], 'failed_liquidation')
        self.assertGreater(result['partial_metrics']['liquidations'], 0)
        self.assertEqual(result['switches'], [])

    def test_below_target_completed_pairs_are_separate_from_primary(self):
        snapshot = MemorySnapshot()
        original = snapshot.candles
        snapshot.candles = lambda pair, start, end: [dict(row, low=97, high=103)
                                                    for row in original(pair, start, end)]
        def low_profit(records, at, running_pairs=(), parameters=None):
            result = scan(records, at, running_pairs, parameters)
            for row in result['radar']:
                row['setup']['quantity'] = .2
            return result
        with patch('trader.research.kucoin_replay.radar', side_effect=low_profit):
            result = run_window(snapshot, START, START+HOUR)
        self.assertGreater(result['metrics']['completed_grids'], 0)
        self.assertEqual(result['metrics']['completed_grids_per_hour_at_net_1_usdt'], 0)
        self.assertEqual(result['metrics']['completed_grids'], result['metrics']['completed_below_target'])
        self.assertGreater(result['metrics']['grid_profit'], result['metrics']['grid_net_profit'])

    @patch('trader.research.kucoin_replay.radar', side_effect=scan)
    def test_missing_actual_funding_marks_metrics_unknown(self, unused):
        snapshot = MemorySnapshot()
        snapshot.funding = lambda pair, start, end: []
        result = run_window(snapshot, START, START+8*HOUR, {'consecutive_hours': 100})
        self.assertFalse(result['coverage']['complete'])
        self.assertIsNone(result['metrics']['funding'])
        self.assertTrue(any('missing actual funding' in reason for reason in result['coverage']['reasons']))

    def test_switch_forecast_and_actual_ledger_both_include_bid_ask_spread(self):
        for direction in ('long', 'short'):
            with self.subTest(direction=direction):
                snapshot = MemorySnapshot()
                snapshot.market = lambda pair, at: dict(bid=99.95, ask=100.05, observed_at_ms=at)
                snapshot.candles = lambda pair, start, end: [dict(timestamp_ms=t,
                    open=100, high=100, low=100, close=100) for t in range(start, end, 60000)]
                def directional(records, at, running_pairs=(), parameters=None):
                    rows = [candidate(row['pair'], direction) for row in records if row['pair'] not in running_pairs]
                    return dict(radar=rows, rejected=[], coverage={'observed': len(records)})
                with patch('trader.research.kucoin_replay.radar', side_effect=directional):
                    result = run_window(snapshot, START, START+2*HOUR, {'consecutive_hours': 1, 'replacement_policy': 'legacy_held'})
                self.assertTrue(result['coverage']['complete'])
                self.assertGreater(len(result['switches']), 0)
                for switch in result['switches']:
                    fills = [row for row in result['ledger'] if row['bot_id'] == switch['bot_id']
                             and row['kind'] == 'replacement']
                    gross, fee = sum(row['gross_pnl'] for row in fills), sum(row['fee'] for row in fills)
                    self.assertAlmostEqual(switch['switch_cost']['net_realized_on_close'], gross-fee)
                    self.assertAlmostEqual(switch['actual_switch_gross_pnl'], gross)
                    self.assertAlmostEqual(switch['actual_switch_close_fees'], fee)
                    self.assertAlmostEqual(switch['actual_switch_net_pnl'], gross-fee)
                    self.assertAlmostEqual(switch['forecast_actual_net_difference'], 0)
                summary = result['hourly_tracker'][0]
                self.assertEqual(summary['mark_floating_pnl'], 0)
                self.assertAlmostEqual(summary['floating_pnl'], -.25)
                self.assertAlmostEqual(summary['close_spread_cost'], .25)

    def test_quote_aware_close_summary_uses_both_sides_for_neutral(self):
        from dataclasses import replace
        from trader.research.kucoin_replay import close_cost_summary
        from trader.research.kucoin_tracker import track_summary
        from trader.strategies.grid_types import GridConfig, Position
        from trader.strategies.kucoin_grid import create_bot
        config = GridConfig(pair='N', low=90, high=110, direction='neutral', quantity=.1, multiplier=.1)
        state = replace(create_bot(config, 100, START), positions=(Position(0, 1, 2, 98),
                        Position(1, -1, 3, 102)))
        summary = close_cost_summary(state, track_summary(state, START, START), {'bid': 99.95, 'ask': 100.05})
        gross = 2*(99.95-98) + 3*(102-100.05)
        fee = (2*99.95+3*100.05)*.0006
        self.assertEqual(summary['mark_floating_pnl'], 10)
        self.assertAlmostEqual(summary['floating_pnl'], gross)
        self.assertAlmostEqual(summary['close_fee'], fee)
        self.assertAlmostEqual(summary['quoted_close_net_pnl'], gross-fee)

    @patch('trader.research.kucoin_replay.radar', side_effect=scan)
    def test_intrabar_stop_without_execution_book_invalidates_cost_coverage(self, unused):
        snapshot = MemorySnapshot()
        original = snapshot.candles
        snapshot.candles = lambda pair, start, end: [dict(row, low=79) for row in original(pair, start, end)]
        result = run_window(snapshot, START, START+HOUR)
        self.assertFalse(result['coverage']['complete'])
        self.assertIsNone(result['metrics']['net'])
        self.assertEqual(result['bots'][0]['stop_reason'], 'stop_loss')
        self.assertTrue(any('intrabar market-close spread is unobserved' in reason
                            for reason in result['coverage']['reasons']))

    def test_scanner_coverage_uses_explicit_flag_not_rejection_prose(self):
        for missing in (False, True):
            with self.subTest(missing=missing):
                rejection = dict(pair='A', reason='quote turnover unknown or below minimum', coverage_issue=missing)
                scanned = dict(radar=[], rejected=[rejection], coverage={'observed': 3})
                with patch('trader.research.kucoin_replay.radar', return_value=scanned):
                    result = run_window(MemorySnapshot(), START, START+HOUR)
                self.assertEqual(result['coverage']['complete'], not missing)
                self.assertEqual(result['metrics']['net'], None if missing else 0)


    def test_default_policy_switches_one_worst_slot_per_hour_when_better_coin_appears(self):
        snapshot = MemorySnapshot()
        original = snapshot.candles
        snapshot.candles = lambda pair, start, end: [dict(row, low=97, high=103)
                                                    for row in original(pair, start, end)]
        def opportunities(records, at, running_pairs=(), parameters=None):
            result = scan(records, at, running_pairs, parameters)
            if at > START:
                result['radar'] = [dict(candidate('C'), score=1000), dict(candidate('D'), score=900)]
            return result
        with patch('trader.research.kucoin_replay.radar', side_effect=opportunities):
            result = run_window(snapshot, START, START+2*HOUR, {'consecutive_hours': 100})
        self.assertTrue(result['coverage']['complete'])
        self.assertEqual(result['initial_capital'], 2400)
        self.assertEqual(len(result['switches']), 1)
        self.assertTrue(result['switches'][0]['replacement_executed'])
        self.assertEqual(result['switches'][0]['replacement']['pair'], 'C')
        self.assertIn('better_cost_adjusted_grid_income', result['switches'][0]['triggers'])
        self.assertEqual(len(result['bots']), 3)
        self.assertEqual(result['portfolio_decisions'][0]['action'], 'replace')

    def test_startup_does_not_fill_slots_with_unsafe_or_subtarget_setups(self):
        rows = [candidate('UNSAFE'), candidate('BELOW')]
        rows[0]['setup']['eligible'] = False
        rows[1]['setup']['preview']['profit_per_grid_min'] = .9
        rows[1]['setup']['minimum_grid_net_usdt'] = 1
        with patch('trader.research.kucoin_replay.radar', return_value=dict(radar=rows, rejected=[], coverage={'observed': 2})):
            result = run_window(MemorySnapshot(), START, START+HOUR)
        self.assertEqual(result['bots'], [])
        self.assertEqual(result['initial_capital'], 2400)

    def test_candle_only_replay_runs_without_book_envelope_and_retains_modeled_results(self):
        class HistoricalMemory(MemorySnapshot):
            historical_candle_only = True
            def bounds(self):
                return START, START+10*HOUR
            def market_bounds(self):
                return None, None
            def funding(self, pair, start, end):
                return []
        with patch('trader.research.kucoin_replay.radar', side_effect=scan):
            result = run_window(HistoricalMemory(), START, START+HOUR,
                                {'historical_candle_only': True})
        self.assertFalse(result['coverage']['complete'])
        self.assertEqual(result['filter_mode'], 'candle-only filters')
        self.assertIsInstance(result['metrics']['net'], (int, float))
        self.assertEqual(len(result['bots']), 2)
        self.assertIsNone(result['verified_metrics']['net'])
        self.assertEqual(result['modeled_metrics']['net'], result['metrics']['net'])

    def test_historical_sparse_execution_does_not_create_fills_for_indicator_gap(self):
        class HistoricalMemory(MemorySnapshot):
            historical_candle_only = True
            def bounds(self):
                return START, START+10*HOUR
            def market_bounds(self):
                return None, None
            def candles(self, pair, start, end):
                return [dict(timestamp_ms=start+30*60000, open=100, high=100, low=100, close=100)]
            def funding(self, pair, start, end):
                return []
        with patch('trader.research.kucoin_replay.radar', side_effect=scan):
            result = run_window(HistoricalMemory(), START, START+HOUR,
                                {'historical_candle_only': True})
        self.assertEqual(result['modeled_metrics']['completed_grids'], 0)
        self.assertGreater(result['coverage']['execution_missing_minutes'], 0)
        self.assertTrue(all(row['kind'] != 'grid_close' for row in result['ledger']))

    def test_same_asof_radar_cache_is_exact_and_refreshes_after_running_set_changes(self):
        from types import SimpleNamespace
        from trader.research.kucoin_replay import _scan
        report = dict(bots=[], hourly_radar=[], coverage={'complete': True, 'reasons': []})
        fixed = dict(radar=[candidate('A'), candidate('B')], rejected=[], coverage={'observed': 3})
        with patch('trader.research.kucoin_replay.radar', return_value=fixed) as scan_call:
            first, _ = _scan(MemorySnapshot(), report, START, {})
            first.pop()
            second, _ = _scan(MemorySnapshot(), report, START, {})
            self.assertEqual(scan_call.call_count, 1)
            self.assertEqual(len(second), 2)
            report['bots'] = [dict(active=True, state=SimpleNamespace(status='running', config=SimpleNamespace(pair='A')))]
            _scan(MemorySnapshot(), report, START, {})
            self.assertEqual(scan_call.call_count, 2)

    @patch('trader.research.kucoin_replay.radar', side_effect=scan)
    def test_realized_grid_income_rates_exclude_seed_pnl_and_use_window_hours(self, unused):
        snapshot = MemorySnapshot()
        original = snapshot.candles
        snapshot.candles = lambda pair, start, end: [dict(row, low=97, high=103)
                                                    for row in original(pair, start, end)]
        result = run_window(snapshot, START, START+2*HOUR)
        metrics = result['metrics']
        self.assertGreater(metrics['grid_net_profit'], 0)
        self.assertAlmostEqual(metrics['grid_income_per_hour'], metrics['grid_net_profit']/2)
        self.assertAlmostEqual(metrics['grid_income_per_day'], metrics['grid_net_profit']*12)
