import copy
import unittest
from trader.research.paper_evaluation import arm_evaluation, start_evaluation, evaluation_clock

REVISION = 'a' * 40
POLICY = dict(strategy='grid-kucoin-policy-v2', margin_used_per_bot=1000,
              reserve_per_bot=200, leverage=5, maximum_bots=2)


class PaperEvaluationTests(unittest.TestCase):
    def test_arming_does_not_start_or_relabel_existing_results(self):
        policy = copy.deepcopy(POLICY)
        plan = arm_evaluation(policy, previous_run_id='old-run', source_revision=REVISION)
        self.assertEqual(plan['bankroll_usdt'], 2400)
        self.assertEqual(plan['status'], 'awaiting_paper_start')
        self.assertIsNone(plan['started_at_ms'])
        self.assertIsNone(plan['first_audit_at_ms'])
        self.assertEqual(plan['previous_run_id'], 'old-run')
        self.assertEqual(evaluation_clock(plan, 1000)['remaining_seconds'], None)
        policy['leverage'] = 9
        self.assertEqual(plan['policy']['leverage'], 5)

    def test_clock_begins_on_matching_paper_start_not_backtest_or_trade(self):
        plan = arm_evaluation(POLICY, source_revision=REVISION)
        event = dict(kind='paper_run_started', run_id='new-run', at_ms=1234567,
                     first_observation_at_ms=1234500, policy_sha256=plan['policy_sha256'])
        started = start_evaluation(plan, event)
        self.assertEqual(started['first_audit_at_ms'], 1234567 + 48*3600000)
        self.assertEqual(evaluation_clock(started, 1234567)['remaining_seconds'], 48*3600)
        self.assertEqual(plan['status'], 'awaiting_paper_start')
        for kind in ('backtest_completed', 'trade_opened', 'schedule_created'):
            with self.assertRaises(ValueError):
                start_evaluation(plan, dict(event, kind=kind))
        with self.assertRaises(ValueError):
            start_evaluation(plan, dict(event, policy_sha256='wrong-policy'))

    def test_48_hours_is_audit_boundary_not_a_profit_or_runtime_stop(self):
        plan = arm_evaluation(POLICY, source_revision=REVISION)
        started = start_evaluation(plan, dict(kind='paper_run_started', run_id='new-run',
            at_ms=1000, first_observation_at_ms=1000, policy_sha256=plan['policy_sha256']))
        clock = evaluation_clock(started, 1000 + 48*3600000)
        self.assertTrue(clock['audit_due'])
        self.assertEqual(clock['remaining_seconds'], 0)
        self.assertFalse(clock['stop_trading'])
        self.assertEqual(clock['evaluation_period_ms'], [1000, 1000+48*3600000])
        self.assertEqual(evaluation_clock(started, 1001)['remaining_seconds'], 172800)

    def test_source_revision_is_sealed_into_the_paper_clock(self):
        first = arm_evaluation(POLICY, source_revision=REVISION)
        other = arm_evaluation(POLICY, source_revision='b' * 40)
        self.assertNotEqual(first['policy_sha256'], other['policy_sha256'])
        event = dict(kind='paper_run_started', run_id='new-run', at_ms=1000,
                     first_observation_at_ms=1000, policy_sha256=first['policy_sha256'])
        with self.assertRaises(ValueError):
            start_evaluation(other, event)
        with self.assertRaises(ValueError):
            arm_evaluation(POLICY, source_revision='unknown')

    def test_inconsistent_persisted_clock_cannot_move_the_audit_deadline(self):
        plan = arm_evaluation(POLICY, source_revision=REVISION)
        event = dict(kind='paper_run_started', run_id='new-run', at_ms=1000,
                     first_observation_at_ms=1000, policy_sha256=plan['policy_sha256'])
        started = start_evaluation(plan, event)
        for field in ('started_at_ms', 'first_audit_at_ms'):
            invalid = dict(started, **{field: started[field]+1000})
            with self.assertRaises(ValueError):
                evaluation_clock(invalid, 2000)
            with self.assertRaises(ValueError):
                start_evaluation(invalid, event)

    def test_repeated_start_cannot_reset_a_running_timer_or_reuse_old_run(self):
        plan = arm_evaluation(POLICY, previous_run_id='old-run', source_revision=REVISION)
        event = dict(kind='paper_run_started', run_id='new-run', at_ms=1000,
                     first_observation_at_ms=1000, policy_sha256=plan['policy_sha256'])
        started = start_evaluation(plan, event)
        self.assertEqual(start_evaluation(started, event), started)
        with self.assertRaises(ValueError):
            start_evaluation(started, dict(event, at_ms=2000))
        with self.assertRaises(ValueError):
            start_evaluation(plan, dict(event, run_id='old-run'))
        with self.assertRaises(ValueError):
            start_evaluation(plan, dict(event, first_observation_at_ms=2000))
        with self.assertRaises(ValueError):
            arm_evaluation(dict(POLICY, reserve_per_bot=0), source_revision=REVISION)


POSITIVE_POLICY = dict(POLICY, strategy='grid-kucoin-v3-positive',
                       minimum_grid_net_usdt=0, range_exit_stop_pct=0)


class PositivePaperEvaluationTests(unittest.TestCase):
    def test_new_policy_arms_without_starting_and_seals_exact_source_and_fields(self):
        policy = copy.deepcopy(POSITIVE_POLICY)
        plan = arm_evaluation(policy, previous_run_id='old-run', source_revision=REVISION)
        self.assertEqual(plan['policy'], POSITIVE_POLICY)
        self.assertEqual(plan['status'], 'awaiting_paper_start')
        self.assertIsNone(plan['started_at_ms'])
        self.assertIsNone(plan['first_audit_at_ms'])
        self.assertEqual(plan['bankroll_usdt'], 2400)
        self.assertEqual(plan['audit_period_ms'], 48*3600000)
        self.assertIsNone(evaluation_clock(plan, 1000)['remaining_seconds'])
        policy['range_exit_stop_pct'] = .01
        self.assertEqual(plan['policy']['range_exit_stop_pct'], 0)
        changed_revision = arm_evaluation(POSITIVE_POLICY, source_revision='b'*40)
        self.assertNotEqual(plan['policy_sha256'], changed_revision['policy_sha256'])
        legacy = arm_evaluation(POLICY, source_revision=REVISION)
        self.assertNotEqual(plan['policy_sha256'], legacy['policy_sha256'])

    def test_new_policy_requires_exact_economics_explicit_zeros_and_rejects_bools(self):
        invalid = []
        for key in POSITIVE_POLICY:
            missing = dict(POSITIVE_POLICY)
            del missing[key]
            invalid.append(missing)
        for key in ('margin_used_per_bot', 'reserve_per_bot', 'leverage', 'maximum_bots',
                    'minimum_grid_net_usdt', 'range_exit_stop_pct'):
            for value in (True, False, None, '0', -1, float('inf'), float('nan')):
                invalid.append(dict(POSITIVE_POLICY, **{key: value}))
        invalid.extend([dict(POSITIVE_POLICY, minimum_grid_net_usdt=1),
                        dict(POSITIVE_POLICY, range_exit_stop_pct=.01),
                        dict(POSITIVE_POLICY, range_exit_stop_pct=.05),
                        dict(POSITIVE_POLICY, reserve_per_bot=0),
                        dict(POSITIVE_POLICY, leverage=6),
                        dict(POSITIVE_POLICY, maximum_bots=3),
                        dict(POSITIVE_POLICY, margin_used_per_bot=1200),
                        dict(POSITIVE_POLICY, extra='undeclared')])
        for policy in invalid:
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                arm_evaluation(policy, source_revision=REVISION)
        for revision in (None, 'a'*39, 'a'*41, 'A'*40, True):
            with self.subTest(revision=revision), self.assertRaises(ValueError):
                arm_evaluation(POSITIVE_POLICY, source_revision=revision)

    def test_new_clock_requires_matching_fresh_paper_event_and_remains_idempotent(self):
        plan = arm_evaluation(POSITIVE_POLICY, previous_run_id='old-run', source_revision=REVISION)
        event = dict(kind='paper_run_started', run_id='new-run', at_ms=100000,
                     first_observation_at_ms=100000, policy_sha256=plan['policy_sha256'])
        legacy = arm_evaluation(POLICY, source_revision=REVISION)
        for changes in ({'kind': 'backtest_completed'}, {'run_id': 'old-run'},
                        {'first_observation_at_ms': 39999}, {'first_observation_at_ms': 100001},
                        {'policy_sha256': legacy['policy_sha256']}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                start_evaluation(plan, dict(event, **changes))
        started = start_evaluation(plan, event)
        self.assertEqual(started['first_audit_at_ms'], event['at_ms']+48*3600000)
        self.assertEqual(start_evaluation(started, event), started)
        self.assertFalse(evaluation_clock(started, started['first_audit_at_ms'])['stop_trading'])
        for key, value in [('range_exit_stop_pct', .01), ('minimum_grid_net_usdt', 1)]:
            tampered = copy.deepcopy(plan)
            tampered['policy'][key] = value
            with self.assertRaises(ValueError):
                start_evaluation(tampered, event)
        with self.assertRaises(ValueError):
            start_evaluation(started, dict(event, at_ms=100001))

    def test_archived_v2_policy_fields_and_hash_remain_unchanged(self):
        plan = arm_evaluation(POLICY, source_revision=REVISION)
        self.assertEqual(plan['policy_sha256'],
                         'ba755fc7fbce9636fb8a641860c7b4f17dfa8211fb50ba3d0863ff20b154bd1f')
        self.assertEqual(plan['policy'], POLICY)
        with self.assertRaises(ValueError):
            arm_evaluation(dict(POLICY, minimum_grid_net_usdt=0), source_revision=REVISION)

    def test_explicit_numeric_zero_fields_are_sealed_without_boolean_coercion(self):
        policy = dict(POSITIVE_POLICY, minimum_grid_net_usdt=0.0, range_exit_stop_pct=0.0)
        plan = arm_evaluation(policy, source_revision=REVISION)
        self.assertEqual(plan['policy'], policy)
