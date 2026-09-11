"""Serve synthetic dashboard evidence on ephemeral loopback ports; no worker loop."""
from contextlib import ExitStack
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import threading
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from paper_grid import analytics, cli, engine, audits, metric_sample, public_snapshot, publish_vercel, server
from phase0_http_evidence import make_public_fixture

FIXTURE_HOST = '127.0.0.1'
DYNAMIC_PORT = 0


def portfolio(doc):
    at = doc['last_tick_at']
    market = doc['observations'][-1]['market']
    accounts = {arm: engine.status(account['state'], market, at, doc['config'])
                for arm, account in doc['accounts'].items()}
    return dict(experiment=dict(mode='paper',status='running',continuous=True,
        start_at=doc['start_at'],report_at=at,last_tick_at=at,end_at=None),
        accounts=accounts,history=[],events=doc['events'],config=doc['config'],
        coinglass=doc['observations'][-1]['coinglass'],errors=[],
        limitations=['SYNTHETIC FIXTURE ONLY; no live runtime, providers or credentials.'])


def public_stage(root, report, learning):
    stage = root/'public'
    (stage/'data').mkdir(parents=True)
    (stage/'reports').mkdir()
    (stage/'index.html').write_bytes(public_snapshot.PUBLIC_INDEX.read_bytes())
    reference = subprocess.run(['git','show','abaf392:paper_grid/public/index.html'],
        cwd=Path(__file__).resolve().parents[2],capture_output=True,check=True).stdout
    (stage/'baseline.html').write_bytes(reference)
    cli.atomic_json(stage/'data'/'analytics.json', learning)
    cli.atomic_json(stage/'data'/'report.json', public_snapshot._report(report, report['experiment']['report_at']))
    cli.atomic_json(stage/'data'/'health.json', {'worker_running': False, 'status':'synthetic'})
    cli.atomic_json(stage/'reports'/'index.json', {'reports': []})
    (stage/'reports'/'synthetic.html').write_text(audits._render(metric_sample.report())[0])
    publish_vercel._site_config(stage, {'project_id':'prj_fixture','org_id':'team_fixture'})
    return stage, json.loads((stage/'vercel.json').read_text())['headers']


def main():
    with tempfile.TemporaryDirectory(prefix='grok-phase1-ui-') as folder, ExitStack() as stack:
        root = Path(folder).resolve()
        runtime = root/'fixture-runtime'
        runtime.mkdir()
        doc = metric_sample.document()
        cli.atomic_json(runtime/'experiment.json', doc)
        learning = analytics.build(runtime, doc['last_tick_at'])
        report = portfolio(doc)
        stage, rules = public_stage(root, report, learning)
        monitor = server.Monitor(runtime)
        stack.enter_context(patch.object(server.analytics, 'build', return_value=learning))
        stack.enter_context(patch.object(server.experiment, 'report', return_value=report))
        local = ThreadingHTTPServer((FIXTURE_HOST,DYNAMIC_PORT), server.make_handler(monitor,DYNAMIC_PORT))
        local.RequestHandlerClass = server.make_handler(monitor, local.server_port)
        public = ThreadingHTTPServer((FIXTURE_HOST,DYNAMIC_PORT), make_public_fixture(stage,rules))
        instances = (local,public)
        threads = [threading.Thread(target=x.serve_forever,daemon=True) for x in instances]
        try:
            for thread in threads: thread.start()
            print(json.dumps({'local':f'http://{FIXTURE_HOST}:{local.server_port}/#learning',
                'public':f'http://{FIXTURE_HOST}:{public.server_port}/#learning',
                'synthetic':True,'worker_started':False}),flush=True)
            input('Enter to close the synthetic fixtures.\n')
        finally:
            for instance in instances: instance.shutdown(); instance.server_close()
            for thread in threads: thread.join()
        assert not monitor.thread.is_alive()
        print('Closed synthetic fixtures; no provider, credential or live-runtime access.')


if __name__ == '__main__':
    main()
