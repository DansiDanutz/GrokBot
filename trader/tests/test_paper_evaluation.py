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
