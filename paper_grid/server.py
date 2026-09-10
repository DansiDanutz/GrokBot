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
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from paper_grid import cli, experiment


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


def make_handler(monitor, port):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, code, content, content_type):
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self):
            if self.headers.get('Host') not in (f'127.0.0.1:{port}', f'localhost:{port}'):
                return self.send(403, b'Local access only', 'text/plain')
            path = urlsplit(self.path).path
            if path == '/':
                return self.send(200, Path(__file__).with_name('dashboard.html').read_bytes(), 'text/html; charset=utf-8')
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
            if path not in ('/api/report', '/api/health', '/api/audits'):
                return self.send(404, b'Not found', 'text/plain')
            try:
                if path == '/api/audits':
                    from paper_grid import audits
                    payload = {'reports': audits.list_reports(monitor.runtime)}
                else:
                    payload = monitor.health() if path == '/api/health' else experiment.report(monitor.runtime)
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
    args = parser.parse_args(argv)
    monitor = Monitor(args.runtime)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(monitor, args.port))
    awake = None
    if sys.platform == 'darwin':
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
        monitor.thread.join(timeout=5)
        if awake is not None and awake.poll() is None:
            awake.terminate()


if __name__ == '__main__':
    main()
