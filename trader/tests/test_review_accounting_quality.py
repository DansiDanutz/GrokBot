"""Incomplete accounting remains visible and cannot promote learned rules."""
import unittest
from datetime import timezone

from trader.review.daily import accounting_quality, analyze_exit, build_proposals, build_report, render_markdown
from trader.review.rules import plan_changes
from trader.tests.test_daily_review import TempDbCase, make_bot, seed_klines, path_after
from trader.tests.test_rules import proposals_fixture


class AccountingQualityTests(TempDbCase):
    def test_counterfactual_uses_evaluated_bots_fee_epoch(self):
        seed_klines(self.conn, 'TESTUSDTM', '1m',
                    path_after(1_789_236_000_000, 360, lambda _: 112.0))
        old = analyze_exit(make_bot(), self.conn)
        new = analyze_exit(make_bot(fee_rate_maker=.0006), self.conn)
        self.assertAlmostEqual(old['would_have_been_pnl'] - new['would_have_been_pnl'],
                               10 * 112 * (.0006 - .0002))

    def test_provisional_close_is_reported_and_propagated_to_proposals(self):
        state = {'open_bots': [], 'closed_bots': [make_bot(
            accounting_status='PENDING_FUNDING_RECONCILIATION')]}
        report = build_report('2026-09-12', state, self.conn, [], tz=timezone.utc)
        self.assertEqual(len(report['closed']), 1)
        self.assertEqual(report['accounting_quality']['status'], 'PROVISIONAL')
        self.assertIn('PROVISIONAL ACCOUNTING', render_markdown(report))
        props = build_proposals(report)
        self.assertEqual(props['totals']['accounting_quality'], report['accounting_quality'])

    def test_persisted_obligations_block_learning_after_history_is_pruned(self):
        for key in ('pending_funding_reconciliation', 'pending_recovery_reconciliation'):
            with self.subTest(key=key):
                state = {'open_bots': [], 'closed_bots': [], 'runtime': {key: {'1': {}}}}
                quality = accounting_quality(state)
                props = proposals_fixture()
                props['totals']['accounting_quality'] = quality
                planned = plan_changes(props, {}, props['date'])
                self.assertTrue(planned)
                self.assertTrue(all(p['status'] == 'defer' for p in planned))
                self.assertTrue(all('provisional accounting' in p['defer_reason'] for p in planned))

    def test_recovery_unknown_is_not_labeled_as_complete_funding(self):
        quality = accounting_quality({'closed_bots': [make_bot(recovery_incomplete_at_close=True)]})
        self.assertEqual(quality['status'], 'PROVISIONAL')
        self.assertEqual(quality['affected_bots'][0]['reasons'], ['incomplete_recovery'])

    def test_existing_clean_samples_keep_existing_gates(self):
        quality = accounting_quality({'open_bots': [], 'closed_bots': [make_bot()]})
        self.assertEqual(quality['status'], 'NO_RECORDED_FAILURE')
        props = proposals_fixture()
        expected = plan_changes(props, {}, props['date'])
        props['totals']['accounting_quality'] = quality
        self.assertEqual(plan_changes(props, {}, props['date']), expected)


if __name__ == '__main__':
    unittest.main()
