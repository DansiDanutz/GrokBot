import copy
import unittest

from paper_grid import public_autopilot
from trader.autopilot import policy


class AutopilotPublicTests(unittest.TestCase):
    def source(self):
        return policy.snapshot(policy.new_state(1000), 2000,
                               dict(heartbeat_ms=2000, tick_age_s=10,
                                    kucoin_ok=True, radar_age_min=1))

    def test_allowlist_preserves_health_groups_and_drops_private_fields(self):
        source = self.source()
        source['private'] = 'do not publish'
        before = copy.deepcopy(source)
        result = public_autopilot.safe(source)
        self.assertNotIn('private', result)
        self.assertEqual(result['equity'], 10000)
        self.assertEqual(result['tick_age_s'], 10)
        self.assertEqual(set(result['groups']), {'LONG', 'SHORT', 'NEUTRAL'})
        self.assertEqual(source, before)

    def test_close_reasons_project_as_a_closed_vocabulary(self):
        source = self.source()
        source['close_reasons'] = dict(RANGE_BREAK=dict(
            bots=40, wins=11, grid_profit=1865.89, close_pnl=-2541.81,
            fees=476.79, funding=-5.28, net=-1147.43))
        result = public_autopilot.safe(source)
        self.assertEqual(result['close_reasons']['RANGE_BREAK']['bots'], 40)
        self.assertAlmostEqual(result['close_reasons']['RANGE_BREAK']['close_pnl'], -2541.81)
        source['close_reasons'] = dict(SECRET_REASON=dict(
            bots=1, wins=0, grid_profit=0, close_pnl=0, fees=0, funding=0, net=0))
        with self.assertRaises(ValueError):
            public_autopilot.safe(source)
        source['close_reasons'] = dict(MAX_AGE=dict(
            bots=1, wins=2, grid_profit=0, close_pnl=0, fees=0, funding=0, net=0))
        with self.assertRaises(ValueError):
            public_autopilot.safe(source)

    def test_snapshot_aggregates_close_reasons_from_all_retained_closed_bots(self):
        from trader.autopilot.policy import new_state, snapshot
        state = new_state(1000)
        base = dict(symbol='RAYUSDTM', direction='LONG', opened_ms=1000, closed_ms=2000,
                    last_price=1.0, equity=1000.0, peak_equity=1000.0, orders=[], lines=[],
                    completed_grids=3, grids=10, grid_profit=30.0, realized_pnl=10.0,
                    unrealized_pnl=0.0, fees_paid=5.0, funding_paid=1.0)
        for bot_id, reason in enumerate(('RANGE_BREAK', 'RANGE_BREAK', 'MAX_AGE'), start=1):
            engine = dict(base, bot_id=bot_id, reason=reason)
            state['closed_bots'].append(dict(engine=engine, reserve_usdt=0.0,
                                             source_section='long', range_verified=1))
        view = snapshot(state, 3000, dict(heartbeat_ms=3000, tick_age_s=1,
                                          kucoin_ok=True, radar_age_min=1))
        reasons = view['close_reasons']
        self.assertEqual(reasons['RANGE_BREAK']['bots'], 2)
        self.assertEqual(reasons['MAX_AGE']['bots'], 1)
        # net = realized + unrealized - fees - funding = 10 - 5 - 1 = 4 per bot
        self.assertAlmostEqual(reasons['RANGE_BREAK']['net'], 8.0)
        # close_pnl separates the final position result from grid income
        self.assertAlmostEqual(reasons['RANGE_BREAK']['close_pnl'], -40.0)
        self.assertEqual(reasons['RANGE_BREAK']['wins'], 2)

    def test_curve_is_validated_before_downsampling_and_bounds_preserved(self):
        source = self.source()
        source['equity_curve'] = [[i, 10000+i] for i in range(3000)]
        result = public_autopilot.safe(source)
        self.assertEqual(len(result['equity_curve']), 2000)
        self.assertEqual(result['equity_curve'][-1], [2999, 12999])
        source['equity_curve'][100][1] = float('nan')
        with self.assertRaises(ValueError):
            public_autopilot.safe(source)

    def test_watchlist_codes_and_history_are_closed_vocabularies(self):
        source = self.source()
        row = dict(symbol='RAYUSDTM', direction='NEUTRAL', score=71, since_ms=1,
                   rank=1, score_parts=[dict(code='OSCILLATION', value=18.4, points=27.6)])
        source['watchlist']['core'] = [row]
        source['watchlist_history'] = [dict(ts_ms=1, type='PROMOTE', symbol='RAYUSDTM',
            score=71, replaced_symbol='SAGAUSDTM', replaced_score=52, margin=19)] * 50
        result = public_autopilot.safe(source)
        self.assertEqual(len(result['watchlist_history']), 48)
        self.assertEqual(result['watchlist']['core'][0]['score'], 71)
        row['score_parts'][0]['code'] = 'private text'
        with self.assertRaises(ValueError):
            public_autopilot.safe(source)

    def test_watchlist_publishes_rank_score_as_an_optional_non_negative_number(self):
        source = self.source()
        entry = dict(symbol='RAYUSDTM', direction='NEUTRAL', score=71, since_ms=1,
                     rank=1, score_parts=[], rank_score=12.5,
                     expected_grids_per_hour=18.4)
        source['watchlist']['core'] = [entry]
        published = public_autopilot.safe(source)['watchlist']['core'][0]
        self.assertEqual(published['rank_score'], 12.5)
        self.assertEqual(published['expected_grids_per_hour'], 18.4)
        # Absent on older entries, and never negative or free text.
        source['watchlist']['core'] = [{k: v for k, v in entry.items()
                                        if k not in ('rank_score', 'expected_grids_per_hour')}]
        absent = public_autopilot.safe(source)['watchlist']['core'][0]
        self.assertIsNone(absent['rank_score'])
        self.assertIsNone(absent['expected_grids_per_hour'])
        for bad in (-1, 'private text', float('nan'), True):
            source['watchlist']['core'] = [dict(entry, rank_score=bad)]
            with self.assertRaises(ValueError):
                public_autopilot.safe(source)

    def test_rank_score_survives_the_whole_decide_to_publish_path(self):
        from trader.autopilot import policy
        from trader.tests.test_autopilot_policy import radar, row
        state, _ = policy.decide(policy.new_state(0),
                                 radar(long=[row('RAYUSDTM', rank_score=42.5)]), {}, 0, 'a')
        view = policy.snapshot(state, 0, dict(heartbeat_ms=0, tick_age_s=0,
                                              kucoin_ok=True, radar_age_min=1))
        published = public_autopilot.safe(view)['watchlist']['core'][0]
        self.assertEqual(published['symbol'], 'RAYUSDTM')
        self.assertEqual(published['rank_score'], 42.5)
        self.assertGreater(published['expected_grids_per_hour'], 0)

    def test_rejects_bool_nonfinite_oversized_and_invalid_symbols(self):
        for key, value in [('equity', True), ('tick_age_s', float('inf'))]:
            source = self.source()
            source[key] = value
            with self.assertRaises(ValueError):
                public_autopilot.safe(source)
        source = self.source()
        source['open_bots'] = [{}] * 6
        with self.assertRaises(ValueError):
            public_autopilot.safe(source)

    def test_events_strip_unknown_numbers_and_reject_free_text(self):
        event = dict(ts_ms=1, bot_id=1, symbol='RAYUSDTM', type='FILL', price=1.5,
                     private_number=123)
        result = public_autopilot.events([event])
        self.assertNotIn('private_number', result[0])
        event['price'] = 'private text'
        with self.assertRaises(ValueError):
            public_autopilot.events([event])
        with self.assertRaises(ValueError):
            public_autopilot.events([event] * 501)

    def test_decision_events_are_explicitly_projected(self):
        event = dict(ts_ms=1, bot_id=1, symbol='RAYUSDTM', type='DECISION',
                     action='skip', direction='LONG', radar_direction='TURNING-UP',
                     radar_score=81, expected_grids_per_hour=12.5,
                     range_width_pct=8.5, funding_rate=-.01, kucoin_ok=1,
                     rule_blocks=[13, 16], private_number=99)
        result = public_autopilot.events([event])[0]
        self.assertEqual(result['rule_blocks'], [13, 16])
        self.assertEqual(result['action'], 'skip')
        self.assertNotIn('private_number', result)

    def test_open_and_closed_bot_details_are_projected_without_engine_orders(self):
        from trader.papergrid.engine import open_bot, close_bot
        source = self.source()
        opened = open_bot(dict(bot_id=1, symbol='RAYUSDTM', direction='LONG',
            range_low=1, range_high=2, step_pct=.8, grids=20, notional_usdt=1000,
            leverage=3, funding_pct=0), 1.5, 1000)
        closed, _ = close_bot(opened, 1.6, 2000, 'MANUAL')
        source['open_bots'] = [opened]
        source['closed_bots'] = [closed]
        result = public_autopilot.safe(source)
        self.assertNotIn('orders', result['open_bots'][0])
        self.assertEqual(result['closed_bots'][0]['reason'], 'MANUAL')
        self.assertEqual(len(result['groups']['LONG']['open_bots']), 1)
        closed['reason'] = 'private reason'
        with self.assertRaises(ValueError):
            public_autopilot.safe(source)

    def test_overflow_truncates_newest_closed_bots_and_watchlist_with_note(self):
        source = self.source()
        source['closed_bots'] = [dict(symbol='RAYUSDTM', direction='LONG',
                                      closed_ms=1000 + 100 * i) for i in range(25)]
        source['watchlist']['core'] = [dict(symbol='RAYUSDTM', direction='NEUTRAL',
            score=50 + i, since_ms=1, rank=i + 1) for i in range(7)]
        result = public_autopilot.safe(source)
        self.assertEqual(result['truncated'], {'closed_bots': 5, 'watchlist': 2})
        self.assertEqual([row['closed_ms'] for row in result['closed_bots']],
                         [1500 + 100 * i for i in range(20)])
        self.assertEqual(len(result['groups']['LONG']['closed_bots']), 20)
        self.assertEqual([row['rank'] for row in result['watchlist']['core']], [1, 2, 3, 4, 5])

    def test_untruncated_payload_reports_zero_counts(self):
        result = public_autopilot.safe(self.source())
        self.assertEqual(result['truncated'], {'closed_bots': 0, 'watchlist': 0})

    def test_hourly_buckets_pass_through_and_are_validated(self):
        source = self.source()
        source['equity_hourly'] = [[3_600_000, 10_000, 10_010, 9_990, 10_005],
                                   [7_200_000, 10_005, 10_005, 9_980, 9_980]]
        result = public_autopilot.safe(source)
        self.assertEqual(result['equity_hourly'], source['equity_hourly'])
        # Closed-vocabulary style: malformed buckets are rejected, not republished.
        for bad in ([[3_600_000, 10_000, 10_010, 9_990]],            # wrong arity
                    [[3_600_000, 10_000, 10_010, 9_990, float('nan')]],  # nonfinite
                    [[3_600_000, 10_000, 9_990, 10_010, 10_005]],    # high below low
                    [[3_600_000, 10_000, 10_010, 9_990, 11_000]],    # close outside range
                    [[7_200_000, 1, 2, 3, 4], [3_600_000, 1, 2, 3, 4]],  # unordered
                    [[True, 1, 2, 3, 4]]):                           # bool stamp
            source['equity_hourly'] = bad
            with self.assertRaises(ValueError):
                public_autopilot.safe(source)

    def test_hourly_buckets_are_capped_at_seven_days(self):
        source = self.source()
        source['equity_hourly'] = [[i * 3_600_000, 10_000, 10_001, 9_999, 10_000]
                                   for i in range(1, 300)]
        result = public_autopilot.safe(source)
        self.assertEqual(len(result['equity_hourly']), 168)
        self.assertEqual(result['equity_hourly'][-1][0], 299 * 3_600_000)
