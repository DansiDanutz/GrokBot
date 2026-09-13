"""Observed range exits remain executable when ancillary evidence is unavailable."""
from copy import deepcopy
from sqlite3 import OperationalError
import unittest
from unittest.mock import patch

from trader.autopilot import policy, evidence_archive
from trader.autopilot.storage import atomic_json, read_json, read_events
from trader.tests.test_autopilot_policy import radar, row
from trader.tests import test_autopilot_runtime as runtime_fixtures

NOW = runtime_fixtures.NOW


class BoundarySafetyTests(unittest.TestCase):
    setUp = runtime_fixtures.RuntimeTests.setUp
    runner = runtime_fixtures.RuntimeTests.runner

    def open_bot(self, direction='NEUTRAL'):
        report = radar(**{direction.lower(): [row('A', direction)]})
        report.update(schema_version=1, asof_ms=self.clock[0])
        atomic_json(self.radar, report)
        runner = self.runner()
        runner.pass_once()
        return runner

    def test_funding_failure_closes_losing_positions_and_preserves_obligation(self):
        for direction, price in [('LONG', 70.), ('SHORT', 140.), ('NEUTRAL', 70.)]:
            with self.subTest(direction=direction), patch.object(policy, '_learned_rules',
                    return_value={'min_hold_hours_before_non_risk_close': 4}):
                # Each direction gets its own on-disk fixture state.
                if self.state.exists():
                    self.state.unlink()
                self.clock[0] = NOW
                self.client.price = 100.
                runner = self.open_bot(direction)
                before = deepcopy(runner.state['open_bots'][0]['engine'])
                if direction == 'NEUTRAL':
                    price = 140. if before['position_contracts'] < 0 else 70.
                self.clock[0] += 10000
                self.client.price = price
                with patch('trader.autopilot.runtime.charge',
                           side_effect=OperationalError('private provider detail')):
                    view = runner.pass_once()
                self.assertEqual(view['open_bots'], [])
                closed = runner.state['closed_bots'][0]['engine']
                self.assertEqual(closed['reason'], 'RANGE_BREAK')
                self.assertEqual(closed['last_price'], price)
                self.assertEqual(closed['position_contracts'], 0)
                self.assertEqual(closed['orders'], [])
                self.assertLess(policy.net(closed), 0)
                exposure = (sum(abs(b['position_contracts']) for b in before['hedge_books'])
                            if 'hedge_books' in before else abs(before['position_contracts']))
                self.assertAlmostEqual(closed['fees_paid'] - before['fees_paid'],
                                       exposure * price * before['fee_rate_taker'])
                self.assertEqual(closed['funding_paid'], before['funding_paid'])
                self.assertEqual(closed['accounting_status'], 'PENDING_FUNDING_RECONCILIATION')
                pending = runner.state['runtime']['pending_funding_reconciliation'][str(closed['bot_id'])]
                self.assertEqual(pending['through_ms'], self.clock[0])
                self.assertEqual(pending['pre_close']['position_contracts'], before['position_contracts'])
                self.assertEqual(pending['error_type'], 'OperationalError')
                self.assertEqual(view['funding_reconciliation_pending'], 1)
                self.assertNotIn('private provider detail', self.state.read_text())

    def test_pending_obligation_survives_restart_and_closed_history_retention(self):
        with patch.object(policy, '_learned_rules', return_value={}):
            runner = self.open_bot()
            self.clock[0] += 10000
            self.client.price = 70.
            with patch('trader.autopilot.runtime.charge', side_effect=OperationalError('offline')):
                runner.pass_once()
            obligation = deepcopy(runner.state['runtime']['pending_funding_reconciliation'])
            restarted = self.runner()
            with patch.object(policy, 'decide', wraps=policy.decide) as decide:
                restarted.pass_once(force_decision=True)
                decide.assert_not_called()
            self.assertEqual(restarted.state['runtime']['pending_funding_reconciliation'], obligation)
            self.clock[0] += 31 * 86400000
            restarted.state = policy.sample(restarted.state, self.clock[0])
            view = restarted._persist(self.clock[0])
            self.assertEqual(restarted.state['closed_bots'], [])
            self.assertEqual(view['funding_reconciliation_status'], 'PENDING')
            self.assertEqual(read_json(self.state)['runtime']['pending_funding_reconciliation'], obligation)

    def test_failed_recovery_allows_boundary_close_but_never_new_entries(self):
        with patch.object(policy, '_learned_rules', return_value={}):
            self.open_bot()
            self.clock[0] += 10000
            self.client.price = 70.
            restarted = self.runner()
            with patch('trader.autopilot.runtime.ingest_open_minutes', side_effect=RuntimeError('offline')), \
                 patch.object(policy, 'decide', wraps=policy.decide) as decide:
                view = restarted.pass_once(force_decision=True)
                decide.assert_not_called()
            self.assertEqual(view['open_bots'], [])
            self.assertTrue(view['recovery_pending'])
            self.assertTrue(view['closed_bots'][0]['recovery_incomplete_at_close'])
            self.assertEqual(view['closed_bots'][0]['reason'], 'RANGE_BREAK')
            self.assertEqual(sum(e['type'] == 'CLOSE' for e in read_events(self.state.parent, 0)), 1)
            pending = deepcopy(restarted.state['runtime']['pending_recovery_reconciliation'])
            self.assertEqual(len(pending), 1)
            self.assertEqual(next(iter(pending.values()))['reason'], 'RECOVERY_COVERAGE_UNVERIFIED')
            self.clock[0] += 31 * 86400000
            restarted.state = policy.sample(restarted.state, self.clock[0])
            view = restarted._persist(self.clock[0])
            self.assertEqual(restarted.state['closed_bots'], [])
            self.assertEqual(view['recovery_reconciliation_pending'], 1)
            fresh = self.runner()
            with patch.object(policy, 'decide', wraps=policy.decide) as decide:
                fresh.pass_once(force_decision=True)
                decide.assert_not_called()
            self.assertEqual(fresh.state['runtime']['pending_recovery_reconciliation'], pending)

    def test_failed_recovery_does_not_replay_in_range_ticks(self):
        with patch.object(policy, '_learned_rules', return_value={}):
            first = self.open_bot()
            before = deepcopy(first.state['open_bots'][0]['engine'])
            self.clock[0] += 10000
            self.client.price = 95.
            restarted = self.runner()
            with patch('trader.autopilot.runtime.ingest_open_minutes', side_effect=RuntimeError('offline')):
                restarted.pass_once(force_decision=True)
            after = restarted.state['open_bots'][0]['engine']
            self.assertEqual(after['last_ts_ms'], before['last_ts_ms'])
            self.assertEqual(after['fills'], before['fills'])

    def test_recovery_accounting_failure_does_not_claim_recovered(self):
        with patch.object(policy, '_learned_rules', return_value={}):
            self.open_bot()
            self.clock[0] += 60000
            restarted = self.runner()
            candle = dict(ts_ms=self.clock[0], open=100., high=101., low=99., close=100.)
            with patch('trader.autopilot.runtime.candles_after', return_value=[candle]), \
                 patch('trader.autopilot.runtime.charge', side_effect=OperationalError('offline')):
                view = restarted.pass_once(force_decision=True)
            self.assertTrue(view['recovery_pending'])
            self.assertEqual(len(view['open_bots']), 1)

    def test_partial_recovery_keeps_prior_safety_close_and_journal_once(self):
        for first_funding_fails in (False, True):
            with self.subTest(first_funding_fails=first_funding_fails), \
                 patch.object(policy, '_learned_rules', return_value={}):
                if self.state.exists():
                    self.state.unlink()
                for journal in self.state.parent.glob('events*.jsonl'):
                    journal.unlink()
                self.clock[0] = NOW
                self.client.price = 100.
                original = self.open_bot()
                second = deepcopy(original.state['open_bots'][0])
                second['engine'].update(bot_id=99, symbol='B')
                original.state['open_bots'].append(second)
                original._persist(self.clock[0])
                self.clock[0] += 120000
                restarted = self.runner()
                def candles(database, symbol, last_ms, now_ms):
                    at = NOW + (60000 if symbol == 'A' else 120000)
                    return [dict(ts_ms=at, open=100., high=101.,
                                 low=70. if symbol == 'A' else 99., close=100.)]
                def funding(database, bot, through_ms):
                    if bot['symbol'] == 'B' or first_funding_fails:
                        raise OperationalError('fixture funding unavailable')
                with patch('trader.autopilot.runtime.ingest_open_minutes'), \
                     patch('trader.autopilot.runtime.candles_after', side_effect=candles), \
                     patch('trader.autopilot.runtime.charge', side_effect=funding):
                    view = restarted.pass_once(force_decision=True)
                    self.assertTrue(view['recovery_pending'])
                    self.assertEqual([b['symbol'] for b in view['open_bots']], ['B'])
                    self.assertEqual(view['closed_bots'][0]['reason'], 'RANGE_BREAK')
                    self.assertEqual(view['open_bots'][0]['last_ts_ms'], NOW)
                    self.assertEqual(view['funding_reconciliation_pending'], int(first_funding_fails))
                    # Replay after restart cannot duplicate the already-journaled close.
                    again = self.runner()
                    repeated = again.pass_once(force_decision=True)
                    self.assertTrue(repeated['recovery_pending'])
                    self.assertEqual([b['symbol'] for b in repeated['open_bots']], ['B'])
                closes = [e for e in read_events(self.state.parent, 0)
                          if e['type'] == 'CLOSE' and e['symbol'] == 'A']
                self.assertEqual(len(closes), 1)

    def test_later_ordinary_funding_failure_cannot_discard_boundary_journal(self):
        with patch.object(policy, '_learned_rules', return_value={}):
            runner = self.open_bot()
            second = deepcopy(runner.state['open_bots'][0])
            second['engine'].update(bot_id=99, symbol='B')
            runner.state['open_bots'].append(second)
            self.clock[0] += 10000
            with patch('trader.autopilot.runtime.charge', side_effect=OperationalError('offline')):
                emitted = runner._apply({'A': dict(ts_ms=self.clock[0], price=70.),
                                         'B': dict(ts_ms=self.clock[0], price=100.)})
            self.assertEqual([w['engine']['symbol'] for w in runner.state['open_bots']], ['B'])
            self.assertEqual(sum(e['type'] == 'CLOSE' for e in emitted), 1)
            runner._queue(emitted)
            runner._persist(self.clock[0])
            self.assertEqual(sum(e['type'] == 'CLOSE' for e in read_events(self.state.parent, 0)), 1)

    def test_decision_funding_failure_is_persisted_without_new_entries(self):
        with patch.object(policy, '_learned_rules', return_value={}):
            runner = self.open_bot()
            self.clock[0] += 300000
            with patch('trader.autopilot.runtime.charge', side_effect=OperationalError('offline')), \
                 patch.object(policy, 'decide', wraps=policy.decide) as decide:
                view = runner.pass_once(force_decision=True)
                decide.assert_not_called()
            self.assertEqual(len(view['open_bots']), 1)
            self.assertIn(5, [e.get('code') for e in read_events(self.state.parent, 0) if e['type'] == 'ERROR'])

    def test_unexpected_archive_errors_cannot_veto_close_or_journal(self):
        with patch.object(policy, '_learned_rules', return_value={}):
            runner = self.open_bot()
            self.clock[0] += 10000
            self.client.price = 70.
            with patch.object(evidence_archive, 'stage', side_effect=TypeError('private archive detail')), \
                 patch.object(evidence_archive, 'flush', side_effect=OSError('private archive detail')):
                view = runner.pass_once()
            self.assertEqual(view['open_bots'], [])
            self.assertEqual(view['setup_evidence_archive_status'], 'BLOCKED')
            self.assertEqual(sum(e['type'] == 'CLOSE' for e in read_events(self.state.parent, 0)), 1)
            self.assertNotIn('private archive detail', self.state.read_text())

    def test_failed_archive_persists_pending_then_retries_verified_write(self):
        with patch.object(policy, '_learned_rules', return_value={}):
            with patch.object(evidence_archive, '_publish', side_effect=OSError('disk unavailable')):
                runner = self.open_bot()
            saved = read_json(self.state)
            pending = saved['runtime']['pending_setup_evidence']
            self.assertEqual(len(pending), 1)
            identifier, dossier = next(iter(pending.items()))
            self.assertEqual(saved['runtime']['setup_evidence_archive_status'], 'BLOCKED')
            self.assertNotIn('setup_evidence_archived_id', saved['open_bots'][0])
            with patch.object(policy, 'decide', wraps=policy.decide) as decide:
                runner.pass_once(force_decision=True)
                decide.assert_not_called()
            runner._persist(self.clock[0])
            self.assertEqual(evidence_archive.read_verified(self.state.parent, identifier), dossier)
            self.assertEqual(read_json(self.state)['runtime']['pending_setup_evidence'], {})
            self.assertEqual(runner.state['open_bots'][0]['setup_evidence_archived_id'], identifier)

    def test_capacity_blocked_archive_retains_unqueued_closed_dossier_without_double_count(self):
        with patch.object(policy, '_learned_rules', return_value={}):
            runner = self.open_bot()
            self.clock[0] += 10000
            emitted = runner._apply({'A': dict(ts_ms=self.clock[0], price=70.)})
            runner._queue(emitted)
            closed = runner.state['closed_bots'][0]
            closed.pop('setup_evidence_archived_id', None)
            runner.state['runtime']['pending_setup_evidence'] = {}
            runner.state['runtime']['setup_evidence_archive_status'] = 'CAPACITY_BLOCKED'
            old_archived = runner.state['archived_net']
            original_net = policy.net(closed['engine'])
            self.clock[0] += 31 * 86400000
            with patch.object(runner, '_archive_evidence', return_value=dict(status='CAPACITY_BLOCKED')):
                runner._sample(self.clock[0])
                runner._sample(self.clock[0] + 1)
            self.assertEqual(len(runner.state['closed_bots']), 1)
            self.assertEqual(policy.net(runner.state['closed_bots'][0]['engine']), original_net)
            self.assertAlmostEqual(runner.state['archived_net'], old_archived)


if __name__ == '__main__':
    unittest.main()
