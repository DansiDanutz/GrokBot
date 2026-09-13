"""Private atomic snapshots, single-instance lock and bounded event history."""

from contextlib import AbstractContextManager
from datetime import datetime, timedelta, timezone
import fcntl
import heapq
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile

MAX_BYTES = 2 * 1024 * 1024
MAX_EVENT_BYTES = 65536
# In-process parse cache for read_events: mtime+size keyed per file so repeated
# dashboard polls skip re-parsing unchanged event logs. Bounded and invalidated
# on any file change; entries hold only validated rows, never raw bytes.
_EVENT_FILE_CACHE_LIMIT = 128
_event_file_cache = {}
EVENT_TYPES = frozenset({"OPEN", "FILL", "GRID", "CLOSE", "RANGE_BREAK",
                         "STOP_LOSS", "RESERVE", "ALERT", "RECOVER", "ERROR",
                         "PROMOTE", "DEMOTE", "DROP", "DIRECTION_CHANGE",
                         "RULE_BLOCK", "DECISION"})


def _safe(path):
    path = Path(path).absolute()
    for part in reversed((path, *path.parents)):
        if part.is_symlink():
            raise ValueError("path contains a symlink")
    return path


def _open(path, flags):
    fd = os.open(_safe(path), flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        os.close(fd)
        raise ValueError("file must be owned and regular")
    return fd


def _decode(raw):
    def invalid(_):
        raise ValueError("nonfinite JSON number")
    value = json.loads(raw, parse_constant=invalid)
    def check(item):
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError("nonfinite JSON number")
        if isinstance(item, dict):
            for child in item.values():
                check(child)
        elif isinstance(item, list):
            for child in item:
                check(child)
    check(value)
    return value


def _read(path, max_bytes):
    with os.fdopen(_open(path, os.O_RDONLY), "rb") as stream:
        if os.fstat(stream.fileno()).st_size > max_bytes:
            raise ValueError("file exceeds size cap")
        raw = stream.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError("file exceeds size cap")
    return raw


def read_json(path, max_bytes=MAX_BYTES):
    return _decode(_read(path, max_bytes))


def atomic_json(path, payload):
    raw = json.dumps(payload, allow_nan=False, separators=(",", ":")).encode()
    path = _safe(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists():
        os.close(_open(path, os.O_RDONLY))
    fd, temporary = tempfile.mkstemp(prefix=".autopilot-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        _safe(path)
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class InstanceLock(AbstractContextManager):
    """flock is released on process exit; retain the inode to prevent split locks."""
    def __init__(self, path):
        self.path = _safe(path)
        self.fd = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = _open(self.path, os.O_RDWR | os.O_CREAT)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise RuntimeError("autopilot already running") from None
        self.fd = fd
        os.fchmod(fd, 0o600)
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
        return self

    def __exit__(self, *_):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def validate_event(event):
    if not isinstance(event, dict):
        raise ValueError("invalid event")
    if event.get("type") not in EVENT_TYPES:
        raise ValueError("invalid event type")
    symbol = event.get("symbol")
    if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z0-9]{1,32}", symbol):
        raise ValueError("invalid event symbol")
    for key in ("ts_ms", "bot_id"):
        if type(event.get(key)) is not int or event[key] < 0:
            raise ValueError("invalid event identity")
    if "event_id" in event and (type(event["event_id"]) is not int or event["event_id"] < 1):
        raise ValueError("invalid event id")
    watchlist_event = event['type'] in ('PROMOTE', 'DEMOTE', 'DROP', 'DIRECTION_CHANGE')
    decision_event = event['type'] == 'DECISION'
    if watchlist_event:
        replacement = event.get('replaced_symbol')
        if not isinstance(replacement, str) or (replacement and not re.fullmatch(r'[A-Z0-9]{1,32}', replacement)):
            raise ValueError('invalid replacement symbol')
        for key in ('score', 'replaced_score', 'margin'):
            if type(event.get(key)) not in (int, float) or not math.isfinite(event[key]):
                raise ValueError('invalid watchlist score')
        if not 0 <= event['score'] <= 100 or not 0 <= event['replaced_score'] <= 100:
            raise ValueError('invalid watchlist score bounds')
    if decision_event:
        if event.get('action') not in ('open', 'close', 'skip', 'influence'):
            raise ValueError('invalid decision action')
        # action='influence' is the agent-influence audit trail (T8): numeric
        # fields only, plus the closed-roster agent id.
        if event['action'] == 'influence' and not re.fullmatch(
                r'[a-z][a-z0-9_]{0,39}', str(event.get('agent_id'))):
            raise ValueError('invalid decision agent')
        if event.get('direction') not in ('LONG', 'SHORT', 'NEUTRAL'):
            raise ValueError('invalid decision direction')
        if event.get('radar_direction') not in (
                'LONG', 'SHORT', 'NEUTRAL', 'TURNING-UP', 'TURNING-DOWN'):
            raise ValueError('invalid decision radar direction')
        rules = event.get('rule_blocks')
        if not isinstance(rules, list) or len(rules) > 32:
            raise ValueError('invalid decision rule blocks')
        if any(type(code) is not int or not 1 <= code <= 999 for code in rules):
            raise ValueError('invalid decision rule code')
        for key in ('radar_score', 'expected_grids_per_hour', 'range_width_pct',
                    'funding_rate', 'kucoin_ok'):
            value = event.get(key)
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError('invalid decision number')
        if event['kucoin_ok'] not in (0, 1):
            raise ValueError('invalid decision health flag')
    for key, value in event.items():
        if key == 'replaced_symbol' and watchlist_event:
            continue
        if decision_event and key in ('action', 'direction', 'radar_direction', 'rule_blocks'):
            continue
        if decision_event and key == 'agent_id' and event['action'] == 'influence':
            continue
        if key in ("type", "symbol"):
            continue
        if (not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key)
                or type(value) not in (int, float) or not math.isfinite(value)):
            raise ValueError("event values must be finite numbers")
    return dict(event)


def _day(ts_ms):
    return datetime.fromtimestamp(ts_ms / 1000, timezone.utc).date()


def _tail_event_id(path):
    """Recover an interrupted trailing write; inspect only a bounded tail."""
    with os.fdopen(_open(path, os.O_RDWR), "r+b") as stream:
        size = os.fstat(stream.fileno()).st_size
        offset = max(0, size - 2 * MAX_EVENT_BYTES)
        stream.seek(offset)
        tail = stream.read(2 * MAX_EVENT_BYTES)
        if tail and not tail.endswith(b"\n"):
            complete = tail.rfind(b"\n") + 1
            if not complete and offset:
                raise ValueError("event exceeds size cap")
            stream.truncate(offset + complete)
            stream.flush()
            os.fsync(stream.fileno())
            tail = tail[:complete]
        if offset:
            tail = tail.partition(b"\n")[2]
        return max((validate_event(_decode(line)).get("event_id", 0)
                    for line in tail.splitlines()), default=0)


def _active_day(path):
    if not path.exists() and not path.is_symlink():
        return None
    with os.fdopen(_open(path, os.O_RDONLY), "rb") as stream:
        first = stream.readline(MAX_EVENT_BYTES + 1)
    if len(first) > MAX_EVENT_BYTES:
        raise ValueError("event exceeds size cap")
    return _day(validate_event(_decode(first))["ts_ms"]) if first else None


class EventLog:
    """Single daemon writer; UTC daily rotation retains thirty calendar days."""
    def __init__(self, directory):
        self.directory = _safe(directory)

    def prune(self, now_ms):
        cutoff = _day(now_ms) - timedelta(days=29)
        for path in self.directory.glob('events-????-??-??.jsonl'):
            _safe(path)
            if path.name[7:17] < cutoff.isoformat():
                path.unlink()
        active = self.directory / 'events.jsonl'
        day = _active_day(active)
        if day is not None and day < cutoff:
            active.unlink()

    def append(self, events):
        events = [validate_event(event) for event in events]
        encoded = [json.dumps(event, allow_nan=False).encode() + b"\n" for event in events]
        if any(len(line) > MAX_EVENT_BYTES for line in encoded):
            raise ValueError("event exceeds size cap")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.directory / "events.jsonl"
        archives = list(self.directory.glob("events-????-??-??.jsonl"))
        existing = archives + ([path] if path.exists() or path.is_symlink() else [])
        last_id = max((_tail_event_id(item) for item in existing), default=0)
        previous = _active_day(path)
        latest = max([datetime.strptime(item.name[7:17], "%Y-%m-%d").date()
                      for item in archives] + ([previous] if previous else []), default=None)
        for event, line in zip(events, encoded):
            event_id = event.get("event_id")
            if event_id is not None and event_id <= last_id:
                continue
            day = _day(event["ts_ms"])
            latest = max(latest, day) if latest else day
            destination = path
            if previous and day > previous:
                archive = _safe(self.directory / f"events-{previous}.jsonl")
                if archive.exists():
                    raise ValueError("event archive already exists")
                path.rename(archive)
                previous = None
            if day < latest:
                destination = self.directory / f"events-{day}.jsonl"
            else:
                previous = day
            with os.fdopen(_open(destination, os.O_WRONLY | os.O_APPEND | os.O_CREAT), "ab") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(line)
                stream.flush()
                os.fsync(stream.fileno())
            last_id = event_id if event_id is not None else last_id
            cutoff = latest - timedelta(days=29)
            for archive in self.directory.glob("events-????-??-??.jsonl"):
                _safe(archive)
                if archive.name[7:17] < cutoff.isoformat():
                    archive.unlink()
        directory_fd = os.open(self.directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)


def _event_rows(path):
    """Validated rows for one event file, cached until its mtime or size changes."""
    path = _safe(path)
    info = os.stat(path)
    stamp = (info.st_mtime_ns, info.st_size)
    cached = _event_file_cache.get(path)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    rows = [validate_event(_decode(line)) for line in _read(path, MAX_BYTES).splitlines()]
    if len(_event_file_cache) >= _EVENT_FILE_CACHE_LIMIT:
        _event_file_cache.pop(next(iter(_event_file_cache)))
    _event_file_cache[path] = (stamp, rows)
    return rows


def read_events(directory, since_ms, limit=500, *, after_event_id=None, latest=False):
    """Return at most 500 events after a timestamp or composite timestamp/ID cursor.

    Omitted after_event_id preserves the original strictly-newer timestamp query.
    Daily files remain size bounded and validated before public projection.
    """
    if type(since_ms) is not int or since_ms < 0 or type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("invalid event query bounds")
    if after_event_id is not None and (type(after_event_id) is not int or after_event_id < 0):
        raise ValueError("invalid event cursor")
    if type(latest) is not bool:
        raise ValueError("invalid latest selector")
    directory = _safe(directory)
    paths = sorted(directory.glob("events-????-??-??.jsonl"))
    active = directory / "events.jsonl"
    if active.exists() or active.is_symlink():
        paths.append(active)
    if len(paths) > 31:
        raise ValueError("event history exceeds retention cap")
    def rows():
        for path in paths:
            for event in _event_rows(path):
                if (event["ts_ms"] > since_ms or
                        (after_event_id is not None and event["ts_ms"] == since_ms
                         and event.get("event_id", 0) > after_event_id)):
                    yield event
    key = lambda event: (event["ts_ms"], event.get("event_id", 0))
    selected = (heapq.nlargest if latest else heapq.nsmallest)(limit, rows(), key=key)
    return sorted(selected, key=key)
