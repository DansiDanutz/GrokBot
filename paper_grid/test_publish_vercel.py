"""Publisher isolation and failure behavior without any Vercel requests."""
import fcntl
import base64
import hashlib
import re
import stat
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

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

    def test_paper_control_and_radar_each_have_own_csp(self):
        stage = self.root / 'stage'
        stage.mkdir()
        (stage / 'index.html').write_text('<script>paper()</script>')
        for page in ('paper', 'radar', 'control'):
            (stage / page).mkdir()
            (stage / page / 'index.html').write_text('<script>' + page + '()</script>')
        rules = {row['source']: row['headers'][0]['value'] for row in publisher._csp_routes(stage)}
        self.assertEqual(rules['/'], rules['/paper'])
        for page in ('paper', 'radar', 'control'):
            digest = base64.b64encode(hashlib.sha256((page + '()').encode()).digest()).decode()
            self.assertIn(digest, rules['/' + page])
            self.assertEqual(rules['/' + page], rules['/' + page + '/(.*)'])

    def test_explicit_snapshot_paths_forwarded_without_provider_calls(self):
        def export(runtime, stage, now, health, **kwargs):
            self.assertEqual(kwargs, {'radar_path': self.root / 'radar.json',
                                      'autopilot_path': self.root / 'autopilot.json'})
            return self.export(runtime, stage, now, health)
        with patch.object(publisher.public_snapshot, 'export_snapshot', side_effect=export), \
             patch.object(publisher, '_health', return_value=None), \
             patch.object(publisher, '_deploy', return_value='https://test.vercel.app'):
            result = publisher.publish(self.runtime, self.state, now=1234567,
                radar_path=self.root / 'radar.json', autopilot_path=self.root / 'autopilot.json')
        self.assertEqual(result['status'], 'published')

    def test_static_csp_hashes_exact_dashboard_bytes_without_reused_nonce(self):
        stage = self.root / 'stage'
        stage.mkdir()
        source = Path(publisher.__file__).with_name('public') / 'index.html'
        html = source.read_bytes()
        (stage / 'index.html').write_bytes(html)
        publisher._site_config(stage, self.config)
        config = json.loads((stage / 'vercel.json').read_text())
        root_rule = next(rule for rule in config['headers'] if rule['source'] == '/')
        policy = root_rule['headers'][0]['value']
        self.assertNotIn('unsafe-inline', policy)
        self.assertNotIn('nonce-', policy)
        self.assertIn("script-src-attr 'none'", policy)
        for tag in (b'script', b'style'):
            body = re.search(b'<' + tag + b'>(.*?)</' + tag + b'>', html, re.S).group(1)
            digest = base64.b64encode(hashlib.sha256(body).digest()).decode()
            self.assertIn("'sha256-" + digest + "'", policy)
        self.assertEqual((stage / 'index.html').read_bytes(), html)

    def test_auth_directory_is_private_and_initial_site_removed_after_success(self):
        auth = self.state / 'auth'
        auth.chmod(0o755)
        initial = self.state / 'initial-site'
        initial.mkdir()
        (initial / 'index.html').write_text('superseded bootstrap')
        result = self.run_publish(lambda *a, **k: subprocess.CompletedProcess(
            a, 0, stdout='https://test.vercel.app', stderr=''))
        self.assertEqual(result['status'], 'published')
        self.assertEqual(stat.S_IMODE(auth.stat().st_mode), 0o700)
        self.assertFalse(initial.exists())
        self.assertTrue((auth / 'auth.json').exists())

    def test_failed_deploy_preserves_initial_site(self):
        initial = self.state / 'initial-site'
        initial.mkdir()
        (initial / 'index.html').write_text('bootstrap')
        self.run_publish(lambda *a, **k: subprocess.CompletedProcess(a, 1, stdout='', stderr=''))
        self.assertEqual((initial / 'index.html').read_text(), 'bootstrap')

    def test_initial_site_symlink_is_rejected_without_deleting_target(self):
        outside = self.root / 'unrelated'
        outside.mkdir()
        (outside / 'sentinel').write_text('preserve')
        (self.state / 'initial-site').symlink_to(outside, target_is_directory=True)
        deploy = Mock(return_value=subprocess.CompletedProcess((), 0, stdout='https://test.vercel.app', stderr=''))
        result = self.run_publish(deploy)
        deploy.assert_not_called()
        self.assertEqual(result['status'], 'failed')
        self.assertEqual((outside / 'sentinel').read_text(), 'preserve')

    def test_report_style_hashes_are_authorized_without_dashboard_scripts(self):
        stage = self.root / 'stage'
        stage.mkdir()
        (stage / 'index.html').write_text('<script>dashboard()</script>')
        reports = stage / 'reports'
        reports.mkdir()
        report = publisher.public_snapshot._audit_files({'kind': 'daily', 'summary': 'Fixture'})['.html']
        style = b'body { color: navy; }'
        (reports / 'daily-fixture.html').write_bytes(b'<style>' + style + b'</style>' + report)
        publisher._site_config(stage, self.config)
        rules = json.loads((stage / 'vercel.json').read_text())['headers']
        report_policy = next(row['headers'][0]['value'] for row in rules if row['source'] == '/reports/(.*)')
        digest = base64.b64encode(hashlib.sha256(style).digest()).decode()
        self.assertIn("style-src 'sha256-" + digest + "'", report_policy)
        self.assertIn("script-src 'none'", report_policy)
        self.assertNotIn("'sha256-" + base64.b64encode(hashlib.sha256(b'dashboard()').digest()).decode(), report_policy)
        self.assertFalse(any(header['key'] == 'Content-Security-Policy' for header in rules[0]['headers']))

    def test_cleanup_failure_preserves_successful_publication_and_warns_safely(self):
        deploy = lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout='https://new.vercel.app', stderr='')
        with patch.object(publisher, '_cleanup_initial_site', side_effect=OSError('private-example')):
            result = self.run_publish(deploy)
        self.assertEqual(result['status'], 'published')
        self.assertEqual(result['last_success_at'], 1234567)
        self.assertEqual(result['deployment_url'], 'https://new.vercel.app')
        self.assertIsNone(result['error'])
        self.assertEqual(result['cleanup_warning'], 'initial_site_cleanup_failed')
        self.assertNotIn('private-example', json.dumps(result))

    def test_initial_site_file_rejected_before_deployment(self):
        (self.state / 'initial-site').write_text('preserve unrelated file')
        deploy = Mock(return_value=subprocess.CompletedProcess((), 0, stdout='https://test.vercel.app', stderr=''))
        result = self.run_publish(deploy)
        deploy.assert_not_called()
        self.assertEqual(result['error'], 'configuration_invalid')
        self.assertEqual((self.state / 'initial-site').read_text(), 'preserve unrelated file')

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
