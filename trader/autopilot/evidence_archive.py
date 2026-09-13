"""Private write-once setup dossiers, independent of the 30-day bot history.

Call stage(state) BEFORE checkpointing state, then flush(state, state_directory),
then checkpoint again. Archive failures retain pending copies and never raise to
veto a safety close. A full/invalid queue is reported for admission to fail closed;
existing pending dossiers are never evicted to make room for new ones.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import stat
import tempfile

from trader.autopilot.setup_evidence import evidence_id
from trader.autopilot.storage import _safe, _read

MAX_DOSSIER_BYTES = 256 * 1024
MAX_PENDING_BYTES = 16 * 1024 * 1024
MAX_PENDING_COUNT = 1024
MAX_FLUSH_COUNT = 32
ID = re.compile(r'[0-9a-f]{64}')


def _encoded(dossier):
    if not isinstance(dossier, dict):
        raise ValueError('dossier must be an object')
    identifier = dossier.get('evidence_id')
    if not isinstance(identifier, str) or not ID.fullmatch(identifier):
        raise ValueError('invalid evidence identifier')
    raw = json.dumps(dossier, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    if len(raw) > MAX_DOSSIER_BYTES or evidence_id(dossier) != identifier:
        raise ValueError('dossier exceeds bound or hash mismatch')
    return identifier, raw


def _summary(meta, failures=None):
    pending = meta['pending_setup_evidence']
    if failures is not None:
        meta['setup_evidence_archive_errors'] = failures
    errors = meta.get('setup_evidence_archive_errors', {})
    status = ('CAPACITY_BLOCKED' if meta.get('setup_evidence_archive_capacity_blocked') else
              'BLOCKED' if errors else 'PENDING' if pending else 'COMPLETE')
    meta['setup_evidence_archive_status'] = status
    return dict(status=status, pending=len(pending), failed=len(errors))


def stage(state):
    """Retain independent pending copies before the caller persists/prunes state."""
    meta = state.setdefault('runtime', {})
    pending = meta.setdefault('pending_setup_evidence', {})
    errors = dict(meta.get('setup_evidence_archive_errors', {}))
    if not isinstance(pending, dict):
        # Preserve malformed state rather than discarding its unarchived contents.
        meta['setup_evidence_archive_status'] = 'BLOCKED'
        return dict(status='BLOCKED', pending=1, failed=1)
    used = 0
    for identifier, dossier in pending.items():
        try:
            actual, raw = _encoded(dossier)
            if actual != identifier:
                raise ValueError('pending identifier mismatch')
            used += len(raw)
        except (ValueError, TypeError, RecursionError):
            errors[str(identifier)] = 'INVALID_PENDING_EVIDENCE'
    capacity = len(pending) >= MAX_PENDING_COUNT or used >= MAX_PENDING_BYTES
    for wrapper in state.get('open_bots', []) + state.get('closed_bots', []):
        dossier = wrapper.get('setup_evidence')
        if dossier is None:
            continue  # Legacy entries have no contemporaneous dossier to invent.
        try:
            identifier, raw = _encoded(dossier)
            if wrapper.get('setup_evidence_archived_id') == identifier:
                continue
            if identifier in pending:
                if _encoded(pending[identifier])[1] != raw:
                    raise ValueError('pending dossier differs')
                continue
            if len(pending) >= MAX_PENDING_COUNT or used + len(raw) > MAX_PENDING_BYTES:
                capacity = True
                continue
            pending[identifier] = deepcopy(dossier)
            used += len(raw)
            errors.pop(identifier, None)
        except (ValueError, TypeError, RecursionError):
            # Index only the bounded existing bot identifier; never a supplied path.
            errors['bot:' + str(wrapper.get('engine', {}).get('bot_id', 'unknown'))[:64]] = 'INVALID_DOSSIER'
    meta['setup_evidence_archive_capacity_blocked'] = capacity
    return _summary(meta, errors)


def _archive_directory(directory):
    directory = Path(directory)
    if not directory.is_absolute() or '..' in directory.parts:
        raise ValueError('canonical absolute runtime directory required')
    parent = _safe(directory)
    target = _safe(parent / 'setup-evidence')
    target.mkdir(mode=0o700, exist_ok=True)
    info = target.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('archive directory must be private and owned')
    return target


def read_verified(directory, identifier):
    """Read an existing archive without creating paths or trusting its filename."""
    if not isinstance(identifier, str) or not ID.fullmatch(identifier):
        raise ValueError('invalid evidence identifier')
    directory = Path(directory)
    if not directory.is_absolute() or '..' in directory.parts:
        raise ValueError('canonical absolute runtime directory required')
    path = _safe(directory / 'setup-evidence' / (identifier + '.json'))
    raw = _read(path, MAX_DOSSIER_BYTES)
    if path.stat().st_mode & 0o077:
        raise ValueError('archive file must be private')
    dossier = json.loads(raw)
    actual, canonical = _encoded(dossier)
    if actual != identifier or canonical != raw:
        raise ValueError('archive content/hash mismatch')
    return dossier


def _publish(directory, dossier):
    identifier, raw = _encoded(dossier)
    archive = _archive_directory(directory)
    path = _safe(archive / (identifier + '.json'))
    if not path.exists():
        fd, temporary = tempfile.mkstemp(prefix='.evidence-', dir=archive)
        try:
            with os.fdopen(fd, 'wb') as handle:
                os.fchmod(handle.fileno(), 0o600)
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            _safe(path)
            try:
                os.link(temporary, path, follow_symlinks=False)
            except FileExistsError:
                pass  # A concurrent identical publisher must still verify below.
            directory_fd = os.open(archive, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            os.unlink(temporary)
    existing = read_verified(directory, identifier)
    if _encoded(existing)[1] != raw:
        raise ValueError('existing archive differs from pending evidence')
    return identifier


def flush(state, directory):
    """Retry a bounded batch; caller owns the subsequent state checkpoint."""
    meta = state.setdefault('runtime', {})
    pending = meta.setdefault('pending_setup_evidence', {})
    if not isinstance(pending, dict):
        meta['setup_evidence_archive_status'] = 'BLOCKED'
        return dict(status='BLOCKED', pending=1, failed=1)
    errors = dict(meta.get('setup_evidence_archive_errors', {}))
    for identifier in list(pending)[:MAX_FLUSH_COUNT]:
        try:
            if _encoded(pending[identifier])[0] != identifier:
                raise ValueError('pending identifier mismatch')
            _publish(directory, pending[identifier])
        except (OSError, ValueError, TypeError, RecursionError) as error:
            errors[str(identifier)] = type(error).__name__
            pending[identifier] = pending.pop(identifier)  # Fair retries across a bounded batch.
            continue
        del pending[identifier]
        errors.pop(identifier, None)
        for wrapper in state.get('open_bots', []) + state.get('closed_bots', []):
            dossier = wrapper.get('setup_evidence')
            if isinstance(dossier, dict) and dossier.get('evidence_id') == identifier:
                wrapper['setup_evidence_archived_id'] = identifier
    return _summary(meta, errors)
