"""Publisher isolation and failure behavior without any Vercel requests."""
import fcntl
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from paper_grid import publish_vercel as publisher


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.runtime = self.root / 'runtime'
        self.runtime.mkdir()
        (self.runtime / 'private.env').write_text('SECRET=private-token')
        self.state = self.root / 'publisher'
        self.state.mkdir()
        (self.state / 'auth').mkdir()
        (self.state / 'auth' / 'auth.json').write_text('{"token":"private-token"}')
        self.node = self.root / 'node'
        self.node.write_text('test')
        self.node.chmod(0o700)
        self.cli = self.root / 'vc.js'
        self.cli.write_text('test')
        self.config = dict(project_id='prj_test', org_id='team_test', scope='test-projects',
                           node=str(self.node), cli=str(self.cli), public_url='https://danslabtrader.vercel.app')
        self.save_config()

    def save_config(self):
        (self.state / 'config.json').write_text(json.dumps(self.config))

    def export(self, runtime, stage, now, health):
        self.assertEqual(runtime, self.runtime)
        self.assertEqual(list(stage.iterdir()), [])
        self.assertNotIn(self.state, stage.parents)
        (stage / 'index.html').write_text('paper-only')
        return {'file_count': 1, 'report_count': 0, 'published_at': now}

    def run_publish(self, action):
        with patch.object(publisher.public_snapshot, 'export_snapshot', side_effect=self.export), \
             patch.object(publisher, '_health', return_value=None), \
             patch.object(publisher.subprocess, 'run', side_effect=action):
            return publisher.publish(self.runtime, self.state, now=1234567)

    def test_isolated_project_and_credentials_only_at_cli(self):
        def deploy(command, **kwargs):
            stage = Path(kwargs['cwd'])
            self.assertEqual(command[-4:], ['--scope', 'test-projects', '--global-config', str(self.state / 'auth')])
            self.assertIn('--prod', command)
            self.assertEqual(json.loads((stage / '.vercel/project.json').read_text()),
                             {'projectId': 'prj_test', 'orgId': 'team_test'})
            self.assertFalse((stage / '.env.local').exists())
            self.assertFalse((stage / 'auth').exists())
            self.assertFalse((stage / 'private.env').exists())
            self.assertNotIn('private-token', json.dumps(kwargs, default=str))
            config = json.loads((stage / 'vercel.json').read_text())
            self.assertIn({'key': 'Cache-Control', 'value': 'no-store'}, config['headers'][0]['headers'])
            self.stage = stage
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps({'status': 'ok',
                'deployment': {'url': 'https://danslabtrader-build.vercel.app', 'readyState': 'READY'}}), stderr='')
        result = self.run_publish(deploy)
        self.assertEqual(result['status'], 'published')
        self.assertEqual(result['deployment_url'], 'https://danslabtrader-build.vercel.app')
        self.assertFalse(self.stage.exists())
        self.assertEqual((self.runtime / 'private.env').read_text(), 'SECRET=private-token')

    def test_failed_deployment_preserves_last_success_and_sanitizes(self):
        (self.state / 'publisher.json').write_text(json.dumps({'last_success_at': 111,
             'deployment_url': 'https://previous.vercel.app'}))
        result = self.run_publish(lambda *a, **k: subprocess.CompletedProcess(a, 1, stdout='private-token', stderr='secret'))
        self.assertEqual(result['error'], 'deployment_failed')
        self.assertEqual(result['last_success_at'], 111)
        self.assertEqual(result['deployment_url'], 'https://previous.vercel.app')
        self.assertNotIn('private-token', (self.state / 'publisher.json').read_text())
        self.assertEqual(list(self.root.glob('danslabtrader-stage-*')), [])

    def test_timeout_is_reported_without_output(self):
        def fail(*args, **kwargs):
            raise subprocess.TimeoutExpired('secret', 240, output='private-token')
        result = self.run_publish(fail)
        self.assertEqual(result['error'], 'deployment_timeout')
        self.assertNotIn('secret', json.dumps(result))

    def test_overlap_and_symlink_rejected_before_export(self):
        with self.assertRaises(ValueError):
            publisher.publish(self.runtime, self.runtime / 'publisher')
        link = self.root / 'alias'
        link.symlink_to(self.state, target_is_directory=True)
        with self.assertRaises(ValueError):
            publisher.publish(self.runtime, link)

    def test_busy_does_not_change_status(self):
        with (self.state / 'publisher.lock').open('a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(publisher.publish(self.runtime, self.state), {'status': 'busy'})
        self.assertFalse((self.state / 'publisher.json').exists())

    def test_invalid_identity_never_deploys(self):
        self.config['scope'] = '--another-project'
        self.save_config()
        with patch.object(publisher.subprocess, 'run') as deploy:
            result = publisher.publish(self.runtime, self.state)
            deploy.assert_not_called()
        self.assertEqual(result['error'], 'configuration_invalid')

    def test_missing_success_url_is_failure(self):
        result = self.run_publish(lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout='ok', stderr=''))
        self.assertEqual(result['status'], 'failed')

    def test_structured_unready_deployment_is_failure(self):
        result = self.run_publish(lambda *a, **k: subprocess.CompletedProcess(a, 0,
            stdout=json.dumps({'status': 'ok', 'deployment': {'url': 'https://test.vercel.app',
                                                             'readyState': 'ERROR'}}), stderr=''))
        self.assertEqual(result['status'], 'failed')

    def test_snapshot_failure_never_deploys(self):
        with patch.object(publisher.public_snapshot, 'export_snapshot', side_effect=ValueError('private-token')), \
             patch.object(publisher, '_health', return_value=None), \
             patch.object(publisher.subprocess, 'run') as deploy:
            result = publisher.publish(self.runtime, self.state)
        deploy.assert_not_called()
        self.assertEqual(result['error'], 'snapshot_failed')
        self.assertEqual(list(self.root.glob('danslabtrader-stage-*')), [])


if __name__ == '__main__':
    unittest.main()
