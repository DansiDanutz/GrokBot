"""Offline fill-history accounting; no broker, database, or provider calls."""
import copy
import unittest

from trader.research.funded_cycles import funded_cycles

HOUR = 3_600_000


def fill(event_id, timestamp_ms, kind='grid_open', slot=1, side=1,
         quantity=2, price=100, fee=.12, gross_pnl=0, bot_id='a', **extra):
    return dict(event_id=event_id, timestamp_ms=timestamp_ms, kind=kind,
                slot=slot, side=side, quantity=quantity, price=price, fee=fee,
                gross_pnl=gross_pnl, bot_id=bot_id,
                completed_grid=kind == 'grid_close', **extra)


def funding(at, rate=.01, mark=100, bot_id='a', kind='funding', **extra):
    return dict(event_id=f'funding:{at}', timestamp_ms=at, kind=kind,
                rate=rate, mark=mark, bot_id=bot_id, **extra)


def close(event_id=2, at=2*HOUR, **extra):
    return fill(event_id, at, kind='grid_close', side=-1, price=110,
                fee=.132, gross_pnl=20, **extra)


class FundedCyclesTests(unittest.TestCase):
    def test_signed_funding_and_actual_serialized_fees(self):
        ledger = [fill(1, 0, fee=.25), funding(HOUR), close()]
        result = funded_cycles(ledger, 0, 2*HOUR, coverage_known=True)
        row = result['completed'][0]
        self.assertAlmostEqual(row['allocated_funding'], -2)
        self.assertAlmostEqual(row['net'], 17.618)
        self.assertEqual(row['net'], row['modeled_net'])
        self.assertEqual(result['summary']['completed_positive_net'], 1)
        self.assertAlmostEqual(result['summary']['grid_income_per_hour'], 8.809)

    def test_neutral_zero_total_still_allocates_opposite_leg_cash(self):
        ledger = [fill(1, 0), fill(2, 0, slot=2, side=-1),
                  funding(HOUR, cash=0, modeled_cash=0), close(),
                  fill(3, 2*HOUR, kind='grid_close', slot=2, side=1,
                       price=90, fee=.108, gross_pnl=20)]
        # Fill ids are scoped to a bot and must be unique.
        ledger[3]['event_id'] = 4
        rows = funded_cycles(ledger, 0, 2*HOUR, True)['completed']
        self.assertEqual([r['allocated_funding'] for r in rows], [-2, 2])
        self.assertAlmostEqual(sum(r['net'] for r in rows), 39.52)

    def test_short_negative_rate_pays_funding(self):
        rows = [fill(1, 0, side=-1), funding(HOUR, rate=-.01),
                fill(2, 2*HOUR, kind='grid_close', side=1, price=90, fee=.108, gross_pnl=20)]
        self.assertEqual(funded_cycles(rows, 0, 2*HOUR, True)['completed'][0]['allocated_funding'], -2)

    def test_seed_cash_is_not_counted_as_grid_income(self):
        rows = [fill(1, 0, kind='seed'), funding(HOUR),
                fill(2, 2*HOUR, kind='seed_close', side=-1, price=110, fee=.132, gross_pnl=20)]
        result = funded_cycles(rows, 0, 2*HOUR, True)
        self.assertEqual(result['completed'], [])
        self.assertEqual(result['summary']['grid_income'], 0)
        self.assertEqual(result['closed'][0]['allocated_funding'], -2)
        self.assertTrue(result['closed'][0]['seed'])

    def test_each_terminal_close_releases_held_funding(self):
        for kind in ('stop_loss', 'replacement', 'liquidation'):
            rows = [fill(1, 0), funding(HOUR),
                    fill(2, 2*HOUR, kind=kind, side=-1, price=90, gross_pnl=-20),
                    funding(3*HOUR)]
            result = funded_cycles(rows, 0, 4*HOUR, True)
            self.assertEqual(result['completed'], [])
            self.assertEqual(result['closed'][0]['allocated_funding'], -2)
            self.assertEqual(result['open'], [])

    def test_full_history_opens_before_six_hour_window(self):
        rows = [fill(1, 0), funding(HOUR), close(at=8*HOUR)]
        result = funded_cycles(rows, 2*HOUR, 8*HOUR, True)
        self.assertEqual(len(result['completed']), 1)
        self.assertEqual(result['completed'][0]['opening_timestamp_ms'], 0)
        self.assertEqual(result['completed'][0]['allocated_funding'], -2)
        self.assertAlmostEqual(result['summary']['grid_income_per_hour'], 17.748/6)
        self.assertEqual((result['start_ms'], result['end_ms']), (2*HOUR, 8*HOUR))

    def test_close_cutoff_is_exclusive_and_end_inclusive(self):
        rows = [fill(1, 0), close(at=HOUR), fill(3, HOUR), close(4, 2*HOUR)]
        result = funded_cycles(rows, HOUR, 2*HOUR, True)
        self.assertEqual([r['closing_event_id'] for r in result['completed']], [4])

    def test_same_timestamp_order_is_preserved(self):
        rows = [fill(1, 0), funding(HOUR), close(at=HOUR),
                fill(3, HOUR), close(4, 2*HOUR)]
        self.assertEqual([r['allocated_funding'] for r in funded_cycles(rows, 0, 2*HOUR, True)['completed']], [-2, 0])
        legacy = [fill(1, 0), close(at=HOUR), funding(HOUR)]
        self.assertEqual(funded_cycles(legacy, 0, HOUR, True)['completed'][0]['allocated_funding'], 0)

    def test_no_future_funding_changes_prior_close(self):
        rows = [fill(1, 0), close(at=HOUR), funding(2*HOUR, rate=99)]
        result = funded_cycles(rows, 0, HOUR, True)
        self.assertEqual(result['completed'][0]['allocated_funding'], 0)

    def test_duplicate_identical_rows_dedup_but_conflict_rejects(self):
        rows = [fill(1, 0), funding(HOUR), close()]
        self.assertEqual(funded_cycles(rows+copy.deepcopy(rows), 0, 2*HOUR, True),
                         funded_cycles(rows, 0, 2*HOUR, True))
        with self.assertRaisesRegex(ValueError, 'conflicting'):
            funded_cycles(rows+[dict(rows[0], fee=.4)], 0, 2*HOUR)

    def test_bot_id_scopes_ids_slots_and_funding(self):
        rows = [fill(1, 0), fill(1, 0, bot_id='b'), funding(HOUR),
                close(), close(bot_id='b')]
        self.assertEqual([r['allocated_funding'] for r in funded_cycles(rows, 0, 2*HOUR, True)['completed']], [-2, 0])

    def test_missing_coverage_retains_model_but_never_verified_zero(self):
        result = funded_cycles([fill(1, 0), close()], 0, 2*HOUR)
        self.assertIsNone(result['summary']['grid_income'])
        self.assertIsNone(result['completed'][0]['net'])
        self.assertAlmostEqual(result['completed'][0]['modeled_net'], 19.748)
        self.assertEqual(result['summary']['modeled_positive'], 1)
        self.assertEqual(result['summary']['completed_unknown_net'], 1)

    def test_missing_opening_fee_or_funding_inputs_are_unknown_not_zero(self):
        cases = [[close()], [dict(fill(1, 0), fee=None), close()],
                 [dict(fill(1, 0), price=None), close()],
                 [fill(1, 0), dict(close(), price=None)],
                 [fill(1, 0), funding(HOUR, mark=None), close()],
                 [fill(1, 0), funding(HOUR, rate=None, kind='funding_unknown', modeled_cash=0), close()],
                 [fill(1, 0), dict(close(), fee=None)]]
        for rows in cases:
            with self.subTest(rows=rows):
                result = funded_cycles(rows, 0, 2*HOUR, True)
                self.assertIsNone(result['completed'][0]['net'])
                self.assertIsNone(result['completed'][0]['modeled_net'])
                self.assertIsNone(result['summary']['modeled_grid_income'])
                self.assertEqual(result['summary']['modeled_unknown'], 1)

    def test_estimated_mark_preserves_modeled_amount_and_flag(self):
        rows = [fill(1, 0), funding(HOUR, kind='funding_estimated', cash=None,
                                   modeled_cash=-2, mark_carried_forward=True), close()]
        result = funded_cycles(rows, 0, 2*HOUR, True)
        row = result['completed'][0]
        self.assertTrue(row['estimated'])
        self.assertIsNone(row['net'])
        self.assertAlmostEqual(row['modeled_net'], 17.748)
        self.assertEqual(result['summary']['completed_estimated'], 1)
        self.assertEqual(result['summary']['modeled_positive'], 1)

    def test_zero_profit_is_nonpositive_using_serialized_decimal_amounts(self):
        rows = [fill(1, 0, fee=.1), dict(close(), gross_pnl=.3, fee=.2)]
        result = funded_cycles(rows, 0, 2*HOUR, True)
        self.assertEqual(result['completed'][0]['net'], 0)
        self.assertEqual(result['summary']['completed_nonpositive_net'], 1)
        self.assertEqual(result['summary']['modeled_nonpositive'], 1)

    def test_funding_conservation_with_open_seed_grid_and_closed_grid(self):
        rows = [fill(1, 0), fill(2, 0, slot=2, kind='seed', side=-1),
                funding(HOUR), close(3), funding(3*HOUR)]
        result = funded_cycles(rows, 0, 4*HOUR, True)
        self.assertEqual(result['closed'][0]['allocated_funding'], -2)
        self.assertEqual(result['open'][0]['allocated_funding'], 4)
        self.assertEqual(result['summary']['allocated_funding'], 2)

    def test_invalid_matching_closure_cannot_silently_consume_inventory(self):
        for replacement in ({'side': 1}, {'quantity': 3}):
            with self.assertRaises(ValueError):
                funded_cycles([fill(1, 0), dict(close(), **replacement)], 0, 2*HOUR, True)
        with self.assertRaises(ValueError):
            funded_cycles([fill(1, 0), fill(2, HOUR)], 0, 2*HOUR, True)

    def test_serialized_grid_engine_cycles_reconcile_fee_net_and_funding(self):
        from dataclasses import asdict
        from trader.strategies.grid_types import GridConfig
        from trader.strategies.kucoin_grid import create_bot, advance
        for direction in ('long', 'short', 'neutral'):
            state = create_bot(GridConfig(pair='TESTUSDTM', low=90, high=110,
                grids=10, direction=direction, quantity=1, entry_price=100,
                range_exit_stop_pct=None), 100, 0)
            rows = [dict(asdict(event), bot_id='a') for event in state.fill_events]
            for step, price in enumerate((104, 96, 104, 96), 1):
                state = advance(state, price, step*60_000, auto_funding=False)
                rows.extend(dict(asdict(event), bot_id='a') for event in state.fill_events)
            result = funded_cycles(rows, 0, 4*60_000, True)
            self.assertGreater(len(result['completed']), 0)
            self.assertEqual(len(result['completed']), state.completed_grids)
            self.assertAlmostEqual(result['summary']['grid_income'], state.grid_net_profit)
            funded = rows+[funding(150_000, rate=.001, mark=100)]
            result = funded_cycles(funded, 0, 4*60_000, True)
            for row in result['completed']:
                expected = (-row['side']*row['quantity']*.1
                            if row['opening_timestamp_ms'] < 150_000 < row['timestamp_ms'] else 0)
                self.assertAlmostEqual(row['allocated_funding'], expected)
                self.assertAlmostEqual(row['net'], row['gross_pnl']-row['opening_fee']
                                       -row['closing_fee']+expected)

    def test_same_timestamp_funding_charges_only_inventory_already_open(self):
        rows = [funding(0), fill(1, 0), close(at=HOUR)]
        self.assertEqual(funded_cycles(rows, 0, HOUR, True)['completed'][0]['allocated_funding'], 0)

    def test_invalid_nonfinite_funding_is_unknown_even_when_rate_zero(self):
        for mark in (None, float('inf'), float('nan'), 0):
            rows = [fill(1, 0), funding(HOUR, rate=0, mark=mark), close()]
            self.assertIsNone(funded_cycles(rows, 0, 2*HOUR, True)['completed'][0]['modeled_net'])

    def test_inputs_unchanged_and_empty_window_rates_unknown(self):
        rows = [fill(1, 0), funding(HOUR), close()]
        before = copy.deepcopy(rows)
        funded_cycles(rows, 0, 2*HOUR, True)
        self.assertEqual(rows, before)
        result = funded_cycles([], 0, 0, True)
        self.assertIsNone(result['summary']['grid_income_per_hour'])
        with self.assertRaises(ValueError):
            funded_cycles([], 2, 1)


if __name__ == '__main__':
    unittest.main()
