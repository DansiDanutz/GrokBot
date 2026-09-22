"""Optional TypeSafe JEV shadow judgments for deterministic radar rows.

Every field written under ``row["jev"]`` is advisory telemetry for the shadow
period described in docs/JEV-TYPESAFE-INTEGRATION.md. Nothing in the radar,
the autopilot policy or the paper executor reads ``abstain``, ``margin`` or
``noul_confidence`` for ordering, sizing or admission; they exist so recorded
outcomes can be calibrated later. A JEV answer never becomes an exchange order
or an authorization.
"""

import json
import math
import os
import sys
import time
from urllib.request import Request, urlopen


URL = "https://api.typesafe.ai/v1/systemone"
# Pinned on purpose: the abstention thresholds below were tuned against this
# model version. Bumping the alias without re-tuning silently changes what
# "abstain" means, so treat MODEL and the ABSTAIN_* constants as one unit.
MODEL = "jev-1.13.0"
KEY_ENV = "TYPESAFE_API_KEY"
QUESTION_IDS = ("direction", "range_quality", "entry_now", "evidence_sufficient")
DIRECTION_CRITERIA = ("LONG", "SHORT", "NEUTRAL", "REJECT")
RANGE_QUALITY_LEGEND = ("poor", "marginal", "good", "strong")
PROBABILITY_SUM_TOLERANCE = 0.02
ABSTAIN_EVIDENCE_FLOOR = 0.5
ABSTAIN_MARGIN_FLOOR = 0.15
LOG_PURPOSE = "radar_shadow_judgment"
DOWNSTREAM_ACTION = "shadow_only"

QUESTIONS = {
    "direction": {"type": "choice",
        "instructions": "Which grid direction best fits this market state for the next several hours?",
        "criteria": dict(zip(DIRECTION_CRITERIA, (
            "upward grid bias", "downward grid bias",
            "two-sided range", "no suitable grid setup")))},
    "range_quality": {"type": "score",
        "instructions": "Rate whether the supplied support/resistance range is likely to contain useful oscillation.",
        "criteria": list(RANGE_QUALITY_LEGEND)},
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


def _number(value, name, low=0, high=1):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"invalid JEV answer: {name} must be finite in [{low}, {high}]")
    return float(value)


def _direction(answer):
    if not isinstance(answer, dict):
        raise ValueError("invalid JEV answer: direction")
    choice = answer.get("choice")
    probabilities = answer.get("probabilities")
    if choice not in DIRECTION_CRITERIA or not isinstance(probabilities, dict):
        raise ValueError("invalid JEV answer: direction choice outside criteria")
    if set(probabilities) != set(DIRECTION_CRITERIA):
        raise ValueError("invalid JEV answer: direction probabilities must cover exactly the criteria")
    probs = {key: _number(probabilities[key], f"direction.probabilities.{key}")
             for key in DIRECTION_CRITERIA}
    if abs(sum(probs.values()) - 1) > PROBABILITY_SUM_TOLERANCE:
        raise ValueError("invalid JEV answer: direction probabilities must sum to 1")
    ordered = sorted(probs.values(), reverse=True)
    if probs[choice] != ordered[0]:
        raise ValueError("invalid JEV answer: direction choice must be the argmax")
    return choice, probs, _number(answer.get("confidence"), "direction.confidence"), ordered[0] - ordered[1]


def validate_answers(payload):
    """Strict codec: normalise a TypeSafe reply or raise ValueError with the reason."""
    if not isinstance(payload, dict) or not isinstance(payload.get("answers"), dict):
        raise ValueError("invalid JEV response: answers missing")
    answers = payload["answers"]
    missing = [key for key in QUESTION_IDS if not isinstance(answers.get(key), dict)]
    if missing or set(answers) != set(QUESTION_IDS):
        raise ValueError("invalid JEV response: expected exactly the question ids "
                         + ", ".join(QUESTION_IDS))
    choice, probs, direction_confidence, margin = _direction(answers["direction"])
    top = len(RANGE_QUALITY_LEGEND) - 1
    quality = _number(answers["range_quality"].get("score"), "range_quality.score", 0, top)
    entry = _number(answers["entry_now"].get("noul"), "entry_now.noul")
    evidence = _number(answers["evidence_sufficient"].get("noul"), "evidence_sufficient.noul")
    return {
        "status": "ok",
        "model": str(payload.get("model") or MODEL)[:64],
        "direction": choice,
        "direction_probabilities": probs,
        "direction_confidence": direction_confidence,
        "margin": round(margin, 6),
        "range_quality": quality,
        "range_quality_label": RANGE_QUALITY_LEGEND[min(top, int(round(quality)))],
        "range_confidence": _number(answers["range_quality"].get("confidence"),
                                    "range_quality.confidence"),
        "entry_probability": entry,
        "evidence_probability": evidence,
        "noul_confidence": {"entry_now": _noul_confidence(entry),
                            "evidence_sufficient": _noul_confidence(evidence)},
        "abstain": (evidence < ABSTAIN_EVIDENCE_FLOOR or margin < ABSTAIN_MARGIN_FLOOR
                    or choice == "REJECT"),
        "input_tokens": _input_tokens(payload),
    }


def _noul_confidence(probability):
    return round(min(1.0, abs(probability - 0.5) * 2), 6)


def _input_tokens(payload):
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    tokens = usage.get("input_tokens", 0)
    return tokens if type(tokens) is int and 0 <= tokens <= 10_000_000 else 0


def _answer(reply, elapsed_ms):
    return {**validate_answers(reply), "latency_ms": round(elapsed_ms, 1)}


def _log_line(symbol, *, latency_ms, answer=None, fallback_reason=None, input_tokens=0):
    """Structured per-call record. Carries no credential or raw header material."""
    return {
        "purpose": LOG_PURPOSE,
        "model": MODEL,
        "symbol": symbol,
        "latency_ms": round(latency_ms, 1),
        "input_tokens": input_tokens,
        "answer_distribution": None if answer is None else {
            "direction": answer["direction_probabilities"],
            "range_quality": answer["range_quality"],
            "entry_now": answer["entry_probability"],
            "evidence_sufficient": answer["evidence_probability"]},
        "confidence": None if answer is None else {
            "direction": answer["direction_confidence"],
            "range_quality": answer["range_confidence"],
            "abstain": answer["abstain"],
            "margin": answer["margin"]},
        "fallback_reason": fallback_reason,
        "downstream_action": DOWNSTREAM_ACTION,
    }


def _stderr_log(event):
    print(json.dumps(event, sort_keys=True, allow_nan=False), file=sys.stderr, flush=True)


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


def enrich(report, *, evaluator, limit=10, log=_stderr_log):
    """Attach advisory judgments to the highest scored qualifying rows in place.

    Ranking, sections and every deterministic field are left untouched. A reply
    that fails ``validate_answers`` marks the row ``{"status": "invalid"}``; a
    transport failure leaves the row without ``jev`` at all. Neither path raises.
    """
    rows = report.get("rows", [])
    sections = report.get("sections") if isinstance(report.get("sections"), dict) else {}
    visible = {item.get("symbol") for key, values in sections.items() if key != "majors"
               for item in values if isinstance(item, dict)}
    pool = [row for row in rows if row.get("passes_liquidity") and row.get("symbol") in visible]
    if not pool:
        pool = [row for row in rows if row.get("passes_liquidity")]
    candidates = sorted(pool,
                        key=lambda row: (-row.get("score", 0), row.get("symbol", "")))[:limit]
    failed = invalid = tokens = 0
    for row in candidates:
        symbol = row.get("symbol")
        started = time.monotonic()
        try:
            reply = evaluator(state_for(row), QUESTIONS)
        except Exception as error:
            failed += 1
            log(_log_line(symbol, latency_ms=(time.monotonic() - started) * 1000,
                          fallback_reason=type(error).__name__))
            continue
        elapsed = (reply.pop("_elapsed_ms", None) if isinstance(reply, dict) else None)
        elapsed = elapsed if elapsed is not None else (time.monotonic() - started) * 1000
        try:
            answer = _answer(reply, elapsed)
        except ValueError:
            invalid += 1
            row["jev"] = {"status": "invalid"}
            log(_log_line(symbol, latency_ms=elapsed, fallback_reason="invalid_payload",
                          input_tokens=_input_tokens(reply) if isinstance(reply, dict) else 0))
            continue
        row["jev"] = answer
        tokens += answer["input_tokens"]
        log(_log_line(symbol, latency_ms=elapsed, answer=answer,
                      input_tokens=answer["input_tokens"]))
    report["jev_shadow"] = {"enabled": True, "model": MODEL,
                            "status": "ok" if not (failed or invalid) else "degraded",
                            "attempted": len(candidates),
                            "evaluated": len(candidates) - failed - invalid,
                            "failed": failed, "invalid": invalid, "input_tokens": tokens}
    return report


def enrich_from_environment(report, environ=os.environ):
    return enrich(report, evaluator=client(environ.get(KEY_ENV, "")))
