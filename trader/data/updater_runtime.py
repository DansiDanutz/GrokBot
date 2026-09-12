"""Owned collector lock and monotonic, anchored update scheduling."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import fcntl
import math
import os
import stat
from pathlib import Path
import time

from trader.data.constants import PRIVATE_DIRECTORY_MODE
from trader.data.store import _safe_path

CADENCE_SECONDS = 300
SECONDS_PER_HOUR = 3600
MILLISECONDS_PER_SECOND = 1000
EARLIEST_EPOCH_SECONDS = 1_000_000_000
LATEST_EPOCH_SECONDS = 10_000_000_000


@dataclass(frozen=True)
class Clock:
    wall: object = time.time
    monotonic: object = time.monotonic
    sleep: object = time.sleep


class AlreadyRunning(RuntimeError):
    """Another updater owns this database's collector lock."""


class CollectorLock:
    """Keep the inode after release so contenders lock the same file."""

    def __init__(self, database, suffix='.updater.lock'):
        if (not isinstance(suffix, str) or not suffix.startswith('.')
                or '/' in suffix or '\0' in suffix or len(suffix) > 64):
            raise ValueError('invalid collector lock suffix')
        self.database = _safe_path(database)
        self.path = Path(str(self.database) + suffix)
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, mode=PRIVATE_DIRECTORY_MODE, exist_ok=True)
        _safe_path(self.database)
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                             0o600)
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.getuid()):
            os.close(descriptor)
            raise OSError('collector lock must be an owned single-link regular file')
        os.fchmod(descriptor, 0o600)
        self.handle = os.fdopen(descriptor, 'r+')
        try:
            fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.handle.close()
            self.handle = None
            raise AlreadyRunning(
                'market-data updater already running') from None
        self.handle.seek(0)
        self.handle.truncate()
        self.handle.write(str(os.getpid()) + '\n')
        self.handle.flush()
        return self

    def __exit__(self, *_):
        if self.handle is not None:
            fcntl.flock(self.handle, fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None


def epoch_ms(value):
    """Normalize current-era s/ms/us/ns with Decimal to avoid float loss."""
    if isinstance(value, bool):
        raise ValueError('invalid provider timestamp')
    try:
        stamp = Decimal(str(value))
        if not stamp.is_finite() or stamp <= 0:
            raise ValueError('invalid provider timestamp')
        if stamp >= Decimal('1e17'):
            stamp /= 1_000_000
        elif stamp >= Decimal('1e14'):
            stamp /= 1000
        elif stamp < Decimal('1e11'):
            stamp *= 1000
        earliest = EARLIEST_EPOCH_SECONDS * MILLISECONDS_PER_SECOND
        latest = LATEST_EPOCH_SECONDS * MILLISECONDS_PER_SECOND
        if not earliest <= stamp < latest:
            raise ValueError('invalid provider timestamp')
        return int(stamp)
    except (InvalidOperation, TypeError):
        raise ValueError('invalid provider timestamp') from None


def scheduled_run(cycle, duration_hours, clock=None):
    """Run immediately then on anchored slots, never bursting missed slots."""
    clock = clock or Clock()
    if not math.isfinite(duration_hours) or not 0 < duration_hours <= 24:
        raise ValueError('duration must be positive and at most 24 hours')
    anchor = clock.monotonic()
    deadline = anchor + duration_hours * SECONDS_PER_HOUR
    next_start, count = anchor, 0
    while clock.monotonic() < deadline:
        clock.sleep(max(0, min(next_start, deadline) - clock.monotonic()))
        if clock.monotonic() >= deadline:
            break
        cycle(deadline)
        count += 1
        elapsed = clock.monotonic() - anchor
        slot = max(count, math.ceil(elapsed / CADENCE_SECONDS))
        next_start = anchor + slot * CADENCE_SECONDS
    return count
