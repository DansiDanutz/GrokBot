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
        "summary": {"opened": 8, "closed": 6, "net": -10.0, "premature": 1,
                    "missed_usd": 48.05},
        "totals": {"closed_total": 25},
        "premature_closes": [{"bot_id": 3, "symbol": "NESUSDTM", "direction": "LONG",
                              "close_reason": "PROFILE_UPDATE", "net": -9.04,
                              "missed_usd": 48.05, "would_have_been_best_usd": 39.01}],
        "trend_buckets": {"with_trend": {"n": 3, "net": 100.0, "grids": 50},
                          "against_trend": {"n": 3, "net": -60.0, "grids": 10},
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

    def applied(self, props, current=None):
        return [c for c in self.plan(props, current) if c.get("status") == "apply"]

    def deferred(self, props, current=None):
        return [c for c in self.plan(props, current) if c.get("status") == "defer"]

    def test_trend_rule_conditions(self):
        changes = [c for c in self.plan(proposals_fixture())
                   if c["rule"] == "require_trend_alignment"]
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["status"], "apply")
        self.assertTrue(changes[0]["after"])
        self.assertEqual(changes[0]["estimated_benefit_usd"], 60.0)  # min(60, 100)
        # with_trend must EXCEED |against net|: 40 < 60 -> no rule at all
        props = proposals_fixture()
        props["trend_buckets"]["with_trend"]["net"] = 40.0
        self.assertFalse([c for c in self.plan(props) if c["rule"] == "require_trend_alignment"])
        # already set -> no change
        changes = self.plan(proposals_fixture(), {"require_trend_alignment": True})
        self.assertFalse([c for c in changes if c["rule"] == "require_trend_alignment"])

    def test_trend_rule_directional_sample_gate(self):
        props = proposals_fixture()
        props["trend_buckets"]["with_trend"]["n"] = 2
        props["trend_buckets"]["against_trend"]["n"] = 2  # 4 directional < 5
        entry = [c for c in self.deferred(props) if c["rule"] == "require_trend_alignment"]
        self.assertEqual(len(entry), 1)
        self.assertIn("directional opens", entry[0]["defer_reason"])

    def test_trend_rule_benefit_boundary(self):
        props = proposals_fixture()
        props["trend_buckets"]["against_trend"]["net"] = -9.99
        props["trend_buckets"]["with_trend"]["net"] = 100.0
        entry = [c for c in self.deferred(props) if c["rule"] == "require_trend_alignment"]
        self.assertEqual(len(entry), 1)
        self.assertIn("$9.99", entry[0]["defer_reason"])
        self.assertEqual(entry[0]["estimated_benefit_usd"], 9.99)
        props["trend_buckets"]["against_trend"]["net"] = -10.00
        applied = [c for c in self.applied(props) if c["rule"] == "require_trend_alignment"]
        self.assertEqual(len(applied), 1)
        self.assertEqual(applied[0]["estimated_benefit_usd"], 10.0)

    def test_min_hold_boundary_25(self):
        props = proposals_fixture()
        props["premature_closes"][0]["missed_usd"] = 24.99
        self.assertFalse([c for c in self.plan(props)
                          if c["rule"] == "min_hold_hours_before_non_risk_close"])
        changes = [c for c in self.plan(proposals_fixture())
                   if c["rule"] == "min_hold_hours_before_non_risk_close"]
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["status"], "apply")
        self.assertEqual(changes[0]["estimated_benefit_usd"], 48.05)
        # already set -> no change
        changes = self.plan(proposals_fixture(),
                            {"min_hold_hours_before_non_risk_close": 4.0})
        self.assertFalse([c for c in changes
                          if c["rule"] == "min_hold_hours_before_non_risk_close"])

    def test_min_hold_daily_sample_gate(self):
        # "3 premature closes fires": three qualifying closes, samples ok
        props = proposals_fixture()
        props["premature_closes"] = [
            dict(props["premature_closes"][0], bot_id=i, missed_usd=30.0 + i)
            for i in (1, 2, 3)]
        applied = [c for c in self.applied(props)
                   if c["rule"] == "min_hold_hours_before_non_risk_close"]
        self.assertEqual(len(applied), 1)
        self.assertEqual(applied[0]["estimated_benefit_usd"], 96.0)  # 31+32+33
        # "1 does not": single close but only 4 closed bots that day
        props = proposals_fixture()
        props["summary"]["closed"] = 4
        entry = [c for c in self.deferred(props)
                 if c["rule"] == "min_hold_hours_before_non_risk_close"]
        self.assertEqual(len(entry), 1)
        self.assertIn("closed bots today", entry[0]["defer_reason"])

    def test_min_hold_cumulative_sample_gate(self):
        props = proposals_fixture()
        props["totals"]["closed_total"] = 10  # < 20 cumulative
        entry = [c for c in self.deferred(props)
                 if c["rule"] == "min_hold_hours_before_non_risk_close"]
        self.assertEqual(len(entry), 1)
        self.assertIn("cumulative", entry[0]["defer_reason"])

    def test_cooldown_rule_needs_two_against_trend_opens(self):
        changes = self.plan(proposals_fixture())
        cooldowns = [c for c in changes if c["rule"] == "symbol_cooldowns"]
        self.assertEqual(len(cooldowns), 1)
        self.assertEqual(cooldowns[0]["status"], "apply")
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
        self.status_path = os.path.join(self.tmp.name, "review-status.json")
        return apply_module.apply_proposals(
            self.proposals_path, self.store_path, self.changelog_path,
            doctrine_path=self.doctrine_path, status_path=self.status_path,
            dry_run=dry_run, today_iso=today, now_ms=1_789_160_400_000)

    def test_dry_run_writes_only_status(self):
        summary = self.run_apply(dry_run=True)
        self.assertEqual(summary["counts"]["applied"], 3)
        self.assertFalse(os.path.exists(self.store_path))
        self.assertFalse(os.path.exists(self.changelog_path))
        self.assertFalse(os.path.exists(self.doctrine_path))
        with open(self.status_path, encoding="utf-8") as handle:
            status = json.load(handle)
        self.assertEqual(status["proposals"]["applied"], 3)
        self.assertEqual(status["proposals"]["deferred"], 0)

    def test_real_apply_writes_store_changelog_doctrine_status(self):
        summary = self.run_apply()
        counts = summary["counts"]
        self.assertEqual((counts["planned"], counts["applied"]), (3, 3))
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
            self.assertIn("sample", line)
            self.assertIn("closed_n", line["sample"])
        hold = [l for l in lines if l["action"] == "set min_hold_hours_before_non_risk_close"]
        self.assertEqual(hold[0]["estimated_benefit_usd"], 48.05)
        trend = [l for l in lines if l["action"] == "set require_trend_alignment"]
        self.assertEqual(trend[0]["estimated_benefit_usd"], 60.0)
        cooldown = [l for l in lines if l["action"] == "add symbol_cooldown"]
        self.assertIsNone(cooldown[0]["estimated_benefit_usd"])
        with open(self.doctrine_path, encoding="utf-8") as handle:
            doctrine = handle.read()
        self.assertLessEqual(len(doctrine.splitlines()), 60)
        self.assertIn("require_trend_alignment: ON", doctrine)
        self.assertIn("2026-09-11", doctrine)
        self.assertIn("estimated benefit $48.05", doctrine)
        with open(self.status_path, encoding="utf-8") as handle:
            status = json.load(handle)
        self.assertEqual(status["proposals"],
                         {"planned": 3, "applied": 3, "deferred": 0, "rejected": 0})
        self.assertTrue(status["active_rules"]["require_trend_alignment"])
        self.assertEqual(status["active_rules"]["min_hold_hours_before_non_risk_close"], 4.0)
        self.assertEqual(status["active_rules"]["symbol_cooldowns"], ["NEARUSDTM"])
        self.assertIsNotNone(status["doctrine_version"])
        self.assertIn("min_hold=4h", status["evidence_headline"])
        self.assertTrue(rules.validate_status(status))

    def test_gated_proposals_write_deferral_notes_and_status(self):
        props = proposals_fixture()
        props["summary"]["closed"] = 4  # min-hold sample gate fails
        props["trend_buckets"]["with_trend"]["n"] = 1  # 4 directional < 5
        props["trend_buckets"]["against_trend"]["n"] = 3
        summary = self.run_apply(props)
        counts = summary["counts"]
        self.assertEqual(counts["applied"], 1)  # only the cooldown survives
        self.assertEqual(counts["deferred"], 2)
        with open(self.store_path, encoding="utf-8") as handle:
            store = json.load(handle)
        deferred_notes = [n for n in store["notes"] if "deferred (insufficient evidence" in n["evidence"]]
        self.assertEqual(len(deferred_notes), 2)
        self.assertTrue(any("min_hold_hours" in n["rule"] for n in deferred_notes))
        self.assertTrue(any("require_trend_alignment" in n["rule"] for n in deferred_notes))
        with open(self.status_path, encoding="utf-8") as handle:
            status = json.load(handle)
        self.assertEqual(status["proposals"]["deferred"], 2)
        self.assertIn("cooldown NEARUSDTM", status["evidence_headline"])
        self.assertFalse(store["rules"]["require_trend_alignment"])
        self.assertIsNone(store["rules"]["min_hold_hours_before_non_risk_close"])

    def test_apply_time_recheck_rejects_stale_benefit(self):
        # Simulate a stale/edited proposals file: planning passes (entries
        # forced to apply), but the apply-time re-check recomputes a benefit
        # below the floor -> the change is rejected, not written.
        real_plan = rules.plan_changes

        def forced_plan(props, current, today):
            entries = real_plan(props, current, today)
            for entry in entries:
                if entry["rule"] in ("min_hold_hours_before_non_risk_close",
                                     "require_trend_alignment"):
                    entry["status"] = "apply"
            return entries

        with mock.patch.object(rules, "estimate_rule_benefit", return_value=5.0), \
                mock.patch.object(rules, "plan_changes", side_effect=forced_plan):
            summary = self.run_apply(proposals_fixture())
        counts = summary["counts"]
        self.assertEqual(counts["applied"], 1)  # cooldown is not benefit-gated
        self.assertEqual(counts["rejected"], 2)
        with open(self.store_path, encoding="utf-8") as handle:
            store = json.load(handle)
        rejected = [n for n in store["notes"] if n["evidence"].startswith("rejected")]
        self.assertEqual(len(rejected), 2)
        with open(self.changelog_path, encoding="utf-8") as handle:
            lines = [json.loads(l) for l in handle if l.strip()]
        self.assertEqual(len(lines), 1)  # only the cooldown was written
        self.assertEqual(lines[0]["action"], "add symbol_cooldown")

    def test_daily_cap_three_and_second_run_noop(self):
        props = proposals_fixture()
        props["against_trend_symbols"] = {"AAAUSDTM": {"n": 2, "net": -10.0},
                                          "BBBUSDTM": {"n": 3, "net": -20.0}}
        summary = self.run_apply(props)
        counts = summary["counts"]
        self.assertEqual((counts["planned"], counts["applied"], counts["deferred"]), (4, 3, 1))
        second = self.run_apply(props)  # same day: quota exhausted
        self.assertEqual(second["counts"]["applied"], 0)
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

    def test_repeated_runs_deduplicate_notes(self):
        self.run_apply()
        self.run_apply()  # same proposals, same day: no duplicate notes
        with open(self.store_path, encoding="utf-8") as handle:
            store = json.load(handle)
        keys = [(n["rule"], n["evidence"]) for n in store["notes"]]
        self.assertEqual(len(keys), len(set(keys)))

    def test_reversibility_defaults_after_delete(self):
        self.run_apply()
        os.remove(self.store_path)
        rules.clear_cache()
        self.assertEqual(rules.load_rules(self.store_path), rules.EMPTY_RULES)


class ReviewStatusTests(TempPathCase):
    def test_load_review_status_fail_closed(self):
        path = os.path.join(self.tmp.name, "review-status.json")
        self.assertIsNone(rules.load_review_status(path))
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{broken")
        self.assertIsNone(rules.load_review_status(path))
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"last_run_at_ms": "nope"}))
        self.assertIsNone(rules.load_review_status(path))
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"last_run_at_ms": 1, "doctrine_version": None,
                                     "proposals": {"planned": 0, "applied": 0, "deferred": 0,
                                                   "rejected": 0},
                                     "active_rules": {"min_hold_hours_before_non_risk_close": None,
                                                      "require_trend_alignment": False,
                                                      "symbol_cooldowns": []},
                                     "evidence_headline": "no tier-1 changes"}))
        loaded = rules.load_review_status(path)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["proposals"]["planned"], 0)

    def test_atomic_write_leaves_no_tmp(self):
        path = os.path.join(self.tmp.name, "nested", "review-status.json")
        rules.atomic_write_json(path, {"a": 1})
        with open(path, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle), {"a": 1})
        self.assertFalse(os.path.exists(path + ".tmp"))


if __name__ == "__main__":
    unittest.main()
