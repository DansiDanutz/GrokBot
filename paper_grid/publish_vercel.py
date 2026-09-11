"""Publish sanitized paper snapshots without giving the web host runtime access."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from paper_grid import public_snapshot, csp

DEFAULT_RUNTIME = Path('/Users/davidai/Sandbox/grokbot/zmarty-paper-runtime')
DEFAULT_STATE = Path('/Users/davidai/Sandbox/grokbot/vercel-publisher')
HEALTH_URL = 'http://127.0.0.1:8873/api/health'
DEPLOY_TIMEOUT = 240
URL = re.compile(r'https://[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.vercel\.app\b')
PRIVATE_DIRECTORY_MODE = 0o700


def _safe_path(path):
    path = Path(os.path.abspath(path))
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('symlink publication path')
    return path


def _separate(first, second):
    if first == second or first in second.parents or second in first.parents:
        raise ValueError('publication paths overlap')


def _read_json(path):
    path = _safe_path(path)
    if not path.is_file() or path.stat().st_size > 65536:
        raise ValueError('invalid publisher configuration')
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError('invalid publisher object')
    return value


def _config(state):
    source = _read_json(state / 'config.json')
    patterns = {'project_id': r'prj_[A-Za-z0-9]+',
                'org_id': r'(?:team|user)_[A-Za-z0-9]+',
                'scope': r'[a-z0-9][a-z0-9-]{0,100}'}
    result = {}
    for key, pattern in patterns.items():
        value = source.get(key)
        if not isinstance(value, str) or not re.fullmatch(pattern, value):
            raise ValueError('invalid publisher project identity')
        result[key] = value
    for key in ('node', 'cli'):
        value = source.get(key)
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise ValueError('invalid publisher executable')
        # Homebrew installs Node through symlinks; pin its real executable path.
        path = Path(value).resolve(strict=True)
        if not path.is_file() or (key == 'node' and not os.access(path, os.X_OK)):
            raise ValueError('missing publisher executable')
        result[key] = str(path)
    if 'public_url' in source:
        if not isinstance(source['public_url'], str) or not URL.fullmatch(source['public_url']):
            raise ValueError('invalid public dashboard URL')
        result['public_url'] = source['public_url']
    _secure_state(state)
    return result


def _secure_state(state):
    auth = _safe_path(state / 'auth')
    if not auth.is_dir():
        raise ValueError('publisher authentication unavailable')
    initial = _safe_path(state / 'initial-site')
    if initial.exists() and not initial.is_dir():
        raise ValueError('invalid initial publication directory')
    auth.chmod(PRIVATE_DIRECTORY_MODE)


def _cleanup_initial_site(state):
    initial = _safe_path(state / 'initial-site')
    if initial.exists():
        if not initial.is_dir():
            raise ValueError('invalid initial publication directory')
        shutil.rmtree(initial)


def _atomic_json(path, value):
    _safe_path(path)
    fd, temporary = tempfile.mkstemp(prefix='.publisher-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(value, handle, indent=2, allow_nan=False)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _health():
    try:
        # Never route local status through environment HTTP proxies or redirects.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        with opener.open(HEALTH_URL, timeout=3) as response:
            data = response.read(65537)
        if len(data) > 65536:
            return None
        result = json.loads(data)
        return result if isinstance(result, dict) else None
    except (OSError, ValueError):
        return None


def _site_config(stage, config):
    headers = [{'key': 'X-Content-Type-Options', 'value': 'nosniff'},
               {'key': 'Referrer-Policy', 'value': 'no-referrer'},
               {'key': 'X-Frame-Options', 'value': 'DENY'},
               {'key': 'Permissions-Policy', 'value': 'camera=(), microphone=(), geolocation=()'},
               {'key': 'Cache-Control', 'value': 'no-store'},
               {'key': 'X-Robots-Tag', 'value': 'noindex, nofollow'}]
    _atomic_json(stage / 'vercel.json', {'version': 2, 'framework': None,
        'buildCommand': None, 'installCommand': None,
        'headers': [{'source': '/(.*)', 'headers': headers}, *_csp_routes(stage)]})
    (stage / '.vercel').mkdir()
    _atomic_json(stage / '.vercel' / 'project.json',
                 {'projectId': config['project_id'], 'orgId': config['org_id']})


def _csp_routes(stage):
    dashboard = csp.static_policy((stage / 'index.html').read_bytes())
    reports = b''.join(_safe_path(path).read_bytes()
                       for path in sorted((stage / 'reports').glob('*.html')))
    policies = [('/', dashboard), ('/index.html', dashboard)]
    for name in ('paper', 'radar', 'control'):
        page = stage / name / 'index.html'
        policy = (csp.static_policy(_safe_path(page).read_bytes())
                  if page.is_file() else csp.policy())
        policies.extend([('/' + name, policy), ('/' + name + '/(.*)', policy)])
    policies.extend([('/reports/(.*)', csp.static_policy(reports, scripts=False)),
                     ('/data/(.*)', csp.policy())])
    return [{'source': path, 'headers': [{'key': 'Content-Security-Policy', 'value': value}]}
            for path, value in policies]


def _cleanup_warning(state):
    try:
        _cleanup_initial_site(state)
    except (OSError, ValueError):
        return 'initial_site_cleanup_failed'
    return None


def _deploy(stage, state, config):
    command = [config['node'], config['cli'], 'deploy', str(stage), '--prod', '--yes',
               '--scope', config['scope'], '--global-config', str(state / 'auth')]
    env = {'HOME': str(Path.home()), 'PATH': str(Path(config['node']).parent) + ':/usr/bin:/bin:/usr/sbin:/sbin',
           'CI': '1', 'NO_COLOR': '1', 'VERCEL_TELEMETRY_DISABLED': '1'}
    completed = subprocess.run(command, cwd=stage, env=env, capture_output=True,
                               text=True, timeout=DEPLOY_TIMEOUT, check=False)
    if completed.returncode:
        raise RuntimeError('deployment_failed')
    # CLI diagnostics are never printed or persisted: they may contain secrets.
    try:
        structured = json.loads(completed.stdout or '')
    except ValueError:
        structured = None
    if isinstance(structured, dict) and isinstance(structured.get('deployment'), dict):
        deployment = structured['deployment']
        if structured.get('status') != 'ok' or deployment.get('readyState') != 'READY':
            raise RuntimeError('deployment_not_ready')
        value = deployment.get('url')
        if isinstance(value, str):
            value = value if value.startswith('https://') else 'https://' + value
            if URL.fullmatch(value):
                return value
        raise RuntimeError('deployment_url_missing')
    urls = URL.findall(completed.stdout or '')
    if not urls:
        raise RuntimeError('deployment_url_missing')
    return urls[-1]


def publish(runtime=DEFAULT_RUNTIME, state=DEFAULT_STATE, *, now=None,
            radar_path=None, autopilot_path=None):
    """Return sanitized status; a failed deployment never deletes the prior site."""
    runtime, state = _safe_path(runtime), _safe_path(state)
    _separate(runtime, state)
    if not runtime.is_dir():
        raise ValueError('paper runtime unavailable')
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = _safe_path(state / 'publisher.lock')
    with lock_path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'status': 'busy'}
        at = time.time() if now is None else now
        previous = {}
        status_path = _safe_path(state / 'publisher.json')
        if status_path.exists():
            previous = _read_json(status_path)
        status = {'status': 'running', 'last_attempt_at': at,
                  'last_success_at': previous.get('last_success_at'),
                  'deployment_url': previous.get('deployment_url'),
                  'public_url': previous.get('public_url'), 'error': None}
        _atomic_json(status_path, status)
        phase = 'configuration_invalid'
        try:
            config = _config(state)
            phase = 'snapshot_failed'
            with tempfile.TemporaryDirectory(prefix='danslabtrader-stage-', dir=state.parent) as directory:
                stage = _safe_path(directory)
                _separate(stage, runtime)
                _separate(stage, state)
                paths = {key: value for key, value in
                         (('radar_path', radar_path), ('autopilot_path', autopilot_path))
                         if value is not None}
                metadata = public_snapshot.export_snapshot(runtime, stage, now=at, health=_health(), **paths)
                _site_config(stage, config)
                phase = 'deployment_failed'
                deployment = _deploy(stage, state, config)
            status.update(status='published', last_success_at=at, deployment_url=deployment,
                          public_url=config.get('public_url'), file_count=metadata['file_count'],
                          report_count=metadata['report_count'], snapshot_at=metadata['published_at'],
                          cleanup_warning=_cleanup_warning(state))
        except subprocess.TimeoutExpired:
            status.update(status='failed', error='deployment_timeout')
        except (OSError, ValueError, RuntimeError, KeyError, TypeError):
            status.update(status='failed', error=phase)
        _atomic_json(status_path, status)
        return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument('--state', type=Path, default=DEFAULT_STATE)
    parser.add_argument('--radar-snapshot', type=Path)
    parser.add_argument('--autopilot-snapshot', type=Path)
    args = parser.parse_args()
    try:
        result = publish(args.runtime, args.state, radar_path=args.radar_snapshot,
                         autopilot_path=args.autopilot_snapshot)
    except (OSError, ValueError):
        result = {'status': 'failed', 'error': 'publisher_paths_or_state_invalid'}
    print(json.dumps(result, allow_nan=False))
    return 1 if result['status'] == 'failed' else 0


if __name__ == '__main__':
    sys.exit(main())
