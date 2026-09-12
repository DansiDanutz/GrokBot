"""Apply tier-1 learned-rule proposals to the rules store.

Validates the store (fail-closed), plans tier-1 changes from one day's
proposals behind the significance gates in trader.review.rules, re-checks
each accepted change's estimated benefit before writing (Item 3 honest
check), enforces the max-3-auto-changes-per-day cap, records notes for
applied/deferred/rejected/advisory proposals, appends an auditable JSONL
changelog line per change (estimated_benefit_usd + sample), regenerates
the doctrine, and writes the dashboard review-status.json (atomic).

Usage:
    python3 -m trader.review.apply --proposals PATH --store PATH \
        --changelog PATH [--doctrine PATH] [--status-out PATH] \
        [--dry-run] [--today YYYY-MM-DD]
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
                    status_path=None, dry_run=False, today_iso=None, now_ms=None):
    """Plan and (unless dry_run) apply tier-1 changes. Returns a summary dict."""
    today_iso = today_iso or date.today().isoformat()
    now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    status_path = status_path or rules.DEFAULT_STATUS_PATH
    with open(proposals_path, "r", encoding="utf-8") as handle:
        props = json.load(handle)

    store, error = load_store(store_path)
    if error:
        raise SystemExit(f"[rules] refusing to apply: {error}")

    planned = rules.plan_changes(props, store["rules"], today_iso)
    applicable = [c for c in planned if c.get("status") == "apply"]
    gated = [c for c in planned if c.get("status") == "defer"]
    quota = max(0, rules.MAX_AUTO_PER_DAY - rules.applied_today_count(store, today_iso))
    accepted, deferred = applicable[:quota], applicable[quota:]

    # Item 3: honest evidence re-check immediately before writing. The
    # benefit is recomputed from the proposals by the shared estimator —
    # a benefit-gated change whose evidence no longer clears the floor is
    # rejected, not applied (defense against stale/edited proposal files).
    confirmed, rejected = [], []
    BENEFIT_GATED = ("min_hold_hours_before_non_risk_close", "require_trend_alignment")
    for change in accepted:
        if change["rule"] in BENEFIT_GATED:
            benefit = rules.estimate_rule_benefit(props, change["rule"])
            if benefit is None or benefit < rules.MIN_RULE_BENEFIT_USD:
                change = dict(change, status="reject",
                              reject_reason="apply-time re-check: estimated benefit "
                                            "${:.2f} < ${:.0f}".format(
                                                benefit or 0.0, rules.MIN_RULE_BENEFIT_USD))
                rejected.append(change)
                continue
            change["estimated_benefit_usd"] = benefit
        confirmed.append(change)
    accepted = confirmed

    notes_today = []
    for change in accepted:
        notes_today.append({"date": today_iso, "rule": change["rule"],
                            "evidence": change["evidence"], "applied": True})
    for change in deferred:
        notes_today.append({"date": today_iso, "rule": change["rule"],
                            "evidence": "NOT auto-applied (daily cap of {} reached): {}".format(
                                rules.MAX_AUTO_PER_DAY, change["evidence"]),
                            "applied": False})
    for change in gated:
        notes_today.append({"date": today_iso, "rule": change["rule"],
                            "evidence": "deferred ({}): {}".format(
                                change.get("defer_reason", "insufficient evidence"),
                                change["evidence"]),
                            "applied": False})
    for change in rejected:
        notes_today.append({"date": today_iso, "rule": change["rule"],
                            "evidence": "rejected ({}): {}".format(
                                change.get("reject_reason", "evidence re-check failed"),
                                change["evidence"]),
                            "applied": False})
    planned_rules = {c["rule"] for c in planned}
    for proposal in props.get("proposals", []):
        if proposal.get("tier") == 1:
            if proposal.get("rule") not in planned_rules:
                notes_today.append({"date": today_iso, "rule": proposal.get("rule", "unknown"),
                                    "evidence": "NOT auto-applied (already active or condition "
                                                "unmet): " + str(proposal.get("evidence", "")),
                                    "applied": False})
        else:
            notes_today.append({"date": today_iso, "rule": proposal.get("rule", "unknown"),
                                "evidence": str(proposal.get("evidence", "")),
                                "applied": False})

    existing = {(n.get("rule"), n.get("evidence")) for n in store.get("notes", [])}
    notes_today = [n for n in notes_today
                   if (n["rule"], n["evidence"]) not in existing]

    counts = {"planned": len(planned), "applied": len(accepted),
              "deferred": len(gated) + len(deferred), "rejected": len(rejected)}
    headline = rules.evidence_headline_for(accepted) if accepted else (
        "deferred: " + gated[0]["defer_reason"] if gated else "no tier-1 changes")
    summary = {"today": today_iso, "changes": accepted, "rejected": rejected,
               "gated": gated, "deferred_quota": deferred, "counts": counts,
               "evidence_headline": headline, "dry_run": dry_run}

    # Dashboard status after every run, including dry-runs. On a dry-run the
    # store is unchanged and the doctrine version is carried over from the
    # previous status when one exists.
    doctrine_version = None
    if dry_run:
        prior = rules.load_review_status(status_path)
        doctrine_version = prior.get("doctrine_version") if prior else None
        rules.atomic_write_json(status_path, rules.build_status(
            store, counts, headline, doctrine_version, now_ms))
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
            handle.write(json.dumps({
                "ts_ms": now_ms, "action": change["action"],
                "before": change["before"], "after": change["after"],
                "evidence": change["evidence"],
                "estimated_benefit_usd": change.get("estimated_benefit_usd"),
                "sample": change.get("sample") or rules._rule_sample(props),
            }) + "\n")
        # Item 3: an auditable trace line on EVERY real run, so "the learner
        # looked and decided nothing" is distinguishable from "the learner
        # never ran". Append-only; no dedup needed (unlike notes).
        trace = {"ts_ms": now_ms, "action": "apply" if accepted else "no_change",
                 "run_date": today_iso, "planned": counts["planned"],
                 "applied": counts["applied"], "deferred": counts["deferred"],
                 "rejected": counts["rejected"]}
        if not accepted:
            if rejected:
                reason = "all candidates rejected at apply-time evidence re-check"
            elif deferred:
                reason = "daily auto-apply cap reached"
            elif gated:
                reason = "all candidates deferred: " + (gated[0].get("defer_reason")
                                                        or "insufficient evidence")
            else:
                reason = "no candidates"
            trace["did_not_change_reason"] = reason
        handle.write(json.dumps(trace) + "\n")

    benefits = {c["rule"]: (c.get("estimated_benefit_usd"),
                            "{closed_n}/{opens_n}".format(**c["sample"]))
                for c in accepted if c.get("sample")}
    if doctrine_path:
        doctrine_version = datetime.fromtimestamp(now_ms / 1000).isoformat(timespec="seconds")
        Path(doctrine_path).write_text(
            rules.render_doctrine(store, props, benefits), encoding="utf-8")

    rules.atomic_write_json(status_path, rules.build_status(
        store, counts, headline, doctrine_version, now_ms))
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(prog="trader.review.apply",
                                     description="Apply tier-1 learned-rule proposals")
    parser.add_argument("--proposals", required=True, help="proposals JSON from daily review")
    parser.add_argument("--store", default=rules.DEFAULT_STORE_PATH)
    parser.add_argument("--changelog", default=rules.DEFAULT_CHANGELOG_PATH)
    parser.add_argument("--doctrine", default=rules.DEFAULT_DOCTRINE_PATH)
    parser.add_argument("--status-out", default=rules.DEFAULT_STATUS_PATH,
                        help="dashboard review-status.json (atomic tmp+rename)")
    parser.add_argument("--dry-run", action="store_true",
                        help="plan and report without writing store/changelog/doctrine/status")
    parser.add_argument("--today", default=None, help="YYYY-MM-DD override (tests/dry-runs)")
    args = parser.parse_args(argv)
    if args.today:
        datetime.strptime(args.today, "%Y-%m-%d")  # validate format early
    summary = apply_proposals(args.proposals, args.store, args.changelog,
                              doctrine_path=args.doctrine, status_path=args.status_out,
                              dry_run=args.dry_run, today_iso=args.today)
    counts = summary["counts"]
    verb = "DRY-RUN" if summary["dry_run"] else "applied"
    print(f"[rules] {verb} {counts['applied']}/{counts['planned']} planned change(s) "
          f"for {summary['today']} (deferred {counts['deferred']}, rejected {counts['rejected']})")
    for change in summary["changes"]:
        target = f" {change['symbol']}" if change.get("symbol") else ""
        print(f"[rules]   {change['action']}{target}: {change['before']} -> {change['after']} "
              f"(benefit ${change.get('estimated_benefit_usd')}, "
              f"sample {change['sample']['closed_n']} closed / {change['sample']['opens_n']} opened)")
    for change in summary["gated"]:
        print(f"[rules]   deferred {change['rule']}: {change.get('defer_reason')}")
    for change in summary["rejected"]:
        print(f"[rules]   rejected {change['rule']}: {change.get('reject_reason')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
