import io
import json
from pathlib import Path
import tempfile
import unittest

from trader.radar.telegram import deliver


def report(symbol='AAAUSDTM'):
    row = dict(symbol=symbol, direction='LONG', expected_grids_per_hour=2.5,
               range_low=1.0, range_high=2.0, step_pct=.8, grids=50)
    return {'sections': {'long': [row], 'short': [], 'neutral': []}}


class RadarTelegramTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / 'telegram-state.json'
        self.requests = []

    def opener(self, request, timeout):
        self.requests.append((request, timeout))
        return io.BytesIO(b'{"ok":true}')

    def test_posts_top_three_then_suppresses_unchanged_symbols(self):
        environment = {'DLS_TELEGRAM_BOT_TOKEN': '12345:test-token'}
        self.assertTrue(deliver(report(), '123456', self.state,
                                opener=self.opener, environ=environment))
        self.assertFalse(deliver(report(), '123456', self.state,
                                 opener=self.opener, environ=environment))
        self.assertEqual(len(self.requests), 1)
        body = json.loads(self.requests[0][0].data)
        self.assertEqual(body['chat_id'], '123456')
        self.assertIn('AAAUSDTM', body['text'])
        self.assertNotIn('test-token', self.state.read_text())
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o600)

    def test_changed_top_three_posts_and_missing_token_fails_closed(self):
        environment = {'DLS_TELEGRAM_BOT_TOKEN': '12345:test-token'}
        deliver(report(), '-123456', self.state, opener=self.opener, environ=environment)
        self.assertTrue(deliver(report('BBBUSDTM'), '-123456', self.state,
                                opener=self.opener, environ=environment))
        with self.assertRaisesRegex(ValueError, 'credential unavailable'):
            deliver(report(), '123456', self.state, opener=self.opener, environ={})
        def leaking_opener(*_args, **_kwargs):
            raise OSError('12345:test-token')
        with self.assertRaisesRegex(RuntimeError, '^Telegram delivery failed$'):
            deliver(report('CCCUSDTM'), '123456', self.state,
                    opener=leaking_opener, environ=environment)


if __name__ == '__main__':
    unittest.main()
