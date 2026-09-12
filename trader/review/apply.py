"""Apply tier-1 learned-rule proposals to the rules store.

Validates the store (fail-closed), plans tier-1 changes from one day's
proposals, enforces the max-3-auto-changes-per-day cap, records notes for
applied and non-applied proposals, appends a JSONL changelog line per
change, and regenerates the doctrine file.

Usage:
    python3 -m trader.review.apply --proposals PATH --store PATH \
        --changelog PATH [--doctrine PATH] [--dry-run] [--today YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path

from trader.review import rules


def load_store(path):
    """Return (store, error). Missing file -> fresh empty store."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return rules.empty_store(), None
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"store unreadable: {exc}"
    violations = rules.validate_store(data)
    if violations:
        return None, "store failed schema validation: " + "; ".join(violations)
    return data, None


def apply_proposals(proposals_path, store_path, changelog_path, *, doctrine_path=None,
                    dry_run=False, today_iso=None, now_ms=None):
    """Plan and (unless dry_run) apply tier-1 changes. Returns a summary dict."""
    today_iso = today_iso or date.today().isoformat()
    now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    with open(proposals_path, "r", encoding="utf-8") as handle:
        props = json.load(handle)

    store, error = load_store(store_path)
    if error:
        raise SystemExit(f"[rules] refusing to apply: {error}")

    planned = rules.plan_changes(props, store["rules"], today_iso)
    quota = max(0, rules.MAX_AUTO_PER_DAY - rules.applied_today_count(store, today_iso))
    accepted, deferred = planned[:quota], planned[quota:]

    notes_today = []
    for change in accepted:
        notes_today.append({"date": today_iso, "rule": change["rule"],
                            "evidence": change["evidence"], "applied": True})
    for change in deferred:
        notes_today.append({"date": today_iso, "rule": change["rule"],
                            "evidence": "NOT auto-applied (daily cap of {} reached): {}".format(
                                rules.MAX_AUTO_PER_DAY, change["evidence"]),
                            "applied": False})
    already = {c["rule"] for c in planned}
    for proposal in props.get("proposals", []):
        if proposal.get("tier") == 1:
            if proposal.get("rule") not in already:
                notes_today.append({"date": today_iso, "rule": proposal.get("rule", "unknown"),
                                    "evidence": "NOT auto-applied (already active or condition "
                                                "unmet): " + str(proposal.get("evidence", "")),
                                    "applied": False})
        else:
            notes_today.append({"date": today_iso, "rule": proposal.get("rule", "unknown"),
                                "evidence": str(proposal.get("evidence", "")),
                                "applied": False})

    summary = {"today": today_iso, "planned": len(planned), "applied": len(accepted),
               "deferred": len(deferred), "changes": accepted, "dry_run": dry_run}
    if dry_run:
        return summary

    store["rules"].setdefault("symbol_cooldowns", {})
    for change in accepted:
        if change["rule"] == "require_trend_alignment":
            store["rules"]["require_trend_alignment"] = change["after"]
        elif change["rule"] == "min_hold_hours_before_non_risk_close":
            store["rules"]["min_hold_hours_before_non_risk_close"] = change["after"]
        elif change["rule"] == "symbol_cooldowns":
            store["rules"]["symbol_cooldowns"][change["symbol"]] = change["after"]
    store["updated_at_ms"] = now_ms
    store["notes"] = (store.get("notes", []) + notes_today)[-rules.NOTES_CAP:]

    store_file = Path(store_path)
    store_file.parent.mkdir(parents=True, exist_ok=True)
    store_file.write_text(json.dumps(store, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    rules.clear_cache()

    changelog_file = Path(changelog_path)
    changelog_file.parent.mkdir(parents=True, exist_ok=True)
    with open(changelog_file, "a", encoding="utf-8") as handle:
        for change in accepted:
            handle.write(json.dumps({"ts_ms": now_ms, "action": change["action"],
                                     "before": change["before"], "after": change["after"],
                                     "evidence": change["evidence"]}) + "\n")

    if doctrine_path:
        Path(doctrine_path).write_text(rules.render_doctrine(store, props), encoding="utf-8")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(prog="trader.review.apply",
                                     description="Apply tier-1 learned-rule proposals")
    parser.add_argument("--proposals", required=True, help="proposals JSON from daily review")
    parser.add_argument("--store", default=rules.DEFAULT_STORE_PATH)
    parser.add_argument("--changelog", default=rules.DEFAULT_CHANGELOG_PATH)
    parser.add_argument("--doctrine", default=rules.DEFAULT_DOCTRINE_PATH)
    parser.add_argument("--dry-run", action="store_true",
                        help="plan and report without writing store/changelog/doctrine")
    parser.add_argument("--today", default=None, help="YYYY-MM-DD override (tests/dry-runs)")
    args = parser.parse_args(argv)
    if args.today:
        datetime.strptime(args.today, "%Y-%m-%d")  # validate format early
    summary = apply_proposals(args.proposals, args.store, args.changelog,
                              doctrine_path=args.doctrine, dry_run=args.dry_run,
                              today_iso=args.today)
    verb = "DRY-RUN" if args.dry_run else "applied"
    print(f"[rules] {verb} {summary['applied']}/{summary['planned']} planned change(s) "
          f"for {summary['today']} (deferred {summary['deferred']})")
    for change in summary["changes"]:
        target = f" {change['symbol']}" if change.get("symbol") else ""
        print(f"[rules]   {change['action']}{target}: {change['before']} -> {change['after']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
