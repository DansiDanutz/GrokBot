"""Offline tests for the skipped-decision recorder (trader.autopilot.counterfactual).

Pure builders plus a tempdir JSONL writer; never reads or writes live runtime files.
"""
import json
from pathlib import Path
import tempfile
import unittest

from trader.autopilot import counterfactual, policy
from trader.tests.test_autopilot_policy import radar, row

DAY_MS = 86_400_000
# 2026-09-13T00:00:00Z, so the daily file name is stable regardless of local time.
NOW = 1_789_257_600_000


def skip_event(symbol, direction, blocks, ts_ms=NOW, **extra):
    event = dict(ts_ms=ts_ms, bot_id=0, symbol=symbol, type='DECISION', action='skip',
                 direction=direction, radar_direction=direction, radar_score=10.0,
                 expected_grids_per_hour=14.0, range_width_pct=50.0, funding_rate=0.0,
                 kucoin_ok=1, rule_blocks=sorted(blocks))
    event.update(extra)
    return event


class CandidateTests(unittest.TestCase):
    def test_policy_blocks_become_candidates_with_an_exact_spec(self):
        rows = [row('A')]
        events = [skip_event('A', 'LONG', [policy.DECISION_RULES['bot_capacity']])]

        built = counterfactual.candidates(rows, events, 'scan-1', NOW)

        self.assertEqual(len(built), 1)
        record = built[0]
        self.assertEqual((record['symbol'], record['direction'], record['scan_id']),
                         ('A', 'LONG', 'scan-1'))
        self.assertEqual(record['rule_blocks'], [policy.DECISION_RULES['bot_capacity']])
        expected, _ = policy.profile(rows[0], 'LONG', 0)
        self.assertEqual(record['spec'], expected)
        for key in ('price', 'range_low', 'range_high', 'grids', 'grid_interval', 'leverage',
                    'notional_usdt', 'funding_pct', 'expected_grids_per_hour', 'rank_score',
                    'score', 'ts_ms'):
            self.assertIn(key, record)

    def test_every_policy_class_is_replayable(self):
        names = ('bot_capacity', 'duplicate_symbol', 'cooldown', 'direction_cap', 'major_cap',
                 'movers_cap', 'learned_trend_alignment', 'learned_symbol_cooldown',
                 'learned_min_hold')
        rows = [row('A')]
        for name in names:
            events = [skip_event('A', 'LONG', [policy.DECISION_RULES[name]])]
            self.assertEqual(len(counterfactual.candidates(rows, events, 'scan-1', NOW)), 1, name)

    def test_structural_blocks_disqualify_the_candidate(self):
        rows = [row('A')]
        for name in ('range_not_verified', 'invalid_profile', 'invalid_layout',
                     'missing_liquidity', 'missing_live_price', 'low_grid_rate',
                     'insufficient_equity'):
            blocks = [policy.DECISION_RULES['bot_capacity'], policy.DECISION_RULES[name]]
            events = [skip_event('A', 'LONG', blocks)]
            self.assertEqual(counterfactual.candidates(rows, events, 'scan-1', NOW), [], name)

    def test_non_skip_and_empty_blocks_are_ignored(self):
        rows = [row('A')]
        opened = skip_event('A', 'LONG', [])
        opened['action'] = 'open'
        empty = skip_event('A', 'LONG', [])
        other = dict(ts_ms=NOW, bot_id=0, symbol='A', type='OPEN', price=100.0, equity=1000.0)
        self.assertEqual(counterfactual.candidates(rows, [opened, empty, other], 's', NOW), [])

    def test_dedupes_by_scan_symbol_and_direction(self):
        rows = [row('A')]
        blocks = [policy.DECISION_RULES['bot_capacity']]
        events = [skip_event('A', 'LONG', blocks),
                  skip_event('A', 'LONG', [policy.DECISION_RULES['direction_cap']]),
                  skip_event('A', 'NEUTRAL', blocks)]

        built = counterfactual.candidates(rows, events, 'scan-1', NOW)

        self.assertEqual([(r['symbol'], r['direction']) for r in built],
                         [('A', 'LONG'), ('A', 'NEUTRAL')])

    def test_profile_failure_and_unknown_symbol_are_skipped(self):
        broken = row('A', step_pct=0.0, grids=0)
        events = [skip_event('A', 'LONG', [policy.DECISION_RULES['bot_capacity']]),
                  skip_event('MISSING', 'LONG', [policy.DECISION_RULES['bot_capacity']])]

        self.assertEqual(counterfactual.candidates([broken], events, 'scan-1', NOW), [])

    def test_inputs_are_never_mutated(self):
        rows, events = [row('A')], [skip_event('A', 'LONG', [policy.DECISION_RULES['cooldown']])]
        before = (json.dumps(rows, sort_keys=True), json.dumps(events, sort_keys=True))

        counterfactual.candidates(rows, events, 'scan-1', NOW)

        self.assertEqual((json.dumps(rows, sort_keys=True),
                          json.dumps(events, sort_keys=True)), before)

    def test_real_decision_pass_yields_only_policy_skips(self):
        report = radar(long=[row(f'L{i}') for i in range(9)])
        _, events = policy.decide(policy.new_state(0), report, {}, 0, 'scan-1')
        skips = [e for e in events if e.get('type') == 'DECISION' and e['action'] == 'skip']
        self.assertTrue(skips)

        built = counterfactual.candidates(report['rows'], events, 'scan-1', 0)

        self.assertTrue(built)
        allowed = counterfactual.POLICY_BLOCK_CODES
        for record in built:
            self.assertTrue(set(record['rule_blocks']) <= allowed)
            self.assertEqual(record['spec']['symbol'], record['symbol'])


class WriterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name).resolve() / 'counterfactual'

    def records(self):
        return counterfactual.candidates(
            [row('A')], [skip_event('A', 'LONG', [policy.DECISION_RULES['bot_capacity']])],
            'scan-1', NOW)

    def test_append_creates_a_private_daily_file(self):
        counterfactual.append(self.directory, self.records(), NOW)
        path = self.directory / 'candidates-2026-09-13.jsonl'

        self.assertTrue(path.exists())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(path.read_text().splitlines()[0])['symbol'], 'A')

    def test_append_is_additive_within_the_day_and_rotates_daily(self):
        counterfactual.append(self.directory, self.records(), NOW)
        counterfactual.append(self.directory, self.records(), NOW)
        counterfactual.append(self.directory, self.records(), NOW + DAY_MS)

        today = self.directory / 'candidates-2026-09-13.jsonl'
        tomorrow = self.directory / 'candidates-2026-09-14.jsonl'
        self.assertEqual(len(today.read_text().splitlines()), 2)
        self.assertEqual(len(tomorrow.read_text().splitlines()), 1)

    def test_record_writes_nothing_when_no_candidate_qualifies(self):
        written = counterfactual.record(self.directory, [row('A')], [], 'scan-1', NOW)

        self.assertEqual(written, [])
        self.assertFalse(list(self.directory.glob('*.jsonl')) if self.directory.exists() else [])

    def test_retain_lists_only_files_outside_the_window(self):
        names = ['candidates-2026-09-13.jsonl', 'candidates-2026-08-15.jsonl',
                 'candidates-2026-08-14.jsonl', 'notes.txt']

        expired = counterfactual.retain(names, NOW, days=30)

        self.assertEqual(expired, ['candidates-2026-08-14.jsonl'])

    def test_prune_deletes_expired_days_only(self):
        counterfactual.append(self.directory, self.records(), NOW - 40 * DAY_MS)
        counterfactual.append(self.directory, self.records(), NOW)

        deleted = counterfactual.prune(self.directory, NOW)

        self.assertEqual([Path(p).name for p in deleted], ['candidates-2026-08-04.jsonl'])
        self.assertEqual([p.name for p in sorted(self.directory.glob('*.jsonl'))],
                         ['candidates-2026-09-13.jsonl'])


if __name__ == '__main__':
    unittest.main()
