"""Offline contracts for the agent-influence interface (T8).

Owner decision (2026-09-13): the review agents may influence entry decisions
directly, through one sanitized file, with a kill switch and a full audit trail.
An agent may reorder and veto. It may never manufacture an entry, and no
structural or risk gate is negotiable.
"""
import json
import os
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from contextlib import ExitStack
from unittest import mock

from trader.autopilot import influence, influence_audit, policy
from trader.autopilot.constants import (INFLUENCE_MAX_AGE_MIN, INFLUENCE_MAX_DELTA,
                                        INFLUENCE_MAX_TOTAL_DELTA, INFLUENCE_ERROR)
from trader.autopilot import counterfactual as autopilot_counterfactual
from trader.autopilot.storage import validate_event
from trader.review import rules as learned_rules
from trader.tests.test_autopilot_policy import radar, row

HOUR = 3_600_000
NOW = 1_789_200_000_000
AGENT = 'risk_sentinel'
SCOUT = 'x_setup_researcher'


def record(symbol='A', direction='LONG', verb='BOOST', delta=5.0, agent=AGENT,
           reason_code=1, evidence_ref='x-2026-09-13-001'):
    return dict(agent_id=agent, symbol=symbol, direction=direction, verb=verb,
                delta=delta, reason_code=reason_code, evidence_ref=evidence_ref)


def payload(*records, generated_at_ms=NOW, schema_version=influence.SCHEMA_VERSION):
    return dict(schema_version=schema_version, generated_at_ms=generated_at_ms,
                records=list(records))


def sections(**named):
    """Section mapping shaped like policy._candidate_sections output."""
    base = {name: [] for name in ('turning_up', 'long', 'turning_down',
                                  'short', 'neutral', 'movers')}
    base.update(named)
    return base


def set_rules(**kwargs):
    store = {'min_hold_hours_before_non_risk_close': None,
             'require_trend_alignment': False, 'symbol_cooldowns': {}}
    store.update(kwargs)
    return mock.patch.object(policy, '_learned_rules', return_value=store)


def skips(events, code):
    return [e for e in events if e['type'] == 'DECISION'
            and e['action'] == 'skip' and code in e['rule_blocks']]


def influences(events):
    return [e for e in events if e['type'] == 'DECISION' and e['action'] == 'influence']


class FileFixture(unittest.TestCase):
    """A real file on disk, so load() is exercised end to end."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'influence.json'

    def write(self, data, mode=0o600):
        self.path.write_text(json.dumps(data), encoding='utf-8')
        os.chmod(self.path, mode)
        return str(self.path)

    def load(self, data, now_ms=NOW, known=None):
        return influence.load(self.write(data), now_ms, known)


class LoadFailsClosedTests(FileFixture):
    def test_a_valid_file_loads(self):
        records, problems = self.load(payload(record()), known={'A'})
        self.assertEqual(problems, [])
        self.assertEqual(records, [record()])

    def test_a_missing_file_is_ignored(self):
        records, problems = influence.load(str(self.path / 'nope.json'), NOW)
        self.assertEqual((records, problems), ([], ['unreadable']))

    def test_an_unreadable_file_is_ignored(self):
        self.path.write_text('{not json', encoding='utf-8')
        self.assertEqual(influence.load(str(self.path), NOW), ([], ['unreadable']))

    def test_an_oversized_file_is_ignored(self):
        self.path.write_text('x' * (influence.MAX_BYTES + 1), encoding='utf-8')
        self.assertEqual(influence.load(str(self.path), NOW), ([], ['too_large']))

    def test_a_wrong_schema_version_is_ignored(self):
        self.assertEqual(self.load(payload(record(), schema_version=2))[1], ['schema_version'])

    def test_unknown_envelope_keys_are_ignored(self):
        data = dict(payload(record()), extra=1)
        self.assertEqual(self.load(data)[1], ['file_keys'])

    def test_an_unknown_agent_id_is_ignored(self):
        self.assertEqual(self.load(payload(record(agent='ceo')))[1],
                         ['record_0_unknown_agent'])

    def test_an_unknown_symbol_is_ignored(self):
        records, problems = self.load(payload(record(symbol='ZZZ')), known={'A', 'B'})
        self.assertEqual((records, problems), ([], ['record_0_unknown_symbol']))
        # Without a universe there is nothing to check the symbol against.
        self.assertEqual(self.load(payload(record(symbol='ZZZ')))[1], [])

    def test_a_malformed_symbol_is_ignored(self):
        self.assertEqual(self.load(payload(record(symbol='a-usdt')))[1],
                         ['record_0_invalid_symbol'])

    def test_a_non_finite_number_is_ignored(self):
        self.path.write_text(json.dumps(payload(record())).replace('5.0', 'NaN'),
                             encoding='utf-8')
        self.assertEqual(influence.load(str(self.path), NOW), ([], ['record_0_invalid_delta']))

    def test_a_negative_delta_is_ignored(self):
        self.assertEqual(self.load(payload(record(delta=-1)))[1], ['record_0_invalid_delta'])

    def test_a_veto_carrying_a_delta_is_ignored(self):
        self.assertEqual(self.load(payload(record(verb='VETO', delta=1.0)))[1],
                         ['record_0_invalid_delta'])

    def test_an_unknown_verb_or_direction_is_ignored(self):
        self.assertEqual(self.load(payload(record(verb='SIZE_UP')))[1],
                         ['record_0_invalid_verb'])
        self.assertEqual(self.load(payload(record(direction='TURNING-UP')))[1],
                         ['record_0_invalid_direction'])

    def test_an_unknown_reason_code_is_ignored(self):
        for value in (0, 99, True, '1'):
            self.assertEqual(self.load(payload(record(reason_code=value)))[1],
                             ['record_0_invalid_reason_code'], value)

    def test_a_bad_evidence_ref_is_ignored(self):
        for value in ('../etc/passwd', 'a/b', 'a\\b', 'two  spaces', ' pad',
                      'line\nbreak', 'x' * (influence.EVIDENCE_REF_MAX + 1), '', 5):
            self.assertEqual(self.load(payload(record(evidence_ref=value)))[1],
                             ['record_0_invalid_evidence_ref'], value)

    def test_a_duplicate_agent_and_symbol_is_ignored(self):
        data = payload(record(), record(direction='NEUTRAL', verb='VETO', delta=0))
        self.assertEqual(self.load(data)[1], ['record_1_duplicate'])
        # Two different agents on the same symbol are fine.
        self.assertEqual(self.load(payload(record(), record(agent=SCOUT)))[1], [])

    def test_missing_or_extra_record_keys_are_ignored(self):
        short = record()
        short.pop('delta')
        self.assertEqual(self.load(payload(short))[1], ['record_0_keys'])
        self.assertEqual(self.load(payload(dict(record(), note='hi')))[1], ['record_0_keys'])

    def test_too_many_records_are_ignored(self):
        many = [record(symbol='S%d' % i) for i in range(influence.MAX_RECORDS + 1)]
        self.assertEqual(self.load(payload(*many))[1], ['too_many_records'])

    def test_every_problem_is_reported_not_just_the_first(self):
        problems = self.load(payload(record(agent='ceo', verb='NOPE')))[1]
        self.assertEqual(problems, ['record_0_unknown_agent', 'record_0_invalid_verb'])


class ExpiryTests(FileFixture):
    def test_a_stale_file_is_ignored_entirely(self):
        stale = NOW - (INFLUENCE_MAX_AGE_MIN * 60_000 + 1)
        records, problems = self.load(payload(record(), generated_at_ms=stale))
        self.assertEqual((records, problems), ([], ['stale']))

    def test_a_file_inside_the_window_still_counts(self):
        fresh = NOW - INFLUENCE_MAX_AGE_MIN * 60_000
        self.assertEqual(self.load(payload(record(), generated_at_ms=fresh))[1], [])

    def test_a_future_dated_file_is_ignored(self):
        self.assertEqual(self.load(payload(record(), generated_at_ms=NOW + 1))[1], ['future'])


class BoundsTests(unittest.TestCase):
    def board(self):
        return sections(long=[row('A', rank_score=5), row('B', rank_score=1)])

    def test_a_boost_is_capped_per_candidate_and_the_clamp_is_logged(self):
        adjusted, applied = influence.apply(
            self.board(), [record(symbol='B', delta=25.0)], NOW)
        self.assertEqual(applied[0]['delta'], INFLUENCE_MAX_DELTA)
        self.assertEqual(applied[0]['requested_delta'], 25.0)
        self.assertEqual(applied[0]['clamped'], 1)
        self.assertEqual(adjusted['long'][1]['influence_delta'], INFLUENCE_MAX_DELTA)

    def test_two_agents_cannot_stack_past_the_per_candidate_cap(self):
        adjusted, applied = influence.apply(
            self.board(), [record(symbol='B', delta=8.0),
                           record(symbol='B', delta=8.0, agent=SCOUT)], NOW)
        self.assertEqual([e['delta'] for e in applied], [8.0, 2.0])
        self.assertEqual(adjusted['long'][1]['influence_delta'], INFLUENCE_MAX_DELTA)

    def test_the_scan_total_is_capped_and_the_clamp_is_logged(self):
        board = sections(long=[row(name, rank_score=1) for name in ('A', 'B', 'C')])
        records = [record(symbol=name, delta=10.0, agent=agent) for name, agent in
                   (('A', AGENT), ('B', SCOUT), ('C', 'research_scout'))]
        adjusted, applied = influence.apply(board, records, NOW)
        self.assertEqual([e['delta'] for e in applied], [10.0, 10.0, 0.0])
        self.assertEqual(sum(e['delta'] for e in applied), INFLUENCE_MAX_TOTAL_DELTA)
        self.assertEqual([e['clamped'] for e in applied], [0, 0, 1])
        self.assertNotIn('influence_delta', adjusted['long'][2])

    def test_a_veto_drops_the_row_and_never_spends_the_budget(self):
        board = sections(long=[row('A'), row('B')])
        records = [record(symbol='A', delta=10.0),
                   record(symbol='A', verb='VETO', delta=0, agent=SCOUT),
                   record(symbol='B', delta=10.0, agent='strategy_manager')]
        adjusted, applied = influence.apply(board, records, NOW)
        self.assertEqual([r['symbol'] for r in adjusted['long']], ['B'])
        self.assertEqual([(e['verb'], e['delta']) for e in applied],
                         [('VETO', 0.0), ('BOOST', 10.0)])

    def test_an_influence_naming_an_unadmitted_candidate_does_nothing(self):
        board = self.board()
        before = deepcopy(board)
        adjusted, applied = influence.apply(
            board, [record(symbol='ZZZ'), record(symbol='A', direction='SHORT')], NOW)
        self.assertEqual(applied, [])
        self.assertEqual(board, before)
        self.assertEqual(adjusted, {name: [dict(r) for r in rows]
                                    for name, rows in before.items()})

    def test_apply_returns_new_rows_and_never_mutates_the_input(self):
        board = self.board()
        before = deepcopy(board)
        adjusted, _ = influence.apply(board, [record(symbol='A')], NOW)
        self.assertEqual(board, before)
        self.assertIsNot(adjusted['long'][0], board['long'][0])
        self.assertNotIn('influence_delta', board['long'][0])

    def test_the_section_table_matches_the_policy_candidate_order(self):
        for direction in ('LONG', 'SHORT', 'NEUTRAL'):
            expected = {name for name, slot in influence.SECTION_DIRECTIONS.items()
                        if slot == direction}
            board = sections(**{name: [row('S%d' % index)]
                                for index, name in enumerate(sorted(expected))})
            named = {name for name, _ in policy._candidates(board, direction)}
            self.assertEqual(named, expected)


class DecideFixture(unittest.TestCase):
    """decide() driven through a real file on disk, not a stubbed loader."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'influence.json'

    def decide(self, report, data=None, now_ms=NOW, enabled=True, **rules):
        if data is not None:
            self.path.write_text(json.dumps(data), encoding='utf-8')
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(influence, 'DEFAULT_PATH', str(self.path)))
            stack.enter_context(mock.patch.object(policy, 'AGENT_INFLUENCE_ENABLED', enabled))
            stack.enter_context(set_rules(**rules))
            return policy.decide(policy.new_state(now_ms), report, {}, now_ms, 'a')

    def long_board(self):
        return radar(long=[row('A', rank_score=5), row('B', rank_score=1)])


class PolicyIntegrationTests(DecideFixture):

    def test_a_boost_changes_which_candidate_is_chosen_first(self):
        plain, _ = self.decide(self.long_board(), payload())
        self.assertEqual(plain['open_bots'][0]['engine']['symbol'], 'A')
        boosted, events = self.decide(self.long_board(),
                                      payload(record(symbol='B', delta=10.0)))
        self.assertEqual(boosted['open_bots'][0]['engine']['symbol'], 'B')
        self.assertEqual(len(influences(events)), 1)

    def test_a_boost_cannot_move_a_row_across_sections(self):
        report = radar(turning_up=[row('A', 'TURNING-UP', rank_score=1)],
                       long=[row('B', rank_score=1)])
        boosted, _ = self.decide(report, payload(record(symbol='B', delta=10.0)))
        self.assertEqual(boosted['open_bots'][0]['engine']['symbol'], 'A')

    def test_a_boost_cannot_open_a_structurally_invalid_candidate(self):
        # An unverified range never reaches the admitted pool, so the influence
        # naming it is not even applied: an agent cannot admit a coin.
        report = radar(long=[row('A', rank_score=5),
                             row('B', rank_score=1, range_verified=0)])
        boosted, events = self.decide(report, payload(record(symbol='B', delta=10.0)))
        self.assertEqual({w['engine']['symbol'] for w in boosted['open_bots']}, {'A'})
        self.assertEqual(influences(events), [])

    def test_a_boost_reorders_majors_but_never_softens_the_major_cap(self):
        report = radar(long=[row('XBTUSDTM', rank_score=5), row('ETHUSDTM', rank_score=1)])
        boosted, events = self.decide(report, payload(record(symbol='ETHUSDTM', delta=10.0)))
        opened = [w['engine']['symbol'] for w in boosted['open_bots']]
        self.assertEqual(opened, ['ETHUSDTM'])
        self.assertTrue(skips(events, policy.DECISION_RULES['major_cap']))

    def test_a_veto_removes_the_candidate_and_reports_a_skip(self):
        data = payload(record(symbol='A', verb='VETO', delta=0, reason_code=3))
        vetoed, events = self.decide(self.long_board(), data)
        self.assertNotIn('A', {w['engine']['symbol'] for w in vetoed['open_bots']})
        self.assertIn('B', {w['engine']['symbol'] for w in vetoed['open_bots']})
        skip = skips(events, policy.DECISION_RULES['influence_veto'])
        self.assertEqual(len(skip), 1)
        self.assertEqual(skip[0]['symbol'], 'A')
        self.assertEqual(skip[0]['rule_blocks'], [policy.DECISION_RULES['influence_veto']])

    def test_the_veto_skip_is_replayable_by_the_counterfactual_recorder(self):
        data = payload(record(symbol='A', verb='VETO', delta=0))
        report = self.long_board()
        _, events = self.decide(report, data)
        built = autopilot_counterfactual.candidates(report['rows'], events, 'a', NOW)
        vetoed = [entry for entry in built if entry['symbol'] == 'A']
        self.assertEqual(len(vetoed), 1)
        self.assertEqual(vetoed[0]['rule_blocks'],
                         [policy.DECISION_RULES['influence_veto']])
        self.assertEqual(vetoed[0]['direction'], 'LONG')

    def test_an_influence_event_is_numeric_plus_the_agent_id(self):
        data = payload(record(symbol='B', delta=25.0, reason_code=5))
        _, events = self.decide(self.long_board(), data)
        event = influences(events)[0]
        self.assertEqual(event['agent_id'], AGENT)
        self.assertEqual(event['reason_code'], 5)
        self.assertEqual(event['influence_delta'], INFLUENCE_MAX_DELTA)
        self.assertEqual(event['influence_clamped'], 1)
        self.assertEqual(event['rule_blocks'], [policy.DECISION_RULES['influence_boost']])
        for key, value in event.items():
            if key not in ('type', 'symbol', 'action', 'direction', 'radar_direction',
                           'rule_blocks', 'agent_id'):
                self.assertIsInstance(value, (int, float), key)
        validate_event(event)

    def test_a_rejected_file_leaves_the_desk_deterministic(self):
        bad = payload(record(agent='ceo'))
        rejected, events = self.decide(self.long_board(), bad)
        plain, plain_events = self.decide(self.long_board(), payload(), enabled=False)
        errors = [e for e in events if e['type'] == 'ERROR']
        self.assertEqual([e['code'] for e in errors], [INFLUENCE_ERROR])
        validate_event(errors[0])
        self.assertEqual(influences(events), [])
        self.assertEqual(rejected, plain)
        self.assertEqual([e for e in events if e['type'] != 'ERROR'], plain_events)

    def test_a_missing_file_leaves_the_desk_deterministic(self):
        absent, events = self.decide(self.long_board())
        plain, plain_events = self.decide(self.long_board(), payload(), enabled=False)
        self.assertEqual(absent, plain)
        self.assertEqual([e for e in events if e['type'] != 'ERROR'], plain_events)

    def test_a_scan_with_no_admitted_candidate_reads_no_file(self):
        with mock.patch.object(influence, 'load') as loader:
            state, events = self.decide(radar(), payload(record()))
        loader.assert_not_called()
        self.assertEqual(state['open_bots'], [])
        self.assertEqual([e for e in events if e['type'] == 'ERROR'], [])

    def test_the_same_file_produces_the_same_events(self):
        data = payload(record(symbol='B', delta=4.0),
                       record(symbol='A', verb='VETO', delta=0, agent=SCOUT))
        first, first_events = self.decide(self.long_board(), data)
        second, second_events = self.decide(self.long_board(), data)
        self.assertEqual(first, second)
        self.assertEqual(first_events, second_events)


class KillSwitchTests(DecideFixture):
    def test_off_is_byte_identical_on_a_fixture_that_would_be_influenced(self):
        data = payload(record(symbol='B', delta=10.0))
        influenced, influenced_events = self.decide(self.long_board(), data)
        off, off_events = self.decide(self.long_board(), data, enabled=False)
        self.assertNotEqual(influenced['open_bots'][0]['engine']['symbol'],
                            off['open_bots'][0]['engine']['symbol'])
        deterministic, deterministic_events = self.decide(
            radar(long=[row('A', rank_score=5), row('B', rank_score=1)]),
            payload(), enabled=False)
        self.assertEqual(off, deterministic)
        self.assertEqual(off_events, deterministic_events)
        self.assertEqual(influences(off_events), [])
        self.assertNotEqual(influenced_events, off_events)

    def test_off_never_reads_the_file(self):
        with mock.patch.object(influence, 'load') as loader:
            self.decide(self.long_board(), payload(record(symbol='B')), enabled=False)
        loader.assert_not_called()

    def test_the_learned_rule_overrides_the_constant_in_both_directions(self):
        data = payload(record(symbol='B', delta=10.0))
        on, _ = self.decide(self.long_board(), data, enabled=False,
                            agent_influence_enabled=True)
        self.assertEqual(on['open_bots'][0]['engine']['symbol'], 'B')
        off, _ = self.decide(self.long_board(), data, enabled=True,
                             agent_influence_enabled=False)
        self.assertEqual(off['open_bots'][0]['engine']['symbol'], 'A')

    def test_an_absent_learned_rule_leaves_the_constant_deciding(self):
        self.assertFalse(policy._influence_enabled({}))
        self.assertFalse(policy._influence_enabled({'agent_influence_enabled': None}))
        self.assertTrue(policy._influence_enabled({'agent_influence_enabled': True}))


class StoreSchemaTests(unittest.TestCase):
    def store(self, **rules):
        base = dict(learned_rules.EMPTY_RULES)
        base.update(rules)
        return {'version': learned_rules.SCHEMA_VERSION, 'updated_at_ms': NOW,
                'rules': base, 'notes': []}

    def test_absent_key_keeps_existing_stores_valid(self):
        self.assertEqual(learned_rules.validate_store(self.store()), [])
        self.assertNotIn('agent_influence_enabled', learned_rules.EMPTY_RULES)

    def test_booleans_are_accepted_and_anything_else_is_not(self):
        for value in (True, False):
            self.assertEqual(
                learned_rules.validate_store(self.store(agent_influence_enabled=value)), [])
        for value in (1, 0, 'true', [], {}):
            self.assertIn('agent_influence_enabled must be a boolean or absent',
                          learned_rules.validate_store(self.store(agent_influence_enabled=value)))


class ExampleFileTests(unittest.TestCase):
    def test_the_shipped_example_validates(self):
        path = Path(__file__).parents[2] / 'config' / 'paper-team' / 'influence.example.json'
        data = json.loads(path.read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'influence.json'
            target.write_text(json.dumps(data), encoding='utf-8')
            records, problems = influence.load(str(target), data['generated_at_ms'])
        self.assertEqual(problems, [])
        self.assertTrue(records)
        self.assertEqual({r['agent_id'] for r in records} - set(influence.ROSTER), set())

    def test_the_roster_is_nine_snake_case_ids(self):
        self.assertEqual(len(influence.ROSTER), 9)
        self.assertEqual(len(set(influence.ROSTER)), 9)
        for agent in influence.ROSTER:
            self.assertRegex(agent, r'^[a-z][a-z0-9_]{0,39}$')


if __name__ == '__main__':
    unittest.main()
