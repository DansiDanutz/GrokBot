import unittest
from statistics import median
from trader.strategies.grid_count_search import cycle_economics, search_grid_counts

MINUTE = 60_000
ASOF = 10080 * MINUTE
FUNDING = {'rate': .0001, 'interval_ms': 8 * 60 * MINUTE, 'observed_at_ms': ASOF}


def bars(pattern=(100.5, 99.5, 100.5, 101.5, 100.5, 99.5)):
    return [dict(timestamp_ms=i*MINUTE, open=pattern[i % len(pattern)],
                 high=pattern[i % len(pattern)], low=pattern[i % len(pattern)],
                 close=pattern[i % len(pattern)]) for i in range(10080)]


def search(rows=None, **overrides):
    args = dict(asof_ms=ASOF, direction='long', quantity_for_count=lambda n: 1.,
                tick_size=.01, funding=FUNDING, parameters={'min_grids': 4, 'max_grids': 4})
    args.update(overrides)
    return search_grid_counts(bars() if rows is None else rows, 98., 102., **args)


class CycleEconomicsTests(unittest.TestCase):
    def test_actual_cash_funding_sign_and_credit_not_rescuing_admission(self):
        funding = dict(FUNDING, rate=.01)
        long = cycle_economics(2, 100, 101, 'long', median_hold_ms=4*60*MINUTE, funding=funding)
        short = cycle_economics(2, 100, 101, 'short', median_hold_ms=4*60*MINUTE, funding=funding)
        self.assertAlmostEqual(long['two_fill_fees_usdt'], .2412)
        self.assertAlmostEqual(long['expected_funding_cost_usdt'], 1.01)
        self.assertAlmostEqual(short['expected_funding_credit_usdt'], 1.01)
        self.assertAlmostEqual(short['admission_net_usdt'], short['fee_net_usdt'])
        self.assertGreater(short['funding_adjusted_net_usdt'], short['admission_net_usdt'])
        tiny = cycle_economics(1, 100, 100.1, 'short', median_hold_ms=4*60*MINUTE, funding=funding)
        self.assertFalse(tiny['eligible'])

    def test_exact_twenty_percent_fee_buffer_and_break_even(self):
        # fee sum=.6, gross=.72 => net=.12, exactly 20% of both fees.
        exact = cycle_economics(1, 499.64, 500.36, 'long', median_hold_ms=MINUTE,
                                funding=dict(FUNDING, rate=0))
        self.assertTrue(exact['funded_economics_eligible'])
        below = cycle_economics(1, 499.640001, 500.359999, 'long', median_hold_ms=MINUTE,
                                funding=dict(FUNDING, rate=0))
        self.assertFalse(below['eligible'])
        zero = cycle_economics(1, 4997, 5003, 'long', fee_safety_ratio=0)
        self.assertFalse(zero['eligible'])

    def test_missing_inputs_are_nullable_provisional_not_zero(self):
        for kwargs in ({}, {'funding': FUNDING}, {'median_hold_ms': MINUTE}):
            row = cycle_economics(1, 100, 101, 'long', **kwargs)
            self.assertTrue(row['eligible'])
            self.assertFalse(row['funded_economics_eligible'])
            self.assertIsNone(row['funding_adjusted_net_usdt'])
        with self.assertRaises(ValueError):
            cycle_economics(1, 100, 101, 'long', funding=dict(FUNDING, interval_ms=0))


class GridCountSearchTests(unittest.TestCase):
    def test_paired_completions_have_holds_and_both_windows(self):
        result = search()
        self.assertEqual(len(result['candidates']), 1)
        best = result['candidates'][0]
        self.assertGreater(best['windows']['24h']['completed_cycles'], 0)
        self.assertEqual(best['windows']['7d']['completed_cycles'], 1680)
        self.assertEqual(best['windows']['7d']['median_hold_ms']['long'], 4*MINUTE)
        self.assertTrue(best['funded_economics_eligible'])
        self.assertEqual(best['selection_income_per_hour'], min(
            best['windows'][w]['funding_adjusted_income_per_hour'] for w in ('24h', '7d')))
        self.assertFalse(best['quantity_calibrated'])
        self.assertTrue(best['liquidation_estimated'])

    def test_monotonic_one_way_crossings_are_not_completed_cycles(self):
        result = search(bars((99.5,))[:-3] + [dict(row, timestamp_ms=(10077+i)*MINUTE)
                         for i, row in enumerate(bars((100.5, 101.5, 101.6))[:3])])
        candidate = result['candidates'][0]
        self.assertEqual(candidate['windows']['7d']['completed_cycles'], 0)
        self.assertFalse(candidate['funded_economics_eligible'])
        self.assertIsNone(candidate['windows']['7d']['funding_adjusted_income_per_hour'])

    def test_gap_and_boundary_censor_open_cycles(self):
        rows = bars((100.5,))
        rows[-3].update(close=99.5, high=99.5, low=99.5)
        rows[-2].update(synthetic=True, crossing_eligible=False)
        rows[-1].update(close=100.5, high=100.5, low=100.5)
        candidate = search(rows)['candidates'][0]
        self.assertEqual(candidate['windows']['7d']['completed_cycles'], 0)
        rows[-2].pop('synthetic')
        rows[-2].pop('crossing_eligible')
        rows[-2].update(low=98.)  # Observed wick touches stop: do not bridge it.
        candidate = search(rows)['candidates'][0]
        self.assertEqual(candidate['windows']['7d']['completed_cycles'], 0)
        self.assertGreater(candidate['boundary_censored_cycles'], 0)

    def test_neutral_legs_separate_and_funding_can_fail_one_leg(self):
        candidate = search(direction='neutral')['candidates'][0]
        self.assertGreater(candidate['windows']['7d']['completed_by_side']['short'], 0)
        self.assertEqual(set(candidate['economics']['7d']), {'long', 'short'})
        result = search(direction='neutral', funding=dict(FUNDING, rate=100))
        self.assertEqual(result['candidates'], [])
        self.assertIn('funding', result['rejected_counts'][0]['reason'])

    def test_top_three_income_order_and_exchange_count_cap(self):
        seen = []
        result = search(quantity_for_count=lambda n: seen.append(n) or 100/n,
                        parameters={'min_grids': 2, 'max_grids': 200, 'exchange_max_grids': 7})
        self.assertTrue(all(2 <= n <= 7 for n in seen))
        candidates = result['candidates']
        self.assertEqual(len(candidates), 3)
        self.assertEqual([r['selection_income_per_hour'] for r in candidates],
                         sorted((r['selection_income_per_hour'] for r in candidates), reverse=True))
        for row in candidates:
            self.assertAlmostEqual(row['step']/.01, round(row['step']/.01))

    def test_unknown_funding_offer_and_sizing_rejection(self):
        candidate = search(funding=None)['candidates'][0]
        self.assertTrue(candidate['provisional'])
        self.assertIsNone(candidate['funding_adjusted_income_per_hour'])
        self.assertEqual(candidate['ranking_basis'], 'fee_only_provisional_estimate')
        result = search(quantity_for_count=lambda n: {'eligible':False, 'reason':'unsafe liquidation'})
        self.assertEqual(result['candidates'], [])
        self.assertEqual(result['rejected_counts'][0]['reason'], 'unsafe liquidation')

    def test_independent_interval_reference_matches_pair_counts_and_holds(self):
        # Slow reference checks each interval independently, unlike the event
        # index traversal in production. No intraminute path is invented.
        rows = bars((100.5, 99.5, 101.5, 100.5, 99.5, 100.5, 101.5))
        rows[9000]['synthetic'] = True
        rows[9020]['low'] = 98.
        rows[9030]['crossing_eligible'] = False
        for direction in ('long', 'short', 'neutral'):
            candidate = search(rows, direction=direction)['candidates'][0]
            for side in (('long','short') if direction == 'neutral' else (direction,)):
                expected = {'24h':[], '7d':[]}
                for buy in (98., 99., 100., 101.):
                    sell, began, previous = buy+1, None, None
                    for row in rows:
                        when, price = row['timestamp_ms']+MINUTE, row['close']
                        if row.get('synthetic'):
                            began, previous = None, None
                            continue
                        if row['low'] <= 98 or row['high'] >= 102:
                            began, previous = None, None
                            continue
                        if row.get('crossing_eligible') is False:
                            began, previous = None, None
                        if previous is not None:
                            opened = previous > buy >= price if side == 'long' else previous < sell <= price
                            closed = previous < sell <= price if side == 'long' else previous > buy >= price
                            if closed and began is not None:
                                for label, hours in (('24h',24),('7d',168)):
                                    if when > ASOF-hours*60*MINUTE:
                                        expected[label].append(when-began)
                                began = None
                            if opened and began is None:
                                began = when
                        previous = price
                for label in expected:
                    self.assertEqual(candidate['windows'][label]['completed_by_side'][side], len(expected[label]))
                    self.assertEqual(candidate['windows'][label]['median_hold_ms'][side], median(expected[label]))

    def test_two_hundred_hard_cap_and_fee_pruning_before_quantity_callback(self):
        called = []
        result = search(quantity_for_count=lambda n: called.append(n) or 1.,
                        parameters={'max_grids':999})
        self.assertEqual(result['count_limits']['max'], 200)
        self.assertEqual(result['evaluated_count'], 199)
        self.assertLess(max(called), 200)
        self.assertTrue(any('before funding' in row['reason'] for row in result['rejected_counts']))

    def test_empty_leg_holds_do_not_borrow_neutral_other_leg_median(self):
        rows = bars((100.5,))
        for offset, price in enumerate((99.5,101.5)):
            rows[-2+offset].update(close=price, low=price, high=price)
        row = search(rows,direction='neutral')['candidates'][0]
        self.assertEqual(row['windows']['7d']['completed_by_side'], {'long':1,'short':0})
        self.assertIsNone(row['economics']['7d']['short']['median_hold_ms'])
        self.assertFalse(row['funded_economics_eligible'])

    def test_validation_coverage_future_and_duplicates(self):
        rows = bars()
        with self.assertRaises(ValueError):
            search(rows + [dict(rows[-1], close=100)])
        with self.assertRaises(ValueError):
            search(funding=dict(FUNDING, observed_at_ms=ASOF+1))
        result = search(rows[:9575])
        self.assertEqual(result['candidates'], [])
        self.assertFalse(result['coverage']['eligible'])
        result = search(rows[:9576])
        self.assertTrue(result['coverage']['eligible'])
        with self.assertRaises(ValueError):
            search(parameters={'max_grids':True})


if __name__ == '__main__':
    unittest.main()
