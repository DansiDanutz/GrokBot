"""Offline registry tests with synthetic contracts and stored bars."""

import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from trader.data.store import Store
from trader.data.universe import (
    main,
    refresh_registry,
    registry_report,
    stored_contracts,
)

MINUTE = 60000
HOUR = 60 * MINUTE
DAY = 24 * HOUR
NOW = 100 * DAY + 7 * HOUR


def contract(symbol="BTCUSDTM", listed=DAY):
    return {
        "symbol": symbol, "status": "Open", "quoteCurrency": "USDT",
        "settleCurrency": "USDT", "isInverse": False, "type": "FFWCSX",
        "expireDate": None, "settleDate": None, "assetClass": "CRYPTO",
        "firstOpenDate": listed, "multiplier": "0.001", "lotSize": 1,
    }


def candle(at, interval="1m", turnover=10, symbol="BTCUSDTM"):
    return {
        "symbol": symbol, "interval": interval, "time_ms": at,
        "open": 100, "high": 102, "low": 98, "close": 100,
        "volume": 1, "turnover": turnover,
    }


def ticker(raw, at=NOW):
    return {
        "symbol": raw["symbol"], "time_ms": at, "observed_at_ms": at,
        "source_time_ms": None, "last": 100, "mark_price": 100,
        "index_price": 100, "volume_24h": 1, "turnover_24h": 100,
        "open_interest": 1, "funding_rate": 0, "raw_json": json.dumps(raw),
    }


class UniverseTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name).resolve() / "market.sqlite"
        self.store = Store(self.path)
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(self.store.close)

    def report(self):
        return registry_report(self.store, now_ms=NOW)

    def first(self):
        return self.report()["contracts"][0]

    def test_over_100_contracts_have_listing_ages_and_nullable_evidence(self):
        contracts = [contract(f"COIN{i:03}USDTM") for i in range(110)]
        before = copy.deepcopy(contracts)
        refresh_registry(self.store, contracts, now_ms=NOW, complete=True)
        report = self.report()
        self.assertEqual(report["stats"]["active"], 110)
        self.assertEqual(len(report["contracts"]), 110)
        self.assertEqual(contracts, before)
        for row in report["contracts"]:
            self.assertEqual(row["listing_age_ms"], NOW - DAY)
            self.assertIsNone(row["first_candle_ms"])
            self.assertIsNone(row["observed_history_age_ms"])
            self.assertIsNone(row["turnover_30d"])
            self.assertIsNone(row["atr_pct"])

    def test_first_candle_is_observed_history_not_listing(self):
        self.store.upsert("klines", [candle(20 * DAY), candle(NOW)])
        refresh_registry(self.store, [contract()], now_ms=NOW)
        row = self.first()
        self.assertEqual(row["first_candle_ms"], 20 * DAY)
        self.assertEqual(row["listed_at_ms"], DAY)
        self.assertEqual(row["observed_history_age_ms"], NOW - 20 * DAY)
        self.assertEqual(row["coverage"]["first_candle"]["meaning"],
                         "oldest_observed_completed_candle")

    def test_turnover_partial_excludes_open_old_and_hourly_bars(self):
        rows = [candle(NOW - 2 * MINUTE, turnover=3),
                candle(NOW - MINUTE, turnover=4),
                candle(NOW, turnover=999),
                candle(NOW - 30 * DAY - MINUTE, turnover=999),
                candle(NOW - HOUR, "1h", turnover=999)]
        self.store.upsert("klines", rows)
        refresh_registry(self.store, [contract()], now_ms=NOW + 1234)
        row = self.first()
        coverage = row["coverage"]["turnover_30d"]
        self.assertEqual(row["turnover_30d"], 7)
        self.assertEqual(coverage["status"], "partial")
        self.assertEqual(coverage["observed_candles"], 2)
        self.assertEqual(coverage["expected_candles"], 43200)

    def test_complete_turnover_coverage_and_real_zero(self):
        rows = [candle(at, turnover=0) for at in
                range(NOW - 30 * DAY, NOW, MINUTE)]
        self.store.upsert("klines", rows)
        refresh_registry(self.store, [contract()], now_ms=NOW)
        row = self.first()
        self.assertEqual(row["turnover_30d"], 0)
        self.assertEqual(row["coverage"]["turnover_30d"]["status"], "complete")

    def test_atr_requires_15_latest_consecutive_completed_hours(self):
        rows = [candle(NOW - i * HOUR, "1h") for i in range(1, 16)]
        rows[0] = {**rows[0], "high": 110, "low": 100, "close": 108}
        self.store.upsert("klines", rows + [candle(NOW, "1h")])
        refresh_registry(self.store, [contract()], now_ms=NOW)
        expected = ((13 * 4 + 10) / 14) / 108 * 100
        self.assertAlmostEqual(self.first()["atr_pct"], expected)
        self.store.query("DELETE FROM klines WHERE time_ms=?",
                         (NOW - 7 * HOUR,))
        refresh_registry(self.store, [contract()], now_ms=NOW)
        self.assertIsNone(self.first()["atr_pct"])

    def test_old_consecutive_hour_bars_are_not_current_atr(self):
        self.store.upsert("klines", [candle(NOW - i * HOUR, "1h")
                                    for i in range(2, 17)])
        refresh_registry(self.store, [contract()], now_ms=NOW)
        self.assertIsNone(self.first()["atr_pct"])

    def test_partial_refresh_preserves_absent_complete_marks_inactive(self):
        first, second = contract(), contract("ETHUSDTM")
        refresh_registry(self.store, [first, second],
                         now_ms=NOW, complete=True)
        refresh_registry(self.store, [first], now_ms=NOW + MINUTE)
        self.assertEqual(self.report()["stats"]["active"], 2)
        refresh_registry(self.store, [first], now_ms=NOW + 2 * MINUTE,
                         complete=True)
        rows = self.report()["contracts"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["active"], 0)
        self.assertEqual(rows[1]["listed_at_ms"], DAY)
        refresh_registry(self.store, [second], now_ms=NOW + 3 * MINUTE)
        self.assertEqual(self.report()["stats"]["active"], 2)

    def test_invalid_metadata_rejects_whole_refresh_without_deactivation(self):
        refresh_registry(self.store, [contract()], now_ms=NOW)
        before = self.store.query("SELECT * FROM universe")
        invalid = {**contract("ETHUSDTM"), "lotSize": 1.5}
        with self.assertRaises(ValueError):
            refresh_registry(self.store, [contract(), invalid], now_ms=NOW,
                             complete=True)
        self.assertEqual(before, self.store.query("SELECT * FROM universe"))
        with self.assertRaises(ValueError):
            refresh_registry(self.store, [], now_ms=NOW, complete=True)

    def test_future_listing_and_invalid_asof_are_rejected(self):
        for value in (True, -1, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                refresh_registry(self.store, [contract()], now_ms=value)
        with self.assertRaises(ValueError):
            refresh_registry(self.store, [contract(listed=NOW + 1)],
                             now_ms=NOW)

    def test_stored_snapshot_does_not_mix_epochs_or_future_records(self):
        self.store.upsert("ticker_snapshots", [
            ticker(contract("OLDUSDTM"), NOW - MINUTE),
            ticker(contract(), NOW), ticker(contract("ETHUSDTM"), NOW),
            ticker(contract("FUTUREUSDTM"), NOW + MINUTE)])
        rows = stored_contracts(self.store, now_ms=NOW)
        self.assertEqual([r["symbol"] for r in rows], ["BTCUSDTM", "ETHUSDTM"])

    def test_cli_needs_explicit_database_and_refreshes_offline(self):
        self.store.upsert("ticker_snapshots", [ticker(contract())])
        with patch("urllib.request.OpenerDirector.open",
                   side_effect=AssertionError("No network")):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                result = main(["--database", str(self.path), "--refresh",
                               "--now-ms", str(NOW)])
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue())["stats"]["active"], 1)
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                main([])

    def test_stored_raw_symbol_mismatch_fails(self):
        row = ticker(contract())
        row["raw_json"] = json.dumps(contract("ETHUSDTM"))
        self.store.upsert("ticker_snapshots", [row])
        with self.assertRaises(ValueError):
            stored_contracts(self.store, now_ms=NOW)

    def test_stale_refresh_cannot_regress_or_deactivate_newer_registry(self):
        refresh_registry(self.store, [contract()], now_ms=NOW)
        before = self.store.query("SELECT * FROM universe")
        with self.assertRaises(ValueError):
            refresh_registry(self.store, [contract("ETHUSDTM")],
                             now_ms=NOW - MINUTE, complete=True)
        self.assertEqual(self.store.query("SELECT * FROM universe"), before)

    def test_refresh_batch_rolls_back_on_store_failure(self):
        refresh_registry(self.store, [contract()], now_ms=NOW)
        before = self.store.query("SELECT * FROM universe")
        with patch.object(self.store, "upsert",
                          side_effect=RuntimeError("fail")):
            with self.assertRaises(RuntimeError):
                refresh_registry(self.store, [contract("ETHUSDTM")],
                                 now_ms=NOW + MINUTE, complete=True)
        self.assertEqual(self.store.query("SELECT * FROM universe"), before)

    def test_stored_refresh_reports_source_age_and_cannot_claim_complete(self):
        self.store.upsert("ticker_snapshots", [ticker(contract(), NOW - DAY)])
        refresh_registry(self.store, now_ms=NOW)
        self.assertEqual(self.first()["coverage"]["contract_metadata"]
                         ["observed_at_ms"], NOW - DAY)
        with self.assertRaises(ValueError):
            refresh_registry(self.store, now_ms=NOW, complete=True)

    def test_missing_observed_history_does_not_assert_listing_age_bound(self):
        refresh_registry(self.store, [contract()], now_ms=NOW)
        self.assertIsNone(self.first()
                          ["observed_history_is_listing_age_lower_bound"])

    def test_stored_unavailable_fails_without_resetting_registry(self):
        refresh_registry(self.store, [contract()], now_ms=NOW)
        with self.assertRaises(ValueError):
            refresh_registry(self.store, now_ms=NOW)
        self.assertEqual(self.report()["stats"]["active"], 1)


if __name__ == "__main__":
    unittest.main()
