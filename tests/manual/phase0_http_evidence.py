"""Curl source-only dashboard fixtures; never start the paper monitor or publisher."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from paper_grid import server, publish_vercel

FIXTURE_HOST = '127.0.0.1'
DYNAMIC_PORT = 0
FIXTURE_STYLE = b'body { color: navy; }'


def public_fixture_headers(rules, path):
    selected = path if path in ('/', '/index.html') else None
    if path.startswith('/reports/'):
        selected = '/reports/(.*)'
    elif path.startswith('/data/'):
        selected = '/data/(.*)'
    return [header for rule in rules if rule['source'] in ('/(.*)', selected)
            for header in rule['headers']]


def make_public_fixture(stage, rules):
    class PublicFixture(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def end_headers(self):
            for header in public_fixture_headers(rules, self.path):
                self.send_header(header['key'], header['value'])
            super().end_headers()
    return partial(PublicFixture, directory=str(stage))


def stage_public(root):
    stage = root / 'public'
    stage.mkdir()
    source = Path(publish_vercel.__file__).with_name('public') / 'index.html'
    (stage / 'index.html').write_bytes(source.read_bytes())
    (stage / 'reports').mkdir()
    report = publish_vercel.public_snapshot._audit_files({'kind': 'daily', 'summary': 'Fixture'})['.html']
    (stage / 'reports' / 'daily-fixture.html').write_bytes(b'<style>' + FIXTURE_STYLE + b'</style>' + report)
    publish_vercel._site_config(stage, {'project_id': 'prj_fixture', 'org_id': 'team_fixture'})
    return stage, json.loads((stage / 'vercel.json').read_text())['headers']


def curl(name, instance, path='/'):
    url = f'http://{FIXTURE_HOST}:{instance.server_port}{path}'
    command = ['curl', '--noproxy', '*', '-sS', '--dump-header', '-', '--output', '/dev/null', url]
    print(f'{name}: curl --noproxy "*" -sS --dump-header - --output /dev/null {url}', flush=True)
    subprocess.run(command, check=True)


def main():
    with tempfile.TemporaryDirectory(prefix='grok-phase0-05-http-') as directory:
        root = Path(directory).resolve()
        runtime = root / 'empty-runtime'
        runtime.mkdir()
        monitor = server.Monitor(runtime)
        local = ThreadingHTTPServer((FIXTURE_HOST, DYNAMIC_PORT), server.make_handler(monitor, DYNAMIC_PORT))
        local.RequestHandlerClass = server.make_handler(monitor, local.server_port)
        stage, rules = stage_public(root)
        public = ThreadingHTTPServer((FIXTURE_HOST, DYNAMIC_PORT), make_public_fixture(stage, rules))
        active = [(instance, threading.Thread(target=instance.serve_forever, daemon=True))
                  for instance in (local, public)]
        try:
            for _, thread in active:
                thread.start()
            curl('LOCAL_NONCE_DASHBOARD', local)
            curl('STATIC_PUBLIC_HEADER_FIXTURE', public)
            curl('STATIC_REPORT_HEADER_FIXTURE', public, '/reports/daily-fixture.html')
            if '--browser' in sys.argv:
                input('Browser fixtures ready; enter to close.\n')
        finally:
            for instance, thread in active:
                instance.shutdown()
                instance.server_close()
                thread.join()
        assert not monitor.thread.is_alive()
        print('Monitor never started; no runtime tick, deploy, credential access or external network call.')


if __name__ == '__main__':
    main()
