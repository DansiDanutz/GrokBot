#!/usr/bin/env python3
"""Evaluate bounded team research requests; never apply rules or trade.

Operator configuration lives outside experiments_root. Native assistants place
<request_id>.json in experiments_root/inbox; run --request ID acknowledges it in
results/ID/result.json. `context` emits the current request fields without writes.
The sole evaluator is the pinned checkout's trader.review.daily. SHADOW means
analysis exists, never that a gate passed or a rule was applied.
This same-user workflow is not an OS security boundary against its own user.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from zoneinfo import ZoneInfo

RULES = frozenset({'min_hold_hours_before_non_risk_close',
                   'require_trend_alignment', 'symbol_cooldowns'})
IDENTIFIER = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}')
SHA256 = re.compile(r'[a-f0-9]{64}')
REVISION = re.compile(r'[a-f0-9]{40}')
MAX_JSON = 64 * 1024
MAX_STATE = 64 * 1024 * 1024
CONFIG_FIELDS = {'version', 'experiments_root', 'source_root', 'strategy_revision',
                 'python', 'state', 'database', 'events', 'evidence_files'}
REQUEST_FIELDS = {'version', 'request_id', 'rule_id', 'review_date', 'created_at',
                  'strategy_revision', 'evidence_hashes'}


class BridgeError(ValueError):
    pass


def checked_path(value, *, exists=True):
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts:
        raise BridgeError('paths must be absolute and canonical')
    for part in reversed([path, *path.parents]):
        try:
            mode = part.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise BridgeError('symlink path refused')
    if exists and not path.exists():
        raise BridgeError('required input missing')
    return path


def read_bytes(path, limit=MAX_JSON):
    checked_path(str(path))
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise BridgeError('input must be a bounded regular file')
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise BridgeError('input too large')
    return data


def json_object(raw):
    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise BridgeError('duplicate JSON key')
            out[key] = value
        return out
    try:
        result = json.loads(raw, object_pairs_hook=unique,
                            parse_constant=lambda _: (_ for _ in ()).throw(BridgeError('nonfinite JSON')))
    except (ValueError, UnicodeError) as error:
        raise BridgeError('invalid JSON') from error
    if not isinstance(result, dict):
        raise BridgeError('expected JSON object')
    return result


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def load_config(path):
    path = checked_path(path)
    info = path.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise BridgeError('operator config must be owned by current user, not group/world writable')
    config = json_object(read_bytes(path))
    if set(config) != CONFIG_FIELDS or type(config.get('version')) is not int or config['version'] != 1:
        raise BridgeError('unsupported operator config')
    if not isinstance(config['strategy_revision'], str) or not REVISION.fullmatch(config['strategy_revision']):
        raise BridgeError('full strategy revision required')
    for key in ['experiments_root', 'source_root', 'python', 'state', 'database', 'events']:
        config[key] = checked_path(config[key])
    root = config['experiments_root']
    if not root.is_dir() or root.stat().st_uid != os.getuid() or root.stat().st_mode & 0o077:
        raise BridgeError('experiments root must be a private owned directory (0700)')
    if path.is_relative_to(root):
        raise BridgeError('operator config must be outside experiments root')
    for key in ['source_root', 'state', 'database', 'events']:
        if config[key].is_relative_to(root) or root.is_relative_to(config[key]):
            raise BridgeError('experiment output must be isolated from source and runtime inputs')
    if not config['events'].is_dir() or not config['source_root'].is_dir():
        raise BridgeError('source and events must be directories')
    # State/events must refer to one canonical paper runtime, not separate systems.
    if config['state'].parent != config['events']:
        raise BridgeError('state and events must share the paper runtime directory')
    evidence = config['evidence_files']
    if not isinstance(evidence, dict) or not 1 <= len(evidence) <= 8:
        raise BridgeError('one to eight evidence inputs required')
    for key, value in evidence.items():
        if not IDENTIFIER.fullmatch(key):
            raise BridgeError('invalid evidence name')
        evidence[key] = checked_path(value)
        if evidence[key].is_relative_to(root):
            raise BridgeError('evidence cannot come from the request inbox')
    return config


def source_revision(config):
    source = config['source_root']
    def git(*args):
        result = subprocess.run(['/usr/bin/git', '-C', str(source), *args],
                                check=True, capture_output=True, text=True, timeout=15)
        return result.stdout.strip()
    if Path(git('rev-parse', '--show-toplevel')) != source:
        raise BridgeError('source must be repository root')
    head = git('rev-parse', 'HEAD')
    if head != config['strategy_revision']:
        raise BridgeError('strategy revision changed')
    if git('status', '--porcelain', '--untracked-files=no', '--', 'trader', 'paper_grid'):
        raise BridgeError('tracked evaluator sources are dirty')
    for name in ['trader/review/daily.py', 'trader/review/rules.py', 'trader/review/__init__.py']:
        checked_path(str(source / name))
        git('ls-files', '--error-unmatch', name)
    return head


def source_manifest(config):
    """Hash the actual evaluator import trees, rejecting untracked Python code."""
    source_revision(config)
    source = config['source_root']
    result = subprocess.run(['/usr/bin/git', '-C', str(source), 'ls-files', '-z'],
                            check=True, capture_output=True, timeout=15)
    tracked = set(result.stdout.decode().split('\0'))
    paths = list(source.glob('*.py'))
    for package in ['trader', 'paper_grid']:
        paths.extend((source / package).rglob('*.py'))
    manifest = {}
    for path in sorted(paths):
        name = path.relative_to(source).as_posix()
        if name not in tracked:
            raise BridgeError('untracked evaluator Python source')
        manifest[name] = digest(read_bytes(path, MAX_STATE))
    if not manifest:
        raise BridgeError('empty evaluator source tree')
    return manifest


def export_sources(config, manifest, directory):
    """Execute only verified Python files, never the checkout's import namespace."""
    target = directory / 'evaluator-source'
    target.mkdir(mode=0o700)
    for name, expected in manifest.items():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts or relative.suffix != '.py':
            raise BridgeError('invalid evaluator source manifest')
        raw = read_bytes(config['source_root'] / relative, MAX_STATE)
        if digest(raw) != expected:
            raise BridgeError('evaluator source changed before export')
        output = target / relative
        output.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        atomic_file(output, raw)
    return target


def evidence_hashes(config):
    return {name: digest(read_bytes(path, MAX_STATE))
            for name, path in config['evidence_files'].items()}


def context(config, now=None):
    now = now or datetime.now(timezone.utc)
    return {'version': 1, 'strategy_revision': source_revision(config),
            'created_at': now.isoformat(),
            'review_date': (now.astimezone(ZoneInfo('Europe/Bucharest')).date() - timedelta(days=1)).isoformat(),
            'evidence_hashes': evidence_hashes(config), 'allowed_rule_ids': sorted(RULES)}


def validate_request(request, config, request_id, now):
    if set(request) != REQUEST_FIELDS or type(request.get('version')) is not int or request['version'] != 1:
        raise BridgeError('unsupported request fields; commands and code are not accepted')
    if request['request_id'] != request_id:
        raise BridgeError('request ID does not match inbox filename')
    if not isinstance(request['rule_id'], str) or not IDENTIFIER.fullmatch(request['rule_id']):
        raise BridgeError('invalid rule identifier')
    if request['strategy_revision'] != config['strategy_revision']:
        raise BridgeError('request strategy revision mismatch')
    try:
        created = datetime.fromisoformat(request['created_at'].replace('Z', '+00:00'))
        day = datetime.strptime(request['review_date'], '%Y-%m-%d').date()
    except (ValueError, TypeError, AttributeError) as error:
        raise BridgeError('invalid request dates') from error
    if created.tzinfo is None or not -30 <= (now - created).total_seconds() <= 3600:
        raise BridgeError('request is stale or future dated')
    today = now.astimezone(ZoneInfo('Europe/Bucharest')).date()
    if request['review_date'] != day.isoformat() or not today - timedelta(days=30) <= day < today:
        raise BridgeError('review must be a completed day in retained 30-day window')
    hashes = request['evidence_hashes']
    if not isinstance(hashes, dict) or set(hashes) != set(config['evidence_files']):
        raise BridgeError('evidence set mismatch')
    if any(not isinstance(value, str) or not SHA256.fullmatch(value) for value in hashes.values()):
        raise BridgeError('invalid evidence hash')
    if hashes != evidence_hashes(config):
        raise BridgeError('evidence changed since request')
    source_revision(config)


def atomic_file(path, raw):
    checked_path(str(path), exists=False)
    if path.exists():
        raise BridgeError('refusing to overwrite an artifact')
    fd, temporary = tempfile.mkstemp(prefix='.bridge-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        # Hard-link publication is atomic and refuses replacement of any existing name.
        os.link(temporary, path, follow_symlinks=False)
    finally:
        os.unlink(temporary)


def atomic_json(path, value):
    atomic_file(path, (json.dumps(value, indent=2, sort_keys=True) + '\n').encode())


@contextmanager
def locked(root):
    checked_path(str(root))
    fd = os.open(root / '.bridge.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise BridgeError('invalid lock')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise BridgeError('another bridge evaluation is running') from error
        yield
    finally:
        os.close(fd)


EVALUATOR_CODE = """
import json, runpy, sys
source, requested_rule, metrics_path, rules_path = sys.argv[1:5]
sys.path.insert(0, source)
sys.argv = ['trader.review.daily'] + sys.argv[5:]
try:
    runpy.run_module('trader.review.daily', run_name='__main__')
except SystemExit as error:
    if error.code not in (None, 0):
        raise
from trader.review import rules
with open(sys.argv[sys.argv.index('--proposals-out') + 1]) as handle:
    props = json.load(handle)
current = rules.load_rules(rules_path) if rules_path else {}
planned = [p for p in rules.plan_changes(props, current, props['date'])
           if p['rule'] == requested_rule]
coverage = (props.get('totals') or {}).get('data_coverage') or {}
metrics = {'requested_rule': requested_rule, 'review_date': props['date'],
           'rule_evidence_statuses': [p['status'] for p in planned],
           'defer_reasons': [p['defer_reason'] for p in planned if p.get('defer_reason')],
           'opened': props['summary']['opened'], 'closed': props['summary']['closed'],
           'cumulative_closed': (props.get('totals') or {}).get('closed_total'),
           'coverage_flagged': coverage.get('flagged'),
           'current_rules_available': bool(rules_path)}
with open(metrics_path, 'x') as handle:
    json.dump(metrics, handle, sort_keys=True, indent=2)
"""


def evaluate(config, request, directory):
    # Freeze the paper state for a coherent review without writing the live runtime.
    manifest = source_manifest(config)
    atomic_json(directory / 'source-hashes.json', manifest)
    evaluator_source = export_sources(config, manifest, directory)
    state = read_bytes(config['state'], MAX_STATE)
    atomic_file(directory / 'state-input.json', state)
    rules_path = directory / 'rules-input.json'
    if 'learned_rules' in config['evidence_files']:
        atomic_file(rules_path, read_bytes(config['evidence_files']['learned_rules'], MAX_STATE))
    command = [str(config['python']), '-B', '-I', '-X',
               'pycache_prefix=' + str(directory / 'unused-cache'), '-c', EVALUATOR_CODE,
               str(evaluator_source), request['rule_id'], str(directory / 'metrics.json'),
               str(rules_path) if rules_path.exists() else '', '--date', request['review_date'],
               '--state', str(directory / 'state-input.json'), '--db', str(config['database']),
               '--events', str(config['events']), '--out', str(directory / 'review.md'),
               '--proposals-out', str(directory / 'proposals.json')]
    # No inherited provider credentials, Python path, or subprocess output in acknowledgments.
    environment = {'PATH': '/usr/bin:/bin', 'HOME': str(Path.home()), 'TZ': 'Europe/Bucharest',
                   'LANG': 'en_US.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1'}
    subprocess.run(command, cwd=evaluator_source, env=environment, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180)
    if source_manifest(config) != manifest:
        raise BridgeError('evaluator source changed during evaluation')
    if evidence_hashes(config) != request['evidence_hashes']:
        raise BridgeError('evidence changed during evaluation')
    proposals = json_object(read_bytes(directory / 'proposals.json', MAX_STATE))
    if proposals.get('date') != request['review_date'] or not isinstance(proposals.get('proposals'), list):
        raise BridgeError('evaluator output has invalid date or proposals')
    matched = [p for p in proposals['proposals'] if isinstance(p, dict) and p.get('rule') == request['rule_id']]
    metrics = json_object(read_bytes(directory / 'metrics.json'))
    if metrics.get('requested_rule') != request['rule_id'] or metrics.get('review_date') != request['review_date']:
        raise BridgeError('evaluator metrics do not match request')
    # An evaluator 'apply' plan is still SHADOW here; this worker has no writer authority.
    statuses = metrics.get('rule_evidence_statuses', [])
    status = 'SHADOW' if matched and 'apply' in statuses and metrics.get('coverage_flagged') is False else 'DEFERRED'
    return {'status': status, 'reason': 'research evaluation only; no rule applied',
            'candidate_observed': bool(matched), 'rule_evidence_statuses': statuses,
            'evidence_hashes': request['evidence_hashes'],
            'evaluator_source_sha256': digest(read_bytes(directory / 'source-hashes.json', MAX_STATE)),
            'metrics_sha256': digest(read_bytes(directory / 'metrics.json')),
            'state_input_sha256': digest(state),
            'proposals_sha256': digest(read_bytes(directory / 'proposals.json', MAX_STATE)),
            'report_sha256': digest(read_bytes(directory / 'review.md', MAX_STATE))}


ACK_STATUSES = frozenset({'SHADOW', 'DEFERRED', 'NEEDS_IMPLEMENTATION', 'REJECTED', 'BLOCKED'})
ACK_BASE = {'version', 'request_id', 'request_sha256', 'strategy_revision',
            'acknowledged_at', 'completed_at', 'applied', 'status', 'reason', 'artifact_hashes'}
ACK_REQUEST = {'rule_id', 'review_date', 'evidence_hashes'}
ACK_EVALUATION = {'candidate_observed', 'rule_evidence_statuses', 'evaluator_source_sha256',
                  'metrics_sha256', 'state_input_sha256', 'proposals_sha256', 'report_sha256'}


def artifact_hashes(directory):
    out = {}
    for path in sorted(directory.rglob('*')):
        checked_path(str(path))
        if path.is_dir():
            continue
        name = path.relative_to(directory).as_posix()
        if name not in {'result.json', 'result.sha256'}:
            out[name] = digest(read_bytes(path, MAX_STATE))
        if len(out) > 1024:
            raise BridgeError('too many result artifacts')
    return out


def cached_receipt(config, request_id, raw, directory):
    """Validate a historical receipt without treating it as fresh evaluation."""
    receipt_bytes = read_bytes(directory / 'result.json')
    prior = json_object(receipt_bytes)
    if prior.get('request_sha256') != digest(raw):
        raise BridgeError('request ID reused with different content')
    if (type(prior.get('version')) is not int or prior['version'] != 1
            or prior.get('request_id') != request_id
            or prior.get('strategy_revision') != config['strategy_revision']
            or prior.get('applied') is not False
            or prior.get('status') not in ACK_STATUSES
            or not ACK_BASE <= set(prior)
            or set(prior) - (ACK_BASE | ACK_REQUEST | ACK_EVALUATION)):
        raise BridgeError('invalid cached acknowledgment')
    if read_bytes(directory / 'result.sha256').decode().strip() != digest(receipt_bytes):
        raise BridgeError('cached acknowledgment was edited')
    if read_bytes(directory / 'request.json') != raw:
        raise BridgeError('archived request mismatch')
    if prior['artifact_hashes'] != artifact_hashes(directory):
        raise BridgeError('cached result artifact changed')
    if set(prior) & ACK_REQUEST:
        request = json_object(raw)
        if not ACK_REQUEST <= set(prior) or any(prior[key] != request.get(key)
                for key in ['rule_id', 'review_date']) or prior['evidence_hashes'] != request.get('evidence_hashes'):
            raise BridgeError('cached request metadata mismatch')
    if prior['status'] == 'NEEDS_IMPLEMENTATION':
        if not ACK_REQUEST <= set(prior) or prior['rule_id'] in RULES or set(prior) & ACK_EVALUATION:
            raise BridgeError('invalid implementation acknowledgment')
    if prior['status'] in {'SHADOW', 'DEFERRED'}:
        if not (ACK_REQUEST | ACK_EVALUATION) <= set(prior) or prior['rule_id'] not in RULES:
            raise BridgeError('incomplete cached evaluation')
        for key, name in [('metrics_sha256', 'metrics.json'), ('state_input_sha256', 'state-input.json'),
                          ('proposals_sha256', 'proposals.json'), ('report_sha256', 'review.md'),
                          ('evaluator_source_sha256', 'source-hashes.json')]:
            if prior[key] != prior['artifact_hashes'].get(name):
                raise BridgeError('cached evaluation hash mismatch')
        metrics = json_object(read_bytes(directory / 'metrics.json'))
        props = json_object(read_bytes(directory / 'proposals.json', MAX_STATE))
        if (metrics.get('requested_rule') != prior['rule_id'] or metrics.get('review_date') != prior['review_date']
                or props.get('date') != prior['review_date'] or not isinstance(props.get('proposals'), list)):
            raise BridgeError('cached evaluator request mismatch')
        matched = any(isinstance(p, dict) and p.get('rule') == prior['rule_id'] for p in props['proposals'])
        statuses = metrics.get('rule_evidence_statuses')
        if (not isinstance(statuses, list) or any(value not in {'apply', 'defer'} for value in statuses)
                or prior['candidate_observed'] is not matched or prior['rule_evidence_statuses'] != statuses):
            raise BridgeError('cached evaluator status mismatch')
        expected = 'SHADOW' if matched and 'apply' in statuses and metrics.get('coverage_flagged') is False else 'DEFERRED'
        if prior['status'] != expected or prior['reason'] != 'research evaluation only; no rule applied':
            raise BridgeError('cached acknowledgment contradicts evaluation')
        manifest = json_object(read_bytes(directory / 'source-hashes.json', MAX_STATE))
        exported = {name.removeprefix('evaluator-source/'): value for name, value in prior['artifact_hashes'].items()
                    if name.startswith('evaluator-source/')}
        if manifest != exported:
            raise BridgeError('cached exported source mismatch')
        if 'learned_rules' in prior['evidence_hashes'] and prior['artifact_hashes'].get('rules-input.json') != prior['evidence_hashes']['learned_rules']:
            raise BridgeError('cached rules evidence mismatch')
    return prior


def process(config, request_id, *, now=None):
    if not IDENTIFIER.fullmatch(request_id):
        raise BridgeError('invalid request ID')
    now = now or datetime.now(timezone.utc)
    root = config['experiments_root']
    with locked(root):
        inbox = checked_path(str(root / 'inbox'))
        raw = read_bytes(inbox / (request_id + '.json'))
        fingerprint = digest(raw)
        results = checked_path(str(root / 'results'), exists=False)
        results.mkdir(mode=0o700, exist_ok=True)
        directory = checked_path(str(results / request_id), exists=False)
        receipt = directory / 'result.json'
        if receipt.exists():
            return cached_receipt(config, request_id, raw, directory)
        if directory.exists():
            raise BridgeError('incomplete reserved request; inspect before using a new ID')
        directory.mkdir(mode=0o700)
        atomic_file(directory / 'request.json', raw)
        result = {'version': 1, 'request_id': request_id, 'request_sha256': fingerprint,
                  'strategy_revision': config['strategy_revision'], 'acknowledged_at': now.isoformat(),
                  'applied': False}
        try:
            request = json_object(raw)
            validate_request(request, config, request_id, now)
            result.update(rule_id=request['rule_id'], review_date=request['review_date'],
                          evidence_hashes=request['evidence_hashes'])
            if request['rule_id'] not in RULES:
                result.update(status='NEEDS_IMPLEMENTATION', reason='unknown rule; no evaluator or code executed')
            else:
                result.update(evaluate(config, request, directory))
                if read_bytes(inbox / (request_id + '.json')) != raw:
                    raise BridgeError('request changed during evaluation')
        except BridgeError as error:
            result.update(status='REJECTED', reason=str(error))
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            result.update(status='BLOCKED', reason='evaluation failed: ' + type(error).__name__)
        result['completed_at'] = datetime.now(timezone.utc).isoformat()
        result['artifact_hashes'] = artifact_hashes(directory)
        atomic_json(receipt, result)
        atomic_file(directory / 'result.sha256', (digest(read_bytes(receipt)) + '\n').encode())
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('operation', choices=['context', 'run'])
    parser.add_argument('--request')
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if args.operation == 'run' and not args.request:
            raise BridgeError('--request required')
        result = context(config) if args.operation == 'context' else process(config, args.request)
        print(json.dumps(result, sort_keys=True))
        return 0 if result.get('status') not in {'REJECTED', 'BLOCKED'} else 1
    except (BridgeError, OSError, TypeError, subprocess.SubprocessError):
        print('paper-team bridge refused input or failed preflight', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
