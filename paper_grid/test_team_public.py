"""team.json has to reach the native bots: published to Vercel and served locally.

A native Grok Bot can only read public URLs, so if this file is missing from
either surface the whole team is unreachable and every role sits idle.
"""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from paper_grid import server
from paper_grid.public_snapshot import _team_payload

TEAM = dict(schema_version=2, generated_at_ms=1789321500000, cycle_id='CTRL-20260913-20',
            last_cycle_at='2026-09-13T17:45:00Z', scheduler='hourly :15 Europe/Bucharest',
            doctor=dict(status='warn', failing=[]), open_dispatches=1,
            dispatches=[dict(dispatch_id='D-1', role_name='Risk Sentinel',
                             room='Paper Grid Trading Team', event='DOCTOR_FAIL',
                             instruction='look at it', created_at='2026-09-13T17:45:00Z',
                             due_at='2026-09-13T19:45:00Z', status='PENDING',
                             payload=dict(check='radar'))],
            roster=[dict(name='Risk Sentinel', kind='native', status='OK',
                         installed=True, idle_h=0.5, max_idle_h=24.0,
                         rooms=['Paper Grid Trading Team'], last_seen_at=None)])


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.runtime = self.root / 'zmarty-paper-runtime'
        self.runtime.mkdir()

    def test_the_controllers_file_is_staged_under_data(self):
        path = self.root / 'autopilot' / 'team.json'
        path.parent.mkdir()
        path.write_text(json.dumps(TEAM))
        payload = json.loads(_team_payload(self.runtime, None, 1789321500))
        self.assertEqual(payload['cycle_id'], 'CTRL-20260913-20')
        self.assertEqual(payload['dispatches'][0]['role_name'], 'Risk Sentinel')
        self.assertEqual(payload['published_at_ms'], 1789321500000)

    def test_a_missing_controller_file_publishes_unavailable_not_stale_work(self):
        payload = json.loads(_team_payload(self.runtime, None, 1789321500))
        self.assertEqual(payload['status'], 'unavailable')
        self.assertEqual(payload['schema_version'], 2)

    def test_an_explicitly_named_snapshot_must_exist(self):
        with self.assertRaises((OSError, ValueError)):
            _team_payload(self.runtime, self.root / 'nope.json', 1789321500)

    def test_a_local_path_inside_the_snapshot_never_reaches_the_site(self):
        path = self.root / 'autopilot' / 'team.json'
        path.parent.mkdir()
        leaky = json.loads(json.dumps(TEAM))
        leaky['dispatches'][0]['instruction'] = 'read /Users/davidai/secret.json'
        path.write_text(json.dumps(leaky))
        payload = _team_payload(self.runtime, None, 1789321500)
        self.assertNotIn(b'/Users', payload)


class ServeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        (self.root / 'autopilot').mkdir()
        (self.root / 'autopilot' / 'team.json').write_text(json.dumps(TEAM))

    def _get(self, path, team=None):
        monitor = type('M', (), dict(runtime=self.root / 'runtime'))()
        handler = server.make_handler(monitor, 8873, read_only=True,
                                     autopilot_snapshot=self.root / 'autopilot' / 'autopilot.json',
                                     team_snapshot=team or self.root / 'autopilot' / 'team.json')
        captured = {}

        def send(self_, code, content, kind, **kw):
            captured.update(code=code, content=content, kind=kind)
        with patch.object(handler, 'send', send):
            request = object.__new__(handler)
            request.path = path
            request.headers = {'Host': '127.0.0.1:8873'}
            request.headers = type('H', (), dict(
                get=lambda _s, key, default=None: '127.0.0.1:8873',
                get_all=lambda _s, key, default=None: ['127.0.0.1:8873']))()
            handler.do_GET(request)
        return captured

    def test_the_desk_serves_the_same_team_file_at_data_team_json(self):
        got = self._get('/data/team.json')
        self.assertEqual(got['code'], 200)
        self.assertEqual(got['kind'], 'application/json')
        payload = json.loads(got['content'])
        self.assertEqual(payload['cycle_id'], 'CTRL-20260913-20')
        self.assertEqual(payload['dispatches'][0]['dispatch_id'], 'D-1')

    def test_a_missing_file_serves_unavailable_rather_than_an_error(self):
        got = self._get('/data/team.json', team=self.root / 'gone.json')
        self.assertEqual(got['code'], 200)
        self.assertEqual(json.loads(got['content'])['status'], 'unavailable')

    def test_no_other_data_path_is_served_locally(self):
        self.assertEqual(self._get('/data/report.json')['code'], 404)


if __name__ == '__main__':
    unittest.main()
