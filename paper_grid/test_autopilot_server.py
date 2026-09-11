import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch, MagicMock

from paper_grid import server
from trader.autopilot import policy


class AutopilotServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / 'autopilot.json'
        self.events = self.root / 'events'
        self.events.mkdir()
        self.source.write_text(json.dumps(policy.snapshot(policy.new_state(1000), 2000,
            dict(heartbeat_ms=2000, tick_age_s=10, kucoin_ok=True, radar_age_min=1))))
        self.monitor = server.Monitor(self.root)
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), server.make_handler(self.monitor, 0))
        self.port = self.http.server_port
        self.http.RequestHandlerClass = server.make_handler(self.monitor, self.port,
            autopilot_snapshot=self.source, events_dir=self.events)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.addCleanup(self.http.server_close)
        self.addCleanup(self.http.shutdown)

    def get(self, path, host=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        conn.request('GET', path, headers={'Host': host} if host else {})
        response = conn.getresponse()
        data = response.read()
        conn.close()
        return response.status, data

    def test_tailnet_host_requires_explicit_exact_allowlist(self):
        host = 'studio.tail-example.ts.net'
        self.assertEqual(self.get('/api/autopilot', host=host)[0], 403)
        self.http.RequestHandlerClass = server.make_handler(self.monitor, self.port,
            autopilot_snapshot=self.source, tailnet_host=host)
        for accepted in (host, host+':443'):
            self.assertEqual(self.get('/api/autopilot', host=accepted)[0], 200)
        for rejected in ('other.tail-example.ts.net', host+':80', 'attacker.example', host+'.evil'):
            self.assertEqual(self.get('/api/autopilot', host=rejected)[0], 403)
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        conn.request('GET', '/api/autopilot', headers={'Host': 'attacker.example',
            'X-Forwarded-Host': host, 'Forwarded': 'host='+host})
        response = conn.getresponse()
        self.assertEqual(response.status, 403)
        response.read()
        conn.close()
        for invalid in ('*.ts.net', 'studio.example', 'Studio.ts.net', 'x.ts.net:443',
                        'x.ts.net/route', '-x.ts.net', 'x..ts.net'):
            with self.assertRaises(ValueError):
                server.make_handler(self.monitor, self.port, tailnet_host=invalid)

    def test_snapshot_readonly_allowlist_and_route_csp(self):
        with patch.object(server.experiment, 'tick', side_effect=AssertionError('no tick')):
            status, data = self.get('/api/autopilot')
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(data)['equity'], 10000)
            self.assertEqual(self.get('/control')[0], 200)

    def test_snapshot_rejects_ancestor_symlink_and_oversize_without_details(self):
        self.source.write_text(' ' * (2*1024*1024+1))
        self.assertEqual(self.get('/api/autopilot')[0], 503)
        linked = self.root / 'linked'
        linked.symlink_to(self.root, target_is_directory=True)
        self.http.RequestHandlerClass = server.make_handler(self.monitor, self.port,
            autopilot_snapshot=linked/'autopilot.json')
        status, data = self.get('/api/autopilot')
        self.assertEqual(status, 503)
        self.assertNotIn(str(self.root).encode(), data)

    def test_events_are_bounded_numeric_allowlist_and_since_validated(self):
        rows = [dict(ts_ms=i+1, bot_id=1, symbol='RAYUSDTM', type='GRID', profit=.2,
                     private_number=123) for i in range(510)]
        (self.events/'events.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
        status, body = self.get('/api/events?since=1')
        events = json.loads(body)['events']
        self.assertEqual(status, 200)
        self.assertEqual(len(events), 500)
        self.assertEqual(events[0]['ts_ms'], 2)
        self.assertNotIn('private_number', events[0])
        for query in ('-1', 'nan', '1.5', '1&since=2', '1&unknown=1'):
            self.assertEqual(self.get('/api/events?since='+query)[0], 400)

    def test_event_cursor_pages_same_timestamp_without_loss_or_duplicates(self):
        rows = [dict(ts_ms=10, event_id=i, bot_id=1, symbol='RAYUSDTM', type='GRID', profit=.2)
                for i in range(1, 511)]
        rows.append(dict(ts_ms=11, event_id=511, bot_id=1, symbol='RAYUSDTM', type='GRID'))
        (self.events/'events.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
        first = json.loads(self.get('/api/events?since=0')[1])['events']
        self.assertEqual(len(first), 500)
        second = json.loads(self.get('/api/events?since=10&after_event_id=500')[1])['events']
        self.assertEqual([r['event_id'] for r in first + second], list(range(1, 512)))
        self.assertEqual(json.loads(self.get('/api/events?since=11&after_event_id=511')[1])['events'], [])
        for query in ('-1', '1.5', 'nan', '1&after_event_id=2', '1000000000000001'):
            self.assertEqual(self.get('/api/events?since=10&after_event_id='+query)[0], 400)

    def test_events_reject_free_text_symlinks_and_oversized_archive(self):
        file = self.events/'events.jsonl'
        file.write_text(json.dumps(dict(ts_ms=1, bot_id=1, symbol='RAYUSDTM',
            type='ERROR', message='private body'))+'\n')
        status, body = self.get('/api/events?since=0')
        self.assertEqual(status, 503)
        self.assertNotIn(b'private body', body)
        file.write_text(' '* (2*1024*1024+1))
        self.assertEqual(self.get('/api/events?since=0')[0], 503)
        file.unlink()
        file.symlink_to(self.source)
        self.assertEqual(self.get('/api/events?since=0')[0], 503)


class ReadOnlyMainTests(unittest.TestCase):
    def test_tailnet_cli_requires_readonly(self):
        with self.assertRaises(SystemExit), patch.object(server, 'ThreadingHTTPServer') as http:
            server.main(['--tailnet-host', 'studio.tail-example.ts.net'])
        http.assert_not_called()

    def test_readonly_never_starts_scheduler_caffeinate_or_audits(self):
        monitor = MagicMock()
        with patch.object(server, 'Monitor', return_value=monitor), \
             patch.object(server, 'ThreadingHTTPServer') as http, \
             patch.object(server.signal, 'signal'), \
             patch.object(server.subprocess, 'Popen') as popen, \
             patch.object(server.experiment, 'report') as report:
            server.main(['--read-only', '--port', '8875', '--tailnet-host', 'studio.tail-example.ts.net'])
        monitor.thread.start.assert_not_called()
        monitor.thread.join.assert_not_called()
        popen.assert_not_called()
        report.assert_not_called()
        self.assertEqual(http.call_args.args[0], ('127.0.0.1', 8875))
