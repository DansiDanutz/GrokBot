import copy
import unittest

from trader.research.paper_evaluation import arm_evaluation, start_evaluation


POLICY = dict(strategy='grid-kucoin-v3-income-chart', margin_used_per_bot=1000,
    reserve_per_bot=200, leverage=5, maximum_bots=2, minimum_grid_net_usdt=0,
    range_exit_stop_pct=0, fee_safety_ratio=.2, min_grids=2, max_grids=200,
    bias_mode='1d+4h', regime_gate=True, minimum_confidence=.75,
    registration_sha256='a'*64)


class IncomeChartEvaluationTests(unittest.TestCase):
    def test_amended_policy_waits_for_matching_fresh_start_and_seals_registration(self):
        plan = arm_evaluation(POLICY, source_revision='b'*40)
        self.assertIsNone(plan['started_at_ms'])
        self.assertEqual(plan['bankroll_usdt'], 2400)
        changed = arm_evaluation(dict(POLICY, registration_sha256='c'*64), source_revision='b'*40)
        self.assertNotEqual(plan['policy_sha256'], changed['policy_sha256'])
        event = dict(kind='paper_run_started', run_id='new-chart-run', at_ms=1000,
            first_observation_at_ms=1000, policy_sha256=plan['policy_sha256'])
        self.assertEqual(start_evaluation(plan,event)['first_audit_at_ms'],1000+48*3600000)
        with self.assertRaises(ValueError):
            start_evaluation(changed,event)

    def test_invalid_chart_policy_is_rejected_without_silent_defaults(self):
        mutations = dict(fee_safety_ratio=-1, min_grids=1, max_grids=201,
            bias_mode='future', regime_gate=1, minimum_confidence=1.1,
            registration_sha256='missing', range_exit_stop_pct=.05)
        for name,value in mutations.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                arm_evaluation(dict(POLICY,**{name:value}),source_revision='b'*40)
        for name in POLICY:
            value=copy.deepcopy(POLICY)
            del value[name]
            with self.subTest(missing=name), self.assertRaises(ValueError):
                arm_evaluation(value,source_revision='b'*40)

    def test_each_explicit_variant_has_distinct_policy_identity(self):
        hashes = {arm_evaluation(dict(POLICY,bias_mode=bias,regime_gate=gate),
            source_revision='b'*40)['policy_sha256']
            for bias in ('4h-only','1d+4h') for gate in (False,True)}
        self.assertEqual(len(hashes),4)
