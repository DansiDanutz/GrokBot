"""Learned-rules store, tier-1 proposal planner, and doctrine renderer.

The daily review emits machine-readable proposals; this module owns the
bounded store those proposals auto-apply into (max 3 auto-applied changes
per day), the schema validation (fail-closed: anything invalid behaves as
"no rules"), and the human-readable doctrine file.

Everything is reversible: deleting a rule or the whole store file returns
behavior to defaults.
"""
from __future__ import annotations

import json
import math
import re
import time
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path

DEFAULT_STORE_PATH = str(Path.home() / "Sandbox" / "grokbot" / "autopilot" / "learned-rules.json")
DEFAULT_CHANGELOG_PATH = str(Path.home() / "Sandbox" / "grokbot" / "reports" / "rules-changelog.jsonl")
DEFAULT_DOCTRINE_PATH = str(Path.home() / "Sandbox" / "grokbot" / "autopilot" / "trader-doctrine.md")

SCHEMA_VERSION = 1
MAX_AUTO_PER_DAY = 3
NOTES_CAP = 20
COOLDOWN_DAYS = 7
MIN_HOLD_HOURS = 4.0
MISSED_USD_THRESHOLD = 25.0
AGAINST_TREND_OPENS_THRESHOLD = 2
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

EMPTY_RULES = {
    "min_hold_hours_before_non_risk_close": None,
    "require_trend_alignment": False,
    "symbol_cooldowns": {},
}


def empty_store(now_ms=None):
    return {
        "version": SCHEMA_VERSION,
        "updated_at_ms": int(now_ms if now_ms is not None else time.time() * 1000),
        "rules": deepcopy(EMPTY_RULES),
        "notes": [],
    }


# ---------------------------------------------------------------------------
# schema validation (fail-closed)


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_store(data):
    """Return a list of schema violations; empty list means valid."""
    errors = []
    if not isinstance(data, dict):
        return ["store is not a JSON object"]
    if data.get("version") != SCHEMA_VERSION:
        errors.append(f"version must be {SCHEMA_VERSION}")
    if not _is_number(data.get("updated_at_ms")):
        errors.append("updated_at_ms must be a finite number")
    rules = data.get("rules")
    if not isinstance(rules, dict):
        errors.append("rules must be an object")
    else:
        hold = rules.get("min_hold_hours_before_non_risk_close")
        if hold is not None and (not _is_number(hold) or hold <= 0):
            errors.append("min_hold_hours_before_non_risk_close must be a positive number or null")
        trend = rules.get("require_trend_alignment")
        if not isinstance(trend, bool):
            errors.append("require_trend_alignment must be a boolean")
        cooldowns = rules.get("symbol_cooldowns")
        if not isinstance(cooldowns, dict):
            errors.append("symbol_cooldowns must be an object")
        else:
            for symbol, until in cooldowns.items():
                if (not isinstance(symbol, str) or not isinstance(until, str)
                        or not _DATE_RE.fullmatch(until)):
                    errors.append("symbol_cooldowns values must be YYYY-MM-DD dates")
                    break
    notes = data.get("notes")
    if not isinstance(notes, list):
        errors.append("notes must be a list")
    else:
        for entry in notes:
            if not isinstance(entry, dict):
                errors.append("notes entries must be objects")
                break
            if not isinstance(entry.get("date"), str) or not _DATE_RE.fullmatch(entry["date"]):
                errors.append("notes entries need a YYYY-MM-DD date")
                break
            if not isinstance(entry.get("rule"), str) or not isinstance(entry.get("evidence"), str):
                errors.append("notes entries need string rule and evidence")
                break
    return errors


# ---------------------------------------------------------------------------
# loading (mtime-cached, fail-closed)


_CACHE = {}


def load_rules(path=DEFAULT_STORE_PATH):
    """Load the rules dict from the store, cached per file mtime.

    Fail-closed: missing, unreadable, or schema-invalid stores yield the
    empty default rules (no behavior change). Deletes are honored: removing
    the file resets the cache because the path disappears.
    """
    try:
        stat = Path(path).stat()
    except OSError:
        _CACHE.pop(path, None)
        return deepcopy(EMPTY_RULES)
    cached = _CACHE.get(path)
    if cached and cached[0] == (stat.st_mtime_ns, stat.st_size):
        return deepcopy(cached[1])
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return deepcopy(EMPTY_RULES)
    if validate_store(data):
        return deepcopy(EMPTY_RULES)
    rules = data["rules"]
    _CACHE[path] = ((stat.st_mtime_ns, stat.st_size), deepcopy(rules))
    return deepcopy(rules)


def clear_cache():
    _CACHE.clear()


# ---------------------------------------------------------------------------
# tier-1 proposal planning (shared by daily.py and apply.py)


def _bucket(props, name):
    bucket = (props.get("trend_buckets") or {}).get(name) or {}
    return {"n": int(bucket.get("n", 0) or 0),
            "net": float(bucket.get("net", 0.0) or 0.0),
            "grids": int(bucket.get("grids", 0) or 0)}


def plan_changes(props, current_rules, today_iso):
    """Plan tier-1 auto-appliable changes from one day's proposals.

    Returns a list of change dicts: {action, rule, before, after, evidence,
    symbol?}. Pure — the caller enforces the per-day cap and persistence.
    Conditions here are the single source of truth shared by the daily
    report and the applier.
    """
    changes = []
    rules = current_rules or {}
    wt, at = _bucket(props, "with_trend"), _bucket(props, "against_trend")
    if (at["net"] < 0 and wt["net"] > abs(at["net"])
            and not rules.get("require_trend_alignment")):
        changes.append({
            "action": "set require_trend_alignment",
            "rule": "require_trend_alignment",
            "before": bool(rules.get("require_trend_alignment")),
            "after": True,
            "evidence": ("against_trend net ${:.2f} over {} open(s) while with_trend made "
                         "${:.2f} over {} — trend agreement would have prevented the losses").format(
                             at["net"], at["n"], wt["net"], wt["n"]),
        })
    big_premature = [p for p in props.get("premature_closes", [])
                     if float(p.get("missed_usd") or 0.0) >= MISSED_USD_THRESHOLD]
    if big_premature and rules.get("min_hold_hours_before_non_risk_close") is None:
        ids = ", ".join(str(p.get("bot_id")) for p in big_premature[:5])
        missed = sum(float(p.get("missed_usd") or 0.0) for p in big_premature)
        changes.append({
            "action": "set min_hold_hours_before_non_risk_close",
            "rule": "min_hold_hours_before_non_risk_close",
            "before": rules.get("min_hold_hours_before_non_risk_close"),
            "after": MIN_HOLD_HOURS,
            "evidence": ("{} premature close(s) (bot {}) each missed >= ${:.0f} (total ${:.2f}) — "
                         "hold negative bots at least {}h unless stop-loss/range-out").format(
                             len(big_premature), ids, MISSED_USD_THRESHOLD, missed, MIN_HOLD_HOURS),
        })
    active = rules.get("symbol_cooldowns") or {}
    for symbol, stats in sorted((props.get("against_trend_symbols") or {}).items()):
        n = int(stats.get("n", 0) or 0)
        net = float(stats.get("net", 0.0) or 0.0)
        if n >= AGAINST_TREND_OPENS_THRESHOLD and net < 0 and symbol not in active:
            changes.append({
                "action": "add symbol_cooldown",
                "rule": "symbol_cooldowns",
                "symbol": symbol,
                "before": None,
                "after": (date.fromisoformat(today_iso) + timedelta(days=COOLDOWN_DAYS)).isoformat(),
                "evidence": ("{} against-trend opens on {} that day, net ${:.2f} — cooling the "
                             "symbol off for {} days").format(n, symbol, net, COOLDOWN_DAYS),
            })
    return changes


def applied_today_count(store, today_iso):
    return sum(1 for note in store.get("notes", [])
               if note.get("date") == today_iso and note.get("applied"))


# ---------------------------------------------------------------------------
# doctrine


def render_doctrine(store, props):
    """Short markdown: active rules, the reviewed day's stats, top learnings."""
    rules = store.get("rules", {})
    lines = ["# Trader doctrine (auto-generated by trader.review.apply)",
             "",
             "Do not edit by hand; regenerated after each apply run. Deleting the",
             "rules store returns every behavior to default.",
             "",
             "## Active learned rules",
             ""]
    hold = rules.get("min_hold_hours_before_non_risk_close")
    lines.append(f"- require_trend_alignment: {'ON' if rules.get('require_trend_alignment') else 'off'}")
    lines.append(f"- min_hold_hours_before_non_risk_close: "
                 f"{f'{hold:g} h' if hold is not None else 'not set'}")
    cooldowns = rules.get("symbol_cooldowns") or {}
    if cooldowns:
        lines.append("- symbol_cooldowns:")
        for symbol, until in sorted(cooldowns.items()):
            lines.append(f"  - {symbol} until {until}")
    else:
        lines.append("- symbol_cooldowns: none")
    lines.append("")
    summary = (props or {}).get("summary") or {}
    lines += ["## Reviewed day: {}".format((props or {}).get("date", "unknown")), ""]
    if summary:
        lines.append("- opened {} / closed {} bots, net ${:.2f}".format(
            summary.get("opened", 0), summary.get("closed", 0), float(summary.get("net", 0.0) or 0.0)))
        if summary.get("premature") is not None:
            lines.append("- premature closes: {}, missed ${:.2f}".format(
                summary["premature"], float(summary.get("missed_usd", 0.0) or 0.0)))
    lines += ["", "## Top evidence-backed learnings", ""]
    proposals = [p for p in ((props or {}).get("proposals") or []) if p.get("evidence")]
    tiered = sorted(proposals, key=lambda p: p.get("tier", 2))
    for index, proposal in enumerate(tiered[:3], 1):
        state = "ACTIVE" if proposal.get("tier") == 1 else "advisory"
        lines.append("{}. [{}] {}".format(index, state, proposal["evidence"]))
    if not tiered:
        lines.append("- none yet")
    lines.append("")
    return "\n".join(lines[:60])
