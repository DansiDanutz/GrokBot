"""Daily 7 AM self-learning trade review.

Reads the paper-trading state, event logs and the local market SQLite
read-only, then emits a markdown audit of the trading day: day summary,
exit quality with post-exit counterfactuals, trend alignment, close-reason
breakdown and PROPOSAL-grade learnings. Nothing here mutates state and
nothing auto-applies; every adjustment is a proposal for a human.

Usage:
    python3 -m trader.review.daily --date YYYY-MM-DD --state PATH --db PATH \
        --out PATH [--proposals-out PATH] [--events DIR] [--radar PATH] [--vault-key ops]

With --proposals-out it also emits the machine-readable findings that
trader.review.apply turns into tier-1 learned rules. Pure stdlib + sqlite3.
All data access is read-only and offline.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path

from trader.autopilot.liquidation import estimate as _liquidation_estimate
from trader.review import rules as learned_rules

DAY_MS = 86_400_000
SIX_HOURS_MS = 6 * 3_600_000
NO_TREND_PCT = 0.5
MIN_COVERAGE_PCT = 95.0
EXPECTED_1M_BARS = DAY_MS // 60_000
STOP_OR_RANGE_REASONS = {"STOP_LOSS", "RANGE_BREAK", "RANGE_OUT"}


# ---------------------------------------------------------------------------
# time helpers


def day_window(date_str, tz=None):
    """Return (start_ms, end_ms) covering the local (or given-tz) calendar day."""
    day = datetime.strptime(date_str, "%Y-%m-%d")
    if tz is not None:
        day = day.replace(tzinfo=tz)
    start = int(day.timestamp() * 1000)
    return start, start + DAY_MS


# ---------------------------------------------------------------------------
# state / event loading (supports both flat bots and the nested engine shape)


def load_state(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def bot_field(bot, name, default=None):
    """Fetch a field from a bot dict, tolerating the nested `engine` shape."""
    if name in bot:
        return bot[name]
    engine = bot.get("engine")
    if isinstance(engine, dict) and name in engine:
        return engine[name]
    return default


def all_bots(state):
    for bot in state.get("open_bots", []):
        yield bot
    for bot in state.get("closed_bots", []):
        yield bot


def bot_net(bot):
    """net = realized + unrealized - fees - funding (matches autopilot policy)."""
    if bot_field(bot, "net") is not None:
        return float(bot_field(bot, "net"))
    return (
        float(bot_field(bot, "realized_pnl", 0.0) or 0.0)
        + float(bot_field(bot, "unrealized_pnl", 0.0) or 0.0)
        - float(bot_field(bot, "fees_paid", 0.0) or 0.0)
        - float(bot_field(bot, "funding_paid", 0.0) or 0.0)
    )


def iter_event_files(events_dir):
    directory = Path(events_dir)
    seen = set()
    for path in sorted(directory.glob("events*.jsonl")):
        if path.name in seen:
            continue
        seen.add(path.name)
        yield path


def day_events(events_dir, start_ms, end_ms):
    """Typed events with ts_ms inside [start_ms, end_ms) across all event files."""
    events = []
    for path in iter_event_files(events_dir):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts = event.get("ts_ms")
                    if isinstance(ts, (int, float)) and start_ms <= ts < end_ms:
                        events.append(event)
        except OSError:
            continue
    events.sort(key=lambda e: (e.get("ts_ms", 0), e.get("event_id", 0)))
    return events


# ---------------------------------------------------------------------------
# market data access (read-only)


def open_market_db(path):
    uri = f"file:{Path(path).resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def klines_window(conn, symbol, interval, start_ms, end_ms):
    """Rows (time_ms, open, high, low, close) for [start_ms, end_ms)."""
    rows = conn.execute(
        "SELECT time_ms, open, high, low, close FROM klines "
        "WHERE symbol = ? AND interval = ? AND time_ms >= ? AND time_ms < ? "
        "ORDER BY time_ms",
        (symbol, interval, start_ms, end_ms),
    ).fetchall()
    return [tuple(row) for row in rows]


def data_coverage(conn, symbols, start_ms, end_ms):
    """1m kline coverage for each traded symbol over the reviewed day.

    Coverage < MIN_COVERAGE_PCT for any traded symbol flags the whole day
    LOW-CONFIDENCE: exit counterfactuals and trend buckets rest on those
    klines, so the applier refuses to auto-learn from the day.
    """
    per_symbol = {}
    for symbol in sorted({s for s in symbols if s}):
        count = conn.execute(
            "SELECT COUNT(*) FROM klines WHERE symbol = ? AND interval = '1m' "
            "AND time_ms >= ? AND time_ms < ?",
            (symbol, start_ms, end_ms),
        ).fetchone()[0]
        per_symbol[symbol] = round(count / EXPECTED_1M_BARS * 100.0, 2)
    flagged = any(pct < MIN_COVERAGE_PCT for pct in per_symbol.values())
    return {"flagged": flagged, "per_symbol": per_symbol}


# ---------------------------------------------------------------------------
# exit quality


def grid_interval_of(bot, exit_price):
    interval = bot_field(bot, "grid_interval")
    if interval:
        return float(interval)
    step_pct = bot_field(bot, "step_pct")
    if step_pct and exit_price:
        return float(exit_price) * float(step_pct) / 100.0
    return None


def position_base_units(bot, exit_price):
    """Approximate |position| in base units for would-have-been PnL math.

    Falls back: |position_contracts| x multiplier, then notional/exit price,
    else None (caller skips with a note).
    """
    contracts = abs(float(bot_field(bot, "position_contracts", 0.0) or 0.0))
    multiplier = float(bot_field(bot, "contract_multiplier", 0.0) or 0.0)
    if contracts > 0 and multiplier > 0:
        return contracts * multiplier
    notional = float(bot_field(bot, "notional_usdt", 0.0) or 0.0)
    if notional > 0 and exit_price:
        return notional / float(exit_price)
    return None


def classify_exit(direction, entry, exit_price, grid_interval, net, reason,
                  best_price, worst_price):
    """Classify one close.

    GOOD      stop/range-out reason AND price kept moving adversely >= 0.5 grid.
    PREMATURE negative net at close AND price later recovered beyond entry by
              >= 1 grid interval (closing early cost money).
    NEUTRAL   everything else.
    """
    if grid_interval is None or best_price is None:
        return "NEUTRAL"
    is_stop = reason in STOP_OR_RANGE_REASONS
    if direction == "LONG":
        adverse = worst_price is not None and worst_price <= exit_price - 0.5 * grid_interval
        recovered = entry is not None and best_price >= entry + grid_interval
    elif direction == "SHORT":
        adverse = worst_price is not None and worst_price >= exit_price + 0.5 * grid_interval
        recovered = entry is not None and best_price <= entry - grid_interval
    else:
        return "NEUTRAL"
    if net < 0 and recovered:
        return "PREMATURE"
    if is_stop and adverse:
        return "GOOD"
    return "NEUTRAL"


def risk_metadata_donors(state):
    """Per-symbol risk metadata history {symbol: [(at_ms, mmr, risk_limit), ...]}.

    maintain_margin / risk_limit are per-symbol contract specs, so a bot
    that closed before its first fill (no metadata of its own) can borrow
    them from any same-symbol bot recorded in state. Sorted by at_ms so
    callers can pick the newest entry at or before their evaluation time.
    """
    donors = {}
    for bot in all_bots(state):
        symbol = bot_field(bot, "symbol")
        maintain = bot_field(bot, "maintain_margin")
        risk_limit = bot_field(bot, "risk_limit")
        at = bot_field(bot, "risk_metadata_at_ms")
        if not symbol or maintain is None or not risk_limit or at is None:
            continue
        donors.setdefault(symbol, []).append((int(at), maintain, risk_limit))
    for entries in donors.values():
        entries.sort()
    return donors


def liquidation_band(bot, entry, exit_price, closed_ms, donors=None):
    """Adverse liquidation boundary for the hypothetical hold after close.

    Prefers a stored liquidation dict on the bot; otherwise reconstructs
    the close-time position (leverage x notional base, entry price,
    close-time collateral) and reuses the autopilot's own
    liquidation.estimate — the same math that guards the live engine. Risk
    metadata comes from the bot itself, or the newest same-symbol donor at
    or before the close. Returns {source, status, price, lower_price,
    upper_price} or None.
    """
    liq = bot_field(bot, "liquidation")
    if isinstance(liq, dict) and (liq.get("price") or liq.get("lower_price")
                                  or liq.get("upper_price")):
        return {"source": "state", "status": liq.get("status"),
                "price": liq.get("price"), "lower_price": liq.get("lower_price"),
                "upper_price": liq.get("upper_price")}
    direction = bot_field(bot, "direction")
    if direction not in ("LONG", "SHORT") or not closed_ms:
        return None
    base = position_base_units(bot, exit_price)
    entry_price = float(entry) if entry else None
    maintain = bot_field(bot, "maintain_margin")
    risk_limit = bot_field(bot, "risk_limit")
    symbol = bot_field(bot, "symbol")
    at = bot_field(bot, "risk_metadata_at_ms")
    if maintain is None or not risk_limit or at is None:
        maintain = risk_limit = at = None
        for candidate_at, candidate_mm, candidate_rl in reversed((donors or {}).get(symbol, [])):
            if candidate_at <= closed_ms:
                maintain, risk_limit, at = candidate_mm, candidate_rl, candidate_at
                break
        if maintain is None or not risk_limit or at is None:
            return None
    if (not base or not entry_price or not symbol or at is None
            or maintain is None or not risk_limit):
        return None
    # notional_usdt is margin, not position size: the held position is
    # leverage x notional (the engine sizes it that way).
    leverage = float(bot_field(bot, "leverage", 1.0) or 1.0)
    sign = 1.0 if direction == "LONG" else -1.0
    pseudo = dict(position_contracts=sign * base * leverage, avg_entry=entry_price,
                  notional_usdt=float(bot_field(bot, "notional_usdt", 0.0) or 0.0),
                  reserve_usdt=float(bot_field(bot, "reserve_usdt", 0.0) or 0.0),
                  reserve_added_usdt=float(bot_field(bot, "reserve_added_usdt", 0.0) or 0.0),
                  realized_pnl=float(bot_field(bot, "realized_pnl", 0.0) or 0.0),
                  fees_paid=float(bot_field(bot, "fees_paid", 0.0) or 0.0),
                  funding_paid=float(bot_field(bot, "funding_paid", 0.0) or 0.0),
                  symbol=symbol)
    try:
        result = _liquidation_estimate(pseudo, {"maintainMargin": maintain,
                                                "minRiskLimit": risk_limit, "symbol": symbol},
                                       int(at), int(closed_ms))
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    band = {"source": "estimate", "status": result.get("status"),
            "price": result.get("price"), "lower_price": result.get("lower_price"),
            "upper_price": result.get("upper_price")}
    if not (band["price"] or band["lower_price"] or band["upper_price"]):
        return None
    return band


def _band_touch_ms(rows, direction, band):
    """First 1m candle touching the adverse boundary, or None."""
    if direction == "LONG":
        boundary = band.get("lower_price") or band.get("price")
        if boundary is None:
            return None
        for time_ms, _o, _h, low, _c in rows:
            if low <= boundary:
                return time_ms
    elif direction == "SHORT":
        boundary = band.get("upper_price") or band.get("price")
        if boundary is None:
            return None
        for time_ms, _o, high, _l, _c in rows:
            if high >= boundary:
                return time_ms
    return None


def analyze_exit(bot, conn, now_ms=None, open_price=None, donors=None):
    """Counterfactual analysis for one closed bot.

    Looks at 1m klines over the 6h window after closed_ms: best achievable
    m2m price, worst adverse excursion and the price at +6h. `open_price`
    (e.g. from the OPEN event) is a fallback entry when the state has none.
    """
    direction = bot_field(bot, "direction")
    symbol = bot_field(bot, "symbol")
    reason = bot_field(bot, "reason") or "UNKNOWN"
    opened_ms = bot_field(bot, "opened_ms")
    closed_ms = bot_field(bot, "closed_ms")
    entry = bot_field(bot, "opening_price") or bot_field(bot, "price") or open_price
    exit_price = bot_field(bot, "last_price") or bot_field(bot, "price")
    net = bot_net(bot)
    result = {
        "bot_id": bot_field(bot, "bot_id"),
        "symbol": symbol,
        "direction": direction,
        "reason": reason,
        "opened_ms": opened_ms,
        "closed_ms": closed_ms,
        "entry": entry,
        "exit": exit_price,
        "net": net,
        "best_price": None,
        "worst_price": None,
        "price_6h": None,
        "would_have_been_pnl": None,
        "window_complete": False,
        "classification": "NEUTRAL",
        "liq_band": None,
        "liq_touch_ms": None,
        "note": "",
    }
    if not symbol or not closed_ms or not exit_price:
        result["note"] = "missing symbol/closed_ms/exit price"
        return result
    if direction not in ("LONG", "SHORT"):
        result["note"] = f"direction {direction!r}: counterfactual skipped"
        return result
    rows = klines_window(conn, symbol, "1m", closed_ms, closed_ms + SIX_HOURS_MS)
    if not rows:
        result["note"] = "no 1m klines for symbol after close"
        return result
    highs = [r[2] for r in rows]
    lows = [r[3] for r in rows]
    result["best_price"] = max(highs) if direction == "LONG" else min(lows)
    result["worst_price"] = min(lows) if direction == "LONG" else max(highs)
    result["price_6h"] = rows[-1][4]
    result["window_complete"] = rows[-1][0] + 60_000 >= closed_ms + SIX_HOURS_MS
    base = position_base_units(bot, exit_price)
    interval = grid_interval_of(bot, exit_price)
    result["grid_interval"] = interval
    notes = []
    if base is None:
        result["note"] = "position size not recoverable; PnL skipped"
        return result
    sign = 1.0 if direction == "LONG" else -1.0
    result["would_have_been_pnl"] = sign * (result["best_price"] - exit_price) * base
    result["classification"] = classify_exit(
        direction, entry, exit_price, interval, net, reason,
        result["best_price"], result["worst_price"],
    )
    if result["classification"] == "PREMATURE":
        # Liquidation-aware counterfactual: the missed profit is only real
        # if holding would NOT have hit the adverse liquidation boundary.
        band = liquidation_band(bot, entry, exit_price, closed_ms, donors)
        result["liq_band"] = band
        if band is None:
            notes.append("liq unavailable (exit risk unverifiable)")
        else:
            if band.get("status") not in ("ESTIMATED", "HEDGE_ESTIMATED", None):
                notes.append(f"liq status {band['status']}")
            touch = _band_touch_ms(rows, direction, band)
            if touch is not None:
                result["classification"] = "RISKY_HOLD"
                result["liq_touch_ms"] = touch
                elapsed = touch - closed_ms
                notes.append("liq band touched at +{}h{:02d}m — hold was not safe".format(
                    elapsed // 3_600_000, (elapsed % 3_600_000) // 60_000))
    if (bot_field(bot, "opening_price") is None and bot_field(bot, "price") is None
            and open_price is not None):
        notes.append("entry taken from OPEN event (state has none)")
    result["note"] = "; ".join(notes)
    return result


# ---------------------------------------------------------------------------
# trend alignment


def _close_at_or_before(rows, time_ms):
    close = None
    for row in rows:
        if row[0] <= time_ms:
            close = row[4]
        else:
            break
    return close


def trend_at(conn, symbol, opened_ms):
    """(bucket_signal, ret_24h_pct, ema_slope_4h) at open time from 1h klines.

    bucket_signal is +1/-1/0 (NO-TREND when |24h ret| < 0.5%). ema_slope_4h is
    the EMA(20) difference between open time and 4h earlier (sign matters).
    """
    rows = klines_window(conn, symbol, "1h", opened_ms - 25 * 3_600_000, opened_ms + 3_600_000)
    if len(rows) < 2:
        return None, None, None
    now_close = _close_at_or_before(rows, opened_ms)
    past_close = _close_at_or_before(rows, opened_ms - 24 * 3_600_000)
    if now_close is None or past_close in (None, 0):
        return None, None, None
    ret_pct = (now_close - past_close) / past_close * 100.0
    closes = [r[4] for r in rows if r[0] <= opened_ms]
    k = 2.0 / (20.0 + 1.0)

    def ema_at(cutoff):
        window = [c for t, o, h, l, c in rows if t <= cutoff]
        if not window:
            return None
        ema = window[0]
        for c in window[1:]:
            ema = c * k + ema * (1 - k)
        return ema

    ema_now = ema_at(opened_ms)
    ema_past = ema_at(opened_ms - 4 * 3_600_000)
    slope = None if ema_now is None or ema_past is None else ema_now - ema_past
    signal = 0 if abs(ret_pct) < NO_TREND_PCT else (1 if ret_pct > 0 else -1)
    return signal, ret_pct, slope


def trend_bucket(direction, signal):
    if direction == "NEUTRAL" or direction is None:
        return "NEUTRAL-BOT"
    if signal is None:
        return "NO-DATA"
    if signal == 0:
        return "NO-TREND"
    wants = 1 if direction == "LONG" else -1
    return "WITH-TREND" if signal == wants else "AGAINST-TREND"


# ---------------------------------------------------------------------------
# report assembly


def build_report(date_str, state, conn, events, radar=None, vault_key=None, now_ms=None,
                 tz=None):
    start_ms, end_ms = day_window(date_str, tz=tz)
    opened = [b for b in all_bots(state)
              if bot_field(b, "opened_ms") and start_ms <= bot_field(b, "opened_ms") < end_ms]
    closed = [b for b in state.get("closed_bots", [])
              if bot_field(b, "closed_ms") and start_ms <= bot_field(b, "closed_ms") < end_ms]

    errors = {}
    for event in events:
        if event.get("type") == "ERROR":
            errors[event.get("code")] = errors.get(event.get("code"), 0) + 1

    open_prices = {e.get("bot_id"): e.get("price") for e in events
                   if e.get("type") == "OPEN" and e.get("price") is not None}
    donors = risk_metadata_donors(state)

    exits = []
    for bot in closed:
        analysis = analyze_exit(bot, conn, now_ms=now_ms,
                                open_price=open_prices.get(bot_field(bot, "bot_id")),
                                donors=donors)
        analysis["ret_24h_pct"] = None
        analysis["ema_slope_4h"] = None
        exits.append(analysis)

    trend_rows = {}
    trend_symbols = {}
    for bot in opened:
        symbol = bot_field(bot, "symbol")
        opened_ms = bot_field(bot, "opened_ms")
        direction = bot_field(bot, "direction")
        signal, ret_pct, slope = (None, None, None)
        if symbol and opened_ms:
            signal, ret_pct, slope = trend_at(conn, symbol, opened_ms)
        bucket = trend_bucket(direction, signal)
        row = trend_rows.setdefault(bucket, {"bots": 0, "net": 0.0, "grids": 0})
        row["bots"] += 1
        row["net"] += bot_net(bot)
        row["grids"] += int(bot_field(bot, "completed_grids", 0) or 0)
        if symbol:
            per = trend_symbols.setdefault((bucket, symbol), {"n": 0, "net": 0.0})
            per["n"] += 1
            per["net"] += bot_net(bot)

    by_reason = {}
    for bot in closed:
        reason = bot_field(bot, "reason") or "UNKNOWN"
        row = by_reason.setdefault(reason, {"bots": 0, "net": 0.0, "fees": 0.0, "funding": 0.0, "grids": 0})
        row["bots"] += 1
        row["net"] += bot_net(bot)
        row["fees"] += float(bot_field(bot, "fees_paid", 0.0) or 0.0)
        row["funding"] += float(bot_field(bot, "funding_paid", 0.0) or 0.0)
        row["grids"] += int(bot_field(bot, "completed_grids", 0) or 0)

    impatience = [e for e in exits
                  if e["reason"] not in STOP_OR_RANGE_REASONS and e["net"] is not None and e["net"] < 0]

    premature = [e for e in exits if e["classification"] == "PREMATURE"]
    premature_costs = [e["would_have_been_pnl"] - e["net"]
                       for e in premature if e["would_have_been_pnl"] is not None]

    data = {
        "date": date_str,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "opened": opened,
        "closed": closed,
        "exits": exits,
        "errors": errors,
        "trend_rows": trend_rows,
        "trend_symbols": trend_symbols,
        "by_reason": by_reason,
        "impatience": impatience,
        "premature": premature,
        "premature_costs": premature_costs,
        # Cumulative significance input for the min-sample gate: bots closed
        # total, as visible in the retained state (archived bots predate the
        # retention window and are not countable here).
        "totals": {"closed_total": len(state.get("closed_bots", []))},
        "data_coverage": data_coverage(
            conn,
            [bot_field(b, "symbol") for b in opened + closed],
            start_ms, end_ms),
        "vault_key": vault_key,
        "radar": radar,
    }
    proposals = derive_proposals(data)
    data["learnings"] = [p["evidence"] for p in proposals]
    data["proposals"] = proposals
    return data


def derive_proposals(data):
    """Structured findings: tier-1 (auto-appliable) + tier-2 (advisory).

    Tier-1 conditions live in trader.review.rules.plan_changes so the
    markdown, the JSON proposals, and the applier always agree. Tier-2
    entries are evidence-only and never auto-apply.
    """
    planned = learned_rules.plan_changes(build_proposals(data, []), {}, data["date"])
    proposals = [dict(p, tier=1, text=p["evidence"]) for p in planned]
    tier2 = []
    impatience = data["impatience"]
    if impatience:
        total = sum(e["net"] for e in impatience)
        whb = sum(e["would_have_been_pnl"] for e in impatience
                  if e["would_have_been_pnl"] is not None)
        tier2.append({
            "rule": "avoid_impatience_closes",
            "evidence": ("{} non-stop-loss/non-range-out close(s) finished negative "
                         "(net ${:.2f}, would-have-been at best ${:.2f}) — only close negative "
                         "bots on stop-loss trigger or range-out.").format(
                             len(impatience), total, whb)})
    closed = data["closed"]
    by_reason = data["by_reason"]
    if len(closed) >= 3 and len(by_reason) == 1:
        tier2.append({
            "rule": "review_exit_calibration",
            "evidence": "All {} close(s) exited via {} — check range width / stop "
                        "calibration against current volatility.".format(
                            len(closed), next(iter(by_reason)))})
    if closed:
        wins = sum(1 for b in closed if bot_net(b) > 0)
        gross = sum(abs(bot_net(b)) for b in closed)
        fees = sum(float(bot_field(b, "fees_paid", 0.0) or 0.0)
                   + float(bot_field(b, "funding_paid", 0.0) or 0.0) for b in closed)
        if gross > 0 and fees / gross > 0.2:
            tier2.append({
                "rule": "review_fee_drag",
                "evidence": "Fees+funding were {:.0f}% of gross |net| (${:.2f}) — "
                            "completed grids must clear fee drag.".format(
                                fees / gross * 100.0, fees)})
        elif wins * 2 < len(closed):
            grids = sum(int(bot_field(b, "completed_grids", 0) or 0) for b in closed)
            total = sum(bot_net(b) for b in closed)
            if total > 0:
                tier2.append({
                    "rule": "review_entry_direction_quality",
                    "evidence": "Win rate {}/{} yet the day closed green — grid profits "
                                "({} grids) offset directional losses; keep throughput high "
                                "but review entry direction.".format(wins, len(closed), grids)})
            else:
                tier2.append({
                    "rule": "tighten_entry_filtering",
                    "evidence": "Win rate only {}/{} and net ${:.2f} — directional losses "
                                "exceeded grid profits ({} grids); tighten entry filtering "
                                "(trend/radar agreement).".format(wins, len(closed), total, grids)})
    no_trend = data["trend_rows"].get("NO-TREND")
    if no_trend and no_trend["bots"] >= 2 and no_trend["grids"] / max(no_trend["bots"], 1) < 1:
        tier2.append({
            "rule": "prefer_trending_movers",
            "evidence": "NO-TREND bots completed almost no grids ({:.1f}/bot) — prefer "
                        "trending movers from radar.".format(
                            no_trend["grids"] / no_trend["bots"])})
    if data["errors"]:
        codes = ", ".join(f"code {k} x{v}" for k, v in
                          sorted(data["errors"].items(), key=lambda kv: str(kv[0])))
        tier2.append({
            "rule": "check_collector_health",
            "evidence": f"{sum(data['errors'].values())} ERROR event(s) ({codes}) — "
                        "check the collector/recovery path before scaling slots."})
    proposals.extend(dict(p, tier=2, text=p["evidence"]) for p in tier2[:max(0, 6 - len(proposals))])
    return proposals


def build_proposals(data, proposals=None):
    """Machine-readable findings dict consumed by trader.review.apply."""
    premature = [{"bot_id": e["bot_id"], "symbol": e["symbol"], "direction": e["direction"],
                  "close_reason": e["reason"], "net": e["net"],
                  "missed_usd": round(e["would_have_been_pnl"] - e["net"], 4),
                  "would_have_been_best_usd": round(e["would_have_been_pnl"], 4)}
                 for e in data["premature"] if e["would_have_been_pnl"] is not None]

    def bucket(name):
        row = data["trend_rows"].get(name)
        return {"n": row["bots"], "net": round(row["net"], 4), "grids": row["grids"]} if row \
            else {"n": 0, "net": 0.0, "grids": 0}

    neutral = {"n": 0, "net": 0.0, "grids": 0}
    for name in ("NO-TREND", "NEUTRAL-BOT", "NO-DATA"):
        row = data["trend_rows"].get(name)
        if row:
            neutral["n"] += row["bots"]
            neutral["net"] += row["net"]
            neutral["grids"] += row["grids"]
    neutral["net"] = round(neutral["net"], 4)

    against_symbols = {symbol: {"n": per["n"], "net": round(per["net"], 4)}
                       for (bucket_name, symbol), per in data["trend_symbols"].items()
                       if bucket_name == "AGAINST-TREND"}

    close_reasons = {}
    for reason, row in data["by_reason"].items():
        holds = []
        for bot in data["closed"]:
            if (bot_field(bot, "reason") or "UNKNOWN") == reason:
                opened_ms, closed_ms = bot_field(bot, "opened_ms"), bot_field(bot, "closed_ms")
                if opened_ms and closed_ms:
                    holds.append((closed_ms - opened_ms) / 1000.0)
        close_reasons[reason] = {
            "n": row["bots"], "total_net": round(row["net"], 4),
            "avg_hold_s": round(statistics.mean(holds), 1) if holds else None}

    closed = data["closed"]
    total_net = sum(bot_net(b) for b in closed)
    return {
        "date": data["date"],
        "summary": {"opened": len(data["opened"]), "closed": len(closed),
                    "net": round(total_net, 4),
                    "premature": len(data["premature"]),
                    "missed_usd": round(sum(data["premature_costs"]), 4)},
        "totals": {"closed_total": int(data.get("totals", {}).get("closed_total", 0)),
                   "data_coverage": data.get("data_coverage")},
        "premature_closes": premature,
        "trend_buckets": {"with_trend": bucket("WITH-TREND"),
                          "against_trend": bucket("AGAINST-TREND"),
                          "neutral": neutral},
        "against_trend_symbols": against_symbols,
        "close_reasons": close_reasons,
        "errors": {str(code): count for code, count in data["errors"].items()},
        "proposals": [{"rule": p["rule"], "tier": p["tier"], "evidence": p["evidence"]}
                      for p in (proposals if proposals is not None else data.get("proposals", []))],
    }


# ---------------------------------------------------------------------------
# markdown rendering


def _fmt(value, digits=2):
    if value is None:
        return "n/a"
    if isinstance(value, float):
        if digits >= 6:
            return f"{value:.{digits}g}"
        return f"{value:.{digits}f}"
    return str(value)


def _hours(ms):
    return None if ms is None else ms / 3_600_000.0


def render_markdown(data):
    lines = []
    lines.append(f"# Daily trade review — {data['date']}")
    lines.append("")
    lines.append(f"- Window: `{data['start_ms']}` → `{data['end_ms']}` (local midnight→midnight)")
    lines.append(f"- Bots opened: **{len(data['opened'])}**, closed: **{len(data['closed'])}**")
    if data.get("vault_key"):
        lines.append(f"- Vault key `{data['vault_key']}` supplied — publication handled by launcher (not written here)")
    coverage = data.get("data_coverage") or {}
    if coverage.get("flagged"):
        worst = sorted(coverage["per_symbol"].items(), key=lambda kv: kv[1])[:3]
        detail = ", ".join(f"{sym} {pct:.1f}%" for sym, pct in worst)
        lines.append(f"- ⚠ **LOW-CONFIDENCE DAY — 1m data coverage below {MIN_COVERAGE_PCT:.0f}%** "
                     f"for: {detail}. Exit counterfactuals and trend buckets rest on incomplete "
                     "data; auto-learning is disabled for this day.")
    lines.append("")
    lines.append("> All learnings below are PROPOSAL-grade. Nothing auto-applies.")

    lines.append("")
    lines.append("## 1. Day summary")
    lines.append("")
    closed = data["closed"]
    if closed:
        total_realized = sum(float(bot_field(b, "realized_pnl", 0.0) or 0.0) for b in closed)
        total_fees = sum(float(bot_field(b, "fees_paid", 0.0) or 0.0) for b in closed)
        total_funding = sum(float(bot_field(b, "funding_paid", 0.0) or 0.0) for b in closed)
        total_net = sum(bot_net(b) for b in closed)
        wins = sum(1 for b in closed if bot_net(b) > 0)
        holds = [_hours(bot_field(b, "closed_ms") - bot_field(b, "opened_ms")) for b in closed
                 if bot_field(b, "opened_ms") and bot_field(b, "closed_ms")]
        avg_hold = statistics.mean(holds) if holds else None
        lines.append(f"- Closed bots: **{len(closed)}** (opened that day: {len(data['opened'])})")
        lines.append(f"- Total realized: **${_fmt(total_realized)}**, "
                     f"fees: ${total_fees:.2f}, funding: ${total_funding:.2f}")
        lines.append(f"- Net (realized+unrealized-fees-funding): **${_fmt(total_net)}**")
        lines.append(f"- Win rate: **{wins}/{len(closed)}** ({wins / len(closed) * 100:.0f}%)")
        lines.append(f"- Avg hold time: **{_fmt(avg_hold, 1)} h**")
    else:
        lines.append("- No bots closed on this day.")
    if data["errors"]:
        pretty = ", ".join(f"code {k}: {v}" for k, v in sorted(data["errors"].items(), key=lambda kv: str(kv[0])))
        lines.append(f"- ERROR events: **{pretty}**")
    else:
        lines.append("- ERROR events: none")

    lines.append("")
    lines.append("## 2. Exit quality (6h post-exit counterfactual)")
    lines.append("")
    lines.append("Counterfactual from 1m klines over the 6h window after each close: "
                 "`best` = most favourable m2m price, `worst` = adverse extreme, `+6h` = last close. "
                 "Would-have-been PnL uses the approximated position size.")
    lines.append("")
    lines.append("| bot | symbol | dir | reason | entry | exit | net | best | +6h | would-have-been | class |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for e in data["exits"]:
        marker = "⚠ " if e["classification"] == "RISKY_HOLD" else ""
        lines.append(
            "| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {}{} |".format(
                e["bot_id"], e["symbol"], e["direction"], e["reason"],
                _fmt(e["entry"], 8), _fmt(e["exit"], 8), _fmt(e["net"]),
                _fmt(e["best_price"], 8), _fmt(e["price_6h"], 8),
                _fmt(e["would_have_been_pnl"]), marker, e["classification"]))
    counts = {"GOOD": 0, "PREMATURE": 0, "NEUTRAL": 0, "RISKY_HOLD": 0}
    for e in data["exits"]:
        counts[e["classification"]] = counts.get(e["classification"], 0) + 1
    missed = sum(data["premature_costs"])
    lines.append("")
    lines.append(f"- GOOD: {counts.get('GOOD', 0)}, PREMATURE: {counts.get('PREMATURE', 0)}, "
                 f"RISKY_HOLD: {counts.get('RISKY_HOLD', 0)}, NEUTRAL: {counts.get('NEUTRAL', 0)}")
    lines.append(f"- Missed opportunity across PREMATURE exits: **${_fmt(missed)}** "
                 "(RISKY_HOLD exits excluded — the profit was not safely capturable)")
    notes = [f"bot {e['bot_id']} ({e['symbol']}): {e['note']}" for e in data["exits"] if e.get("note")]
    if notes:
        lines.append("- Notes: " + "; ".join(notes))

    lines.append("")
    lines.append("## 3. Trend alignment at open")
    lines.append("")
    lines.append("Signal from 1h klines at each OPEN: 24h return sign (NO-TREND when "
                 f"|24h ret| < {NO_TREND_PCT}%) plus EMA(20) 4h slope. Thesis under test: "
                 "grids should trade WITH trend.")
    lines.append("")
    lines.append("| bucket | bots | net | completed grids |")
    lines.append("|---|---|---|---|")
    for bucket in ("WITH-TREND", "AGAINST-TREND", "NO-TREND", "NEUTRAL-BOT", "NO-DATA"):
        row = data["trend_rows"].get(bucket)
        if row:
            lines.append(f"| {bucket} | {row['bots']} | {_fmt(row['net'])} | {row['grids']} |")

    lines.append("")
    lines.append("## 4. Close-reason breakdown")
    lines.append("")
    lines.append("| reason | bots | net | fees | funding | grids |")
    lines.append("|---|---|---|---|---|---|")
    for reason, row in sorted(data["by_reason"].items()):
        lines.append(f"| {reason} | {row['bots']} | {_fmt(row['net'])} | {_fmt(row['fees'])} | {_fmt(row['funding'])} | {row['grids']} |")
    lines.append("")
    if data["impatience"]:
        lines.append("**Candidate impatience closes** (not stop-loss, not range-out, negative net):")
        lines.append("")
        for e in data["impatience"]:
            lines.append(
                "- bot {} ({} {}): net ${}, would-have-been at best ${}".format(
                    e["bot_id"], e["symbol"], e["direction"],
                    _fmt(e["net"]), _fmt(e["would_have_been_pnl"])))
    else:
        lines.append("No candidate impatience closes.")

    lines.append("")
    lines.append("## 5. Learnings + proposed adjustments")
    lines.append("")
    if data["proposals"]:
        for proposal in data["proposals"]:
            tag = "TIER-1 (auto-applies via trader.review.apply)" if proposal["tier"] == 1 \
                else "TIER-2 (advisory only)"
            lines.append(f"- [{tag}] {proposal['evidence']}")
    else:
        lines.append("- Insufficient data for a rule proposal today.")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI


def default_path(*parts):
    return str(Path.home().joinpath(*parts))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="trader.review.daily",
                                     description="Daily self-learning trade review")
    parser.add_argument("--date", required=True, help="YYYY-MM-DD (local day)")
    parser.add_argument("--state", default=default_path("Sandbox", "grokbot", "autopilot", "state.json"))
    parser.add_argument("--db", default=default_path("Sandbox", "grokbot", "market-data",
                                                   "phase-2-20260911", "market.sqlite3"))
    parser.add_argument("--events", default=None,
                        help="directory with events*.jsonl (default: state file's directory)")
    parser.add_argument("--radar", default=None, help="optional radar.json (read-only)")
    parser.add_argument("--out", required=True, help="markdown output path")
    parser.add_argument("--proposals-out", default=None,
                        help="optional path for the machine-readable proposals JSON")
    parser.add_argument("--vault-key", default=None,
                        help="recorded in the report; the launcher owns vault publication")
    args = parser.parse_args(argv)

    try:
        datetime.strptime(args.date, "%Y-%m-%d")
    except ValueError:
        parser.error(f"--date must be YYYY-MM-DD, got {args.date!r}")

    state = load_state(args.state)
    events_dir = args.events or str(Path(args.state).resolve().parent)
    start_ms, end_ms = day_window(args.date)
    events = day_events(events_dir, start_ms, end_ms)
    radar = None
    if args.radar:
        with open(args.radar, "r", encoding="utf-8") as handle:
            radar = json.load(handle)
    conn = open_market_db(args.db)
    try:
        data = build_report(args.date, state, conn, events, radar=radar,
                            vault_key=args.vault_key)
    finally:
        conn.close()
    markdown = render_markdown(data)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(markdown, encoding="utf-8")
    if args.proposals_out:
        proposals_path = Path(args.proposals_out)
        proposals_path.parent.mkdir(parents=True, exist_ok=True)
        proposals_path.write_text(json.dumps(build_proposals(data, data["proposals"]), indent=2) + "\n",
                                 encoding="utf-8")

    # compact stdout summary for the 7 AM job log
    closed = data["closed"]
    total_net = sum(bot_net(b) for b in closed)
    errors = sum(data["errors"].values())
    missed = sum(data["premature_costs"])
    print(f"[daily-review] {args.date}: opened={len(data['opened'])} closed={len(closed)} "
          f"net=${total_net:.2f} premature={len(data['premature'])} missed=${missed:.2f} "
          f"errors={errors} -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
