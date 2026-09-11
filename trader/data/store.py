"""Dependency-free, versioned SQLite store for paper research market data."""

from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
import stat

from trader.data.constants import (
    BUSY_TIMEOUT_MS,
    PRIMARY_KEYS,
    PRIVATE_DIRECTORY_MODE,
    PRIVATE_FILE_MODE,
    SCHEMA_VERSION,
    SIDECAR_SUFFIXES,
    TABLE_COLUMNS,
)
from trader.data.validation import validate


def _safe_path(path):
    raw = Path(path).expanduser()
    if ".." in raw.parts or str(raw) == ":memory:":
        raise ValueError("database path must be a file without traversal")
    target = raw.absolute()
    for part in (target, *target.parents):
        if part.is_symlink():
            raise ValueError("database path must not contain symlinks")
    if target.exists() and not target.is_file():
        raise ValueError("database path is not a regular file")
    for suffix in SIDECAR_SUFFIXES:
        sidecar = Path(str(target) + suffix)
        if sidecar.is_symlink() or (
            sidecar.exists() and not sidecar.is_file()
        ):
            raise ValueError("unsafe database sidecar")
    return target


def _prepare_path(path):
    target = _safe_path(path)
    missing = [p for p in target.parents if not p.exists()]
    for parent in reversed(missing):
        parent.mkdir(mode=PRIVATE_DIRECTORY_MODE, exist_ok=True)
    _safe_path(target)
    descriptor = os.open(
        target, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, PRIVATE_FILE_MODE
    )
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.getuid()):
            raise ValueError("database must be an owned single-link regular file")
        os.fchmod(descriptor, PRIVATE_FILE_MODE)
    finally:
        os.close(descriptor)
    return target


def _schema_sql():
    return Path(__file__).with_name("schema.sql").read_text()


def _statements(script):
    pending = ""
    for line in script.splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            yield pending
            pending = ""
    if pending.strip():
        raise ValueError("incomplete schema SQL")


def _version(connection):
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise ValueError("database schema is newer than this application")
    if version < 0:
        raise ValueError("invalid database schema version")
    return version


def _validate_schema(connection):
    for table, columns in TABLE_COLUMNS.items():
        actual = connection.execute(f"PRAGMA table_info({table})").fetchall()
        if [row["name"] for row in actual] != [name for name, _ in columns]:
            raise ValueError("database schema columns mismatch: " + table)
        ordered = sorted(actual, key=lambda row: row["pk"])
        keys = tuple(row["name"] for row in ordered if row["pk"])
        if keys != PRIMARY_KEYS[table]:
            raise ValueError("database schema key mismatch: " + table)


def _migrate(connection):
    _version(connection)  # Refuse future formats before changing journal mode.
    mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
    if mode.lower() != "wal":
        raise ValueError("SQLite WAL mode unavailable")
    connection.execute("BEGIN IMMEDIATE")
    try:
        if _version(connection) == 0:
            tables = connection.execute(
                "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
            )
            if tables.fetchone() is not None:
                raise ValueError("unversioned database requires review")
            for statement in _statements(_schema_sql()):
                connection.execute(statement)
            connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        _validate_schema(connection)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def _upsert_sql(table):
    columns = [name for name, _ in TABLE_COLUMNS[table]]
    keys = PRIMARY_KEYS[table]
    assignments = ", ".join(
        f"{name}=excluded.{name}" for name in columns if name not in keys
    )
    placeholders = ", ".join("?" for _ in columns)
    names = ", ".join(columns)
    return (
        f"INSERT INTO {table} ({names}) VALUES ({placeholders}) "
        f"ON CONFLICT ({', '.join(keys)}) DO UPDATE SET {assignments}"
    )


@contextmanager
def _batch(connection):
    # Reserve the writer before reads to avoid WAL snapshot-upgrade failures.
    owned = not connection.in_transaction
    connection.execute('BEGIN IMMEDIATE' if owned else 'SAVEPOINT trader_upsert')
    try:
        yield
        if owned:
            connection.commit()
        else:
            connection.execute('RELEASE SAVEPOINT trader_upsert')
    except BaseException:
        if owned:
            connection.rollback()
        else:
            connection.execute('ROLLBACK TO SAVEPOINT trader_upsert')
            connection.execute('RELEASE SAVEPOINT trader_upsert')
        raise


class Store:
    """Open one SQLite connection; records and query results are independent.

    ``connection`` is exposed for explicit caller-controlled transactions.
    ``upsert`` rolls back its whole batch on failure, preserving an outer
    transaction if present. The connection is confined to its creating thread.
    """

    def __init__(self, path):
        self.path = _prepare_path(path)
        self.connection = sqlite3.connect(
            self.path, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None
        )
        self.connection.row_factory = sqlite3.Row
        try:
            self.connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
            self.connection.execute("PRAGMA foreign_keys=ON")
            _migrate(self.connection)
        except BaseException:
            self.connection.close()
            raise

    def upsert(self, table, rows):
        """Apply a whole batch; repeated keys update existing records."""
        if not isinstance(table, str) or table not in TABLE_COLUMNS:
            raise ValueError("unsupported storage table")
        records = [validate(table, row) for row in rows]
        if not records:
            return 0
        _safe_path(self.path)
        with _batch(self.connection):
            self.connection.executemany(_upsert_sql(table), records)
        return len(records)

    @contextmanager
    def transaction(self):
        """Group operations atomically; nested scopes preserve outer work."""
        _safe_path(self.path)
        with _batch(self.connection):
            yield self

    def query(self, sql, parameters=()):
        """Run parameterized caller SQL; return independent dictionaries."""
        return [dict(row) for row in self.connection.execute(sql, parameters)]

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, exception_type, exception, traceback):
        self.close()
