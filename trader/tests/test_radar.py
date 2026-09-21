from trader.radar.rates import expected_grids_per_hour
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from trader.radar.cli import main
from trader.radar import jev as jev_module
from trader.radar.jev import enrich, validate_answers
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
        self.assertGreater(rows["UPUSDTM"]["profit_pct_min"], 1)
        self.assertGreater(rows["XBTUSDTM"]["profit_pct_min"], 1)
        self.assertAlmostEqual(rows["UPUSDTM"]["expected_grids_per_hour"],
                               round(expected_grids_per_hour(rows["UPUSDTM"]["atr_1h_pct"], rows["UPUSDTM"]["step_pct"], rows["UPUSDTM"]["turnover_24h_usdt"]), 2))
        self.assertAlmostEqual(rows["XBTUSDTM"]["expected_grids_per_hour"],
                               round(expected_grids_per_hour(rows["XBTUSDTM"]["atr_1h_pct"], rows["XBTUSDTM"]["step_pct"], rows["XBTUSDTM"]["turnover_24h_usdt"]), 2))
        self.assertLess(rows["UPUSDTM"]["range_low"], rows["UPUSDTM"]["price"])
        self.assertGreaterEqual(rows["UPUSDTM"]["range_high"], rows["UPUSDTM"]["high_7d"])
        self.assertLessEqual(rows["UPUSDTM"]["grids"], 200)

    def test_sections_are_bounded_and_ranked_by_score(self):
        report = analyse(self.database, NOW)
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual([row["symbol"] for row in report["sections"]["majors"]], ["XBTUSDTM"])
        self.assertEqual(report["sections"]["turning_up"], [])
        turning = next(r for r in report["rows"] if r["symbol"] == "TURNUSDTM")
        self.assertEqual(turning["range_verified"], 0)  # Trend alone is not support.
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

    def test_jev_shadow_enriches_top_qualifying_rows_without_changing_rank(self):
        report = analyse(self.database, NOW)
        before = [row["symbol"] for row in report["rows"]]
        calls = []
        def evaluator(state, questions):
            calls.append((state, questions))
            return {"model": "jev-test", "answers": {
                "direction": {"choice": "LONG", "probabilities": {
                    "LONG": .7, "SHORT": .1, "NEUTRAL": .15, "REJECT": .05}, "confidence": .72},
                "range_quality": {"score": 2.4, "probabilities": {
                    "0": .05, "1": .1, "2": .35, "3": .5}, "confidence": .61},
                "entry_now": {"noul": .64}, "evidence_sufficient": {"noul": .81}},
                "usage": {"input_tokens": 123}}
        enriched = enrich(report, evaluator=evaluator, limit=2)
        self.assertEqual([row["symbol"] for row in enriched["rows"]], before)
        selected = [row for row in enriched["rows"] if "jev" in row]
        self.assertEqual(len(selected), 2)
        self.assertEqual(selected[0]["jev"]["direction"], "LONG")
        self.assertEqual(set(calls[0][1]), {"direction", "range_quality", "entry_now", "evidence_sufficient"})
        self.assertEqual(enriched["jev_shadow"]["failed"], 0)

    def test_jev_shadow_failure_keeps_deterministic_rows(self):
        report = analyse(self.database, NOW)
        expected = json.loads(json.dumps(report["rows"]))
        enriched = enrich(report, evaluator=lambda *_: (_ for _ in ()).throw(RuntimeError("private")), limit=1)
        self.assertEqual(enriched["rows"], expected)
        self.assertEqual(enriched["jev_shadow"]["status"], "degraded")

    def _jev_reply(self, **overrides):
        reply = {"model": "jev-test", "answers": {
            "direction": {"choice": "LONG", "probabilities": {
                "LONG": .7, "SHORT": .1, "NEUTRAL": .15, "REJECT": .05}, "confidence": .72},
            "range_quality": {"score": 2.4, "confidence": .61},
            "entry_now": {"noul": .64}, "evidence_sufficient": {"noul": .81}},
            "usage": {"input_tokens": 123}}
        reply["answers"].update(overrides)
        return reply

    def test_jev_model_is_pinned_to_a_version(self):
        self.assertEqual(jev_module.MODEL, "jev-1.13.0")

    def test_validate_answers_rejects_choice_that_is_not_argmax(self):
        reply = self._jev_reply(direction={"choice": "SHORT", "probabilities": {
            "LONG": .7, "SHORT": .1, "NEUTRAL": .15, "REJECT": .05}, "confidence": .72})
        with self.assertRaisesRegex(ValueError, "argmax"):
            validate_answers(reply)

    def test_validate_answers_rejects_probabilities_off_criteria_or_not_normalised(self):
        extra = self._jev_reply(direction={"choice": "LONG", "probabilities": {
            "LONG": .7, "SHORT": .1, "NEUTRAL": .15, "REJECT": .05, "FLAT": 0}, "confidence": .7})
        with self.assertRaisesRegex(ValueError, "criteria"):
            validate_answers(extra)
        skewed = self._jev_reply(direction={"choice": "LONG", "probabilities": {
            "LONG": .7, "SHORT": .3, "NEUTRAL": .15, "REJECT": .05}, "confidence": .7})
        with self.assertRaisesRegex(ValueError, "sum"):
            validate_answers(skewed)

    def test_validate_answers_rejects_missing_ids_and_out_of_range_values(self):
        reply = self._jev_reply()
        del reply["answers"]["entry_now"]
        with self.assertRaisesRegex(ValueError, "question"):
            validate_answers(reply)
        with self.assertRaisesRegex(ValueError, "range_quality"):
            validate_answers(self._jev_reply(range_quality={"score": 3.5, "confidence": .5}))
        with self.assertRaisesRegex(ValueError, "entry_now"):
            validate_answers(self._jev_reply(entry_now={"noul": float("nan")}))
        with self.assertRaisesRegex(ValueError, "evidence_sufficient"):
            validate_answers(self._jev_reply(evidence_sufficient={"noul": 1.2}))

    def test_validate_answers_labels_range_quality_from_legend(self):
        answer = validate_answers(self._jev_reply())
        self.assertEqual(answer["range_quality"], 2.4)
        self.assertEqual(answer["range_quality_label"], "good")
        self.assertEqual(jev_module.RANGE_QUALITY_LEGEND, ("poor", "marginal", "good", "strong"))

    def test_jev_shadow_derives_advisory_abstention_fields(self):
        report = analyse(self.database, NOW)
        enriched = enrich(report, evaluator=lambda *_: self._jev_reply(), limit=1, log=lambda _e: None)
        jev = next(row["jev"] for row in enriched["rows"] if "jev" in row)
        self.assertEqual(jev["status"], "ok")
        self.assertFalse(jev["abstain"])
        self.assertAlmostEqual(jev["margin"], .55)
        self.assertAlmostEqual(jev["noul_confidence"]["entry_now"], .28)
        self.assertAlmostEqual(jev["noul_confidence"]["evidence_sufficient"], .62)

    def test_jev_shadow_abstains_on_thin_margin_low_evidence_or_reject(self):
        thin = self._jev_reply(direction={"choice": "LONG", "probabilities": {
            "LONG": .4, "SHORT": .3, "NEUTRAL": .2, "REJECT": .1}, "confidence": .4})
        weak = self._jev_reply(evidence_sufficient={"noul": .3})
        reject = self._jev_reply(direction={"choice": "REJECT", "probabilities": {
            "LONG": .05, "SHORT": .05, "NEUTRAL": .1, "REJECT": .8}, "confidence": .8})
        for reply in (thin, weak, reject):
            report = analyse(self.database, NOW)
            enriched = enrich(report, evaluator=lambda *_, reply=reply: reply, limit=1,
                              log=lambda _e: None)
            jev = next(row["jev"] for row in enriched["rows"] if "jev" in row)
            self.assertTrue(jev["abstain"], reply["answers"]["direction"])

    def test_jev_shadow_invalid_payload_marks_row_invalid_and_keeps_rank(self):
        report = analyse(self.database, NOW)
        before = json.loads(json.dumps(report["rows"]))
        bad = self._jev_reply(direction={"choice": "SHORT", "probabilities": {
            "LONG": .7, "SHORT": .1, "NEUTRAL": .15, "REJECT": .05}, "confidence": .72})
        events = []
        enriched = enrich(report, evaluator=lambda *_: bad, limit=1, log=events.append)
        self.assertEqual([row["symbol"] for row in enriched["rows"]],
                         [row["symbol"] for row in before])
        marked = [row for row in enriched["rows"] if "jev" in row]
        self.assertEqual(len(marked), 1)
        self.assertEqual(marked[0]["jev"], {"status": "invalid"})
        stripped = [{k: v for k, v in row.items() if k != "jev"} for row in enriched["rows"]]
        self.assertEqual(stripped, before)
        self.assertEqual(enriched["jev_shadow"]["invalid"], 1)
        self.assertEqual(enriched["jev_shadow"]["status"], "degraded")
        self.assertEqual(events[0]["fallback_reason"], "invalid_payload")
        json.dumps(enriched, allow_nan=False)

    def test_jev_shadow_log_line_is_structured_and_never_contains_the_key(self):
        secret = "sk-typesafe-SECRET-0123456789"
        report = analyse(self.database, NOW)
        events = []
        enrich(report, evaluator=lambda *_: self._jev_reply(), limit=1, log=events.append)
        failing = analyse(self.database, NOW)
        enrich(failing, evaluator=lambda *_: (_ for _ in ()).throw(RuntimeError(secret)),
               limit=1, log=events.append)
        self.assertEqual(len(events), 2)
        ok, failed = events
        self.assertEqual(ok["purpose"], "radar_shadow_judgment")
        self.assertEqual(ok["model"], jev_module.MODEL)
        self.assertEqual(ok["downstream_action"], "shadow_only")
        self.assertEqual(ok["input_tokens"], 123)
        self.assertIn("latency_ms", ok)
        self.assertEqual(ok["answer_distribution"]["direction"]["LONG"], .7)
        self.assertEqual(ok["confidence"]["direction"], .72)
        self.assertIsNone(ok["fallback_reason"])
        self.assertEqual(failed["fallback_reason"], "RuntimeError")
        self.assertEqual(failed["downstream_action"], "shadow_only")
        for event in events:
            self.assertNotIn(secret, json.dumps(event))
            self.assertNotIn("Authorization", json.dumps(event))

    def test_jev_shadow_default_log_writes_json_line_without_key(self):
        import io
        from contextlib import redirect_stderr
        report = analyse(self.database, NOW)
        buffer = io.StringIO()
        with redirect_stderr(buffer):
            enrich(report, evaluator=lambda *_: self._jev_reply(), limit=1)
        line = json.loads(buffer.getvalue().strip().splitlines()[-1])
        self.assertEqual(line["downstream_action"], "shadow_only")
        self.assertNotIn("Bearer", buffer.getvalue())

    def test_cli_jev_shadow_requires_explicit_key(self):
        destination = Path(self.temp.name) / "radar.json"
        with self.assertRaisesRegex(ValueError, "TYPESAFE_API_KEY"):
            main(["--database", str(self.database), "--json", str(destination),
                  "--asof-ms", str(NOW), "--no-liquidation-clusters", "--jev-shadow"],
                 printer=lambda _line: None, environ={})

    def test_oldest_required_snapshot_age_blocks_liquidity_and_is_printed(self):
        connection = sqlite3.connect(self.database)
        connection.execute("UPDATE top_of_book SET time_ms=? WHERE symbol='UPUSDTM'",
                           (NOW - 121 * 60_000,))
        connection.commit()
        connection.close()
        report = analyse(self.database, NOW)
        row = next(row for row in report["rows"] if row["symbol"] == "UPUSDTM")
        self.assertEqual(row["snapshot_age_min"], 121)
        self.assertFalse(row["passes_liquidity"])
        destination = Path(self.temp.name) / "radar.json"
        lines = []
        main(["--database", str(self.database), "--json", str(destination),
              "--asof-ms", str(NOW)], printer=lines.append)
        self.assertIn("age min", "\n".join(lines))
        self.assertEqual(next(item for item in json.loads(destination.read_text())["rows"]
                              if item["symbol"] == "UPUSDTM")["snapshot_age_min"], 121)
        connection = sqlite3.connect(self.database)
        connection.execute("UPDATE top_of_book SET time_ms=? WHERE symbol='UPUSDTM'",
                           (NOW - 120 * 60_000,))
        connection.commit()
        connection.close()
        row = next(row for row in analyse(self.database, NOW)["rows"]
                   if row["symbol"] == "UPUSDTM")
        self.assertTrue(row["passes_liquidity"])


if __name__ == "__main__":
    unittest.main()
