"""Watchlist admission and persistence use offline fixtures only."""
from copy import deepcopy
import unittest

from trader.autopilot import policy
from trader.autopilot.storage import validate_event
from trader.tests.test_autopilot_policy import row, radar

HOUR = 3600000


def candidate(symbol, direction='LONG', **extra):
    return row(symbol, direction, spread_pct=.05, listing_age_days=30,
               snapshot_age_min=0, position_7d=.5, change_24h_pct=0, **extra)


class WatchlistIntegrationTests(unittest.TestCase):
    def test_core_only_five_total_borrow_to_four_and_snapshot(self):
        rows = [candidate(f'L{i}') for i in range(8)]
        report = radar(long=rows)
        state, _ = policy.decide(policy.new_state(0), report, {}, 0, '0')
        view = policy.snapshot(state, 0, {})
        core = {r['symbol'] for r in view['watchlist']['core']}
        self.assertEqual(core, {'L0', 'L1', 'L2', 'L3', 'L4'})
        self.assertEqual(len(state['open_bots']), 4)
        self.assertTrue({w['engine']['symbol'] for w in state['open_bots']} <= core)
        self.assertEqual(len(view['watchlist']['bench']), 3)
        self.assertEqual(view['watchlist_history'], [])
        self.assertEqual(view['watchlist_scan_id'], '0')

    def test_demoted_bot_continues_but_cannot_reopen(self):
        initial = [candidate(f'N{i}', 'NEUTRAL') for i in range(5)]
        report = radar(neutral=initial)
        state, _ = policy.decide(policy.new_state(0), report, {}, 0, '0')
        # Give the lowest-scoring occupied core seat a candidate >10 points ahead.
        victim = state['open_bots'][0]['engine']['symbol']
        revised = [dict(r, expected_grids_per_hour=1 if r['symbol'] == victim else 14)
                   for r in initial]
        report = radar(neutral=revised + [candidate('NEW', 'NEUTRAL', expected_grids_per_hour=20,
                                                     turnover_24h_usdt=30_000_000)])
        state, events = policy.decide(state, report, {}, 2*HOUR, str(2*HOUR))
        self.assertNotIn(victim, {r['symbol'] for r in state['watchlist']['core']})
        self.assertIn(victim, {w['engine']['symbol'] for w in state['open_bots']})
        self.assertTrue(any(e['type'] == 'PROMOTE' for e in events))
        for event in events:
            validate_event(event)
        wrapper = next(w for w in state['open_bots'] if w['engine']['symbol'] == victim)
        wrapper['signals'] = ['RANGE_BREAK']
        closed, _ = policy.decide(state, report, {}, 2*HOUR+1, str(2*HOUR))
        closed['cooldowns'] = {}
        again, _ = policy.decide(closed, report, {}, 2*HOUR+2, str(2*HOUR))
        self.assertNotIn(victim, {w['engine']['symbol'] for w in again['open_bots']})

    def test_candidate_outside_truncated_sections_can_open(self):
        a = candidate('BEST', expected_grids_per_hour=20)
        report = radar(long=[candidate('OTHER')])
        report['rows'].append(a)
        state, _ = policy.decide(policy.new_state(0), report, {}, 0, '0')
        self.assertIn('BEST', {w['engine']['symbol'] for w in state['open_bots']})

    def test_failed_filter_is_missing_and_same_scan_does_not_advance(self):
        state, _ = policy.decide(policy.new_state(0), radar(long=[candidate('A')]), {}, 0, '0')
        report = radar(long=[candidate('A', passes_liquidity=False)])
        once, _ = policy.decide(state, report, {}, HOUR, str(HOUR))
        twice, _ = policy.decide(once, report, {}, HOUR+1, str(HOUR))
        self.assertEqual(len(twice['watchlist']['core']), 1)
        dropped, _ = policy.decide(twice, report, {}, 2*HOUR, str(2*HOUR))
        self.assertEqual(dropped['watchlist']['core'], [])

    def test_regressed_scan_cannot_change_labels_or_openings(self):
        report = radar(long=[candidate('A')])
        state, _ = policy.decide(policy.new_state(0), report, {}, 2*HOUR, str(2*HOUR))
        old = radar(short=[candidate('A', 'SHORT')], long=[candidate('B')])
        unchanged, _ = policy.decide(state, old, {}, 2*HOUR+1, str(HOUR))
        self.assertEqual(unchanged['closed_bots'], [])
        self.assertEqual([w['engine']['symbol'] for w in unchanged['open_bots']], ['A'])
        unchanged['open_bots'][0]['signals'] = ['RANGE_BREAK']
        closed, _ = policy.decide(unchanged, old, {}, 2*HOUR+2, str(HOUR))
        self.assertEqual(closed['closed_bots'][0]['engine']['reason'], 'RANGE_BREAK')

    def test_watchlist_events_reject_unexpected_text(self):
        event = dict(ts_ms=0, bot_id=0, type='PROMOTE', symbol='A', score=80,
                     replaced_symbol='B', replaced_score=50, margin=30)
        self.assertEqual(validate_event(event), event)
        for bad in [dict(event, replaced_symbol='<script>'), dict(event, reason='free text')]:
            with self.assertRaises(ValueError):
                validate_event(bad)
        with self.assertRaises(ValueError):
            validate_event(dict(event, type='FILL'))
