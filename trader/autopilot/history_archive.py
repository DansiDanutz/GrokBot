"""Private, append-only history; operational retention is not account history."""
from contextlib import closing
import hashlib
import json
import os
import sqlite3

from trader.autopilot.storage import _safe, _open, _decode, validate_event, MAX_EVENT_BYTES


def _json(value):
    return json.dumps(value, sort_keys=True, allow_nan=False, separators=(',', ':'))


def _logs(directory):
    paths = sorted(directory.glob('events-????-??-??.jsonl'))
    active = directory / 'events.jsonl'
    if active.exists() or active.is_symlink():
        paths.append(active)
    for path in paths:
        with os.fdopen(_open(path, os.O_RDONLY), 'rb') as stream:
            while line := stream.readline(MAX_EVENT_BYTES + 1):
                if len(line) > MAX_EVENT_BYTES:
                    raise ValueError('event exceeds size cap')
                yield validate_event(_decode(line))


def checkpoint(directory, state, *, import_logs=False):
    """Commit evidence before pruning; retries reuse immutable natural keys.

    Events predating the known run are retained under run 0 (unknown provenance).
    The archive never deletes records or changes the engine's bankroll.
    """
    directory = _safe(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = _safe(directory / 'history.sqlite3')
    for suffix in ('-journal', '-wal', '-shm'):
        _safe(str(path) + suffix)
    fd = _open(path, os.O_RDWR | os.O_CREAT)
    os.fchmod(fd, 0o600)
    os.close(fd)
    run = state['started_ms']
    with closing(sqlite3.connect(path, timeout=0.25)) as db, db:
        db.execute('PRAGMA synchronous=FULL')
        db.executescript('''
            CREATE TABLE IF NOT EXISTS equity (
                run_ms INTEGER, ts_ms INTEGER, value REAL NOT NULL,
                PRIMARY KEY(run_ms, ts_ms));
            CREATE TABLE IF NOT EXISTS positions (
                run_ms INTEGER, bot_id INTEGER, closed_ms INTEGER, payload TEXT NOT NULL,
                PRIMARY KEY(run_ms, bot_id));
            CREATE TABLE IF NOT EXISTS events (
                run_ms INTEGER, identity TEXT, ts_ms INTEGER, payload TEXT NOT NULL,
                PRIMARY KEY(run_ms, identity));
        ''')
        last = db.execute('SELECT MAX(ts_ms) FROM equity WHERE run_ms=?', (run,)).fetchone()[0]
        for ts, value in state['equity_curve']:
            if last is None or ts > last:
                _json([ts, value])
                db.execute('INSERT OR IGNORE INTO equity VALUES(?,?,?)', (run, ts, value))
        for wrapper in state['closed_bots']:
            bot = wrapper['engine']
            db.execute('INSERT OR IGNORE INTO positions VALUES(?,?,?,?)',
                       (run, bot['bot_id'], bot['closed_ms'], _json(wrapper)))
        def record(event):
            payload = _json(event)
            event_run = run if event['ts_ms'] >= run else 0
            identity = ('id:' + str(event['event_id']) if event_run and event.get('event_id') is not None
                        else 'sha256:' + hashlib.sha256(payload.encode()).hexdigest())
            db.execute('INSERT OR IGNORE INTO events VALUES(?,?,?,?)',
                       (event_run, identity, event['ts_ms'], payload))
        if import_logs:
            for event in _logs(directory):
                record(event)
        for event in state.get('pending_events', []):
            record(validate_event(event))
