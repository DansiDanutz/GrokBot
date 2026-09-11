import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from trader.radar.cli import main
from trader.radar.radar import analyse


HOUR = 3_600_000
DAY = 24 * HOUR
NOW = 100 * DAY


def fixture(path):
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE klines(symbol TEXT, interval TEXT, time_ms INTEGER,
            open REAL, high REAL, low REAL, close REAL, volume REAL, turnover REAL);
        CREATE TABLE ticker_snapshots(symbol TEXT, time_ms INTEGER,
            observed_at_ms INTEGER, last REAL, turnover_24h REAL, funding_rate REAL);
        CREATE TABLE top_of_book(symbol TEXT, time_ms INTEGER,
            observed_at_ms INTEGER, bid REAL, ask REAL, bid_size REAL, ask_size REAL);
        CREATE TABLE universe(symbol TEXT, listed_at_ms INTEGER,
            first_candle_ms INTEGER, active INTEGER);
    """)
    specs = {
        "XBTUSDTM": (100.0, 60_000_000, 0.0004),
        "UPUSDTM": (20.0, 10_000_000, 0.0008),
        "TURNUSDTM": (20.0, 10_000_000, 0.0008),
        "NEUTRALUSDTM": (20.0, 10_000_000, 0.0008),
        "ILLIQUIDUSDTM": (20.0, 2_999_999, 0.0008),
    }
    for symbol, (base, turnover, spread) in specs.items():
        candles = []
        for index in range(60 * 24):
            if symbol == "TURNUSDTM":
                close = base - index * 0.01 if index < 56 * 24 else base - 56 * 24 * 0.01 + (index - 56 * 24) * 0.08
            elif symbol == "NEUTRALUSDTM":
                close = base
            else:
                close = base + index * 0.02
            candles.append((symbol, "1h", NOW - (60 * 24 - index) * HOUR,
                            close, close + 0.3, close - 0.3, close, 1000, 1000 * close))
        connection.executemany("INSERT INTO klines VALUES (?,?,?,?,?,?,?,?,?)", candles)
        price = candles[-1][6]
        connection.execute("INSERT INTO ticker_snapshots VALUES (?,?,?,?,?,?)",
                           (symbol, NOW, NOW, price, turnover, 0.0001))
        connection.execute("INSERT INTO top_of_book VALUES (?,?,?,?,?,?,?)",
                           (symbol, NOW, NOW, price * (1-spread/2), price * (1+spread/2), 10, 10))
        connection.execute("INSERT INTO universe VALUES (?,?,?,?)",
                           (symbol, NOW - 60 * DAY, NOW - 60 * DAY, 1))
    connection.commit()
    connection.close()


class RadarTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "market.sqlite3"
        fixture(self.database)

    def tearDown(self):
        self.temp.cleanup()

    def test_prototype_direction_ranges_constants_and_liquidity_gate(self):
        report = analyse(self.database, NOW)
        rows = {row["symbol"]: row for row in report["rows"]}
        self.assertEqual(rows["UPUSDTM"]["direction"], "LONG")
        self.assertEqual(rows["TURNUSDTM"]["direction"], "TURNING-UP")
        self.assertEqual(rows["NEUTRALUSDTM"]["direction"], "NEUTRAL")
        self.assertFalse(rows["ILLIQUIDUSDTM"]["passes_liquidity"])
        self.assertEqual(rows["UPUSDTM"]["step_pct"], 0.8)
        self.assertEqual(rows["XBTUSDTM"]["step_pct"], 0.52)
        self.assertAlmostEqual(rows["UPUSDTM"]["expected_grids_per_hour"],
                               round(1.9 * rows["UPUSDTM"]["atr_1h_pct"] / 0.8, 2))
        self.assertAlmostEqual(rows["XBTUSDTM"]["expected_grids_per_hour"],
                               round(0.45 * rows["XBTUSDTM"]["atr_1h_pct"] / 0.52, 2))
        self.assertLess(rows["UPUSDTM"]["range_low"], rows["UPUSDTM"]["price"])
        self.assertGreaterEqual(rows["UPUSDTM"]["range_high"], rows["UPUSDTM"]["high_7d"])
        self.assertLessEqual(rows["UPUSDTM"]["grids"], 200)

    def test_sections_are_bounded_and_ranked_by_score(self):
        report = analyse(self.database, NOW)
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual([row["symbol"] for row in report["sections"]["majors"]], ["XBTUSDTM"])
        self.assertEqual([row["symbol"] for row in report["sections"]["turning_up"]], ["TURNUSDTM"])
        for rows in report["sections"].values():
            self.assertLessEqual(len(rows), 8)
        for key in ("turning_up", "turning_down", "long", "short", "neutral"):
            scores = [row["rank_score"] for row in report["sections"][key]]
            self.assertEqual(scores, sorted(scores, reverse=True))

    def test_cli_writes_json_and_prints_tables(self):
        destination = Path(self.temp.name) / "radar.json"
        lines = []
        status = main(["--database", str(self.database), "--json", str(destination),
                       "--asof-ms", str(NOW)], printer=lines.append)
        self.assertEqual(status, 0)
        payload = json.loads(destination.read_text())
        self.assertEqual(payload["asof_ms"], NOW)
        self.assertTrue(any("TURNING UP" in line for line in lines))
        self.assertTrue(any("est.grids/h" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
