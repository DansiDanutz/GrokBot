"""The controller has to be scheduled by launchd, not by a token-funded heartbeat."""
from pathlib import Path
import plistlib
import unittest

EXAMPLE = (Path(__file__).parents[2]
           / 'config/launchd/com.danslab.trader-team-controller.plist.example')


class TeamControllerLaunchdTests(unittest.TestCase):
    def setUp(self):
        self.config = plistlib.loads(EXAMPLE.read_bytes())

    def test_it_runs_hourly_at_minute_fifteen(self):
        self.assertEqual(self.config['Label'], 'com.danslab.trader-team-controller')
        self.assertEqual(self.config['StartCalendarInterval'], {'Minute': 15})
        self.assertFalse(self.config['RunAtLoad'])

    def test_it_runs_the_team_module_from_the_production_checkout(self):
        arguments = self.config['ProgramArguments']
        self.assertEqual(self.config['WorkingDirectory'],
                         '/Users/davidai/ZCodeProject/GrokBot-prod')
        self.assertEqual(arguments[0], '/opt/homebrew/bin/python3')
        self.assertIn('trader.team', arguments)
        self.assertIn('/Users/davidai/Sandbox/grokbot/team-evidence', arguments)
        self.assertIn('/Users/davidai/Sandbox/grokbot/team-experiments', arguments)
        self.assertIn('/Users/davidai/Sandbox/grokbot/autopilot', arguments)
        self.assertIn('/Users/davidai/Sandbox/grokbot/radar/radar.json', arguments)

    def test_it_logs_where_the_controller_keeps_its_evidence(self):
        self.assertEqual(self.config['StandardOutPath'],
                         '/Users/davidai/Sandbox/grokbot/team-evidence/controller.out.log')

    def test_it_carries_no_credentials_of_any_kind(self):
        self.assertNotIn('EnvironmentVariables', self.config)
        values = str(self.config).lower()
        for banned in ('token', 'secret', 'api_key', 'password', 'chat-id',
                       'credential'):
            self.assertNotIn(banned, values)


if __name__ == '__main__':
    unittest.main()
