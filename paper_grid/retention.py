"""Archive completed UTC days without changing cumulative paper accounting.

Call under the CLI lock, before atomically committing the returned document.
An interruption between archive and document commits is safe to replay.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re

from paper_grid import cli

DIRECTORY = 'experiment-archive'
RECORDS = ('observations', 'events', 'errors')
DAY_SECONDS = 86400
FILENAME = re.compile(r'\d{4}-\d{2}-\d{2}\.json\Z')


def _timestamp(value):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0):
        raise ValueError('invalid archive timestamp')
    return value


def _day(value):
    return datetime.fromtimestamp(_timestamp(value), timezone.utc).strftime('%Y-%m-%d')


def _key(record):
    if not isinstance(record, dict):
        raise ValueError('invalid archive record')
    _timestamp(record.get('time'))
    return json.dumps(record, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _merge(existing, incoming, kind):
    result, seen, observations = [], set(), {}
    for record in existing + incoming:
        key = _key(record)
        if kind == 'observations':
            at = record['time']
            if at in observations and observations[at] != key:
                raise ValueError('conflicting observations at the same timestamp')
            observations[at] = key
        if key not in seen:
            seen.add(key)
            result.append(deepcopy(record))
    return sorted(result, key=lambda record: record['time'])


def _directory(runtime):
    directory = Path(runtime) / DIRECTORY
    if directory.is_symlink():
        raise ValueError('archive directory must not be a symlink')
    if directory.exists() and not directory.is_dir():
        raise ValueError('invalid archive directory')
    return directory


def _read(path):
    if not FILENAME.fullmatch(path.name) or path.is_symlink() or not path.is_file():
        raise ValueError('invalid archive file')
    day = path.stem
    # strptime rejects impossible dates even when the filename shape is valid.
    datetime.strptime(day, '%Y-%m-%d')
    archive = json.loads(path.read_text())
    if archive.get('schema') != 1 or archive.get('day') != day:
        raise ValueError('archive schema/day mismatch')
    for kind in RECORDS:
        rows = archive.get(kind, [] if kind == 'errors' else None)
        if not isinstance(rows, list):
            raise ValueError('invalid archive records')
        for row in rows:
            _key(row)
            if _day(row['time']) != day:
                raise ValueError('archive record is in the wrong UTC day')
        archive[kind] = _merge([], rows, kind)
    return archive


def archive_history(runtime, doc, now):
    """Keep today and three full preceding UTC days; archive everything older.

    Archives commit before the caller commits this returned deep copy. Archive
    I/O or validation failures raise, so the original hot document remains the
    authority. Positions, statistics, balances and lifetime equity are untouched.
    """
    cutoff = (int(_timestamp(now)) // DAY_SECONDS - 3) * DAY_SECONDS
    result = deepcopy(doc)
    pending = {}

    def retain(rows, kind, account=None):
        if not isinstance(rows, list):
            raise ValueError('invalid history records')
        recent = []
        for row in rows:
            _key(row)
            if row['time'] >= cutoff:
                recent.append(row)
                continue
            day = _day(row['time'])
            archive = pending.setdefault(day, dict(schema=1, day=day,
                                                  observations=[], events=[], errors=[]))
            stored = dict(row, account=account) if account is not None else row
            archive[kind].append(stored)
        return recent

    for kind in RECORDS:
        result[kind] = retain(result.get(kind, []), kind)
    for name, account in result.get('accounts', {}).items():
        account['events'] = retain(account.get('events', []), 'events', name)

    directory = _directory(runtime)
    filenames = result.get('archive_files', [])
    if not isinstance(filenames, list):
        raise ValueError('invalid archive filenames')
    for filename in filenames:
        if not isinstance(filename, str) or not FILENAME.fullmatch(filename):
            raise ValueError('invalid archive filename')
        datetime.strptime(filename[:-5], '%Y-%m-%d')

    # Validate every merge before writing any day, including crash-replay data.
    for day, archive in pending.items():
        path = directory / (day + '.json')
        if path.exists() or path.is_symlink():
            previous = _read(path)
        else:
            previous = {kind: [] for kind in RECORDS}
        for kind in RECORDS:
            archive[kind] = _merge(previous[kind], archive[kind], kind)

    if pending:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory.chmod(0o700)
        for day, archive in sorted(pending.items()):
            cli.atomic_json(directory / (day + '.json'), archive)
    result['archive_files'] = sorted(set(filenames) | {day + '.json' for day in pending})
    return result


def read_archives(runtime, start, end):
    """Read validated archived records in (start, end], without state changes."""
    _timestamp(start)
    _timestamp(end)
    if end < start:
        raise ValueError('invalid archive interval')
    result = {kind: [] for kind in RECORDS}
    directory = _directory(runtime)
    if not directory.exists():
        return result
    first, last = _day(start), _day(end)
    for path in sorted(directory.iterdir()):
        if not FILENAME.fullmatch(path.name) or not first <= path.stem <= last:
            continue
        archive = _read(path)
        for kind in RECORDS:
            result[kind].extend(row for row in archive[kind] if start < row['time'] <= end)
    for kind in RECORDS:
        result[kind] = _merge([], result[kind], kind)
    return result
