"""Offline scheduler, timestamp and ownership regression tests."""
import multiprocessing
from pathlib import Path
import tempfile
import unittest

from trader.data.updater_runtime import (
    AlreadyRunning, Clock, CollectorLock, epoch_ms, scheduled_run,
)


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def contend(path, queue):
    try:
        with CollectorLock(path):
            queue.put('acquired')
    except AlreadyRunning:
        queue.put('blocked')


class RuntimeTests(unittest.TestCase):
    def test_twenty_four_hours_has_288_anchored_cycles(self):
        fake = FakeClock()
        started = []

        def cycle(deadline):
            started.append(fake.now)
            fake.now += 11

        count = scheduled_run(cycle, 24, fake)
        self.assertEqual(count, 288)
        self.assertEqual(started, list(range(0, 86400, 300)))
        self.assertEqual(fake.now, 86400)

    def test_slow_cycle_skips_missed_slots_without_burst(self):
        fake = FakeClock()
        started = []

        def cycle(deadline):
            started.append(fake.now)
            fake.now += 701

        scheduled_run(cycle, 1, fake)
        self.assertEqual(started, [0, 900, 1800, 2700])
        self.assertEqual(fake.now, 3600)

    def test_lock_blocks_other_process_and_recovers_after_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / 'market.sqlite3'
            ctx = multiprocessing.get_context('spawn')
            queue = ctx.Queue()
            with CollectorLock(path):
                child = ctx.Process(target=contend, args=(path, queue))
                child.start()
                child.join(10)
                self.assertEqual(child.exitcode, 0)
                self.assertEqual(queue.get(timeout=1), 'blocked')
            with CollectorLock(path):
                pass
            self.assertTrue(Path(str(path) + '.updater.lock').exists())

    def test_provider_timestamp_units_preserve_millisecond_precision(self):
        expected = 1789123456123
        self.assertEqual(epoch_ms(expected), expected)
        self.assertEqual(epoch_ms(expected * 1000000), expected)
        self.assertEqual(epoch_ms(expected * 1000), expected)
        self.assertEqual(epoch_ms(1789123456), 1789123456000)
        for value in [True, float('nan'), -1, 0, 'oops']:
            with self.assertRaises(ValueError):
                epoch_ms(value)

    def test_lock_refuses_symlink_without_overwriting_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            target = root / 'target'
            target.write_text('unchanged')
            database = root / 'market.sqlite3'
            Path(str(database) + '.updater.lock').symlink_to(target)
            with self.assertRaises(OSError):
                with CollectorLock(database):
                    pass
            self.assertEqual(target.read_text(), 'unchanged')

    def test_clock_defaults_are_callables(self):
        self.assertTrue(callable(Clock().monotonic))
        self.assertTrue(callable(Clock().wall))


class LockPathTests(unittest.TestCase):
    def test_parent_alias_is_rejected_without_creating_external_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            actual = root / 'actual'
            actual.mkdir()
            alias = root / 'alias'
            alias.symlink_to(actual, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, 'symlink'):
                with CollectorLock(alias / 'market.sqlite3'):
                    pass
            self.assertEqual(list(actual.iterdir()), [])

    def test_new_collector_directory_is_private(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder).resolve() / 'new' / 'market.sqlite3'
            with CollectorLock(database):
                self.assertEqual(database.parent.stat().st_mode & 0o777, 0o700)


if __name__ == '__main__':
    unittest.main()
