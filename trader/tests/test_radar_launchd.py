from pathlib import Path
import plistlib
import unittest


class RadarLaunchdTests(unittest.TestCase):
    def test_template_runs_read_only_radar_at_minute_five(self):
        path = Path(__file__).parents[2] / 'config/launchd/com.danslab.trader-radar.plist.example'
        config = plistlib.loads(path.read_bytes())
        arguments = config['ProgramArguments']
        self.assertEqual(config['Label'], 'com.danslab.trader-radar')
        self.assertEqual(config['StartCalendarInterval'], {'Minute': 5})
        self.assertFalse(config['RunAtLoad'])
        self.assertIn('trader.radar', arguments)
        self.assertIn('/Users/davidai/Sandbox/grokbot/market-data/phase-2-20260911/market.sqlite3', arguments)
        self.assertIn('/Users/davidai/Sandbox/grokbot/radar/radar.json', arguments)
        self.assertIn('scripts/credential-exec.py', arguments)
        self.assertIn('/Users/davidai/.config/danslab/credentials/telegram.json', arguments)
        self.assertIn('--telegram-chat-id', arguments)
        self.assertNotIn('EnvironmentVariables', config)


if __name__ == '__main__':
    unittest.main()
