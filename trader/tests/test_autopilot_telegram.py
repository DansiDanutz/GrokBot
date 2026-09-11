"""Offline delivery, accounting summaries, and restart deduplication."""
import copy
import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from trader.autopilot.telegram import deliver, notifications


NOW = int(datetime(2026, 9, 11, 5, tzinfo=timezone.utc).timestamp() * 1000)


def snapshot():
    bot = dict(bot_id=1, symbol='RAYUSDTM', direction='NEUTRAL', range_low=1.1,
               range_high=2, completed_grids=4, grid_profit=3, unrealized_pnl=2,
               realized_pnl=5, fees_paid=1, funding_paid=0.5,
               grids_per_hour=4, opened_ms=NOW - 3600000, closed_ms=None)
    return dict(open_bots=[bot], closed_bots=[], equity=10005.5,
                tick_age_s=10, kucoin_ok=True)


class TelegramTests(unittest.TestCase):
    def test_open_close_daily_and_pure_persistent_dedupe(self):
        report = snapshot()
        closed = dict(report['open_bots'][0], bot_id=2, closed_ms=NOW,
                      reason='MAX_AGE', realized_pnl=-3, unrealized_pnl=0)
        report['closed_bots'] = [closed]
        events = [dict(type=kind, ts_ms=NOW, bot_id=bid, symbol='RAYUSDTM')
                  for kind, bid in [('OPEN', 1), ('CLOSE', 2)]]
        before = copy.deepcopy((report, events))
        messages, state = notifications(report, events, NOW, {})
        self.assertEqual(len(messages), 3)
        text = '\n'.join(message['text'] for message in messages)
        for value in ['1.1..2', 'MAX_AGE', 'LONG', 'SHORT', 'NEUTRAL',
                      'grids/h', 'net', 'fees', 'funding', 'Best', 'Worst']:
            self.assertIn(value, text)
        self.assertIn('5.50', messages[0]['text'])
        self.assertEqual(notifications(report, events, NOW, state)[0], [])
        self.assertEqual((report, events), before)

    def test_health_thresholds_alert_once_and_recovery_after_restart(self):
        report = snapshot()
        now = NOW - 3600000
        report['tick_age_s'] = 179
        self.assertEqual(notifications(report, [], now, {})[0], [])
        report['tick_age_s'] = 180
        messages, state = notifications(report, [], now, {})
        self.assertEqual(len(messages), 1)
        self.assertEqual(state['sent'], {})
        self.assertEqual(notifications(report, [], now + 10000, state)[0], [])
        report['tick_age_s'] = 0
        self.assertIn('recovered', notifications(report, [], now + 20000, state)[0][0]['text'])
        report.update(kucoin_ok=False, kucoin_down_since_ms=now - 299000)
        self.assertEqual(notifications(report, [], now, {})[0], [])
        report['kucoin_down_since_ms'] -= 1000
        self.assertEqual(len(notifications(report, [], now, {})[0]), 1)

    def test_partial_delivery_retry_and_no_token_in_state_or_exception(self):
        events = [dict(type='OPEN', ts_ms=NOW, bot_id=1, symbol='RAYUSDTM')]
        attempts = []
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, size): return b'{"ok":true}'
        def opener(request, timeout):
            attempts.append(json.loads(request.data)['text'])
            self.assertEqual(timeout, 5)
            if len(attempts) == 2:
                raise RuntimeError('private-test-token')
            return Response()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / 'delivery.json'
            with self.assertRaisesRegex(RuntimeError, '^Telegram delivery failed$'):
                deliver(snapshot(), events, NOW, 123, path, opener=opener,
                        environ={'DLS_TELEGRAM_BOT_TOKEN': 'private-test-token'}, max_messages=10)
            self.assertNotIn('private-test-token', path.read_text())
            self.assertEqual(deliver(snapshot(), events, NOW, 123, path, opener=opener,
                environ={'DLS_TELEGRAM_BOT_TOKEN': 'private-test-token'}, max_messages=10), 1)
            self.assertEqual(attempts[1], attempts[2])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_summary_current_local_day_and_message_limit(self):
        report = snapshot()
        report['closed_bots'] = [dict(report['open_bots'][0], bot_id=2,
            closed_ms=NOW - 86400000, realized_pnl=999999)]
        messages, _ = notifications(report, [], NOW, {})
        self.assertIn('Best: none', messages[0]['text'])
        self.assertTrue(all(len(item['text']) <= 4096 for item in messages))

    def test_dedupe_retention_uses_event_time_and_discards_expired_events(self):
        now = NOW - 3600000
        old = now - 31 * 86400000
        events = [dict(type='OPEN', ts_ms=old, bot_id=1, symbol='RAYUSDTM')]
        messages, state = notifications(snapshot(), events, now,
            {'sent': {f'OPEN:1:{old}': old}, 'health_active': False})
        self.assertEqual(messages, [])
        self.assertEqual(state['sent'], {})
        event_time = now - 60000
        events[0]['ts_ms'] = event_time
        _, state = notifications(snapshot(), events, now, {})
        self.assertEqual(state['sent'][f'OPEN:1:{event_time}'], event_time)

    def test_idle_delivery_persists_pruned_dedupe_state_without_network(self):
        now = NOW - 3600000
        old = now - 31 * 86400000
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / 'delivery.json'
            path.write_text(json.dumps({'sent': {f'OPEN:1:{old}': old}}))
            def never(*args, **kwargs):
                self.fail('no transport for idle delivery')
            self.assertEqual(deliver(snapshot(), [], now, 123, path, opener=never,
                environ={'DLS_TELEGRAM_BOT_TOKEN': 'private-test-token'}), 0)
            self.assertEqual(json.loads(path.read_text())['sent'], {})

    def test_direction_rate_is_sum_of_bot_rates(self):
        report = snapshot()
        report['open_bots'].append(dict(report['open_bots'][0], bot_id=2,
                                       grids_per_hour=7))
        messages, _ = notifications(report, [], NOW, {})
        neutral = next(line for line in messages[0]['text'].splitlines()
                       if line.startswith('NEUTRAL:'))
        self.assertIn('11.00 grids/h', neutral)

    def test_health_priority_and_default_single_message_cap(self):
        report = snapshot()
        report['tick_age_s'] = 180
        events = [dict(type='OPEN', ts_ms=NOW, bot_id=1, symbol='RAYUSDTM')]
        messages, _ = notifications(report, events, NOW, {})
        self.assertTrue(messages[0]['id'].startswith('ALERT:'))
        self.assertEqual(messages[0]['state']['sent'], {})
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, size): return b'{"ok":true}'
        attempts = []
        def opener(request, timeout):
            attempts.append(json.loads(request.data)['text'])
            return Response()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / 'delivery.json'
            self.assertEqual(deliver(report, events, NOW, 123, path, opener=opener,
                environ={'DLS_TELEGRAM_BOT_TOKEN': 'private-test-token'}), 1)
            self.assertEqual(len(attempts), 1)
            state = json.loads(path.read_text())
            self.assertTrue(state['health_active'])
            self.assertEqual(state['sent'], {})
            self.assertEqual(deliver(report, events, NOW + 10000, 123, path, opener=opener,
                environ={'DLS_TELEGRAM_BOT_TOKEN': 'private-test-token'}), 1)
            self.assertIn('OPEN', attempts[1])


if __name__ == '__main__':
    unittest.main()
