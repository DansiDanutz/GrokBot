"""Offline tests for trader.review.daily — synthetic fixtures only.

Builds tiny fake sqlite klines + in-memory state dicts; never touches the
live runtime files.
"""
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import timezone

from trader.review.daily import (
    DAY_MS,
    analyze_exit,
    bot_net,
    build_report,
    classify_exit,
    day_events,
    day_window,
    render_markdown,
    trend_at,
    trend_bucket,
)

UTC = timezone.utc
HOUR = 3_600_000
MINUTE = 60_000


def make_bot(**overrides):
    engine = {
        "bot_id": 1,
        "symbol": "TESTUSDTM",
        "direction": "LONG",
        "opening_price": 100.0,
        "last_price": 100.0,
        "range_low": 90.0,
        "range_high": 110.0,
        "grid_interval": 1.0,
        "step_pct": 1.0,
        "opened_ms": 1_789_200_000_000,
        "closed_ms": 1_789_236_000_000,
        "reason": "RANGE_BREAK",
        "realized_pnl": -5.0,
        "unrealized_pnl": 0.0,
        "fees_paid": 0.5,
        "funding_paid": 0.0,
        "grid_profit": 0.0,
        "completed_grids": 0,
        "notional_usdt": 1000.0,
        "contract_multiplier": 1.0,
        "position_contracts": 0.0,
    }
    engine.update(overrides)
    return {"engine": engine, "pnl_curve": []}


def seed_klines(conn, symbol, interval, rows):
    conn.executemany(
        "INSERT INTO klines (symbol, interval, time_ms, open, high, low, close, volume, turnover) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 1.0, 1.0)",
        [(symbol, interval, t, o, h, l, c) for t, o, h, l, c in rows],
    )
    conn.commit()


def path_after(start_ms, n_minutes, price_fn):
    """n 1m bars: (time, open, high, low, close)."""
    rows = []
    for i in range(n_minutes):
        price = price_fn(i)
        rows.append((start_ms + i * MINUTE, price, price, price, price))
    return rows


class TempDbCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "market.sqlite3")
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "CREATE TABLE klines (symbol TEXT NOT NULL, interval TEXT NOT NULL, "
            "time_ms INTEGER NOT NULL, open REAL, high REAL, low REAL, close REAL, "
            "volume REAL, turnover REAL, PRIMARY KEY(symbol, interval, time_ms))"
        )
        self.conn = conn

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()


class DayWindowTests(unittest.TestCase):
    def test_utc_day_window_is_midnight_to_midnight(self):
        start, end = day_window("2026-09-12", tz=UTC)
        self.assertEqual(end - start, 86_400_000)
        self.assertEqual(start % 86_400_000, 0)
        import datetime as dt
        self.assertEqual(dt.datetime.fromtimestamp(start / 1000, UTC).hour, 0)

    def test_day_events_filters_across_midnight_and_rotated_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = 1_789_200_000_000  # inside the UTC day used below
            day_start, _ = day_window("2026-09-12", tz=UTC)
            with open(os.path.join(tmp, "events-2026-09-11.jsonl"), "w") as fh:
                fh.write(json.dumps({"ts_ms": day_start - MINUTE, "type": "OPEN"}) + "\n")  # prev day
                fh.write(json.dumps({"ts_ms": day_start + 5 * MINUTE, "type": "ERROR", "code": 1}) + "\n")
            with open(os.path.join(tmp, "events.jsonl"), "w") as fh:
                fh.write(json.dumps({"ts_ms": day_start + HOUR, "type": "OPEN"}) + "\n")
                fh.write(json.dumps({"ts_ms": day_start + 86_400_000, "type": "CLOSE"}) + "\n")  # next day
                fh.write("not-json\n")
            events = day_events(tmp, day_start, day_start + 86_400_000)
            self.assertEqual([e["type"] for e in events], ["ERROR", "OPEN"])
            self.assertEqual(events[0]["code"], 1)


class ExitClassificationTests(TempDbCase):
    def test_long_dip_then_rally_is_premature(self):
        bot = make_bot(realized_pnl=-5.0, fees_paid=0.0)
        # 6h path: dips to 95, then rallies to 112 (entry 100, exit 100, gi 1.0)
        seed_klines(self.conn, "TESTUSDTM", "1m",
                    path_after(1_789_236_000_000, 360, lambda i: 95.0 if i < 60 else 112.0))
        result = analyze_exit(bot, self.conn)
        self.assertEqual(result["best_price"], 112.0)
        self.assertEqual(result["price_6h"], 112.0)
        self.assertEqual(result["classification"], "PREMATURE")
        # base = notional/exit = 10 units -> (112-100)*10 = 120
        self.assertAlmostEqual(result["would_have_been_pnl"], 120.0)

    def test_long_keeps_falling_is_good(self):
        bot = make_bot(realized_pnl=-5.0, fees_paid=0.0)
        seed_klines(self.conn, "TESTUSDTM", "1m",
                    path_after(1_789_236_000_000, 360, lambda i: 100.0 - i * 0.01))
        result = analyze_exit(bot, self.conn)
        self.assertEqual(result["classification"], "GOOD")
        self.assertAlmostEqual(result["worst_price"], 96.41, places=2)

    def test_short_rally_after_stop_is_good(self):
        bot = make_bot(direction="SHORT", realized_pnl=-4.0, fees_paid=0.0)
        seed_klines(self.conn, "TESTUSDTM", "1m",
                    path_after(1_789_236_000_000, 360, lambda i: 100.0 + i * 0.01))
        result = analyze_exit(bot, self.conn)
        self.assertEqual(result["best_price"], 100.0)  # min for SHORT (rally after close)
        self.assertAlmostEqual(result["worst_price"], 103.59, places=2)
        self.assertEqual(result["classification"], "GOOD")

    def test_short_dip_beyond_entry_is_premature(self):
        bot = make_bot(direction="SHORT", realized_pnl=-3.0, fees_paid=0.0)
        # falls 2 grids below entry after close -> recovery for a short
        seed_klines(self.conn, "TESTUSDTM", "1m",
                    path_after(1_789_236_000_000, 360, lambda i: 98.0 if i > 30 else 100.0))
        result = analyze_exit(bot, self.conn)
        self.assertEqual(result["classification"], "PREMATURE")
        self.assertAlmostEqual(result["would_have_been_pnl"], 20.0)  # (100-98)*10

    def test_neutral_direction_skips_with_note(self):
        bot = make_bot(direction="NEUTRAL")
        result = analyze_exit(bot, self.conn)
        self.assertEqual(result["classification"], "NEUTRAL")
        self.assertIn("counterfactual skipped", result["note"])
        self.assertIsNone(result["would_have_been_pnl"])

    def test_no_klines_skips_with_note(self):
        bot = make_bot()
        result = analyze_exit(bot, self.conn)
        self.assertIn("no 1m klines", result["note"])

    def test_open_event_price_fills_missing_entry(self):
        bot = make_bot(opening_price=None, realized_pnl=-9.0, fees_paid=0.0,
                       reason="PROFILE_UPDATE")
        # entry from OPEN event 100; best 112 >= entry + 1 grid -> PREMATURE
        seed_klines(self.conn, "TESTUSDTM", "1m",
                    path_after(1_789_236_000_000, 360, lambda i: 112.0))
        result = analyze_exit(bot, self.conn, open_price=100.0)
        self.assertEqual(result["entry"], 100.0)
        self.assertEqual(result["classification"], "PREMATURE")
        self.assertIn("OPEN event", result["note"])


class LiquidationAwareExitTests(TempDbCase):
    def _premature_long(self, **overrides):
        return make_bot(realized_pnl=-5.0, fees_paid=0.0, reason="PROFILE_UPDATE",
                        **overrides)

    def test_band_touched_reclassifies_to_risky_hold(self):
        # stored band: liquidation at 80 for this LONG; path dips to 79
        bot = self._premature_long(liquidation={"status": "ESTIMATED", "price": 80.0})
        seed_klines(self.conn, "TESTUSDTM", "1m",
                    path_after(1_789_236_000_000, 360,
                               lambda i: 79.0 if 60 <= i < 120 else 112.0))
        result = analyze_exit(bot, self.conn)
        self.assertEqual(result["classification"], "RISKY_HOLD")
        self.assertIsNotNone(result["liq_touch_ms"])
        self.assertIn("liq band touched", result["note"])
        self.assertEqual(result["liq_band"]["source"], "state")

    def test_band_not_touched_stays_premature(self):
        # dips to 85 (above the 80 band) then rallies -> still PREMATURE
        bot = self._premature_long(liquidation={"status": "ESTIMATED", "price": 80.0})
        seed_klines(self.conn, "TESTUSDTM", "1m",
                    path_after(1_789_236_000_000, 360,
                               lambda i: 85.0 if 60 <= i < 120 else 112.0))
        result = analyze_exit(bot, self.conn)
        self.assertEqual(result["classification"], "PREMATURE")
        self.assertIsNone(result["liq_touch_ms"])

    def test_short_mirror_band_above_touched(self):
        bot = make_bot(direction="SHORT", realized_pnl=-4.0, fees_paid=0.0,
                       reason="PROFILE_UPDATE",
                       liquidation={"status": "ESTIMATED", "price": 120.0})
        # SHORT exit 100; dips to 95 (recovery, would be PREMATURE) then
        # rallies through the 120 band -> RISKY_HOLD
        seed_klines(self.conn, "TESTUSDTM", "1m",
                    path_after(1_789_236_000_000, 360,
                               lambda i: 95.0 if i < 60 else 121.0))
        result = analyze_exit(bot, self.conn)
        self.assertEqual(result["classification"], "RISKY_HOLD")
        self.assertIsNotNone(result["liq_touch_ms"])

    def test_liq_absent_keeps_old_behavior_with_note(self):
        bot = self._premature_long()  # no liquidation fields at all
        seed_klines(self.conn, "TESTUSDTM", "1m",
                    path_after(1_789_236_000_000, 360, lambda i: 112.0))
        result = analyze_exit(bot, self.conn)
        self.assertEqual(result["classification"], "PREMATURE")
        self.assertIsNone(result["liq_band"])
        self.assertIn("liq unavailable", result["note"])

    def test_reconstructed_band_from_risk_metadata(self):
        # no stored liquidation dict, but maintain_margin/risk_limit present:
        # the estimator rebuilds the close-time band (10 units @ entry 100,
        # 1000 notional, 5x leverage -> liq well below 95)
        bot = make_bot(realized_pnl=-5.0, fees_paid=0.0, reason="PROFILE_UPDATE",
                       maintain_margin=0.007, risk_limit=25000,
                       risk_metadata_at_ms=1_789_236_000_000 - 600_000)
        seed_klines(self.conn, "TESTUSDTM", "1m",
                    path_after(1_789_236_000_000, 360, lambda i: 112.0))
        result = analyze_exit(bot, self.conn)
        self.assertEqual(result["classification"], "PREMATURE")
        self.assertIsNotNone(result["liq_band"])
        self.assertEqual(result["liq_band"]["source"], "estimate")
        self.assertEqual(result["liq_band"]["status"], "ESTIMATED")

    def test_same_symbol_donor_metadata_rescues_missing_band(self):
        from trader.review.daily import risk_metadata_donors
        # bot 3-style: closed before its first fill, no risk metadata of its
        # own; another same-symbol bot donates per-symbol contract specs.
        # The donor's metadata timestamp must be at or before the close.
        bot = make_bot(realized_pnl=-5.0, fees_paid=0.0, reason="PROFILE_UPDATE")
        donor = make_bot(bot_id=7, maintain_margin=0.025, risk_limit=10000,
                         risk_metadata_at_ms=1_789_236_000_000 - 600_000)
        late_donor = make_bot(bot_id=8, maintain_margin=0.05, risk_limit=10000,
                              risk_metadata_at_ms=1_789_236_000_000 + 3_600_000)
        donors = risk_metadata_donors({"open_bots": [],
                                       "closed_bots": [bot, donor, late_donor]})
        self.assertEqual(len(donors["TESTUSDTM"]), 2)  # late entry excluded by picker
        seed_klines(self.conn, "TESTUSDTM", "1m",
                    path_after(1_789_236_000_000, 360, lambda i: 112.0))
        result = analyze_exit(bot, self.conn, donors=donors)
        self.assertEqual(result["classification"], "PREMATURE")
        self.assertIsNotNone(result["liq_band"])
        self.assertNotIn("liq unavailable", result["note"])

    def test_risky_hold_excluded_from_missed_sums(self):
        from trader.review.daily import build_proposals
        start, end = day_window("2026-09-12", tz=UTC)
        risky = make_bot(bot_id=1, opened_ms=start + HOUR, closed_ms=start + 2 * HOUR,
                         realized_pnl=-5.0, fees_paid=0.0, reason="PROFILE_UPDATE",
                         liquidation={"status": "ESTIMATED", "price": 80.0})
        premature = make_bot(bot_id=2, symbol="TEST2USDTM", opened_ms=start + 3 * HOUR,
                             closed_ms=start + 4 * HOUR,
                             realized_pnl=-2.0, fees_paid=0.0, reason="PROFILE_UPDATE")
        bots = [risky, premature]
        for bot in bots:
            seed_klines(self.conn, bot["engine"]["symbol"], "1m",
                        path_after(bot["engine"]["closed_ms"], 360,
                                   lambda i: 79.0 if 60 <= i < 120 else 112.0))
        data = build_report("2026-09-12", {"open_bots": [], "closed_bots": bots},
                            self.conn, [], tz=UTC)
        self.assertEqual([e["classification"] for e in data["exits"]],
                         ["RISKY_HOLD", "PREMATURE"])
        self.assertEqual(len(data["premature"]), 1)
        # missed = 120 - (-2) for the PREMATURE bot only; RISKY_HOLD excluded
        self.assertAlmostEqual(sum(data["premature_costs"]), 122.0)
        props = build_proposals(data, data["proposals"])
        self.assertEqual(len(props["premature_closes"]), 1)
        self.assertEqual(props["premature_closes"][0]["bot_id"], 2)
        md = render_markdown(data)
        self.assertIn("RISKY_HOLD: 1", md)
        self.assertIn("⚠ RISKY_HOLD", md)
        self.assertIn("not safely capturable", md)


class DataCoverageTests(TempDbCase):
    def test_half_day_coverage_flags_low_confidence(self):
        from trader.review.daily import build_proposals
        from trader.review import rules as learned_rules
        start, end = day_window("2026-09-12", tz=UTC)
        bot = make_bot(bot_id=1, opened_ms=start + HOUR, closed_ms=start + 2 * HOUR,
                       realized_pnl=-5.0, fees_paid=0.0, reason="PROFILE_UPDATE")
        # only 10h of the day present -> ~41.7% coverage; the post-close
        # 6h window (start+2h..+8h) is inside the seeded span and rallies
        # to 112 so the day produces a real PREMATURE candidate
        seed_klines(self.conn, "TESTUSDTM", "1m",
                    path_after(start, 600, lambda i: 112.0 if 120 <= i < 480 else 100.0))
        state = {"open_bots": [], "closed_bots": [bot]}
        data = build_report("2026-09-12", state, self.conn, [], tz=UTC)
        self.assertTrue(data["data_coverage"]["flagged"])
        self.assertAlmostEqual(data["data_coverage"]["per_symbol"]["TESTUSDTM"],
                               round(600 / 1440 * 100, 2))
        # the day does produce a min-hold candidate (1 premature close,
        # $120 missed) — the coverage gate must defer it
        self.assertEqual(len(data["premature"]), 1)
        props = build_proposals(data, data["proposals"])
        self.assertTrue(props["totals"]["data_coverage"]["flagged"])
        md = render_markdown(data)
        self.assertIn("LOW-CONFIDENCE DAY", md)
        planned = learned_rules.plan_changes(props, {}, "2026-09-12")
        self.assertTrue(planned)
        self.assertTrue(all(c["status"] == "defer" for c in planned))
        self.assertIn("low data coverage (<95%) for TESTUSDTM",
                      planned[0]["defer_reason"])

    def test_full_coverage_not_flagged(self):
        start, end = day_window("2026-09-12", tz=UTC)
        bot = make_bot(bot_id=1, opened_ms=start + HOUR, closed_ms=start + 2 * HOUR)
        seed_klines(self.conn, "TESTUSDTM", "1m",
                    path_after(start, 1440, lambda i: 100.0))
        data = build_report("2026-09-12", {"open_bots": [], "closed_bots": [bot]},
                            self.conn, [], tz=UTC)
        self.assertFalse(data["data_coverage"]["flagged"])
        self.assertEqual(data["data_coverage"]["per_symbol"]["TESTUSDTM"], 100.0)

    def test_classify_exit_direct_thresholds(self):
        # LONG, net negative, recovered >= 1 grid -> PREMATURE even without stop reason
        self.assertEqual(classify_exit("LONG", 100.0, 99.0, 1.0, -1.0, "LABEL_FLIP", 101.5, 98.0), "PREMATURE")
        # LONG, stop reason, adverse >= 0.5 grid -> GOOD
        self.assertEqual(classify_exit("LONG", 100.0, 100.0, 1.0, -2.0, "RANGE_BREAK", 100.1, 99.4), "GOOD")
        # LONG, stop reason, but price recovered -> NEUTRAL (not GOOD: no adverse follow-through)
        self.assertEqual(classify_exit("LONG", 100.0, 100.0, 1.0, 2.0, "RANGE_BREAK", 100.2, 99.9), "NEUTRAL")


class TrendBucketTests(TempDbCase):
    def _seed_1h(self, price_fn, start_ms):
        rows = [(start_ms + i * HOUR, price_fn(i), price_fn(i), price_fn(i), price_fn(i))
                for i in range(50)]
        seed_klines(self.conn, "TESTUSDTM", "1h", rows)

    def test_uptrend_buckets(self):
        opened_ms = 1_789_200_000_000
        self._seed_1h(lambda i: 100.0 + i, opened_ms - 49 * HOUR)
        signal, ret_pct, slope = trend_at(self.conn, "TESTUSDTM", opened_ms)
        self.assertEqual(signal, 1)
        self.assertGreater(ret_pct, 0.5)
        self.assertGreater(slope, 0)
        self.assertEqual(trend_bucket("LONG", signal), "WITH-TREND")
        self.assertEqual(trend_bucket("SHORT", signal), "AGAINST-TREND")
        self.assertEqual(trend_bucket("NEUTRAL", signal), "NEUTRAL-BOT")

    def test_flat_market_is_no_trend(self):
        opened_ms = 1_789_200_000_000
        self._seed_1h(lambda i: 100.0, opened_ms - 49 * HOUR)
        signal, ret_pct, _ = trend_at(self.conn, "TESTUSDTM", opened_ms)
        self.assertEqual(signal, 0)
        self.assertAlmostEqual(ret_pct, 0.0)
        self.assertEqual(trend_bucket("LONG", signal), "NO-TREND")

    def test_missing_symbol_returns_none(self):
        signal, ret_pct, slope = trend_at(self.conn, "NOPEUSDTM", 1_789_200_000_000)
        self.assertIsNone(signal)
        self.assertIsNone(ret_pct)
        self.assertIsNone(slope)
        self.assertEqual(trend_bucket("LONG", None), "NO-DATA")


class ReportTests(TempDbCase):
    def _state(self, bots_closed, bots_opened=None):
        return {"open_bots": bots_opened or [], "closed_bots": bots_closed}

    def test_missed_opportunity_summation(self):
        start, end = day_window("2026-09-12", tz=UTC)
        bots = [
            make_bot(bot_id=1, opened_ms=start + HOUR, closed_ms=start + 2 * HOUR,
                     realized_pnl=-5.0, fees_paid=0.0),
            make_bot(bot_id=2, symbol="TEST2USDTM", opened_ms=start + 3 * HOUR,
                     closed_ms=start + 4 * HOUR,
                     realized_pnl=-2.0, fees_paid=0.0, reason="LABEL_FLIP"),
        ]
        for bot in bots:
            seed_klines(self.conn, bot["engine"]["symbol"], "1m",
                        path_after(bot["engine"]["closed_ms"], 360, lambda i: 112.0))
        events = [{"ts_ms": start + HOUR, "type": "ERROR", "code": 1}]
        data = build_report("2026-09-12", self._state(bots), self.conn, events, tz=UTC)
        self.assertEqual(len(data["premature"]), 2)
        # each: whb 120 - net(-5 / -2)
        self.assertAlmostEqual(sum(data["premature_costs"]), 120 - (-5) + 120 - (-2))
        md = render_markdown(data)
        self.assertIn("## 2. Exit quality", md)
        self.assertIn("PREMATURE", md)
        self.assertIn("## 5. Learnings", md)
        self.assertIn("code 1: 1", md)

    def test_day_window_selection_ignores_out_of_range_bots(self):
        start, end = day_window("2026-09-12", tz=UTC)
        inside = make_bot(bot_id=1, opened_ms=start + HOUR, closed_ms=start + 2 * HOUR)
        before = make_bot(bot_id=2, opened_ms=start - HOUR, closed_ms=start - HOUR)
        after = make_bot(bot_id=3, opened_ms=end + HOUR, closed_ms=end + 2 * HOUR)
        data = build_report("2026-09-12", self._state([inside, before, after]), self.conn, [], tz=UTC)
        self.assertEqual([b["engine"]["bot_id"] for b in data["opened"]], [1])
        self.assertEqual([b["engine"]["bot_id"] for b in data["closed"]], [1])

    def test_empty_day_renders(self):
        data = build_report("2026-09-12", self._state([]), self.conn, [], tz=UTC)
        md = render_markdown(data)
        self.assertIn("Daily trade review — 2026-09-12", md)
        self.assertIn("No bots closed on this day", md)
        self.assertIn("ERROR events: none", md)
        self.assertIn("Insufficient data", md)

    def test_nested_and_flat_bot_shapes_both_work(self):
        flat = {
            "bot_id": 7, "symbol": "TESTUSDTM", "direction": "LONG",
            "opening_price": 100.0, "last_price": 100.0, "grid_interval": 1.0,
            "opened_ms": 1_789_200_000_000, "closed_ms": 1_789_236_000_000,
            "reason": "RANGE_BREAK", "realized_pnl": -1.0, "unrealized_pnl": 0.0,
            "fees_paid": 0.1, "funding_paid": 0.0, "net": -1.1,
            "notional_usdt": 1000.0, "completed_grids": 3,
        }
        self.assertAlmostEqual(bot_net(flat), -1.1)
        nested = make_bot(realized_pnl=-1.0, fees_paid=0.1)
        self.assertAlmostEqual(bot_net(nested), -1.1)
        seed_klines(self.conn, "TESTUSDTM", "1m",
                    path_after(flat["closed_ms"], 360, lambda i: 90.0))
        result = analyze_exit(flat, self.conn)
        self.assertEqual(result["classification"], "GOOD")

    def test_trend_table_groups_buckets(self):
        start, end = day_window("2026-09-12", tz=UTC)
        long_bot = make_bot(bot_id=1, opened_ms=start + HOUR, closed_ms=start + 2 * HOUR,
                            completed_grids=4)
        short_bot = make_bot(bot_id=2, direction="SHORT", opened_ms=start + HOUR,
                             closed_ms=start + 5 * HOUR, completed_grids=1)
        # uptrending 1h klines covering both opens
        seed_klines(self.conn, "TESTUSDTM", "1h",
                    [(start - 48 * HOUR + i * HOUR, 100 + i, 100 + i, 100 + i, 100 + i)
                     for i in range(56)])
        data = build_report("2026-09-12", self._state([long_bot, short_bot]), self.conn, [], tz=UTC)
        self.assertEqual(data["trend_rows"]["WITH-TREND"]["bots"], 1)
        self.assertEqual(data["trend_rows"]["AGAINST-TREND"]["bots"], 1)
        md = render_markdown(data)
        self.assertIn("| WITH-TREND | 1 |", md)
        self.assertIn("| AGAINST-TREND | 1 |", md)

    def test_cumulative_closed_total_comes_from_state(self):
        from trader.review.daily import build_proposals
        start, end = day_window("2026-09-12", tz=UTC)
        inside = make_bot(bot_id=1, opened_ms=start + HOUR, closed_ms=start + 2 * HOUR)
        history = [make_bot(bot_id=100 + i, opened_ms=start - 10 * DAY_MS - i * HOUR,
                            closed_ms=start - 9 * DAY_MS - i * HOUR) for i in range(24)]
        state = {"open_bots": [], "closed_bots": [inside] + history}
        data = build_report("2026-09-12", state, self.conn, [], tz=UTC)
        self.assertEqual(data["totals"]["closed_total"], 25)
        props = build_proposals(data, data["proposals"])
        self.assertEqual(props["totals"]["closed_total"], 25)


if __name__ == "__main__":
    unittest.main()
