from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from trader.autopilot.storage import (
    EventLog, InstanceLock, atomic_json, read_events, read_json,
)


class StorageTests(unittest.TestCase):
    def test_idle_event_log_still_expires_thirty_day_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            log = EventLog(root)
            log.append([dict(ts_ms=86400000, bot_id=0, symbol='SYSTEM', type='ERROR', event_id=1)])
            log.prune(32 * 86400000)
            self.assertEqual(read_events(root, 0), [])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def test_atomic_snapshot_is_complete_and_private_during_writes(self):
        path = self.root / "state.json"
        atomic_json(path, {"generation": 0, "values": [0] * 1000})
        failures = []
        def writer():
            for generation in range(1, 50):
                atomic_json(path, {"generation": generation,
                                   "values": [generation] * 1000})
        thread = threading.Thread(target=writer)
        thread.start()
        while thread.is_alive():
            try:
                data = read_json(path)
                self.assertEqual(set(data["values"]), {data["generation"]})
            except Exception as error:
                failures.append(error)
        thread.join()
        self.assertEqual(failures, [])
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(self.root.iterdir()), [path])

    def test_json_rejects_symlinks_size_nonfinite_and_preserves_old_snapshot(self):
        path = self.root / "state.json"
        atomic_json(path, {"a": 1})
        for value in (float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                atomic_json(path, {"a": value})
        self.assertEqual(read_json(path), {"a": 1})
        with self.assertRaisesRegex(ValueError, "size"):
            read_json(path, max_bytes=1)
        path.write_text('{"a":NaN}')
        with self.assertRaises(ValueError):
            read_json(path)
        alias = self.root / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        for operation in (lambda: read_json(alias / "state.json"),
                          lambda: atomic_json(alias / "state.json", {})):
            with self.assertRaisesRegex(ValueError, "symlink"):
                operation()

    def test_lock_excludes_second_instance_and_reuses_inode(self):
        path = self.root / "instance.lock"
        with InstanceLock(path):
            inode = path.stat().st_ino
            with self.assertRaisesRegex(RuntimeError, "already running"):
                with InstanceLock(path):
                    self.fail("second lock acquired")
        with InstanceLock(path):
            self.assertEqual(path.stat().st_ino, inode)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_interrupted_replace_keeps_old_snapshot_and_removes_temporary(self):
        path = self.root / "state.json"
        atomic_json(path, {"generation": 1})
        with patch("trader.autopilot.storage.os.replace", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                atomic_json(path, {"generation": 2})
        self.assertEqual(read_json(path), {"generation": 1})
        self.assertEqual(list(self.root.iterdir()), [path])

    def test_events_rotate_retain_thirty_days_and_read_bounded(self):
        log = EventLog(self.root)
        for day in range(40):
            log.append([{"ts_ms": day * 86400000, "bot_id": 1,
                         "symbol": "RAYUSDTM", "type": "FILL", "price": 1.5}])
        self.assertEqual(len(list(self.root.glob("events*.jsonl"))), 30)
        events = read_events(self.root, 0, limit=2)
        self.assertEqual([e["ts_ms"] for e in events], [10 * 86400000, 11 * 86400000])
        self.assertEqual(len(read_events(self.root, 38 * 86400000)), 1)
        with self.assertRaises(ValueError):
            read_events(self.root, 0, limit=501)
        for path in self.root.glob("*.jsonl"):
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_events_schema_rejects_text_bool_and_invalid_identity_before_append(self):
        valid = {"ts_ms": 1, "bot_id": 0, "symbol": "SYSTEM", "type": "ERROR"}
        log = EventLog(self.root)
        for patch in ({"message": "secret"}, {"price": True},
                      {"price": float("inf")}, {"type": "OTHER"},
                      {"bot_id": -1}, {"symbol": "../../token"}):
            with self.assertRaises(ValueError):
                log.append([valid, dict(valid, **patch)])
        self.assertFalse((self.root / "events.jsonl").exists())
        log.append([dict(valid, event_id=1)])
        self.assertEqual(read_events(self.root, 0), [dict(valid, event_id=1)])

    def test_event_ids_dedupe_retried_batch_and_historical_day_appends(self):
        log = EventLog(self.root)
        def event(day, event_id):
            return {"ts_ms": day * 86400000, "bot_id": 1,
                    "symbol": "RAYUSDTM", "type": "FILL", "event_id": event_id}
        log.append([event(20, 1), event(21, 2)])
        EventLog(self.root).append([event(20, 1), event(21, 2), event(21, 3)])
        log.append([event(20, 4)])
        EventLog(self.root).append([event(20, 4), event(21, 5)])
        events = read_events(self.root, 0)
        self.assertEqual(sorted(e["event_id"] for e in events), [1, 2, 3, 4, 5])
        self.assertEqual(len(list(self.root.glob("events*.jsonl"))), 2)
        self.assertEqual(read_json_line(self.root / "events.jsonl")["ts_ms"], 21 * 86400000)

    def test_event_retry_repairs_interrupted_trailing_line(self):
        first = {"ts_ms": 1, "bot_id": 0, "symbol": "SYSTEM",
                 "type": "ERROR", "event_id": 1}
        second = dict(first, ts_ms=2, event_id=2)
        EventLog(self.root).append([first])
        with (self.root / "events.jsonl").open("ab") as stream:
            stream.write(b'{"ts_ms":2,"bot_id":')
        EventLog(self.root).append([first, second])
        self.assertEqual(read_events(self.root, 0), [first, second])

    def test_event_reader_rejects_symlink_and_oversized_logs(self):
        path = self.root / "events.jsonl"
        path.symlink_to(self.root / "other")
        with self.assertRaisesRegex(ValueError, "symlink"):
            read_events(self.root, 0)
        path.unlink()
        path.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
        with self.assertRaisesRegex(ValueError, "size"):
            read_events(self.root, 0)


def read_json_line(path):
    import json
    return json.loads(path.read_text().splitlines()[0])
