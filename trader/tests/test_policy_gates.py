"""Offline tests for the learned-rule gates in trader.autopilot.policy."""
import json
import os
import tempfile
import unittest
from datetime import datetime
from unittest import mock

from trader.autopilot import policy
from trader.autopilot.storage import validate_event
from trader.review import rules
from trader.tests.test_autopilot_policy import row, radar

HOUR = 3_600_000
MINUTE = 60_000
NOW = 1_789_200_000_000


def local_today(now_ms=NOW):
    return datetime.fromtimestamp(now_ms / 1000).date().isoformat()


def set_rules(**kwargs):
    rules_dict = {"min_hold_hours_before_non_risk_close": None,
                  "require_trend_alignment": False, "symbol_cooldowns": {}}
    rules_dict.update(kwargs)
    return mock.patch.object(policy, "_learned_rules", return_value=rules_dict)


class TrendGateTests(unittest.TestCase):
    def test_gate_semantics_direct(self):
        labels = {"X": "SHORT"}
        on = {"require_trend_alignment": True}
        self.assertTrue(policy._trend_gate_blocks("LONG", row("X"), labels, on))
        self.assertFalse(policy._trend_gate_blocks("SHORT", row("X"), labels, on))
        self.assertFalse(policy._trend_gate_blocks("NEUTRAL", row("X"), labels, on))
        self.assertFalse(policy._trend_gate_blocks("LONG", row("X"), labels,
                                                   {"require_trend_alignment": False}))
        # stale/absent radar data never blocks
        self.assertFalse(policy._trend_gate_blocks("LONG", row("X"), {}, on))
        # turning labels count as disagreement too
        labels = {"X": "TURNING-DOWN"}
        self.assertTrue(policy._trend_gate_blocks("LONG", row("X"), labels, on))

    def test_gate_does_not_disturb_normal_opens(self):
        report = radar(long=[row("L1"), row("L2")], short=[row("S1", "SHORT")])
        with set_rules(require_trend_alignment=True):
            gated, gated_events = policy.decide(policy.new_state(0), report, {}, 0, "a")
        clean, _ = policy.decide(policy.new_state(0), report, {}, 0, "a")
        self.assertEqual({b["engine"]["symbol"] for b in gated["open_bots"]},
                         {b["engine"]["symbol"] for b in clean["open_bots"]})
        self.assertFalse(any(e["type"] == "RULE_BLOCK" for e in gated_events))


class HoldGateTests(unittest.TestCase):
    def _open_profile_update_candidate(self):
        state, _ = policy.decide(policy.new_state(0), radar(long=[row("A")]), {}, 0, "a")
        state["open_bots"][0]["engine"]["accounting_version"] = 0
        return state

    def test_blocks_profile_update_close_on_young_bot(self):
        state = self._open_profile_update_candidate()
        with set_rules(min_hold_hours_before_non_risk_close=4.0):
            result, events = policy.decide(state, radar(long=[row("A")]), {"A": 100}, HOUR, "b")
        self.assertEqual(len(result["open_bots"]), 1)  # still open
        self.assertEqual(result["closed_bots"], [])
        blocks = [e for e in events if e["type"] == "RULE_BLOCK"]
        self.assertEqual(len(blocks), 1)
        event = blocks[0]
        self.assertEqual(event["rule"], 1)
        self.assertEqual(event["min_hold_hours"], 4.0)
        for key, value in event.items():
            if key not in ("type", "symbol"):
                self.assertIsInstance(value, (int, float), f"{key} must be numeric")
        validate_event(event)  # must pass the events.jsonl schema
        # latch persisted: second cycle does not re-emit
        with set_rules(min_hold_hours_before_non_risk_close=4.0):
            again, events2 = policy.decide(result, radar(long=[row("A")]), {"A": 100},
                                           2 * HOUR, "c")
        self.assertFalse(any(e["type"] == "RULE_BLOCK" for e in events2))
        self.assertEqual(len(again["open_bots"]), 1)

    def test_allows_close_after_hold_elapsed_and_without_rule(self):
        state = self._open_profile_update_candidate()
        with set_rules(min_hold_hours_before_non_risk_close=4.0):
            result, _ = policy.decide(state, radar(long=[row("A")]), {"A": 100}, 5 * HOUR, "b")
        self.assertEqual(result["closed_bots"][0]["engine"]["reason"], "PROFILE_UPDATE")
        with set_rules():
            clean, _ = policy.decide(state, radar(long=[row("A")]), {"A": 100}, HOUR, "b")
        self.assertEqual(clean["closed_bots"][0]["engine"]["reason"], "PROFILE_UPDATE")

    def test_range_break_close_always_allowed(self):
        state, _ = policy.decide(policy.new_state(0), radar(long=[row("A")]), {}, 0, "a")
        state["open_bots"][0]["signals"] = ["RANGE_BREAK"]
        with set_rules(min_hold_hours_before_non_risk_close=4.0):
            result, _ = policy.decide(state, radar(long=[row("A")]), {}, HOUR, "b")
        self.assertEqual(result["closed_bots"][0]["engine"]["reason"], "RANGE_BREAK")

    def test_advance_range_break_not_blocked(self):
        state, _ = policy.decide(policy.new_state(0), radar(long=[row("A")]), {}, 0, "a")
        update = {"ts_ms": 10 * MINUTE, "open": 100, "high": 140, "low": 98, "close": 135}
        with set_rules(min_hold_hours_before_non_risk_close=4.0):
            result, events = policy.advance(state, {"A": update})
        self.assertEqual(result["closed_bots"][0]["engine"]["reason"], "RANGE_BREAK")
        self.assertFalse(any(e["type"] == "RULE_BLOCK" for e in events))


class CooldownGateTests(unittest.TestCase):
    def test_cooldown_blocks_open_until_date_passes(self):
        from datetime import date, timedelta
        report = radar(long=[row("A"), row("B")])
        until = (date.fromisoformat(local_today()) + timedelta(days=7)).isoformat()
        with set_rules(symbol_cooldowns={"A": until}):
            result, _ = policy.decide(policy.new_state(NOW), report, {}, NOW, "a")
        symbols = {b["engine"]["symbol"] for b in result["open_bots"]}
        self.assertNotIn("A", symbols)
        self.assertIn("B", symbols)

    def test_expired_cooldown_allows_open(self):
        from datetime import date, timedelta
        expired = (date.fromisoformat(local_today()) - timedelta(days=1)).isoformat()
        with set_rules(symbol_cooldowns={"A": expired}):
            result, _ = policy.decide(policy.new_state(NOW),
                                      radar(long=[row("A")]), {}, NOW, "a")
        self.assertIn("A", {b["engine"]["symbol"] for b in result["open_bots"]})


class SnapshotReviewStatusTests(unittest.TestCase):
    STATUS = {"last_run_at_ms": 1_789_160_400_000,
              "doctrine_version": "2026-09-12T07:00:04",
              "proposals": {"planned": 1, "applied": 1, "deferred": 0, "rejected": 0},
              "active_rules": {"min_hold_hours_before_non_risk_close": 4.0,
                               "require_trend_alignment": False,
                               "symbol_cooldowns": []},
              "evidence_headline": "min_hold=4h from premature close(s) ($48.05 missed)"}

    def _snapshot_with_status_file(self, content):
        with tempfile.TemporaryDirectory() as tmp:
            status_path = os.path.join(tmp, "review-status.json")
            if content is not None:
                with open(status_path, "w", encoding="utf-8") as handle:
                    handle.write(content)
            with mock.patch.object(rules, "DEFAULT_STATUS_PATH", status_path):
                rules.clear_cache()
                return policy.snapshot(policy.new_state(0), 0, {})

    def test_snapshot_includes_valid_status(self):
        view = self._snapshot_with_status_file(json.dumps(self.STATUS))
        self.assertEqual(view["review_status"]["evidence_headline"],
                         self.STATUS["evidence_headline"])
        self.assertEqual(view["review_status"]["proposals"]["applied"], 1)

    def test_snapshot_omits_missing_or_invalid_status(self):
        self.assertNotIn("review_status", self._snapshot_with_status_file(None))
        self.assertNotIn("review_status", self._snapshot_with_status_file("{broken"))
        self.assertNotIn("review_status",
                         self._snapshot_with_status_file(json.dumps({"last_run_at_ms": "x"})))


class FailClosedTests(unittest.TestCase):
    def test_invalid_store_file_means_no_behavior_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            store_path = os.path.join(tmp, "learned-rules.json")
            with open(store_path, "w", encoding="utf-8") as handle:
                handle.write('{"version": 99}')
            with mock.patch.object(rules, "DEFAULT_STORE_PATH", store_path):
                rules.clear_cache()
                loaded = policy._learned_rules()
            self.assertEqual(loaded, rules.EMPTY_RULES)
            # decide behaves exactly like no rules
            report = radar(long=[row("A")])
            with set_rules():
                pass
            state, _ = policy.decide(policy.new_state(0), report, {}, 0, "a")
            self.assertIn("A", {b["engine"]["symbol"] for b in state["open_bots"]})


if __name__ == "__main__":
    unittest.main()
