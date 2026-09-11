"""Private rebuildable event indexes for already validated daily archives.

Archive stat identities invalidate indexes, including same-size rewrites and
atomic replacements. Sources remain authoritative; a missing index is rebuilt.
No provider data or credentials are requested by this module.
"""
import json
import os
import secrets
import stat
from contextlib import contextmanager

from paper_grid import engine, retention

DIRECTORY = '.metric-event-index'
SCHEMA = 1
PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600


def identity(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('required observation archive is missing or unsafe')
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns]


@contextmanager
def directory(runtime):
    path = runtime / DIRECTORY
    path.mkdir(mode=PRIVATE_DIRECTORY_MODE, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise ValueError('metric event index directory is unsafe')
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != PRIVATE_DIRECTORY_MODE:
            raise ValueError('metric event index directory is unsafe')
        yield descriptor
    finally:
        os.close(descriptor)


def _validate(value, name):
    if not isinstance(value, dict) or value.get('schema') != SCHEMA:
        raise ValueError('invalid metric event index schema')
    rows = value.get('events')
    marks = value.get('equity_times')
    if not isinstance(rows, list) or not isinstance(marks, dict):
        raise ValueError('invalid metric event index records')
    for row in rows:
        retention._key(row)
        if retention._day(row['time']) != name[:-5]:
            raise ValueError('invalid metric event index day')
    for at in marks.values():
        if retention._day(at) != name[:-5]:
            raise ValueError('invalid metric event index equity time')
    return value


def read(descriptor, name, fingerprint, limit):
    try:
        source = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=descriptor)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError('metric event index file is unsafe') from exc
    with os.fdopen(source, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != PRIVATE_FILE_MODE):
            raise ValueError('metric event index file is unsafe')
        payload = stream.read(limit + 1)
    if len(payload) > limit:
        raise ValueError('metric event index size limit exceeded')
    value = _validate(json.loads(payload), name)
    return value if value.get('identity') == fingerprint else None


def write(descriptor, name, fingerprint, archive):
    marks = {}
    for row in archive['observations']:
        equity = row.get('equity')
        if row.get('telemetry_schema') != 1 or row.get('skipped') or not isinstance(equity, dict):
            continue
        for arm, mark in equity.items():
            if isinstance(mark, dict) and engine._number(mark.get('equity')):
                marks[arm] = max(marks.get(arm, 0), row['time'])
    value = dict(schema=SCHEMA, identity=fingerprint, events=archive['events'],
                 equity_times=marks)
    temporary = '.' + secrets.token_hex(16) + '.tmp'
    target = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     PRIVATE_FILE_MODE, dir_fd=descriptor)
    try:
        with os.fdopen(target, 'w') as stream:
            json.dump(value, stream, allow_nan=False, separators=(',', ':'))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, name, src_dir_fd=descriptor, dst_dir_fd=descriptor)
    finally:
        try:
            os.unlink(temporary, dir_fd=descriptor)
        except FileNotFoundError:
            pass
    return value
