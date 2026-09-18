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
import os
import re
import time
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path

SCHEMA_VERSION = 1
MAX_AUTO_PER_DAY = 3
NOTES_CAP = 20
COOLDOWN_DAYS = 7
MIN_HOLD_HOURS = 4.0
MISSED_USD_THRESHOLD = 25.0
AGAINST_TREND_OPENS_THRESHOLD = 2
# Anti-noise significance gates: a handful of bots is anecdote, not evidence.
MIN_DAILY_CLOSED = 5
MIN_CUMULATIVE_CLOSED = 20
MIN_DAILY_DIRECTIONAL_OPENS = 5
MIN_RULE_BENEFIT_USD = 10.0
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

EMPTY_RULES = {
    "min_hold_hours_before_non_risk_close": None,
    "require_trend_alignment": False,
    "symbol_cooldowns": {},
    # radar_flip_hysteresis_cycles: absent by default (staged, OFF).
    # opportunity_cost_close: absent by default; the constant
    # trader.autopilot.constants.OPPORTUNITY_COST_CLOSE decides until a store
    # overrides it. Absent keeps every existing store valid.
    # hedge_enabled: absent by default (staged, OFF). Existing stores written
    # before the T5 hedge trigger stay valid precisely because it is absent.
    # agent_influence_enabled: absent by default (T8 ships dark); the constant
    # trader.autopilot.constants.AGENT_INFLUENCE_ENABLED decides until a store
    # overrides it.
}

DEFAULT_STORE_PATH = str(Path.home() / "Sandbox" / "grokbot" / "autopilot" / "learned-rules.json")
DEFAULT_CHANGELOG_PATH = str(Path.home() / "Sandbox" / "grokbot" / "reports" / "rules-changelog.jsonl")
DEFAULT_DOCTRINE_PATH = str(Path.home() / "Sandbox" / "grokbot" / "autopilot" / "trader-doctrine.md")
DEFAULT_STATUS_PATH = str(Path.home() / "Sandbox" / "grokbot" / "autopilot" / "review-status.json")
DEFAULT_LEDGER_PATH = str(Path.home() / "Sandbox" / "grokbot" / "reports" / "proposals-ledger.jsonl")


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
        hysteresis = rules.get("radar_flip_hysteresis_cycles")
        if hysteresis is not None and (isinstance(hysteresis, bool)
                                       or not isinstance(hysteresis, int)
                                       or not 1 <= hysteresis <= 100):
            errors.append("radar_flip_hysteresis_cycles must be an integer between 1 and 100")
        opportunity = rules.get("opportunity_cost_close")
        if opportunity is not None and not isinstance(opportunity, bool):
            errors.append("opportunity_cost_close must be a boolean or absent")
        hedge_flag = rules.get("hedge_enabled")
        if hedge_flag is not None and not isinstance(hedge_flag, bool):
            errors.append("hedge_enabled must be a boolean or absent")
        agent_influence = rules.get("agent_influence_enabled")
        if agent_influence is not None and not isinstance(agent_influence, bool):
            errors.append("agent_influence_enabled must be a boolean or absent")
        stall = rules.get("stall_exit")
        if stall is not None:
            if not isinstance(stall, dict) or set(stall) != {
                    "warmup_hours", "min_grids_per_hour", "adverse_travel"}:
                errors.append("stall_exit must be null or an object with warmup_hours,"
                              " min_grids_per_hour and adverse_travel")
            else:
                if not _is_number(stall["warmup_hours"]) or not 0.5 <= stall["warmup_hours"] <= 48:
                    errors.append("stall_exit.warmup_hours must be between 0.5 and 48")
                if not _is_number(stall["min_grids_per_hour"]) or not 0 < stall["min_grids_per_hour"] <= 100:
                    errors.append("stall_exit.min_grids_per_hour must be between 0 and 100")
                if not _is_number(stall["adverse_travel"]) or not 0.5 <= stall["adverse_travel"] <= 0.99:
                    errors.append("stall_exit.adverse_travel must be between 0.5 and 0.99")
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


def _summary(props):
    return props.get("summary") or {}


def _totals(props):
    return props.get("totals") or {}


def estimate_rule_benefit(props, rule):
    """Estimated USD benefit of a tier-1 rule, recomputed from proposal detail.

    This is the single shared math used by the planner (Item 1), the
    apply-time honest re-check (Item 3), and the changelog/doctrine numbers —
    import it; do not reimplement.

    min_hold: sum of missed_usd across the premature closes that drove the
    proposal (missed_usd >= threshold). trend: |against_trend.net| clipped at
    with_trend.net when the latter is positive. cooldown: not benefit-gated
    (None).
    """
    if rule == "min_hold_hours_before_non_risk_close":
        return round(sum(float(p.get("missed_usd") or 0.0)
                         for p in props.get("premature_closes", [])
                         if float(p.get("missed_usd") or 0.0) >= MISSED_USD_THRESHOLD), 4)
    if rule == "require_trend_alignment":
        against = abs(_bucket(props, "against_trend")["net"])
        with_trend = _bucket(props, "with_trend")["net"]
        return round(min(against, with_trend) if with_trend > 0 else against, 4)
    return None


def _rule_sample(props):
    return {"closed_n": int(_summary(props).get("closed", 0) or 0),
            "opens_n": int(_summary(props).get("opened", 0) or 0)}


def _defer(entry, reason):
    entry["status"] = "defer"
    entry["defer_reason"] = reason
    return entry


def plan_changes(props, current_rules, today_iso):
    """Plan tier-1 changes; significance gates turn thin evidence into deferrals.

    Returns a list of entries, each: {status: "apply"|"defer", action, rule,
    before, after, evidence, estimated_benefit_usd, sample, defer_reason?,
    symbol?}. Anti-noise gates (Item 1):

    - min_hold: needs >= MIN_DAILY_CLOSED closed bots that day AND
      >= MIN_CUMULATIVE_CLOSED closed bots total (props.totals.closed_total),
      plus estimated benefit >= MIN_RULE_BENEFIT_USD.
    - trend: needs >= MIN_DAILY_DIRECTIONAL_OPENS directional opens that day
      (with + against buckets), plus the same benefit floor.
    - symbol cooldowns: unchanged day-specific rule (n >= 2 against-trend
      opens with negative net on one symbol).

    A day flagged LOW-CONFIDENCE by the data-coverage gate defers every
    candidate (reason names the worst-covered symbol).

    Deferral reasons name the failing gate so notes/changelog can show them.
    """
    planned = []
    rules = current_rules or {}
    summary, totals = _summary(props), _totals(props)
    closed_day = int(summary.get("closed", 0) or 0)
    closed_total = int(totals.get("closed_total", 0) or 0)
    sample = _rule_sample(props)

    wt, at = _bucket(props, "with_trend"), _bucket(props, "against_trend")
    directional_opens = wt["n"] + at["n"]
    if at["net"] < 0 and wt["net"] > abs(at["net"]) and not rules.get("require_trend_alignment"):
        entry = {
            "action": "set require_trend_alignment", "rule": "require_trend_alignment",
            "before": bool(rules.get("require_trend_alignment")), "after": True,
            "evidence": ("against_trend net ${:.2f} over {} open(s) while with_trend made "
                         "${:.2f} over {} — trend agreement would have prevented the losses").format(
                             at["net"], at["n"], wt["net"], wt["n"]),
            "estimated_benefit_usd": estimate_rule_benefit(props, "require_trend_alignment"),
            "sample": sample,
        }
        if directional_opens < MIN_DAILY_DIRECTIONAL_OPENS:
            planned.append(_defer(entry, "insufficient evidence: {} directional opens < {} minimum".format(
                directional_opens, MIN_DAILY_DIRECTIONAL_OPENS)))
        elif entry["estimated_benefit_usd"] < MIN_RULE_BENEFIT_USD:
            planned.append(_defer(entry, "insufficient evidence: estimated benefit ${:.2f} < ${:.0f} minimum".format(
                entry["estimated_benefit_usd"], MIN_RULE_BENEFIT_USD)))
        else:
            entry["status"] = "apply"
            planned.append(entry)

    big_premature = [p for p in props.get("premature_closes", [])
                     if float(p.get("missed_usd") or 0.0) >= MISSED_USD_THRESHOLD]
    if big_premature and rules.get("min_hold_hours_before_non_risk_close") is None:
        ids = ", ".join(str(p.get("bot_id")) for p in big_premature[:5])
        missed = sum(float(p.get("missed_usd") or 0.0) for p in big_premature)
        entry = {
            "action": "set min_hold_hours_before_non_risk_close",
            "rule": "min_hold_hours_before_non_risk_close",
            "before": rules.get("min_hold_hours_before_non_risk_close"),
            "after": MIN_HOLD_HOURS,
            "evidence": ("{} premature close(s) (bot {}) each missed >= ${:.0f} (total ${:.2f}) — "
                         "hold negative bots at least {}h unless stop-loss/range-out").format(
                             len(big_premature), ids, MISSED_USD_THRESHOLD, missed, MIN_HOLD_HOURS),
            "estimated_benefit_usd": estimate_rule_benefit(props, "min_hold_hours_before_non_risk_close"),
            "sample": sample,
        }
        if closed_day < MIN_DAILY_CLOSED:
            planned.append(_defer(entry, "insufficient evidence: {} closed bots today < {} minimum".format(
                closed_day, MIN_DAILY_CLOSED)))
        elif closed_total < MIN_CUMULATIVE_CLOSED:
            planned.append(_defer(entry, "insufficient evidence: {} cumulative closed bots < {} minimum".format(
                closed_total, MIN_CUMULATIVE_CLOSED)))
        elif entry["estimated_benefit_usd"] < MIN_RULE_BENEFIT_USD:
            planned.append(_defer(entry, "insufficient evidence: estimated benefit ${:.2f} < ${:.0f} minimum".format(
                entry["estimated_benefit_usd"], MIN_RULE_BENEFIT_USD)))
        else:
            entry["status"] = "apply"
            planned.append(entry)

    active = rules.get("symbol_cooldowns") or {}
    for symbol, stats in sorted((props.get("against_trend_symbols") or {}).items()):
        n = int(stats.get("n", 0) or 0)
        net = float(stats.get("net", 0.0) or 0.0)
        if n >= AGAINST_TREND_OPENS_THRESHOLD and net < 0 and symbol not in active:
            planned.append({
                "action": "add symbol_cooldown", "rule": "symbol_cooldowns", "symbol": symbol,
                "before": None,
                "after": (date.fromisoformat(today_iso) + timedelta(days=COOLDOWN_DAYS)).isoformat(),
                "evidence": ("{} against-trend opens on {} that day, net ${:.2f} — cooling the "
                             "symbol off for {} days").format(n, symbol, net, COOLDOWN_DAYS),
                "estimated_benefit_usd": None,
                "sample": sample,
                "status": "apply",
            })

    # Data-coverage gate: a day flagged LOW-CONFIDENCE (any traded symbol
    # under 95% 1m coverage) must not teach the system anything — every
    # candidate defers with the offending symbol named. Appended to any
    # existing gate reason so the specific one is preserved.
    coverage = totals.get("data_coverage") or {}
    if coverage.get("flagged"):
        worst_symbol = min(coverage.get("per_symbol", {}).items(), key=lambda kv: kv[1])[0]
        reason = "low data coverage (<95%) for {}".format(worst_symbol)
        for entry in planned:
            if entry.get("status") == "defer":
                entry["defer_reason"] = (entry.get("defer_reason", "insufficient evidence")
                                         + "; " + reason)
            else:
                _defer(entry, reason)
    quality = totals.get("accounting_quality") or {}
    if quality.get("status") == "PROVISIONAL":
        reason = "provisional accounting: unresolved funding or recovery reconciliation"
        for entry in planned:
            if entry.get("status") == "defer":
                entry["defer_reason"] = entry.get("defer_reason", "insufficient evidence") + "; " + reason
            else:
                _defer(entry, reason)
    return planned


def applied_today_count(store, today_iso):
    return sum(1 for note in store.get("notes", [])
               if note.get("date") == today_iso and note.get("applied"))


# ---------------------------------------------------------------------------
# radar-flip hysteresis (STAGED — tier-2 advisory only, never auto-applies)

FLIP_HYSTERESIS_CYCLES = 2
MAX_LABEL_FLIP_HOLD_S = 4 * 3_600


def radar_flip_hysteresis_proposal(props):
    """Tier-2 proposal: require N consecutive conflicting radar scans before
    a LABEL_FLIP close is allowed, when the day showed >=2 LABEL_FLIP closes
    with sub-4h holds (label noise whipsawing young bots). Never tier-1.
    """
    flip = (props.get("close_reasons") or {}).get("LABEL_FLIP") or {}
    n = int(flip.get("n", 0) or 0)
    hold_s = flip.get("avg_hold_s")
    if n < 2 or hold_s is None or hold_s >= MAX_LABEL_FLIP_HOLD_S:
        return None
    return {
        "rule": "radar_flip_hysteresis_cycles",
        "tier": 2,
        "action": "set",
        "value": FLIP_HYSTERESIS_CYCLES,
        "evidence": ("{} LABEL_FLIP close(s) averaging {:.1f}h hold — label noise is "
                     "whipsawing young bots; PROPOSAL (staged, off by default): "
                     "radar_flip_hysteresis_cycles={} so a LABEL_FLIP close needs {} "
                     "consecutive conflicting radar scans".format(
                         n, hold_s / 3_600, FLIP_HYSTERESIS_CYCLES,
                         FLIP_HYSTERESIS_CYCLES)),
    }


# ---------------------------------------------------------------------------
# counterfactual cost of the caps (STAGED — tier-2 advisory only)

COUNTERFACTUAL_COST_USD = 25.0
CAPACITY_BLOCKS = ("bot_capacity", "direction_cap")
LEARNED_BLOCK_PREFIX = "learned_"


def _block_cost(blocks, names):
    """Summed replayed net and candidate count over the named rule blocks."""
    rows = [blocks[name] for name in names if isinstance(blocks.get(name), dict)]
    return {"net": round(sum(float(row.get("sum_net", 0.0) or 0.0) for row in rows), 4),
            "n": sum(int(row.get("count", 0) or 0) for row in rows),
            "names": [name for name in names if isinstance(blocks.get(name), dict)]}


def _cost_note(rule, cost, headline):
    return {"rule": rule, "tier": 2, "action": "review", "value": cost["net"],
            "evidence": ("{} — {} skipped candidate(s) blocked by {} replayed to "
                         "${:.2f} net over the day (threshold ${:.0f}); PROPOSAL (advisory "
                         "only): review whether that refusal is still earning its "
                         "keep".format(headline, cost["n"], ", ".join(cost["names"]),
                                       cost["net"], COUNTERFACTUAL_COST_USD))}


def counterfactual_proposals(counterfactual):
    """Tier-2 advisories from the skipped-decision replay; never auto-applied.

    Fires only when a refusal demonstrably cost money: capacity_cost when the
    hard caps (bot_capacity/direction_cap) blocked more than
    COUNTERFACTUAL_COST_USD of replayed net, learned_rule_cost when the learned
    gates did. A negative sum means the block saved money and says nothing.

    Reads `by_sole_block_complete`: one rule, and a horizon that elapsed. An
    entry refused for two reasons would still have been refused if only one were
    lifted, and an entry whose 24h replay has not finished has not been measured
    at all. Either would propose a change on money nobody has counted.
    """
    blocks = (((counterfactual or {}).get("summary") or {}).get("by_sole_block_complete")) or {}
    if not isinstance(blocks, dict):
        return []
    notes = []
    capacity = _block_cost(blocks, CAPACITY_BLOCKS)
    if capacity["net"] > COUNTERFACTUAL_COST_USD:
        notes.append(_cost_note("capacity_cost", capacity,
                                "the slot caps turned away profitable entries"))
    learned_names = sorted(name for name in blocks if name.startswith(LEARNED_BLOCK_PREFIX))
    learned = _block_cost(blocks, learned_names)
    if learned["net"] > COUNTERFACTUAL_COST_USD:
        notes.append(_cost_note("learned_rule_cost", learned,
                                "a learned rule blocked profitable entries"))
    return notes


# ---------------------------------------------------------------------------
# agent influence cost (T8, STAGED — tier-2 advisory only)

INFLUENCE_COST_USD = 25.0


def influence_proposals(influence):
    """Tier-2 advisories when an agent's influence demonstrably cost money.

    `influence` is trader.autopilot.influence_audit.summarize(...). One
    `influence_cost` note per agent whose net influence over the day is worse
    than -INFLUENCE_COST_USD, priced against the counterfactual replay of the
    entries their vetoes turned away. Never auto-applies: the only levers are
    the kill switch and the steward's file.
    """
    agents = ((influence or {}).get("agents")) or {}
    if not isinstance(agents, dict):
        return []
    notes = []
    for agent, row in sorted(agents.items()):
        if not isinstance(row, dict):
            continue
        net = float(row.get("net_usdt", 0.0) or 0.0)
        if net >= -INFLUENCE_COST_USD:
            continue
        notes.append({
            "rule": "influence_cost", "tier": 2, "action": "review", "value": net,
            "agent_id": agent,
            "evidence": ("agent {} influenced {} entry decision(s) ({} veto(es), {} priced) "
                         "for a net ${:.2f} over the day (threshold -${:.0f}); PROPOSAL "
                         "(advisory only): review that agent's say, or turn the influence "
                         "channel off".format(agent, int(row.get("influences", 0) or 0),
                                              int(row.get("vetoes", 0) or 0),
                                              int(row.get("matched_vetoes", 0) or 0),
                                              net, INFLUENCE_COST_USD))})
    return notes


# ---------------------------------------------------------------------------
# review status (dashboard feed), fail-closed


_STATUS_CACHE = {}


def load_review_status(path=None):
    """Parse review-status.json; None when absent/invalid (never raises)."""
    path = path or DEFAULT_STATUS_PATH
    try:
        stat = Path(path).stat()
    except OSError:
        _STATUS_CACHE.pop(path, None)
        return None
    cached = _STATUS_CACHE.get(path)
    if cached and cached[0] == (stat.st_mtime_ns, stat.st_size):
        return deepcopy(cached[1])
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    if not validate_status(data):
        return None
    _STATUS_CACHE[path] = ((stat.st_mtime_ns, stat.st_size), deepcopy(data))
    return deepcopy(data)


def validate_status(data):
    """Closed-vocabulary shape check for review-status.json."""
    if not isinstance(data, dict):
        return False
    if not _is_number(data.get("last_run_at_ms")):
        return False
    stamp = data.get("doctrine_version")
    if stamp is not None and (not isinstance(stamp, str) or len(stamp) > 40):
        return False
    proposals = data.get("proposals")
    if not isinstance(proposals, dict):
        return False
    for key in ("planned", "applied", "deferred", "rejected"):
        value = proposals.get(key)
        if not _is_number(value) or value < 0:
            return False
    active = data.get("active_rules")
    if not isinstance(active, dict):
        return False
    hold = active.get("min_hold_hours_before_non_risk_close")
    if hold is not None and (not _is_number(hold) or hold <= 0):
        return False
    if not isinstance(active.get("require_trend_alignment"), bool):
        return False
    cooldowns = active.get("symbol_cooldowns")
    if not isinstance(cooldowns, list) or len(cooldowns) > 24:
        return False
    if any(not isinstance(symbol, str) or len(symbol) > 24 for symbol in cooldowns):
        return False
    headline = data.get("evidence_headline")
    return isinstance(headline, str) and 0 < len(headline) <= 240


def atomic_write_json(path, data):
    """tmp+rename write so readers never see a partial status file."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, target)


def build_status(store, counts, evidence_headline, doctrine_version, now_ms):
    rules = store.get("rules", {})
    return {
        "doctrine_version": doctrine_version,
        "last_run_at_ms": int(now_ms),
        "proposals": {"planned": counts["planned"], "applied": counts["applied"],
                      "deferred": counts["deferred"], "rejected": counts["rejected"]},
        "active_rules": {
            "min_hold_hours_before_non_risk_close": rules.get("min_hold_hours_before_non_risk_close"),
            "require_trend_alignment": bool(rules.get("require_trend_alignment")),
            "symbol_cooldowns": sorted((rules.get("symbol_cooldowns") or {}).keys()),
        },
        "evidence_headline": evidence_headline,
    }


def evidence_headline_for(changes):
    """One-line dashboard headline, e.g. 'min_hold=4h from 1 premature close ($48.05)'.

    Priority: min_hold first (the user's canonical evidence), then trend,
    then cooldowns — regardless of plan order.
    """
    order = {"min_hold_hours_before_non_risk_close": 0,
             "require_trend_alignment": 1, "symbol_cooldowns": 2}
    for change in sorted(changes, key=lambda c: order.get(c["rule"], 9)):
        if change["rule"] == "min_hold_hours_before_non_risk_close":
            missed = change.get("estimated_benefit_usd") or 0.0
            return "min_hold={:g}h from premature close(s) (${:.2f} missed)".format(
                float(change["after"]), missed)
        if change["rule"] == "require_trend_alignment":
            return "trend-gate on (against-trend lost ${:.2f})".format(
                abs(float(change.get("estimated_benefit_usd") or 0.0)))
        if change["rule"] == "symbol_cooldowns":
            return "cooldown {} until {}".format(change.get("symbol"), change.get("after"))
    return "no tier-1 changes"


# ---------------------------------------------------------------------------
# doctrine


def render_doctrine(store, props, benefits=None):
    """Short markdown: active rules (with estimated benefit), day stats, learnings.

    `benefits` maps rule name -> (estimated_benefit_usd, "closed_n/opens_n")
    so the doctrine carries the same audited numbers as the changelog.
    """
    benefits = benefits or {}
    rules = store.get("rules", {})
    lines = ["# Trader doctrine (auto-generated by trader.review.apply)",
             "",
             "Do not edit by hand; regenerated after each apply run. Deleting the",
             "rules store returns every behavior to default.",
             "",
             "fee_model: fixed 0.06% maker/taker on all grid fills (KuCoin",
             "Futures Trading Bot schedule; restored 2026-09-13 after the",
             "one-day maker-0.02% experiment of 2026-09-12). Seed/close-out",
             "stay taker at 0.06%; liquidation fee unchanged 0.06%.",
             "Historical bot fees stay as recorded.",
             "",
             "## Active learned rules",
             ""]
    hold = rules.get("min_hold_hours_before_non_risk_close")
    hold_note = ""
    if hold is not None and "min_hold_hours_before_non_risk_close" in benefits:
        amount, sample = benefits["min_hold_hours_before_non_risk_close"]
        hold_note = " (estimated benefit ${:.2f}, sample {})".format(amount, sample)
    lines.append(f"- require_trend_alignment: {'ON' if rules.get('require_trend_alignment') else 'off'}")
    lines.append(f"- min_hold_hours_before_non_risk_close: "
                 f"{f'{hold:g} h' if hold is not None else 'not set'}{hold_note}")
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
    profiles = (props or {}).get("entry_profiles") or {}
    lines += ["", "## What entry profiles actually make money", ""]
    completed = []
    for kind, buckets in (("score", profiles.get("score_quartiles") or {}),
                          ("funding", profiles.get("funding_signs") or {})):
        for name, row in buckets.items():
            closed = int(row.get("closed", 0) or 0)
            if closed:
                wins = int(row.get("wins", 0) or 0)
                completed.append((float(row.get("net", 0.0) or 0.0), kind, name,
                                  closed, wins))
    for net, kind, name, closed, wins in sorted(completed, reverse=True)[:4]:
        lines.append("- {} {}: ${:.2f} net, {}/{} wins".format(
            kind, name, net, wins, closed))
    if not completed:
        lines.append("- no completed trades with persisted entry decisions yet")
    blocked = profiles.get("rule_blocks") or {}
    if blocked:
        top = sorted(blocked.items(), key=lambda item: (-item[1], int(item[0])))[:3]
        lines.append("- most frequent entry blocks: " + ", ".join(
            "code {} x{}".format(code, count) for code, count in top))
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


# ---------------------------------------------------------------------------
# proposals ledger (trackable '0 applied' history)


def rule_text_for(rule, change=None, proposal=None):
    """Short human description of a rule as configured, e.g.
    'min_hold_hours_before_non_risk_close=4' or 'symbol_cooldowns[NEARUSDTM]=7d'."""
    source = change or proposal or {}
    value = source.get("after", source.get("value"))
    if rule == "min_hold_hours_before_non_risk_close" and value is not None:
        return "min_hold_hours_before_non_risk_close={:g}".format(float(value))
    if rule == "require_trend_alignment":
        return "require_trend_alignment={}".format(
            "true" if (change or {}).get("after", True) else "false")
    if rule == "symbol_cooldowns":
        symbol = (change or {}).get("symbol") or (proposal or {}).get("symbol") or "?"
        return "symbol_cooldowns[{}]={}d".format(symbol, COOLDOWN_DAYS)
    if rule and value is not None:
        return "{}={}".format(rule, value)
    return rule


def read_ledger(path):
    """All parseable ledger lines (bad lines skipped); [] when absent."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return [json.loads(line) for line in handle
                    if line.strip() and not line.startswith("#")]
    except (OSError, json.JSONDecodeError):
        return []


def ledger_window(lines, date_str, days=7):
    """Ledger lines with run_date within [date - (days-1), date]."""
    try:
        anchor = date.fromisoformat(date_str)
    except ValueError:
        return []
    earliest = anchor - timedelta(days=days - 1)
    return [line for line in lines
            if isinstance(line, dict) and isinstance(line.get("run_date"), str)
            and earliest.isoformat() <= line["run_date"] <= date_str]
