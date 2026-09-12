"""Offline tests for the learned-rules store, planner, applier, and doctrine."""
import json
import os
import tempfile
import unittest
from datetime import date, timedelta
from unittest import mock

from trader.review import rules
from trader.review import apply as apply_module


def valid_store(**rule_overrides):
    store = rules.empty_store(now_ms=1_789_000_000_000)
    store["rules"].update(rule_overrides)
    return store


def proposals_fixture(**overrides):
    props = {
        "date": "2026-09-11",
        "summary": {"opened": 5, "closed": 4, "net": -10.0, "premature": 1,
                    "missed_usd": 30.0},
        "premature_closes": [{"bot_id": 3, "symbol": "NESUSDTM", "direction": "LONG",
                              "close_reason": "PROFILE_UPDATE", "net": -9.04,
                              "missed_usd": 48.05, "would_have_been_best_usd": 39.01}],
        "trend_buckets": {"with_trend": {"n": 2, "net": 100.0, "grids": 50},
                          "against_trend": {"n": 2, "net": -60.0, "grids": 10},
                          "neutral": {"n": 1, "net": 5.0, "grids": 20}},
        "against_trend_symbols": {"NEARUSDTM": {"n": 2, "net": -106.28}},
        "close_reasons": {"PROFILE_UPDATE": {"n": 2, "total_net": -18.25,
                                             "avg_hold_s": 3700.5}},
        "errors": {"3": 1},
        "proposals": [
            {"rule": "min_hold_hours_before_non_risk_close", "tier": 1,
             "evidence": "premature close cost 48.05"},
            {"rule": "avoid_impatience_closes", "tier": 2,
             "evidence": "6 impatience closes"},
        ],
    }
    props.update(overrides)
    return props


class TempPathCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store_path = os.path.join(self.tmp.name, "learned-rules.json")
        self.changelog_path = os.path.join(self.tmp.name, "rules-changelog.jsonl")
        self.doctrine_path = os.path.join(self.tmp.name, "trader-doctrine.md")
        self.proposals_path = os.path.join(self.tmp.name, "proposals.json")
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(rules.clear_cache)

    def write_proposals(self, props=None):
        with open(self.proposals_path, "w", encoding="utf-8") as handle:
            json.dump(props if props is not None else proposals_fixture(), handle)

    def write_store(self, store):
        with open(self.store_path, "w", encoding="utf-8") as handle:
            json.dump(store, handle)


class StoreValidationTests(TempPathCase):
    def test_minimal_store_is_valid(self):
        self.assertEqual(rules.validate_store(rules.empty_store()), [])

    def test_reject_wrong_version_and_types(self):
        store = valid_store()
        store["version"] = 2
        self.assertTrue(rules.validate_store(store))
        store = valid_store()
        store["rules"]["require_trend_alignment"] = 1  # bool, not int
        self.assertTrue(rules.validate_store(store))
        store = valid_store()
        store["rules"]["min_hold_hours_before_non_risk_close"] = -1
        self.assertTrue(rules.validate_store(store))
        store = valid_store()
        store["rules"]["symbol_cooldowns"] = {"BTCUSDTM": "09/19/2026"}
        self.assertTrue(rules.validate_store(store))
        store = valid_store()
        store["notes"] = {"not": "a list"}
        self.assertTrue(rules.validate_store(store))

    def test_load_rules_fail_closed_and_mtime_cache(self):
        self.assertEqual(rules.load_rules(self.store_path), rules.EMPTY_RULES)
        with open(self.store_path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        self.assertEqual(rules.load_rules(self.store_path), rules.EMPTY_RULES)
        self.write_store(valid_store(require_trend_alignment=True))
        loaded = rules.load_rules(self.store_path)
        self.assertTrue(loaded["require_trend_alignment"])
        # mutate the file; cache must refresh because mtime changed
        self.write_store(valid_store(min_hold_hours_before_non_risk_close=6.0))
        os.utime(self.store_path, None)
        loaded = rules.load_rules(self.store_path)
        self.assertEqual(loaded["min_hold_hours_before_non_risk_close"], 6.0)


class PlanChangesTests(unittest.TestCase):
    TODAY = "2026-09-12"

    def plan(self, props, current=None):
        return rules.plan_changes(props, current or {}, self.TODAY)

    def test_trend_rule_conditions(self):
        changes = self.plan(proposals_fixture())
        trend = [c for c in changes if c["rule"] == "require_trend_alignment"]
        self.assertEqual(len(trend), 1)
        self.assertTrue(trend[0]["after"])
        # with_trend must EXCEED |against net|: 40 < 60 -> no rule
        props = proposals_fixture()
        props["trend_buckets"]["with_trend"]["net"] = 40.0
        self.assertFalse([c for c in self.plan(props) if c["rule"] == "require_trend_alignment"])
        # already set -> no change
        changes = self.plan(proposals_fixture(), {"require_trend_alignment": True})
        self.assertFalse([c for c in changes if c["rule"] == "require_trend_alignment"])

    def test_min_hold_boundary_25(self):
        props = proposals_fixture()
        props["premature_closes"][0]["missed_usd"] = 24.99
        self.assertFalse([c for c in self.plan(props)
                          if c["rule"] == "min_hold_hours_before_non_risk_close"])
        props["premature_closes"][0]["missed_usd"] = 25.0
        changes = self.plan(props)
        hold = [c for c in changes if c["rule"] == "min_hold_hours_before_non_risk_close"]
        self.assertEqual(len(hold), 1)
        self.assertEqual(hold[0]["after"], rules.MIN_HOLD_HOURS)
        # already set -> no change
        changes = self.plan(proposals_fixture(),
                            {"min_hold_hours_before_non_risk_close": 4.0})
        self.assertFalse([c for c in changes
                          if c["rule"] == "min_hold_hours_before_non_risk_close"])

    def test_cooldown_rule_needs_two_against_trend_opens(self):
        changes = self.plan(proposals_fixture())
        cooldowns = [c for c in changes if c["rule"] == "symbol_cooldowns"]
        self.assertEqual(len(cooldowns), 1)
        self.assertEqual(cooldowns[0]["symbol"], "NEARUSDTM")
        expected_until = (date.fromisoformat(self.TODAY)
                          + timedelta(days=rules.COOLDOWN_DAYS)).isoformat()
        self.assertEqual(cooldowns[0]["after"], expected_until)
        props = proposals_fixture()
        props["against_trend_symbols"] = {"NEARUSDTM": {"n": 1, "net": -50.0}}
        self.assertFalse([c for c in self.plan(props) if c["rule"] == "symbol_cooldowns"])
        props = proposals_fixture()
        props["against_trend_symbols"] = {"NEARUSDTM": {"n": 2, "net": 10.0}}
        self.assertFalse([c for c in self.plan(props) if c["rule"] == "symbol_cooldowns"])


class ApplyEndToEndTests(TempPathCase):
    def run_apply(self, props=None, dry_run=False, today="2026-09-12"):
        self.write_proposals(props)
        return apply_module.apply_proposals(
            self.proposals_path, self.store_path, self.changelog_path,
            doctrine_path=self.doctrine_path, dry_run=dry_run, today_iso=today,
            now_ms=1_789_160_400_000)

    def test_dry_run_writes_nothing(self):
        summary = self.run_apply(dry_run=True)
        self.assertEqual(summary["applied"], 3)
        self.assertFalse(os.path.exists(self.store_path))
        self.assertFalse(os.path.exists(self.changelog_path))
        self.assertFalse(os.path.exists(self.doctrine_path))

    def test_real_apply_writes_store_changelog_doctrine(self):
        summary = self.run_apply()
        self.assertEqual((summary["planned"], summary["applied"]), (3, 3))
        with open(self.store_path, encoding="utf-8") as handle:
            store = json.load(handle)
        self.assertTrue(store["rules"]["require_trend_alignment"])
        self.assertEqual(store["rules"]["min_hold_hours_before_non_risk_close"], 4.0)
        self.assertIn("NEARUSDTM", store["rules"]["symbol_cooldowns"])
        notes = store["notes"]
        self.assertTrue(any(n["applied"] and n["rule"] == "require_trend_alignment" for n in notes))
        self.assertTrue(any(not n["applied"] and n["rule"] == "avoid_impatience_closes"
                            for n in notes))
        with open(self.changelog_path, encoding="utf-8") as handle:
            lines = [json.loads(line) for line in handle if line.strip()]
        self.assertEqual(len(lines), 3)
        for line in lines:
            self.assertIn("ts_ms", line)
            self.assertIn("action", line)
            self.assertIn("evidence", line)
        with open(self.doctrine_path, encoding="utf-8") as handle:
            doctrine = handle.read()
        self.assertLessEqual(len(doctrine.splitlines()), 60)
        self.assertIn("require_trend_alignment: ON", doctrine)
        self.assertIn("2026-09-11", doctrine)

    def test_daily_cap_three_and_second_run_noop(self):
        props = proposals_fixture()
        props["against_trend_symbols"] = {"AAAUSDTM": {"n": 2, "net": -10.0},
                                          "BBBUSDTM": {"n": 3, "net": -20.0}}
        summary = self.run_apply(props)
        self.assertEqual((summary["planned"], summary["applied"], summary["deferred"]),
                         (4, 3, 1))
        second = self.run_apply(props)  # same day: quota exhausted
        self.assertEqual(second["applied"], 0)
        with open(self.store_path, encoding="utf-8") as handle:
            store = json.load(handle)
        applied_today = [n for n in store["notes"]
                         if n["date"] == "2026-09-12" and n["applied"]]
        self.assertEqual(len(applied_today), 3)

    def test_notes_cap_drops_oldest(self):
        store = valid_store()
        store["notes"] = [{"date": "2026-09-01", "rule": f"r{i}", "evidence": "e",
                           "applied": True} for i in range(rules.NOTES_CAP)]
        self.write_store(store)
        self.run_apply()
        with open(self.store_path, encoding="utf-8") as handle:
            saved = json.load(handle)
        self.assertEqual(len(saved["notes"]), rules.NOTES_CAP)
        self.assertNotIn("r0", [n["rule"] for n in saved["notes"]])

    def test_invalid_store_fails_closed(self):
        self.write_store({"version": 99})
        with self.assertRaises(SystemExit):
            self.run_apply()
        # nothing was written
        with open(self.store_path, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["version"], 99)

    def test_reversibility_defaults_after_delete(self):
        self.run_apply()
        os.remove(self.store_path)
        rules.clear_cache()
        self.assertEqual(rules.load_rules(self.store_path), rules.EMPTY_RULES)


if __name__ == "__main__":
    unittest.main()
