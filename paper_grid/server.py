"""Local read-only dashboard and durable scheduler for a bounded paper experiment."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
import re
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from paper_grid import cli, experiment, analytics, csp, public_autopilot
from trader.autopilot.storage import read_json, read_events


class Monitor:
    def __init__(self, runtime):
        self.runtime = Path(runtime)
        self.stop = threading.Event()
        self.last_poll_at = None
        self.last_error = None
        self.last_audit_error = None
        self.thread = threading.Thread(target=self.loop, name='paper-monitor', daemon=True)

    def loop(self):
        while not self.stop.is_set():
            try:
                result = experiment.tick(self.runtime)
                self.last_error = None
                self.last_poll_at = time.time()
                if result.get('experiment', {}).get('continuous') is True:
                    try:
                        from paper_grid import audits
                        audits.generate_due(self.runtime)
                        self.last_audit_error = None
                    except Exception as error:
                        self.last_audit_error = type(error).__name__
                        print('paper audit: ' + self.last_audit_error, file=sys.stderr, flush=True)
                if result.get('experiment', {}).get('status') in ('frozen', 'completed'):
                    return
            except Exception as error:
                # Provider bodies and credential-bearing exception messages never reach HTTP.
                self.last_error = type(error).__name__
                self.last_poll_at = time.time()
                print('paper monitor: ' + self.last_error, file=sys.stderr, flush=True)
            self.stop.wait(30)

    def health(self):
        result = dict(server_time=time.time(), worker_alive=self.thread.is_alive(),
                      last_poll_at=self.last_poll_at, last_error=self.last_error)
        result['last_audit_error'] = self.last_audit_error
        try:
            data = json.loads((self.runtime / 'experiment.json').read_text())
            result.update(experiment_status=data.get('status'),
                          last_tick_at=data.get('last_tick_at'),
                          report_at=data.get('report_at'),
                          errors=data.get('errors', [])[-5:])
        except (OSError, ValueError):
            result['experiment_status'] = 'unavailable'
        return result


def _tailnet_host(value):
    if not isinstance(value, str) or len(value) > 253:
        raise ValueError('invalid tailnet hostname')
    labels = value.split('.')
    if (len(labels) < 3 or labels[-2:] != ['ts', 'net'] or
            any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                for label in labels)):
        raise ValueError('invalid tailnet hostname')
    return value


def make_handler(monitor, port, *, autopilot_snapshot=None, events_dir=None,
                 radar_snapshot=None, tailnet_host=None, tailnet_port=443,
                 read_only=False):
    allowed_hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
    if tailnet_host is not None:
        host = _tailnet_host(tailnet_host)
        if type(tailnet_port) is not int or not 1 <= tailnet_port <= 65535:
            raise ValueError('invalid tailnet port')
        allowed_hosts.update((host, f'{host}:{tailnet_port}'))
    autopilot_snapshot = Path(autopilot_snapshot or monitor.runtime.parent / "autopilot" / "autopilot.json")
    events_dir = Path(events_dir or autopilot_snapshot.parent)
    radar_snapshot = Path(radar_snapshot or monitor.runtime.parent / "radar" / "radar.json")
    analytics_lock = threading.Lock()
    analytics_cache = {'at': 0, 'payload': None}

    def read_analytics():
        # Bound concurrent archive reads from multiple open dashboard tabs.
        with analytics_lock:
            if analytics_cache['payload'] is None or time.monotonic() - analytics_cache['at'] >= 60:
                payload = analytics.build(monitor.runtime)
                analytics_cache.update(at=time.monotonic(), payload=payload)
            return analytics_cache['payload']

    def read_only_health():
        # No monitor thread in read-only mode: derive liveness from the autopilot
        # snapshot the trader daemon writes. This reports the trader paper
        # pipeline, not the zmarty experiment the full worker health describes.
        result = dict(server_time=time.time(), source='trader-autopilot-file',
                      note='Trader paper pipeline health; not the zmarty experiment.',
                      worker_alive=False, last_poll_at=None, last_error=None,
                      tick_age_s=None)
        try:
            data = read_json(autopilot_snapshot)
        except (OSError, ValueError):
            return result
        stamps = [value for value in (data.get('heartbeat_ms'), data.get('generated_at_ms'))
                  if type(value) in (int, float)]
        age = time.time() - max(stamps) / 1000 if stamps else None
        result['worker_alive'] = age is not None and age < 90
        result['tick_age_s'] = data.get('tick_age_s')
        return result

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, code, content, content_type, *, dashboard=False):
            policy = csp.policy()
            if content_type.startswith('text/html'):
                content, policy = csp.nonce_document(content, scripts=dashboard)
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', policy)
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self):
            if (len(self.headers.get_all('Host', [])) != 1 or
                    self.headers.get('Host') not in allowed_hosts):
                return self.send(403, b'Local access only', 'text/plain')
            path = urlsplit(self.path).path
            if path in ('/', '/paper', '/paper/'):
                return self.send(200, Path(__file__).with_name('paper.html').read_bytes(), 'text/html; charset=utf-8', dashboard=True)
            if path in ('/control', '/control/'):
                return self.send(200, Path(__file__).with_name('dashboard.html').read_bytes(), 'text/html; charset=utf-8', dashboard=True)
            if path in ('/radar', '/radar/'):
                return self.send(200, Path(__file__).with_name('radar.html').read_bytes(),
                                 'text/html; charset=utf-8', dashboard=True)
            if path.startswith('/audits/'):
                name = path[len('/audits/'):]
                if not re.fullmatch(r'[a-zA-Z0-9_-]+\.(html|json|md)', name):
                    return self.send(404, b'Not found', 'text/plain')
                root = (monitor.runtime / 'audits').resolve()
                file = root / name
                if not file.is_file() or file.is_symlink() or file.resolve().parent != root:
                    return self.send(404, b'Not found', 'text/plain')
                kind = {'html': 'text/html; charset=utf-8', 'json': 'application/json', 'md': 'text/plain; charset=utf-8'}[file.suffix[1:]]
                return self.send(200, file.read_bytes(), kind)
            if path not in ('/api/report', '/api/health', '/api/audits', '/api/analytics', '/api/radar', '/api/autopilot', '/api/events'):
                return self.send(404, b'Not found', 'text/plain')
            since = 0
            after_event_id = None
            if path == '/api/events':
                query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                values = query.get('since', ['0'])
                if (set(query) - {'since', 'after_event_id'} or len(values) != 1 or
                        not re.fullmatch(r'[0-9]{1,16}', values[0]) or
                        int(values[0]) > 1e15):
                    return self.send(400, b'{"error":"invalid since"}', 'application/json')
                since = int(values[0])
                if 'after_event_id' in query:
                    cursor = query['after_event_id']
                    if (len(cursor) != 1 or not re.fullmatch(r'[0-9]{1,16}', cursor[0])
                            or int(cursor[0]) > 1e15):
                        return self.send(400, b'{"error":"invalid cursor"}', 'application/json')
                    after_event_id = int(cursor[0])
            try:
                if path == '/api/autopilot':
                    payload = public_autopilot.safe(read_json(autopilot_snapshot))
                elif path == '/api/events':
                    payload = {'events': public_autopilot.events(read_events(events_dir, since, limit=500, after_event_id=after_event_id))}
                elif path == '/api/radar':
                    from paper_grid.public_snapshot import _radar
                    payload = _radar(read_json(radar_snapshot))
                elif path == '/api/analytics':
                    payload = read_analytics()
                elif path == '/api/audits':
                    from paper_grid import audits
                    payload = {'reports': audits.list_reports(monitor.runtime)}
                else:
                    payload = (read_only_health() if read_only else monitor.health()) \
                        if path == '/api/health' else experiment.report(monitor.runtime)
                content = json.dumps(payload, allow_nan=False).encode()
            except Exception as error:
                content = json.dumps({'error': type(error).__name__, 'message': 'Report unavailable; account state preserved.'}).encode()
                return self.send(503, content, 'application/json')
            return self.send(200, content, 'application/json')

        def do_POST(self):
            self.send(405, b'Read-only dashboard', 'text/plain')

        do_PUT = do_POST
        do_DELETE = do_POST

    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, default=cli.DEFAULT_RUNTIME)
    parser.add_argument('--port', type=int, default=8873)
    parser.add_argument('--tailnet-host', type=_tailnet_host, help='Exact tailnet hostname for the read-only proxy')
    parser.add_argument('--tailnet-port', type=int, default=443, help='HTTPS port tailscale serve uses for this proxy')
    parser.add_argument('--read-only', action='store_true', help='Serve files without starting the control scheduler')
    root = Path.home() / 'Sandbox' / 'grokbot'
    parser.add_argument('--autopilot-snapshot', type=Path, default=root / 'autopilot' / 'autopilot.json')
    parser.add_argument('--radar-snapshot', type=Path, default=root / 'radar' / 'radar.json')
    parser.add_argument('--events-dir', type=Path, default=root / 'autopilot')
    args = parser.parse_args(argv)
    if args.tailnet_host and not args.read_only:
        parser.error('--tailnet-host requires --read-only')
    monitor = Monitor(args.runtime)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(monitor, args.port,
        autopilot_snapshot=args.autopilot_snapshot, radar_snapshot=args.radar_snapshot,
        events_dir=args.events_dir, tailnet_host=args.tailnet_host, tailnet_port=args.tailnet_port,
        read_only=args.read_only))
    awake = None
    if not args.read_only and sys.platform == 'darwin':
        try:
            info = experiment.report(args.runtime)['experiment']
            if info.get('continuous') is True and info['status'] == 'running':
                awake = subprocess.Popen(['/usr/bin/caffeinate', '-i', '-w', str(os.getpid())])
            elif info['status'] == 'running':
                remaining = math.ceil(info['end_at'] - time.time())
                if remaining > 0:
                    awake = subprocess.Popen(['/usr/bin/caffeinate', '-i', '-t', str(remaining), '-w', str(os.getpid())])
        except (OSError, ValueError, KeyError):
            print('paper monitor: bounded sleep prevention unavailable', file=sys.stderr, flush=True)
    if not args.read_only:
        monitor.thread.start()

    def shutdown(*_):
        monitor.stop.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        monitor.stop.set()
        server.server_close()
        if not args.read_only:
            monitor.thread.join(timeout=5)
        if awake is not None and awake.poll() is None:
            awake.terminate()


if __name__ == '__main__':
    main()
