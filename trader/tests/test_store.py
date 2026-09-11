"""Storage contracts tested with disposable local SQLite databases."""

from copy import deepcopy
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from trader.data.store import Store


CANDLE = dict(
    symbol="XBTUSDTM",
    interval="1m",
    time_ms=60000,
    open=10,
    high=12,
    low=9,
    close=11,
    volume=3,
    turnover=33,
)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.path = self.root / "market.sqlite3"

    def store(self):
        store = Store(self.path)
        self.addCleanup(store.close)
        return store

    def test_read_then_write_reserves_transaction_before_another_writer(self):
        first = self.store()
        ready, go, attempted, finished = [threading.Event() for _ in range(4)]
        errors = []

        def writer():
            try:
                with Store(self.path) as second:
                    ready.set()
                    if go.wait(5):
                        attempted.set()
                        second.upsert("klines", [dict(CANDLE, time_ms=120000)])
            except Exception as error:
                errors.append(error)
            finally:
                finished.set()

        thread = threading.Thread(target=writer)
        thread.start()
        try:
            self.assertTrue(ready.wait(5))
            with first.transaction():
                first.query("SELECT * FROM klines")
                go.set()
                self.assertTrue(attempted.wait(5))
                self.assertFalse(finished.wait(0.2))
                first.upsert("klines", [CANDLE])
        finally:
            go.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(first.query("SELECT * FROM klines")), 2)

    def test_database_hardlink_is_rejected_before_permission_changes(self):
        target = self.root / "original"
        target.write_text("unchanged")
        target.chmod(0o640)
        os.link(target, self.path)
        with self.assertRaises(ValueError):
            Store(self.path)
        self.assertEqual(target.read_text(), "unchanged")
        self.assertEqual(target.stat().st_mode & 0o777, 0o640)

    def test_fresh_schema_wal_and_idempotent_reopen(self):
        store = self.store()
        self.assertEqual(
            store.query("PRAGMA user_version")[0]["user_version"], 1
        )
        self.assertEqual(
            store.query("PRAGMA journal_mode")[0]["journal_mode"], "wal"
        )
        self.assertGreater(store.query("PRAGMA busy_timeout")[0]["timeout"], 0)
        store.upsert("klines", [CANDLE])
        store.close()
        self.assertEqual(self.store().query("SELECT * FROM klines"), [CANDLE])

    def test_upsert_replaces_one_key_and_preserves_other_intervals(self):
        store = self.store()
        before = deepcopy(CANDLE)
        self.assertEqual(store.upsert("klines", [CANDLE, CANDLE]), 2)
        updated = dict(CANDLE, close=10.5, turnover=31.5)
        store.upsert(
            "klines", [updated, dict(CANDLE, interval="1h", time_ms=0)]
        )
        self.assertEqual(len(store.query("SELECT * FROM klines")), 2)
        self.assertEqual(
            store.query("SELECT close FROM klines WHERE interval=?", ("1m",)),
            [{"close": 10.5}],
        )
        self.assertEqual(CANDLE, before)

    def test_all_market_tables_upsert_twice_once(self):
        records = {
            "funding": dict(
                symbol="XBTUSDTM", time_ms=0, rate=-0.001, period_ms=28800000
            ),
            "open_interest": dict(
                symbol="XBTUSDTM",
                time_ms=0,
                observed_at_ms=0,
                source_time_ms=None,
                open_interest=200,
            ),
            "top_of_book": dict(
                symbol="XBTUSDTM",
                time_ms=1,
                observed_at_ms=2,
                bid=10,
                ask=11,
                bid_size=2,
                ask_size=3,
            ),
            "coinglass_liquidations": dict(
                symbol="BTC",
                exchange="Binance,OKX,Bybit",
                time_ms=0,
                long_usd=0,
                short_usd=500,
            ),
        }
        store = self.store()
        for table, row in records.items():
            with self.subTest(table=table):
                store.upsert(table, [row])
                store.upsert(table, [row])
                self.assertEqual(store.query("SELECT * FROM " + table), [row])

    def test_registry_checkpoint_quality_and_ticker_foundations(self):
        records = {
            "checkpoints": dict(
                source="kucoin",
                symbol="XBTUSDTM",
                interval="1m",
                next_time_ms=120000,
                updated_at_ms=123456,
            ),
            "universe": dict(
                symbol="XBTUSDTM",
                updated_at_ms=1,
                first_candle_ms=None,
                listed_at_ms=None,
                turnover_30d=None,
                atr_pct=None,
                multiplier=0.001,
                lot_size=1,
                active=1,
                coverage_json="{}",
            ),
            "data_quality": dict(
                check_name="candle_gaps",
                symbol="XBTUSDTM",
                interval="1m",
                start_ms=0,
                end_ms=60000,
                checked_at_ms=120000,
                status="pass",
                details_json=json.dumps({"gaps": 0}),
            ),
        }
        store = self.store()
        for table, row in records.items():
            with self.subTest(table=table):
                store.upsert(table, [row, row])
                self.assertEqual(store.query("SELECT * FROM " + table), [row])

    def test_full_raw_ticker_snapshot_round_trip(self):
        row = dict(
            symbol="XBTUSDTM",
            time_ms=1,
            observed_at_ms=2,
            source_time_ms=None,
            last=10,
            mark_price=10,
            index_price=10,
            volume_24h=4,
            turnover_24h=40,
            open_interest=100,
            funding_rate=-0.001,
            raw_json="{}",
        )
        store = self.store()
        store.upsert("ticker_snapshots", [row, row])
        self.assertEqual(store.query("SELECT * FROM ticker_snapshots"), [row])

    def test_invalid_second_record_rolls_back_entire_batch(self):
        store = self.store()
        store.upsert("klines", [CANDLE])
        with self.assertRaises(ValueError):
            store.upsert(
                "klines", [dict(CANDLE, close=10), dict(CANDLE, high=1)]
            )
        self.assertEqual(store.query("SELECT * FROM klines"), [CANDLE])

    def test_database_failure_rolls_back_entire_batch(self):
        store = self.store()
        store.connection.execute(
            "CREATE TRIGGER fail_second BEFORE INSERT ON klines "
            "WHEN NEW.time_ms=120000 BEGIN "
            "SELECT RAISE(ABORT, 'fixture failure'); END"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            store.upsert("klines", [CANDLE, dict(CANDLE, time_ms=120000)])
        self.assertEqual(store.query("SELECT * FROM klines"), [])

    def test_rejects_nonfinite_ohlc_negative_volume_and_bad_timestamps(self):
        store = self.store()
        changes = [
            dict(high=8),
            dict(low=13),
            dict(close=13),
            dict(open=0),
            dict(volume=-1),
            dict(turnover=-1),
            dict(open=float("nan")),
            dict(close=float("inf")),
            dict(time_ms=True),
            dict(time_ms=1.5),
            dict(time_ms=-1),
            dict(time_ms=1000),
            dict(interval="5m"),
        ]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                store.upsert("klines", [dict(CANDLE, **change)])
        self.assertEqual(store.query("SELECT * FROM klines"), [])

    def test_rejects_unknown_tables_fields_missing_fields_and_inverted_book(
        self,
    ):
        store = self.store()
        for table, row in [
            ("klines; DROP TABLE klines", CANDLE),
            ("klines", dict(CANDLE, surprise=1)),
            ("klines", {"symbol": "XBTUSDTM"}),
            (
                "top_of_book",
                dict(
                    symbol="XBTUSDTM",
                    time_ms=1,
                    observed_at_ms=2,
                    bid=12,
                    ask=10,
                    bid_size=2,
                    ask_size=3,
                ),
            ),
        ]:
            with self.subTest(table=table), self.assertRaises(ValueError):
                store.upsert(table, [row])

    def test_future_schema_refused_without_downgrade(self):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("PRAGMA user_version=999")
        with self.assertRaisesRegex(ValueError, "newer"):
            Store(self.path)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            self.assertEqual(
                connection.execute("PRAGMA user_version").fetchone()[0], 999
            )
            self.assertEqual(
                connection.execute("PRAGMA journal_mode").fetchone()[0],
                "delete",
            )

    def test_failed_migration_leaves_no_partial_tables_or_version(self):
        from trader.data import store as module

        with patch.object(
            module,
            "_schema_sql",
            return_value=("CREATE TABLE partial (id INTEGER);\nINVALID SQL;"),
        ):
            with self.assertRaises(sqlite3.OperationalError):
                Store(self.path)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            self.assertEqual(
                connection.execute("PRAGMA user_version").fetchone()[0], 0
            )
            self.assertEqual(
                connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall(),
                [],
            )
        self.assertEqual(
            self.store().query("PRAGMA user_version")[0]["user_version"], 1
        )

    def test_symlink_leaf_ancestor_and_sidecar_are_rejected(self):
        target = self.root / "target"
        target.mkdir()
        (self.root / "linked").symlink_to(target, target_is_directory=True)
        with self.assertRaises(ValueError):
            Store(self.root / "linked" / "db.sqlite3")
        self.path.symlink_to(target / "db.sqlite3")
        with self.assertRaises(ValueError):
            Store(self.path)
        self.path.unlink()
        Path(str(self.path) + "-wal").symlink_to(target / "wal")
        with self.assertRaises(ValueError):
            Store(self.path)
        self.assertFalse(self.path.exists())

    def test_parent_creation_private_permissions_and_context_manager(self):
        path = self.root / "nested" / "private" / "db.sqlite3"
        with Store(path) as store:
            store.upsert("klines", [CANDLE])
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(path.parent.parent.stat().st_mode & 0o777, 0o700)
        with self.assertRaises(sqlite3.ProgrammingError):
            store.query("SELECT 1")

    def test_corrupt_existing_version_one_schema_fails_closed(self):
        store = self.store()
        store.connection.execute("DROP TABLE klines")
        store.close()
        with self.assertRaisesRegex(ValueError, "schema"):
            Store(self.path)

    def test_unversioned_nonempty_database_fails_closed(self):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("CREATE TABLE unrelated (id INTEGER)")
        with self.assertRaisesRegex(ValueError, "unversioned"):
            Store(self.path)

    def test_transaction_groups_candles_and_checkpoint_with_rollback(self):
        store = self.store()
        checkpoint = dict(
            source="kucoin",
            symbol="XBTUSDTM",
            interval="1m",
            next_time_ms=120000,
            updated_at_ms=123456,
        )
        with self.assertRaisesRegex(RuntimeError, "checkpoint failure"):
            with store.transaction():
                store.upsert("klines", [CANDLE])
                store.upsert("checkpoints", [checkpoint])
                raise RuntimeError("checkpoint failure")
        self.assertEqual(store.query("SELECT * FROM klines"), [])
        self.assertEqual(store.query("SELECT * FROM checkpoints"), [])
        with store.transaction():
            store.upsert("klines", [CANDLE])
            store.upsert("checkpoints", [checkpoint])
        self.assertEqual(
            store.query("SELECT * FROM checkpoints"), [checkpoint]
        )
        self.assertEqual(store.query("SELECT * FROM klines"), [CANDLE])

    def test_nested_scope_failure_preserves_outer_transaction(self):
        store = self.store()
        with store.transaction():
            store.upsert("klines", [CANDLE])
            with self.assertRaises(RuntimeError):
                with store.transaction():
                    store.upsert("klines", [dict(CANDLE, time_ms=120000)])
                    raise RuntimeError("inner fails")
            store.upsert("klines", [dict(CANDLE, time_ms=180000)])
        times = store.query("SELECT time_ms FROM klines ORDER BY time_ms")
        self.assertEqual(times, [{"time_ms": 60000}, {"time_ms": 180000}])

    def test_caller_connection_transaction_is_not_committed_by_upsert(self):
        store = self.store()
        store.connection.execute("BEGIN")
        store.upsert("klines", [CANDLE])
        self.assertTrue(store.connection.in_transaction)
        store.connection.rollback()
        self.assertEqual(store.query("SELECT * FROM klines"), [])

    def test_funding_unknown_period_and_data_quality_cycle_cadence(self):
        store = self.store()
        row = dict(symbol="XBTUSDTM", time_ms=1234, rate=0.01, period_ms=None)
        store.upsert("funding", [row])
        self.assertEqual(store.query("SELECT * FROM funding"), [row])
        with self.assertRaises(ValueError):
            store.upsert("funding", [dict(row, period_ms=0)])
        quality = dict(
            check_name="collection_cycle",
            symbol="*",
            interval="5m",
            start_ms=0,
            end_ms=300000,
            checked_at_ms=300000,
            status="warn",
            details_json='{"missing": ["funding"]}',
        )
        store.upsert("data_quality", [quality])
        self.assertEqual(store.query("SELECT * FROM data_quality"), [quality])

    def test_json_source_rejects_invalid_nonfinite_and_nonobject_payload(self):
        store = self.store()
        row = dict(
            check_name="coverage",
            symbol="BTC",
            interval="1h",
            start_ms=0,
            end_ms=3600000,
            checked_at_ms=3600001,
            status="pass",
        )
        for payload in ("broken", '{"value": NaN}', '{"value": 1e400}', "[]"):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                store.upsert("data_quality", [dict(row, details_json=payload)])
        self.assertEqual(store.query("SELECT * FROM data_quality"), [])

    def test_relative_traversal_and_sidecar_created_after_open_are_rejected(
        self,
    ):
        with self.assertRaises(ValueError):
            Store(self.root / "nested" / ".." / "db.sqlite3")
        store = self.store()
        journal = Path(str(self.path) + "-journal")
        journal.symlink_to(self.root / "elsewhere")
        with self.assertRaises(ValueError):
            store.upsert("klines", [CANDLE])
        self.assertEqual(store.query("SELECT * FROM klines"), [])


if __name__ == "__main__":
    unittest.main()
