"""Bounded, explicitly requested offline chart replay; no scheduling or retrieval.

Each invocation advances a limited number of chunks. A new process may resume
only the same source, data, funding, registration, variant, window and mode.
"""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys
import tempfile
import zlib

from trader.research.kucoin_cli import _safe_location, _read_json, _encoded, _asof, _wrap_snapshot, load_funding_history
from trader.research.kucoin_portfolio import ROOT, _registered
from trader.research.kucoin_snapshot import Snapshot, SnapshotError
from trader.research.kucoin_replay import run_chunk, range_policy_parameters

HOUR = 3600000
MARKER = '.offline-chart-replay.json'
PURPOSE = 'offline-income-chart-replay'
ARTIFACTS = {MARKER, 'checkpoint.json.gz', 'result.json.gz', 'summary.json.gz'}
MAX_COMPRESSED = 512*1024*1024
MAX_EXPANDED = 2*1024*1024*1024


def _source_revision():
    revision = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'],
        check=True, capture_output=True, text=True).stdout.strip()
    changed = subprocess.run(['git', '-C', str(ROOT), 'diff', '--quiet', 'HEAD', '--', 'trader'], capture_output=True)
    untracked = subprocess.run(['git', '-C', str(ROOT), 'ls-files', '--others', '--exclude-standard', '--', 'trader'],
        check=True, capture_output=True, text=True).stdout
    if not re.fullmatch('[0-9a-f]{40}', revision) or changed.returncode or untracked:
        raise ValueError('replay requires a frozen full source HEAD and clean trader tree')
    return revision


def _hash_file(path):
    path = _safe_location(path)
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError('input must be a detached regular file')
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _digest(value):
    return hashlib.sha256(_encoded(value)).hexdigest()


def _parameters(document, args):
    if document.get('id') != 'grid-kucoin-v3-income-chart':
        raise ValueError('the committed income-chart registration is required')
    variants = dict(bias_mode=args.bias_mode, regime_gate=args.regime_gate == 'on')
    for key, value in variants.items():
        if value not in document.get('sweep', {}).get(key, []):
            raise ValueError('variant is not declared in the committed registration')
    fixed = document.get('fixed_parameters')
    if not isinstance(fixed, dict) or fixed.get('strategy') != 'income_chart_v3':
        raise ValueError('registration must seal income_chart_v3 fixed parameters')
    if any(key in fixed and fixed[key] != value for key, value in variants.items()):
        raise ValueError('variant conflicts with registered fixed parameters')
    return range_policy_parameters(dict(fixed, **variants))


def _inputs(args):
    start, end = _asof(args.start), _asof(args.end)
    if min(start, end) < 0 or start % HOUR or end % HOUR or start >= end:
        raise ValueError('start/end require increasing aligned UTC hours')
    if not 1 <= args.chunk_hours <= 744 or not 1 <= args.max_chunks <= 744:
        raise ValueError('chunk-hours and max-chunks must be between one and 744')
    paths = {key: _safe_location(getattr(args, key)) for key in ('snapshot', 'funding_history', 'registration', 'output')}
    document = _registered(paths['registration'])
    parameters = _parameters(document, args)
    funding = load_funding_history(paths['funding_history'])
    manifest = _safe_location(paths['snapshot'].with_name('snapshot.json'))
    _read_json(manifest)
    hashes = dict(snapshot_sha256=_hash_file(paths['snapshot']), funding_sha256=_hash_file(paths['funding_history']),
                  registration_sha256=_hash_file(paths['registration']), snapshot_manifest_sha256=_hash_file(manifest))
    seed = document.get('random_seed')
    if type(seed) is not int:
        raise ValueError('registration requires an integer random seed')
    binding = dict(schema_version=1, source_revision=_source_revision(), **hashes,
                   start_ms=start, end_ms=end, parameters=parameters, mode=args.mode, seed=seed)
    return paths, funding, binding


def _fsync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_json(path, value, compressed=True):
    path = _safe_location(path)
    descriptor, temporary = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            if compressed:
                with gzip.GzipFile(fileobj=stream, mode='wb', mtime=0) as zipped:
                    for fragment in json.JSONEncoder(sort_keys=True, allow_nan=False).iterencode(value):
                        zipped.write(fragment.encode())
            else:
                stream.write(_encoded(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _read_gzip(path):
    path = _safe_location(path)
    if not stat.S_ISREG(path.stat().st_mode) or path.stat().st_size > MAX_COMPRESSED:
        raise ValueError('checkpoint/result exceeds bounded compressed file size')
    with gzip.open(path, 'rb') as stream:
        raw = stream.read(MAX_EXPANDED+1)
    if len(raw) > MAX_EXPANDED:
        raise ValueError('checkpoint/result exceeds bounded expanded size')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate checkpoint/result JSON key')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite checkpoint/result')))


def _existing(output, binding):
    if not output.exists():
        return None
    if not output.is_dir() or not (output/MARKER).is_file():
        raise ValueError('output must be new or owned by this offline replay')
    owner = _read_json(output/MARKER)
    if owner != dict(schema_version=1, purpose=PURPOSE, binding=binding):
        raise ValueError('output binding differs from source, data, registration or parameters')
    for path in output.iterdir():
        _safe_location(path)
        if not path.is_file() or path.name not in ARTIFACTS and not path.name.startswith('.pending-'):
            raise ValueError('foreign file in owned replay output')
    if not (output/'checkpoint.json.gz').exists():
        return None
    packet = _read_gzip(output/'checkpoint.json.gz')
    expected = {'schema_version', 'binding', 'complete', 'cursor_ms', 'replay_checkpoint', 'artifacts', 'sha256'}
    if not isinstance(packet, dict) or set(packet) != expected or packet['schema_version'] != 1:
        raise ValueError('invalid replay checkpoint envelope')
    if packet['binding'] != binding or packet['sha256'] != _digest({k:v for k,v in packet.items() if k != 'sha256'}):
        raise ValueError('checkpoint binding or integrity mismatch')
    if (type(packet['complete']) is not bool or type(packet['cursor_ms']) is not int or packet['cursor_ms'] % HOUR or
            not binding['start_ms'] <= packet['cursor_ms'] <= binding['end_ms']):
        raise ValueError('invalid checkpoint completion/cursor')
    replay = packet['replay_checkpoint']
    if (not packet['complete'] and not isinstance(replay, dict) or replay is not None and
            (not isinstance(replay, dict) or replay.get('cursor_ms') != packet['cursor_ms'] or replay.get('complete') != packet['complete'])):
        raise ValueError('checkpoint resume state disagrees with envelope')
    return packet


def _own_output(output, binding):
    if output.exists():
        return output
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.pending-replay-', dir=output.parent))
    try:
        _atomic_json(temporary/MARKER, dict(schema_version=1, purpose=PURPOSE, binding=binding), False)
        os.rename(temporary, output)
        _fsync_directory(output.parent)
    finally:
        if temporary.exists():
            for path in temporary.iterdir():
                path.unlink()
            temporary.rmdir()
    return output


def _summary(report):
    keys = ('status', 'completed_hours', 'start_ms', 'end_ms', 'metrics', 'modeled_metrics',
            'partial_metrics', 'funded_cycle_diagnostics',
            'verified_metrics', 'coverage', 'direction_mix', 'per_coin', 'provenance', 'limitations')
    return dict({key:report[key] for key in keys if key in report},
                bots_count=len(report.get('bots', [])), ledger_rows=len(report.get('ledger', [])),
                switches_count=len(report.get('switches', [])))


def _save_chunk(output, binding, chunk):
    replay = chunk['checkpoint']
    result = chunk['result']
    cursor = replay['cursor_ms'] if replay else binding['start_ms']+int(result.get('completed_hours', 0)*HOUR)
    if chunk['complete']:
        if not isinstance(result, dict):
            raise ValueError('complete replay must return a full result')
        result = dict(result, provenance=binding, live_actionable=False)
        _atomic_json(output/'result.json.gz', result)
        _atomic_json(output/'summary.json.gz', _summary(result))
    elif replay is None:
        raise ValueError('incomplete replay must return a checkpoint')
    artifacts = {name:_hash_file(output/name) for name in ('result.json.gz', 'summary.json.gz')} if chunk['complete'] else {}
    envelope = dict(schema_version=1, binding=binding, complete=chunk['complete'],
                    cursor_ms=cursor, replay_checkpoint=replay, artifacts=artifacts)
    envelope['sha256'] = _digest(envelope)
    _atomic_json(output/'checkpoint.json.gz', envelope)
    return envelope


def _progress(output, packet, chunks):
    result = dict(output=str(output), complete=packet['complete'], cursor_ms=packet['cursor_ms'],
                  completed_hours=(packet['cursor_ms']-packet['binding']['start_ms'])/HOUR,
                  chunks_this_invocation=chunks, binding_sha256=_digest(packet['binding']), live_actionable=False)
    if packet['complete']:
        artifacts = packet.get('artifacts')
        if (not isinstance(artifacts, dict) or set(artifacts) != {'result.json.gz', 'summary.json.gz'} or
                any(_hash_file(output/name) != digest for name,digest in artifacts.items())):
            raise ValueError('completed artifact integrity mismatch')
        summary = _read_gzip(output/'summary.json.gz')
        if summary.get('provenance') != packet['binding'] or not (output/'result.json.gz').is_file():
            raise ValueError('completed result provenance or artifacts do not match checkpoint')
        result.update(status=summary.get('status'), summary=summary)
    return result


def _execute(args):
    paths, funding, binding = _inputs(args)
    packet = _existing(paths['output'], binding)
    if packet is not None and packet['complete']:
        return _progress(paths['output'], packet, 0)
    identity = dict(source=binding['source_revision'], registration=binding['registration_sha256'],
                    data=_digest({key:value for key,value in binding.items() if key.endswith('sha256')}))
    with Snapshot(paths['snapshot']) as raw:
        snapshot = _wrap_snapshot(raw, binding['parameters'], funding)
        output = _own_output(paths['output'], binding)
        for count in range(1, args.max_chunks+1):
            chunk = run_chunk(snapshot, binding['start_ms'], binding['end_ms'], parameters=binding['parameters'],
                mode=binding['mode'], seed=binding['seed'], max_hours=args.chunk_hours,
                checkpoint=packet['replay_checkpoint'] if packet else None, identity=identity)
            packet = _save_chunk(output, binding, chunk)
            if packet['complete']:
                break
    return _progress(output, packet, count)


def _arguments(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('snapshot', 'funding-history', 'registration', 'start', 'end', 'output'):
        parser.add_argument('--'+name, required=True)
    parser.add_argument('--bias-mode', choices=('4h-only', '1d+4h'), required=True)
    parser.add_argument('--regime-gate', choices=('on', 'off'), required=True)
    parser.add_argument('--mode', choices=('system', 'random_radar_identical_rules'), default='system')
    parser.add_argument('--chunk-hours', type=int, default=6)
    parser.add_argument('--max-chunks', type=int, default=1)
    return parser.parse_args(argv)


def main(argv=None):
    args = _arguments(argv)
    try:
        print(json.dumps(_execute(args), allow_nan=False))
        return 0
    except (ValueError, OSError, EOFError, zlib.error, SnapshotError, sqlite3.Error, subprocess.CalledProcessError) as error:
        print(json.dumps(dict(error=str(error), live_actionable=False)), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
