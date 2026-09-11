"""Offline two-tier watchlist transitions and hysteresis."""
import copy
import unittest

from trader.autopilot.watchlist import initial, is_older, update

HOUR = 3_600_000


def row(symbol, score, direction='LONG'):
    return {'symbol': symbol, 'direction': direction, 'score': score,
            'score_parts': [{'code': 'OSCILLATION', 'value': score, 'points': 1}]}


def rows():
    return [row(chr(65 + i), 80 - i * 5) for i in range(12)]


class WatchlistTests(unittest.TestCase):
    def test_strict_older_scan_detection(self):
        self.assertTrue(is_older('99', 100))
        self.assertFalse(is_older('100', 100))
        self.assertFalse(is_older(101, '100'))
        self.assertFalse(is_older('scan-a', 'scan-b'))
        self.assertFalse(is_older(1, None))
        self.assertFalse(is_older('NaN', 100))
        self.assertFalse(is_older('-Infinity', 100))

    def test_cold_start_sorted_dedup_bounded_and_pure(self):
        old = initial()
        inputs = rows() + [row('A', 1)]
        before = copy.deepcopy(inputs)
        state, events = update(old, inputs[::-1], 0, '100')
        self.assertEqual([x['symbol'] for x in state['core']], list('ABCDE'))
        self.assertEqual([x['symbol'] for x in state['bench']], list('FGHIJ'))
        self.assertEqual([x['rank'] for x in state['core']], [1, 2, 3, 4, 5])
        self.assertEqual(events, [])
        self.assertEqual(state['asof_ms'], 0)
        self.assertEqual(old, initial())
        self.assertEqual(inputs, before)

    def test_margin_and_exact_hold_boundary_one_swap(self):
        state, _ = update(initial(), rows(), 0, '1')
        candidates = rows() + [row('X', 99), row('Y', 98)]
        state, events = update(state, candidates, 2 * HOUR - 1, '2')
        self.assertEqual(events, [])
        state, events = update(state, candidates, 2 * HOUR, '3')
        self.assertEqual([e['type'] for e in events], ['DEMOTE', 'PROMOTE'])
        self.assertEqual(events[1]['symbol'], 'X')
        self.assertEqual(events[1]['replaced_symbol'], 'E')
        self.assertEqual(events[1]['margin'], 39)
        self.assertNotIn('Y', [x['symbol'] for x in state['core']])
        self.assertEqual(state['swap_times'], [2 * HOUR])
        self.assertEqual(next(x for x in state['core'] if x['symbol'] == 'X')['since_ms'], 2 * HOUR)

    def test_promotion_requires_full_ten_points(self):
        state, _ = update(initial(), rows(), 0, 1)
        state, events = update(state, rows()[:5] + [row('X', 69.99)], 2 * HOUR, 2)
        self.assertEqual(events, [])
        state, events = update(state, rows()[:5] + [row('X', 70)], 3 * HOUR, 3)
        self.assertEqual(events[-1]['margin'], 10)

    def test_repeated_or_older_scan_is_noop(self):
        state, _ = update(initial(), rows(), 0, '100')
        for scan in ['100', '99', 99, 100]:
            same, events = update(state, [], HOUR, scan)
            self.assertEqual(same, state)
            self.assertEqual(events, [])

    def test_two_misses_forces_drop_and_refill_without_hold(self):
        state, _ = update(initial(), rows(), 0, 1)
        missing = [x for x in rows() if x['symbol'] != 'A']
        state, events = update(state, missing, 1, 2)
        self.assertEqual(events, [])
        self.assertIn('A', [x['symbol'] for x in state['core']])
        state, events = update(state, missing, 2, 3)
        self.assertEqual([e['type'] for e in events], ['DROP', 'PROMOTE'])
        self.assertEqual(events[-1]['symbol'], 'F')
        self.assertEqual(events[-1]['replaced_symbol'], 'A')
        self.assertEqual(state['swap_times'], [2])
        self.assertNotIn('A', state['misses'])

    def test_returning_coin_resets_missing_count(self):
        state, _ = update(initial(), rows(), 0, 1)
        state, _ = update(state, rows()[1:], 1, 2)
        state, _ = update(state, rows(), 2, 3)
        state, events = update(state, rows()[1:], 3, 4)
        self.assertEqual(events, [])
        self.assertEqual(state['misses']['A'], 1)

    def test_forced_removals_fill_only_one_seat_per_scan(self):
        state, _ = update(initial(), rows(), 0, 1)
        remaining = rows()[2:] + [row('X', 100), row('Y', 99), row('Z', 98)]
        state, _ = update(state, remaining, 1, 2)
        state, events = update(state, remaining, 3 * HOUR, 3)
        self.assertEqual(sum(e['type'] == 'DROP' for e in events), 2)
        self.assertEqual(sum(e['type'] == 'PROMOTE' for e in events), 1)
        self.assertEqual(sum(e['type'] == 'DEMOTE' for e in events), 0)
        self.assertNotIn('Z', [x['symbol'] for x in state['core']])
        self.assertEqual(len(state['swap_times']), 1)
        self.assertEqual(len(state['core']), 4)
        state, events = update(state, remaining, 4 * HOUR, 4)
        self.assertEqual([e['type'] for e in events], ['PROMOTE'])
        self.assertEqual(events[0]['symbol'], 'Y')
        self.assertEqual(len(state['core']), 5)
        self.assertEqual(len(state['swap_times']), 2)

    def test_direction_and_score_updates_preserve_tier_since(self):
        state, _ = update(initial(), rows(), 10, 1)
        candidates = rows()
        candidates[0] = row('A', 81, 'SHORT')
        candidates[5] = row('F', 54, 'NEUTRAL')
        state, events = update(state, candidates, HOUR, 2)
        self.assertEqual({e['symbol'] for e in events}, {'A', 'F'})
        self.assertTrue(all(e['type'] == 'DIRECTION_CHANGE' for e in events))
        self.assertEqual(state['core'][0]['since_ms'], 10)
        self.assertEqual(state['core'][0]['score'], 81)
        self.assertEqual(state['bench'][0]['since_ms'], 10)

    def test_history_and_swap_count_retention(self):
        state, _ = update(initial(), rows(), 0, 1)
        state['swap_times'] = [0, 30 * 24 * HOUR]
        for i in range(2, 55):
            candidates = rows()
            candidates[0]['direction'] = 'SHORT' if i % 2 == 0 else 'LONG'
            state, _ = update(state, candidates, 31 * 24 * HOUR + i, i)
        self.assertEqual(len(state['history']), 48)
        self.assertEqual(state['swap_times'], [30 * 24 * HOUR])
        self.assertEqual(set(state['history'][-1]), {'ts_ms', 'type', 'symbol', 'score',
                         'replaced_symbol', 'replaced_score', 'margin'})

    def test_empty_cold_start_can_fill_later(self):
        state, _ = update(initial(), [], 0, 1)
        state, events = update(state, rows(), HOUR, 2)
        self.assertEqual(len(state['core']), 1)
        self.assertEqual(len(state['bench']), 5)
        self.assertEqual(sum(e['type'] == 'PROMOTE' for e in events), 1)
        self.assertEqual(state['swap_times'], [HOUR])
