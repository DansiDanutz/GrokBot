import unittest
from dataclasses import replace

from trader.research.kucoin_tracker import track_bars
from trader.strategies.kucoin_grid import advance, create_bot
from trader.strategies.grid_types import GridConfig

HOUR = 3_600_000
MINUTE = 60_000


def initial(direction='long', start=0, price=100, **changes):
    config = GridConfig(pair='TESTUSDTM', low=90, high=110, grids=10,
        quantity=1, investment=1000, reserved_margin=200, leverage=5,
        direction=direction, entry_price=price, range_exit_stop_pct=0, **changes)
    return create_bot(config, price, start)


def bars(start, end, price=100):
    return [dict(timestamp_ms=t, open=price, high=price, low=price, close=price)
            for t in range(start, end, MINUTE)]


def event(timestamp, rate=.001, **changes):
    return dict(dict(symbol='TESTUSDTM', timestamp_ms=timestamp, rate=rate), **changes)


def coverage(start, end):
    return dict(coverage_verified=True, start_ms=start, end_ms=end)


def recorded(state, candles, events=(), **kwargs):
    return track_bars(state, candles, events, funding_mode='recorded_events', **kwargs)


def settlements(result):
    return [row for row in result['ledger'] if row['kind'].startswith('funding')]


class RecordedFundingTests(unittest.TestCase):
    def test_legacy_default_is_unchanged_and_core_clock_can_be_disabled(self):
        state = initial()
        legacy = advance(state, 100, 8*HOUR, funding_rate=.001)
        self.assertAlmostEqual(legacy.funding, -.5)
        self.assertEqual(legacy, advance(state, 100, 8*HOUR, funding_rate=.001, auto_funding=True))
        disabled = advance(state, 100, 8*HOUR, funding_rate=.001, auto_funding=False)
        self.assertEqual(disabled.funding, 0)
        self.assertEqual(disabled.last_funding_ms, state.last_funding_ms)
        old = track_bars(state, bars(0, 8*HOUR), [event(8*HOUR)])
        explicit = track_bars(state, bars(0, 8*HOUR), [event(8*HOUR)], funding_mode='legacy_8h')
        self.assertEqual(old, explicit)

    def test_one_four_eight_hour_cadence_changes_use_only_actual_events(self):
        events = [event(HOUR), event(2*HOUR, -.001), event(6*HOUR, .002), event(14*HOUR, .003)]
        result = recorded(initial(), bars(0, 14*HOUR), events,
                          start_ms=0, funding_coverage=coverage(0, 14*HOUR))
        self.assertEqual([row['timestamp_ms'] for row in settlements(result)], [HOUR, 2*HOUR, 6*HOUR, 14*HOUR])
        self.assertAlmostEqual(result['state'].funding, -2.5)
        self.assertEqual(result['state'].last_funding_ms, 14*HOUR)
        self.assertEqual(result['summary']['funding_mode'], 'recorded_events')
        self.assertTrue(result['summary']['funding_coverage_complete'])
        self.assertEqual(result['summary']['funding'], result['state'].funding)

    def test_newly_created_bot_is_not_charged_at_equal_start(self):
        result = recorded(initial(start=HOUR), bars(HOUR, 2*HOUR),
                          [event(HOUR, .2), event(2*HOUR)], start_ms=HOUR)
        self.assertEqual([row['timestamp_ms'] for row in settlements(result)], [2*HOUR])
        self.assertAlmostEqual(result['state'].funding, -.5)

    def test_funding_precedes_orders_at_the_same_timestamp_and_ignores_future_rates(self):
        candle = dict(timestamp_ms=0, open=100, high=100, low=98, close=100)
        result = recorded(initial(), [candle], [event(20_000, .01), event(2*HOUR, 10)], start_ms=0)
        rows = result['ledger']
        funding_index = next(i for i, row in enumerate(rows) if row['kind'] == 'funding')
        order_index = next(i for i, row in enumerate(rows) if row['kind'] == 'grid_open')
        self.assertLess(funding_index, order_index)
        self.assertAlmostEqual(rows[funding_index]['cash'], -4.9)
        self.assertAlmostEqual(result['state'].funding, -4.9)
        self.assertEqual(len(settlements(result)), 1)

    def test_signed_funding_includes_both_neutral_legs(self):
        for direction, price, expected in (('long', 100, -.5), ('short', 100, .5), ('neutral', 101, .101)):
            with self.subTest(direction=direction):
                state = initial(direction, price=price)
                result = recorded(state, bars(0, HOUR, price), [event(HOUR)], start_ms=0)
                self.assertAlmostEqual(result['state'].funding, expected)
                if direction == 'neutral':
                    self.assertTrue(any(p.side == 1 for p in state.positions))
                    self.assertTrue(any(p.side == -1 for p in state.positions))

    def test_duplicate_events_are_idempotent_across_tracker_chunks(self):
        events = [event(HOUR), event(HOUR), event(2*HOUR), event(4*HOUR, -.002)]
        whole = recorded(initial(), bars(0, 4*HOUR), events, start_ms=0)
        first = recorded(initial(), bars(0, 2*HOUR), events, start_ms=0)
        second = recorded(first['state'], bars(2*HOUR, 4*HOUR), events, start_ms=0)
        self.assertEqual(whole['state'], second['state'])
        combined = first['ledger']+second['ledger']
        self.assertEqual(combined, whole['ledger'])
        ids = [row['event_id'] for row in combined]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(settlements(whole)), 3)

    def test_malformed_or_conflicting_recorded_events_reject_without_mutation(self):
        state = initial()
        invalid = ([event(HOUR), event(HOUR, .002)], [event(HOUR, float('nan'))],
            [event(HOUR, True)], [event('hour')], [event(-1)], [event(HOUR, symbol='OTHER')],
            [dict(timestamp_ms=HOUR, rate=.001)], [None])
        for events in invalid:
            with self.subTest(events=events), self.assertRaises(ValueError):
                recorded(state, bars(0, HOUR), events, start_ms=0)
        self.assertEqual(state.funding, 0)

    def test_missing_marks_use_carried_estimates_without_synthetic_grid_fills(self):
        candles = bars(0, MINUTE)+bars(3*HOUR, 3*HOUR+MINUTE, 105)
        result = recorded(initial(), candles, [event(2*HOUR)], start_ms=0,
                          asof_ms=4*HOUR, historical_candle_only=True,
                          funding_coverage=coverage(0, 4*HOUR))
        funding = settlements(result)
        self.assertEqual(len(funding), 1)
        self.assertEqual(funding[0]['kind'], 'funding_estimated')
        self.assertIsNone(funding[0]['cash'])
        self.assertAlmostEqual(funding[0]['modeled_cash'], -.5)
        self.assertEqual(funding[0]['mark'], 100)
        self.assertFalse(result['summary']['funding_coverage_complete'])
        self.assertFalse(any(row['kind'] in ('grid_open', 'grid_close', 'seed_close') for row in result['ledger']))

    def test_gap_estimate_provenance_and_risk_marks_survive_later_chunks(self):
        first = recorded(initial(), bars(0, MINUTE), [event(2*HOUR)], start_ms=0,
                         asof_ms=3*HOUR, historical_candle_only=True,
                         funding_coverage=coverage(0, 4*HOUR))
        marks = [row for row in first['equity_timeline'] if row['timestamp_ms'] == 2*HOUR]
        self.assertTrue(marks)
        self.assertTrue(all(row['funding_mark_estimated'] for row in marks))
        self.assertAlmostEqual(marks[-1]['equity']-marks[0]['equity'], -.5)
        second = recorded(first['state'], bars(3*HOUR, 4*HOUR), [event(2*HOUR), event(4*HOUR)],
                          start_ms=0, funding_coverage=coverage(0, 4*HOUR))
        self.assertFalse(second['summary']['funding_coverage_complete'])
        self.assertIsNone(second['summary']['funding'])
        self.assertEqual(second['summary']['funding_estimated_settlements'], [2*HOUR])

    def test_empty_or_unverified_history_never_becomes_verified_zero(self):
        for proof in (None, {'complete': True}, coverage(0, 9*HOUR)):
            with self.subTest(proof=proof):
                result = recorded(initial(), bars(0, 9*HOUR), [], start_ms=0, funding_coverage=proof)
                self.assertEqual(result['state'].funding, 0)
                self.assertEqual(settlements(result), [])
                self.assertIsNone(result['summary']['funding'])
                self.assertFalse(result['summary']['funding_coverage_complete'])
                self.assertTrue(result['summary']['funding_unknown_reasons'])

    def test_modeled_coverage_metadata_never_upgrades_unknown_history(self):
        proof = dict(modeled_complete=True, verified=False, known=False, start_ms=0, end_ms=HOUR)
        result = recorded(initial(), bars(0, HOUR), [event(HOUR)], start_ms=0, funding_coverage=proof)
        self.assertTrue(result['summary']['funding_modeled_complete'])
        self.assertFalse(result['summary']['funding_history_known'])
        self.assertFalse(result['summary']['funding_coverage_complete'])
        self.assertIsNone(result['summary']['funding'])
        self.assertAlmostEqual(result['summary']['funding_modeled_cash'], -.5)

    def test_future_only_event_list_does_not_verify_zero_funding_for_prior_time(self):
        result = recorded(initial(), bars(0, HOUR), [event(8*HOUR, .2)], start_ms=0,
                          funding_coverage=coverage(0, HOUR))
        self.assertEqual(result['state'].funding, 0)
        self.assertFalse(result['summary']['funding_coverage_complete'])
        self.assertIsNone(result['summary']['funding'])
        self.assertEqual(settlements(result), [])

    def test_funding_can_liquidate_before_any_same_timestamp_grid_order(self):
        result = recorded(initial(), bars(0, HOUR), [event(HOUR, 3)], start_ms=0)
        self.assertEqual(result['state'].status, 'liquidated')
        rows = [row for row in result['ledger'] if row['timestamp_ms'] == HOUR]
        self.assertEqual(rows[0]['kind'], 'funding')
        self.assertTrue(all(row['kind'] == 'liquidation' for row in rows[1:]))
        self.assertAlmostEqual(result['state'].funding, -1500)
        self.assertTrue(result['summary']['liquidated'])

    def test_funding_then_exact_boundary_stop_does_not_charge_closed_inventory_later(self):
        candle = dict(timestamp_ms=0, open=100, high=100, low=90, close=95)
        result = recorded(initial(), [candle], [event(20_000, .01), event(HOUR, 3)],
                          start_ms=0, asof_ms=2*HOUR, historical_candle_only=True)
        self.assertEqual(result['state'].status, 'stopped')
        self.assertEqual(result['state'].price, 90)
        self.assertEqual([row['timestamp_ms'] for row in settlements(result)], [20_000])
        self.assertAlmostEqual(result['state'].funding, -4.5)

    def test_waiting_activation_does_not_reset_recorded_settlement_clock(self):
        from trader.strategies.kucoin_grid import settle_recorded_funding
        config = GridConfig(pair='TESTUSDTM', low=90, high=110, grids=10, quantity=1,
                            investment=1000, leverage=5, trigger=100, entry_price=100)
        state = create_bot(config, 99, 0)
        state = settle_recorded_funding(state, 99, HOUR, .1)
        self.assertEqual(state.funding, 0)
        activated = advance(state, 100, 9*HOUR, auto_funding=False)
        self.assertEqual(activated.last_funding_ms, HOUR)
        self.assertEqual(activated.funding, 0)

    def test_late_record_requires_replay_instead_of_retroactive_charge(self):
        first = recorded(initial(), bars(0, 2*HOUR), [], start_ms=0)
        with self.assertRaisesRegex(ValueError, 'predates|earlier'):
            recorded(first['state'], bars(2*HOUR, 3*HOUR), [event(HOUR)], start_ms=0)


if __name__ == '__main__':
    unittest.main()
