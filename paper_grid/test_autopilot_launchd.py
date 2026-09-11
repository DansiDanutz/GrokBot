"""Offline validation of operator-only launchd examples."""
from pathlib import Path
import plistlib
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / 'config' / 'launchd'


class AutopilotLaunchdTests(unittest.TestCase):
    def load(self, name):
        return plistlib.loads((EXAMPLES / f'com.danslab.trader-{name}.plist.example').read_bytes())

    def test_autopilot_is_wrapped_single_daemon_not_cron(self):
        data = self.load('autopilot')
        self.assertEqual(data['Label'], 'com.danslab.trader-autopilot')
        self.assertTrue(data['KeepAlive'])
        self.assertFalse(data['RunAtLoad'])
        self.assertNotIn('StartInterval', data)
        self.assertNotIn('StartCalendarInterval', data)
        args = data['ProgramArguments']
        self.assertIn('scripts/credential-exec.py', args)
        self.assertEqual(args[args.index('-m')+1:args.index('-m')+3], ['trader.autopilot', 'run'])
        for flag in ('--database', '--state', '--radar', '--snapshot', '--telegram-state'):
            self.assertTrue(args[args.index(flag)+1].startswith('/Users/davidai/Sandbox/grokbot/'))
        self.assertIn('REPLACE_WITH_DAN_CHAT_ID', args)

    def test_publisher_targets_grokbot_with_readonly_control_input(self):
        data = self.load('publisher')
        self.assertEqual(data['StartInterval'], 300)
        args = data['ProgramArguments']
        self.assertIn('scripts/credential-exec.py', args)
        self.assertIn('/Users/davidai/ZCodeProject/GrokBot/paper_grid/publish_vercel.py', args)
        self.assertEqual(args[args.index('--runtime')+1], '/Users/davidai/Sandbox/grokbot/zmarty-paper-runtime')
        self.assertEqual(args[args.index('--autopilot-snapshot')+1], '/Users/davidai/Sandbox/grokbot/autopilot/autopilot.json')
        self.assertEqual(args[args.index('--radar-snapshot')+1], '/Users/davidai/Sandbox/grokbot/radar/radar.json')

    def test_all_examples_have_logs_and_no_embedded_environment(self):
        for name in ('radar', 'autopilot', 'publisher'):
            with self.subTest(name=name):
                data = self.load(name)
                self.assertNotIn('EnvironmentVariables', data)
                self.assertEqual(data['WorkingDirectory'], '/Users/davidai/ZCodeProject/GrokBot')
                self.assertIn('StandardOutPath', data)
                self.assertIn('StandardErrorPath', data)
                self.assertNotIn('bootout', str(data))
                self.assertNotIn('bootstrap', str(data))
        self.assertEqual(self.load('radar')['StartCalendarInterval'], {'Minute': 5})
