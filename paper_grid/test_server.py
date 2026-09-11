import http.client
from http.server import ThreadingHTTPServer
import json
import re
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from paper_grid import server


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.monitor = server.Monitor(Path(self.temp.name))
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), server.make_handler(self.monitor, 0))
        self.port = self.http.server_port
        self.http.RequestHandlerClass = server.make_handler(self.monitor, self.port)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.http.server_close)
        self.addCleanup(self.http.shutdown)

    def request(self, path, method='GET', host=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        conn.request(method, path, headers={'Host': host or f'localhost:{self.port}'})
        response = conn.getresponse()
        content = response.read()
        conn.close()
        return response, content

    def test_dashboard_nonce_matches_policy_and_changes_per_response(self):
        first, html = self.request('/')
        second, other = self.request('/')
        policy = first.getheader('Content-Security-Policy')
        nonce = re.search(r"'nonce-([^']+)'", policy)
        self.assertIsNotNone(nonce)
        self.assertNotIn('unsafe-inline', policy)
        self.assertEqual(re.findall(rb'<(?:script|style) nonce="([^"]+)"', html),
                         [nonce.group(1).encode()] * 2)
        self.assertNotEqual(policy, second.getheader('Content-Security-Policy'))
        self.assertNotEqual(html, other)
        self.assertEqual(int(first.getheader('Content-Length')), len(html))
        self.assertIn("script-src-attr 'none'", policy)

    def test_json_and_archive_scripts_have_no_authorization(self):
        response, _ = self.request('/api/health')
        self.assertIn("script-src 'none'", response.getheader('Content-Security-Policy'))
        root = self.monitor.runtime / 'audits'
        root.mkdir()
        (root / 'audit48-123.html').write_text('<style>body{color:red}</style><script>bad()</script>')
        response, body = self.request('/audits/audit48-123.html')
        self.assertIn("script-src 'none'", response.getheader('Content-Security-Policy'))
        self.assertIn(b'<style nonce=', body)
        self.assertIn(b'<script>bad()', body)
        self.assertNotIn(b'<script nonce=', body)

    def test_radar_page_and_json_are_read_only(self):
        radar = self.monitor.runtime.parent / 'radar'
        radar.mkdir(exist_ok=True)
        source = radar / 'radar.json'
        source.write_text('{"schema_version":1,"sections":{}}')
        self.addCleanup(source.unlink, missing_ok=True)
        page, html = self.request('/radar')
        response, content = self.request('/api/radar')
        self.assertEqual(page.status, 200)
        self.assertIn(b'KuCoin grid radar', html)
        self.assertIn("'nonce-", page.getheader('Content-Security-Policy'))
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(content)['schema_version'], 1)
        self.assertEqual(self.request('/api/radar', method='POST')[0].status, 405)

    def test_read_only_routes_and_no_directory_or_state_exposure(self):
        for path in ('/account.json', '/experiment.json', '/../../etc/passwd', '/.env'):
            response, _ = self.request(path)
            self.assertEqual(response.status, 404)
        response, _ = self.request('/api/report', method='POST')
        self.assertEqual(response.status, 405)
        response, _ = self.request('/api/report', host='attacker.example')
        self.assertEqual(response.status, 403)

    def test_report_has_no_side_effect_and_no_store_headers(self):
        with patch.object(server.experiment, 'report', return_value={'mode': 'paper'}) as report:
            with patch.object(server.experiment, 'tick', side_effect=AssertionError('HTTP must not tick')):
                response, content = self.request('/api/report')
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(content), {'mode': 'paper'})
        self.assertEqual(response.getheader('Cache-Control'), 'no-store')
        report.assert_called_once()

    def test_error_does_not_expose_provider_or_secret_message(self):
        with patch.object(server.experiment, 'report', side_effect=ValueError('secret-example-value')):
            response, content = self.request('/api/report')
        self.assertEqual(response.status, 503)
        self.assertNotIn(b'secret-example-value', content)

    def test_analytics_is_read_only_and_cached_independently(self):
        value = {'mode': 'paper', 'status': 'ok', 'windows': {}}
        with patch.object(server.analytics, 'build', return_value=value) as build:
            with patch.object(server.experiment, 'tick', side_effect=AssertionError('must not tick')):
                first, content = self.request('/api/analytics')
                second, _ = self.request('/api/analytics')
        self.assertEqual(first.status, 200)
        self.assertEqual(second.status, 200)
        self.assertEqual(json.loads(content), value)
        self.assertEqual(build.call_count, 1)
        self.assertEqual(first.getheader('Cache-Control'), 'no-store')
        self.assertEqual(self.request('/api/analytics', method='POST')[0].status, 405)

    def test_analytics_failure_is_sanitized_and_does_not_break_report(self):
        with patch.object(server.analytics, 'build', side_effect=ValueError('private-token')):
            response, content = self.request('/api/analytics')
        self.assertEqual(response.status, 503)
        self.assertNotIn(b'private-token', content)
        with patch.object(server.experiment, 'report', return_value={'mode': 'paper'}):
            self.assertEqual(self.request('/api/report')[0].status, 200)

    def test_frozen_experiment_stops_scheduler(self):
        with patch.object(server.experiment, 'tick', return_value={'experiment': {'status': 'frozen'}}) as tick:
            self.monitor.loop()
        self.assertEqual(tick.call_count, 1)
        self.assertIsNotNone(self.monitor.last_poll_at)

    def test_archive_routes_serve_reports_but_reject_symlinks_and_traversal(self):
        root=self.monitor.runtime/'audits'
        root.mkdir()
        (root/'audit48-123.html').write_text('<h1>Paper audit</h1>')
        (root/'private.html').symlink_to(self.monitor.runtime/'experiment.json')
        response,content=self.request('/audits/audit48-123.html')
        self.assertEqual(response.status,200)
        self.assertIn(b'Paper audit',content)
        for path in ('/audits/../experiment.json','/audits/private.html','/audits/.lock','/audits/manifest.json/extra'):
            self.assertEqual(self.request(path)[0].status,404)

    def test_audit_failure_is_visible_without_stopping_paper_worker(self):
        from paper_grid import audits
        def no_wait(_): self.monitor.stop.set()
        self.monitor.stop.wait=no_wait
        with patch.object(server.experiment,'tick',return_value={'experiment':{'continuous':True,'status':'running'}}):
            with patch.object(audits,'generate_due',side_effect=ValueError('do not expose this')):
                self.monitor.loop()
        self.assertIsNone(self.monitor.last_error)
        self.assertEqual(self.monitor.health()['last_audit_error'],'ValueError')


if __name__ == '__main__':
    unittest.main()
