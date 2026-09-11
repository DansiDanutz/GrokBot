"""Watchlist delivery uses scan checkpoints, with no provider requests."""
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from trader.autopilot.telegram import deliver, notifications


NOW = int(datetime(2026, 9, 11, 4, tzinfo=timezone.utc).timestamp() * 1000)


def report():
    entry = dict(symbol='RAYUSDTM', direction='NEUTRAL', score=71,
                 score_parts=[dict(code='OSCILLATION', value=18.4, points=27.6),
                              dict(code='MOVER_RISK', value=31, points=-15)],
                 since_ms=NOW, rank=1)
    bench = dict(entry, symbol='SAGAUSDTM', score=52)
    return dict(tick_age_s=0, kucoin_ok=True, watchlist_scan_id='scan-1',
                watchlist_asof_ms=NOW, watchlist={'core': [entry], 'bench': [bench]},
                watchlist_history=[dict(ts_ms=NOW, type='PROMOTE', symbol='RAYUSDTM',
                                       score=71, replaced_symbol='SAGAUSDTM',
                                       replaced_score=52, margin=19)],
                watchlist_swap_times=[NOW])


class WatchlistTelegramTests(unittest.TestCase):
    def test_code_rows_swap_scores_and_silent_unchanged_scans(self):
        snapshot = report()
        original = copy.deepcopy(snapshot)
        messages, state = notifications(snapshot, [], NOW, {})
        self.assertEqual(len(messages), 1)
        self.assertIn('Core: RAYUSDTM NEUTRAL 71 (OSCILLATION)', messages[0]['text'])
        self.assertIn('Bench: SAGAUSDTM NEUTRAL 52 (OSCILLATION)', messages[0]['text'])
        self.assertIn('71 vs 52', messages[0]['text'])
        self.assertIn('margin 19', messages[0]['text'])
        self.assertEqual(snapshot, original)
        self.assertEqual(notifications(snapshot, [], NOW, state)[0], [])
        snapshot['watchlist_scan_id'] = 'scan-2'
        snapshot['watchlist']['core'][0]['since_ms'] += 1000
        messages, final = notifications(snapshot, [], NOW, state)
        self.assertEqual(messages, [])
        self.assertEqual(final['watchlist_scan_id'], 'scan-2')

    def test_new_scan_with_score_change_sends_once_only(self):
        snapshot = report()
        _, state = notifications(snapshot, [], NOW, {})
        snapshot['watchlist']['core'][0]['score'] = 72
        self.assertEqual(notifications(snapshot, [], NOW, state)[0], [])
        snapshot['watchlist_scan_id'] = 'scan-2'
        messages, state = notifications(snapshot, [], NOW, state)
        self.assertEqual(len(messages), 1)
        self.assertIn('72', messages[0]['text'])
        self.assertEqual(notifications(snapshot, [], NOW, state)[0], [])

    def test_rejected_delivery_retries_identical_message_without_checkpoint(self):
        attempts = []
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, size): return b'{"ok":true}'
        def opener(request, timeout):
            attempts.append(json.loads(request.data)['text'])
            if len(attempts) == 1:
                raise OSError('offline test')
            return Response()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / 'delivery.json'
            options = dict(opener=opener, environ={'DLS_TELEGRAM_BOT_TOKEN': 'fixture-token'})
            with self.assertRaisesRegex(RuntimeError, 'delivery failed'):
                deliver(report(), [], NOW, 123, path, **options)
            self.assertFalse(path.exists())
            self.assertEqual(deliver(report(), [], NOW, 123, path, **options), 1)
            self.assertEqual(attempts[0], attempts[1])
            self.assertEqual(deliver(report(), [], NOW, 123, path, **options), 0)

    def test_health_precedes_watchlist_and_directions_and_drop_are_visible(self):
        snapshot = report()
        snapshot['tick_age_s'] = 180
        snapshot['watchlist_history'] += [
            dict(ts_ms=NOW, type='DROP', symbol='OLDUSDTM', score=30,
                 replaced_symbol='', replaced_score=0, margin=0),
            dict(ts_ms=NOW, type='DIRECTION_CHANGE', symbol='RAYUSDTM', score=71,
                 replaced_symbol='', replaced_score=0, margin=0)]
        messages, _ = notifications(snapshot, [], NOW, {})
        self.assertTrue(messages[0]['id'].startswith('ALERT:'))
        self.assertTrue(messages[1]['id'].startswith('WATCHLIST:'))
        self.assertIn('DROP OLDUSDTM', messages[1]['text'])
        self.assertIn('DIRECTION_CHANGE RAYUSDTM', messages[1]['text'])
        self.assertNotIn('watchlist_fingerprint', messages[0]['state'])

    def test_failed_scan_is_retained_when_a_newer_scan_arrives(self):
        snapshot = report()
        keys = ('watchlist', 'watchlist_history', 'watchlist_scan_id', 'watchlist_asof_ms')
        old = {key: copy.deepcopy(snapshot[key]) for key in keys}
        snapshot['watchlist_scan_id'] = 'scan-2'
        snapshot['watchlist_asof_ms'] += 3600000
        snapshot['watchlist']['core'][0]['score'] = 82
        snapshot['watchlist_history'] = []
        newer = {key: copy.deepcopy(snapshot[key]) for key in keys}
        snapshot['pending_watchlists'] = [old, newer]
        attempts = []
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, size): return b'{"ok":true}'
        def opener(request, timeout):
            attempts.append(json.loads(request.data)['text'])
            if len(attempts) == 1:
                raise OSError('offline test')
            return Response()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / 'delivery.json'
            options = dict(opener=opener, environ={'DLS_TELEGRAM_BOT_TOKEN': 'fixture-token'})
            with self.assertRaisesRegex(RuntimeError, 'delivery failed'):
                deliver(snapshot, [], NOW, 123, path, **options)
            self.assertEqual(deliver(snapshot, [], NOW, 123, path, **options), 1)
            self.assertEqual(attempts[0], attempts[1])
            self.assertIn('71 vs 52', attempts[1])
            self.assertEqual(deliver(snapshot, [], NOW, 123, path, **options), 1)
            self.assertIn('82 (OSCILLATION)', attempts[2])
            self.assertEqual(deliver(snapshot, [], NOW, 123, path, **options), 0)
            state = json.loads(path.read_text())
            self.assertIn('WATCHLIST:scan-1', state['sent'])
            self.assertIn('WATCHLIST:scan-2', state['sent'])
        snapshot['pending_watchlists'] = []
        self.assertEqual(notifications(snapshot, [], NOW, {})[0], [])

    def test_daily_swap_count_uses_local_day_full_times_not_history(self):
        snapshot = report()
        snapshot['watchlist_swap_times'] = [NOW] * 60 + [NOW - 86400000]
        messages, _ = notifications(snapshot, [], NOW + 3600000, {})
        daily = next(message['text'] for message in messages if message['id'].startswith('DAILY:'))
        self.assertIn('60 watchlist swaps', daily)


if __name__ == '__main__':
    unittest.main()
