"""Optional TypeSafe JEV shadow judgments for deterministic radar rows."""

import json
import math
import os
import time
from urllib.request import Request, urlopen


URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
KEY_ENV = "TYPESAFE_API_KEY"
QUESTION_IDS = ("direction", "range_quality", "entry_now", "evidence_sufficient")

QUESTIONS = {
    "direction": {"type": "choice",
        "instructions": "Which grid direction best fits this market state for the next several hours?",
        "criteria": {"LONG": "upward grid bias", "SHORT": "downward grid bias",
                     "NEUTRAL": "two-sided range", "REJECT": "no suitable grid setup"}},
    "range_quality": {"type": "score",
        "instructions": "Rate whether the supplied support/resistance range is likely to contain useful oscillation.",
        "criteria": ["poor", "marginal", "good", "strong"]},
    "entry_now": {"type": "noul",
        "instructions": "Is the current price a suitable entry inside the supplied range now?"},
    "evidence_sufficient": {"type": "noul",
        "instructions": "Does the supplied market state contain sufficient evidence for this judgment?"},
}

STATE_FIELDS = ("symbol", "direction", "price", "range_low", "range_high",
                "support", "resistance", "range_verified", "position_7d",
                "atr_1h_pct", "atr_4h_pct", "slope_4h_pct", "change_24h_pct",
                "turnover_24h_usdt", "spread_pct", "funding_pct",
                "expected_grids_per_hour", "step_pct", "grids", "profit_pct_min",
                "snapshot_age_min")


def state_for(row):
    """Return a compact, numeric radar state without internal scoring prose."""
    return {key: row[key] for key in STATE_FIELDS if key in row}


def _number(value, low=0, high=1):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError("invalid JEV answer")
    return float(value)


def _answer(reply, elapsed_ms):
    if not isinstance(reply, dict) or not isinstance(reply.get("answers"), dict):
        raise ValueError("invalid JEV response")
    answers = reply["answers"]
    if set(answers) != set(QUESTION_IDS):
        raise ValueError("invalid JEV response")
    direction = answers["direction"]
    choice = direction.get("choice")
    probabilities = direction.get("probabilities")
    if choice not in ("LONG", "SHORT", "NEUTRAL", "REJECT") or not isinstance(probabilities, dict):
        raise ValueError("invalid JEV direction")
    direction_probs = {key: _number(probabilities.get(key))
                       for key in ("LONG", "SHORT", "NEUTRAL", "REJECT")}
    quality = answers["range_quality"]
    result = {
        "model": str(reply.get("model") or MODEL)[:64],
        "direction": choice,
        "direction_probabilities": direction_probs,
        "direction_confidence": _number(direction.get("confidence")),
        "range_quality": _number(quality.get("score"), 0, 3),
        "range_confidence": _number(quality.get("confidence")),
        "entry_probability": _number(answers["entry_now"].get("noul")),
        "evidence_probability": _number(answers["evidence_sufficient"].get("noul")),
        "latency_ms": round(elapsed_ms, 1),
        "input_tokens": 0,
    }
    usage = reply.get("usage") or {}
    tokens = usage.get("input_tokens", 0)
    if type(tokens) is int and 0 <= tokens <= 10_000_000:
        result["input_tokens"] = tokens
    return result


def client(api_key, *, opener=urlopen, timeout=6):
    """Build the explicit network evaluator used only by --jev-shadow."""
    if not isinstance(api_key, str) or not api_key:
        raise ValueError("TYPESAFE_API_KEY is required for --jev-shadow")

    def evaluate(state, questions):
        body = json.dumps({"state": state, "model": MODEL, "questions": questions},
                          allow_nan=False).encode()
        request = Request(URL, data=body, method="POST", headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
        })
        started = time.monotonic()
        with opener(request, timeout=timeout) as response:
            raw = response.read(262145)
        if len(raw) > 262144:
            raise ValueError("JEV response too large")
        reply = json.loads(raw)
        reply["_elapsed_ms"] = (time.monotonic() - started) * 1000
        return reply
    return evaluate


def enrich(report, *, evaluator, limit=10):
    """Attach advisory judgments to the highest scored qualifying rows in place."""
    rows = report.get("rows", [])
    sections = report.get("sections") if isinstance(report.get("sections"), dict) else {}
    visible = {item.get("symbol") for key, values in sections.items() if key != "majors"
               for item in values if isinstance(item, dict)}
    pool = [row for row in rows if row.get("passes_liquidity") and row.get("symbol") in visible]
    if not pool:
        pool = [row for row in rows if row.get("passes_liquidity")]
    candidates = sorted(pool,
                        key=lambda row: (-row.get("score", 0), row.get("symbol", "")))[:limit]
    failed = tokens = 0
    for row in candidates:
        try:
            started = time.monotonic()
            reply = evaluator(state_for(row), QUESTIONS)
            elapsed = reply.pop("_elapsed_ms", (time.monotonic() - started) * 1000)
            row["jev"] = _answer(reply, elapsed)
            tokens += row["jev"]["input_tokens"]
        except Exception:
            failed += 1
    report["jev_shadow"] = {"enabled": True, "model": MODEL,
                            "status": "ok" if not failed else "degraded",
                            "attempted": len(candidates),
                            "evaluated": len(candidates) - failed,
                            "failed": failed, "input_tokens": tokens}
    return report


def enrich_from_environment(report, environ=os.environ):
    return enrich(report, evaluator=client(environ.get(KEY_ENV, "")))
