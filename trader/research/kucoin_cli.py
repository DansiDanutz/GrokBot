"""Explicit offline operator CLI; no default data paths, accounts or network access."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sqlite3
import sys
import tempfile
from trader.research.kucoin_operator import operator_report, validate_running
from trader.research.kucoin_snapshot import Snapshot, SnapshotError, HistoricalSnapshot
from trader.research.chart_snapshot import ChartSnapshot
from trader.research.public_funding import _bounds, _records, _initial_state

OWNER = {'schema_version': 1, 'purpose': 'offline-kucoin-operator-artifacts'}
MARKER = '.offline-grid-operator.json'
MEMBERSHIP_PATH = Path(__file__).resolve().parents[2]/'config/solana-ecosystem.json'
PROTECTED = ('Sandbox/grokbot/market-data', 'ZCodeProject/ZmartyChat-paper-grid',
             'Sandbox/grokbot/vercel-publisher', 'ZCodeProject/GrokBot',
             'Sandbox/grokbot/zmarty-paper-runtime', 'Sandbox/grokbot/trader-v2-runtime',
             'Library/LaunchAgents', '.openclaw-secrets', '.openclaw', '.claude', '.paperclip')
FORBIDDEN_PARTS = {'.runtime', '.git', '.ssh', '.aws', '.codex', '.config'}


def _safe_location(value):
    path = Path(os.path.abspath(os.fspath(value)))
    roots = [Path.home()/part for part in PROTECTED]
    if any(path == root or root in path.parents for root in roots) or FORBIDDEN_PARTS.intersection(path.parts):
        raise ValueError('protected path is forbidden for offline operator inputs or output')
    if path.name.startswith('.env') or path.name in ('id_rsa', 'id_ed25519'):
        raise ValueError('credential files are forbidden')
    for component in reversed((path, *path.parents)):
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            raise ValueError('symlink paths are forbidden')
        if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
            raise ValueError('hardlink files are forbidden')
    return path


def _read_json(value, maximum=1_048_576):
    path = _safe_location(value)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
        raise ValueError('JSON input must be a bounded detached regular file')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate JSON keys are forbidden')
            result[key] = value
        return result
    return json.loads(path.read_text(), object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON value')))


def load_running(path, asof_ms, parameters=None):
    document = _read_json(path)
    if parameters is None:
        validate_running(document, asof_ms)
    else:
        validate_running(document, asof_ms, parameters=parameters)
    return document


def _funding_value(pair, value):
    checkpoint_keys = {'symbol', 'start_ms', 'end_ms', 'next_to_ms', 'records',
                       'complete', 'exhausted', 'pages', 'coverage_verified'}
    rows = value.get('records') if isinstance(value, dict) else value
    if not isinstance(rows, list) or any(not isinstance(row, dict) or
            set(row) != {'symbol', 'timestamp_ms', 'rate'} for row in rows):
        raise ValueError('funding history requires canonical public record lists')
    if any(type(row['rate']) not in (int, float) for row in rows):
        raise ValueError('canonical funding rates must be numeric and not boolean')
    _bounds(pair, 0, 2**63-1)
    normalized = _records(rows, pair, 0, 2**63-1)
    if isinstance(value, dict):
        if set(value) != checkpoint_keys or value['coverage_verified'] is not False:
            raise ValueError('funding checkpoint requires exact public unverified-coverage fields')
        _bounds(pair, value['start_ms'], value['end_ms'])
        return _initial_state(pair, value['start_ms'], value['end_ms'], value)
    return normalized


def load_funding_history(path):
    """Read only bounded detached public histories; retrieval is never invoked."""
    document = _read_json(path, maximum=32*1024*1024)
    if not isinstance(document, dict):
        raise ValueError('funding history must map contract symbols to records or checkpoints')
    return {pair: _funding_value(pair, value) for pair, value in document.items()}


def _parameters(arguments):
    income = arguments.strategy == 'income_chart_v3'
    if not income and (arguments.funding_history is not None or arguments.bias_mode != '1d+4h'
                       or arguments.regime_gate != 'on'):
        raise ValueError('chart bias, regime and funding options require --strategy income_chart_v3')
    parameters = dict(strategy='income_chart_v3', bias_mode=arguments.bias_mode,
                      regime_gate=arguments.regime_gate == 'on') if income else {}
    if arguments.candle_only:
        parameters['historical_candle_only'] = True
    return parameters


def _load_membership():
    """The bundled sourced registry is required for this explicit chart strategy."""
    registry = _read_json(MEMBERSHIP_PATH)
    fields = {'schema_version', 'reviewed_at', 'coverage_complete', 'basis',
              'historical_basis', 'contract_identifier_policy', 'members', 'non_members'}
    if not isinstance(registry, dict) or set(registry) != fields:
        raise ValueError('membership registry requires exact sourced schema fields')
    if type(registry['schema_version']) is not int or registry['schema_version'] != 1 or type(registry['coverage_complete']) is not bool:
        raise ValueError('invalid membership registry schema or coverage flag')
    if any(not isinstance(registry[key], str) or not registry[key].strip()
           for key in ('reviewed_at', 'basis', 'historical_basis', 'contract_identifier_policy')):
        raise ValueError('membership registry requires provenance statements')
    members, nonmembers = registry['members'], registry['non_members']
    if not isinstance(members, dict) or not isinstance(nonmembers, dict):
        raise ValueError('membership registry requires explicit member and non-member mappings')
    if members.keys() & nonmembers.keys():
        raise ValueError('conflicting membership registry classifications')
    for pair, row in {**members, **nonmembers}.items():
        _bounds(pair, 0, 0)
        if (not isinstance(row, dict) or set(row) != {'basis', 'sources'} or
                not isinstance(row['basis'], str) or not row['basis'].strip() or
                not isinstance(row['sources'], list) or not row['sources'] or
                any(not isinstance(source, str) or not source.strip() for source in row['sources'])):
            raise ValueError('membership classification requires explicit basis and source evidence')
    return registry


def _wrap_snapshot(snapshot, parameters, funding):
    source = HistoricalSnapshot(snapshot, parameters) if parameters.get('historical_candle_only') else snapshot
    if parameters.get('strategy') == 'income_chart_v3':
        return ChartSnapshot(source, parameters, funding_histories=funding, membership=_load_membership())
    return source


def _encoded(document):
    return (json.dumps(document, indent=2, sort_keys=True, allow_nan=False)+'\n').encode()


def _own_directory(value):
    output = _safe_location(value)
    if not output.exists():
        output.mkdir(parents=True)
        marker = _safe_location(output/MARKER)
        with marker.open('xb') as stream:
            stream.write(_encoded(OWNER))
    if not output.is_dir() or not (output/MARKER).is_file():
        raise ValueError('output must be new or an owned offline operator directory')
    if _read_json(output/MARKER) != OWNER:
        raise ValueError('output is not owned by this offline operator')
    return output


def _artifact_files(report):
    files = {'report.json': _encoded(report), 'radar.json': _encoded(report['radar'])}
    for bot in report['running']:
        bot_id = bot['bot_id']
        if not isinstance(bot_id, str) or not bot_id or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in bot_id):
            raise ValueError('unsafe bot artifact identifier')
        files['ledger/'+bot_id+'.json'] = _encoded(bot['ledger'])
        files['history/'+bot_id+'.json'] = _encoded(bot['hourly_history'])
        files['tracker/'+bot_id+'.json'] = _encoded(bot['tracker'])
        files['verdict/'+bot_id+'.json'] = _encoded(bot['verdict'])
    return files


def _same_artifacts(directory, expected):
    directory = _safe_location(directory)
    actual = set()
    for root, directories, files in os.walk(directory, followlinks=False):
        for name in directories + files:
            _safe_location(Path(root)/name)
        for name in files:
            path = Path(root)/name
            relative = path.relative_to(directory).as_posix()
            actual.add(relative)
            if relative not in expected or path.read_bytes() != expected[relative]:
                raise ValueError('hourly artifact conflict; existing output is immutable')
    if actual != set(expected):
        raise ValueError('hourly artifact conflict; missing or unexpected files')


def persist_report(report, output):
    """Publish an immutable complete hourly directory using one atomic rename."""
    timestamp = report['asof_ms']
    if type(timestamp) is not int or timestamp < 0 or timestamp % 3_600_000:
        raise ValueError('artifact asof_ms must be a UTC hour boundary')
    files = _artifact_files(report)
    root = _own_directory(output)
    target = _safe_location(root/('hour-'+str(timestamp)))
    if target.exists():
        _same_artifacts(target, files)
        return str(target)
    temporary = Path(tempfile.mkdtemp(prefix='.pending-', dir=root))
    try:
        for relative, content in files.items():
            path = temporary/relative
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        try:
            os.rename(temporary, target)
        except OSError:
            if not target.exists():
                raise
            _same_artifacts(target, files)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return str(target)


def _asof(value):
    if value.isdecimal():
        return int(value)
    try:
        date = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as error:
        raise ValueError('asof must be epoch milliseconds or an ISO time with timezone') from error
    if date.tzinfo is None:
        raise ValueError('asof requires an explicit timezone')
    if date.microsecond % 1000:
        raise ValueError('asof cannot contain submillisecond precision')
    return int(date.astimezone(timezone.utc).timestamp()*1000)


def _snapshot_asof(value, snapshot):
    if value != 'latest':
        return _asof(value)
    upper = snapshot.market_bounds()[1]
    if not isinstance(upper, int) or upper < 0:
        raise ValueError('latest requires bounded historical snapshot observations')
    return upper // 3_600_000 * 3_600_000


def _arguments(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    operator = commands.add_parser('operator', help='reconstruct an explicitly supplied offline snapshot')
    descriptions = dict(snapshot='detached SQLite copy with sibling snapshot.json attestation',
        asof='UTC hour: epoch milliseconds, ISO time, or latest completed hour in the offline snapshot',
        running='schema_version 1 JSON containing at most two exact bot forms',
        output='new or previously owned offline artifact directory; never a runtime directory')
    for name, description in descriptions.items():
        operator.add_argument('--'+name, required=True, help=description)
    operator.add_argument('--strategy', choices=('legacy', 'income_chart_v3'), default='legacy')
    operator.add_argument('--bias-mode', choices=('4h-only', '1d+4h'), default='1d+4h')
    operator.add_argument('--regime-gate', choices=('on', 'off'), default='on')
    operator.add_argument('--candle-only', action='store_true',
                          help='explicitly permit labeled historical candle-only assumptions')
    operator.add_argument('--funding-history',
                          help='detached public symbol-to-record/checkpoint JSON, at most 32 MiB; no retrieval')
    return parser.parse_args(argv)


def main(argv=None):
    arguments = _arguments(argv)
    try:
        _safe_location(arguments.output)
        snapshot_path = _safe_location(arguments.snapshot)
        parameters = _parameters(arguments)
        funding = load_funding_history(arguments.funding_history) if arguments.funding_history else None
        with Snapshot(snapshot_path) as snapshot:
            source = _wrap_snapshot(snapshot, parameters, funding)
            asof = _snapshot_asof(arguments.asof, source)
            running = load_running(arguments.running, asof, parameters=parameters) if parameters else load_running(arguments.running, asof)
            report = (operator_report(source, asof, running, parameters=parameters) if parameters else
                      operator_report(source, asof, running))
            with snapshot.path.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        report['snapshot'] = dict(sha256=digest, source='kucoin-public', offline_copy=True)
        directory = persist_report(report, arguments.output)
        print(json.dumps(dict(artifact_directory=directory, report=report), allow_nan=False))
        return 0
    except (ValueError, OSError, SnapshotError, sqlite3.Error) as error:
        print(json.dumps(dict(error=str(error), live_actionable=False)), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
