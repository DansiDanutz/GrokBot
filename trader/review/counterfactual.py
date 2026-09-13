"""Replay the entries the desk declined for capacity or policy reasons.

trader.autopilot.counterfactual records, per scan, the exact bot specification
the desk would have opened for every structurally valid candidate it skipped
because it had no room (bot/direction/major/movers caps, duplicate symbol,
cooldown) or because a learned rule said no. This module opens each of those
specs on the real paper engine, walks it over the stored 1m candles for a fixed
horizon and closes it on the live rules — first RANGE_BREAK or STOP_LOSS, else
the horizon — so the day's report can price what those refusals cost or saved.

This is measurement, not a claim of edge: fills come from 1m snapshots, the
horizon is arbitrary, and a replayed bot never competed for capital with the
bots that actually ran. See docs/counterfactual.md.

Usage:
    python3 -m trader.review.counterfactual --database PATH --candidates-dir DIR \
        --date YYYY-MM-DD --out PATH [--horizon-hours 24]

Pure stdlib + sqlite3. All data access is read-only and offline.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
import time
from contextlib import closing
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from trader.autopilot.counterfactual import FILE_PREFIX, FILE_SUFFIX
from trader.autopilot.policy import DECISION_RULES, net as bot_net
from trader.data.store import _safe_path
from trader.papergrid.engine import close_bot, open_bot, step
from trader.review.rules import atomic_write_json

SCHEMA_VERSION = 1
MINUTE_MS = 60_000
HOUR_MS = 3_600_000
DEFAULT_HORIZON_H = 24
HORIZON_EXIT = 'HORIZON'
# The engine only knows live close reasons; a horizon stop is a flat-out at the
# clock, so it closes as MANUAL and is reported as HORIZON.
HORIZON_CLOSE_REASON = 'MANUAL'
# Live close rules that a replayed bot can hit on its own; every other live
# close needs radar labels or portfolio context this replay does not model.
SIGNAL_EXITS = ('RANGE_BREAK', 'STOP_LOSS')
RULE_NAMES = {code: name for name, code in DECISION_RULES.items()}
REQUIRED_FIELDS = ('ts_ms', 'symbol', 'direction', 'rule_blocks', 'price', 'spec')


# ---------------------------------------------------------------------------
# replay


def _replay_spec(candidate):
    """The recorded spec with engine-charged funding.

    The live desk books funding separately from observed rates, so the recorded
    spec carries funding_managed=True. A replay has no settlement ledger to draw
    on, so it charges the snapshot rate at the engine's 8h boundaries instead —
    an approximation, but a cost of zero would flatter every skipped entry.
    """
    return dict(deepcopy(candidate['spec']), funding_managed=False)


def _window(candles, start_ms, end_ms):
    return sorted((candle for candle in candles or ()
                   if start_ms < candle['ts_ms'] <= end_ms),
                  key=lambda candle: candle['ts_ms'])


def _walk(bot, candles):
    """Step until the first live close signal; returns (bot, exit reason)."""
    for candle in candles:
        bot, events = step(bot, candle)
        signal = next((event['type'] for event in events
                       if event['type'] in SIGNAL_EXITS), None)
        if signal is not None:
            return bot, signal
    return bot, HORIZON_EXIT


def replay(candidate, candles, horizon_h=DEFAULT_HORIZON_H):
    """Open the skipped spec and run it over the horizon under the live close rules."""
    start = int(candidate['ts_ms'])
    end = start + int(horizon_h * HOUR_MS)
    window = _window(candles, start, end)
    bot = open_bot(_replay_spec(candidate), float(candidate['price']), start)
    bot, reason = _walk(bot, window)
    horizon_exit = reason == HORIZON_EXIT
    closed, _ = close_bot(bot, bot['last_price'], end if horizon_exit else bot['last_ts_ms'],
                          HORIZON_CLOSE_REASON if horizon_exit else reason)
    hold_h = (closed['closed_ms'] - closed['opened_ms']) / HOUR_MS
    grids = int(closed['completed_grids'])
    return dict(
        symbol=candidate['symbol'],
        direction=candidate['direction'],
        rule_blocks=sorted(int(code) for code in candidate['rule_blocks']),
        ts_ms=start,
        price=float(candidate['price']),
        exit_price=round(float(closed['last_price']), 10),
        hold_h=round(hold_h, 4),
        grids=grids,
        grid_profit=round(float(closed['grid_profit']), 4),
        fees=round(float(closed['fees_paid']), 4),
        funding=round(float(closed['funding_paid']), 4),
        net=round(bot_net(closed), 4),
        grids_per_hour=round(grids / hold_h, 4) if hold_h > 0 else 0.0,
        exit_reason=reason,
        # A horizon exit that ran out of candles priced less than a full day.
        partial=reason == HORIZON_EXIT and len(window) < int(horizon_h * 60),
    )


# ---------------------------------------------------------------------------
# aggregation


def _aggregate(outcomes):
    nets = [float(outcome['net']) for outcome in outcomes]
    rates = [float(outcome['grids_per_hour']) for outcome in outcomes]
    return {
        'count': len(outcomes),
        'sum_net': round(sum(nets), 4),
        'median_grids_per_hour': round(statistics.median(rates), 4) if rates else 0.0,
        'share_with_positive_net': (round(sum(1 for value in nets if value > 0) / len(nets), 4)
                                    if nets else 0.0),
    }


def _group(outcomes, key):
    grouped = {}
    for outcome in outcomes:
        for name in key(outcome):
            grouped.setdefault(name, []).append(outcome)
    return {name: _aggregate(rows) for name, rows in sorted(grouped.items())}


def _names(outcome):
    return [RULE_NAMES.get(int(code), 'rule_%d' % int(code))
            for code in dict.fromkeys(outcome.get('rule_blocks', ()))]


def _sole_name(outcome):
    """The one rule that refused this entry, or nothing when several did.

    Attribution matters because an advisory answers "what would lifting THIS
    rule have earned". An entry the desk turned away for two reasons earns
    nothing when only one of them is lifted, so it belongs to neither bucket:
    a second bot on a coin we already hold is not money the slot cap cost us.
    """
    names = _names(outcome)
    return names if len(names) == 1 else []


def summarize(outcomes):
    """Aggregate replayed outcomes per rule block, per direction, and overall.

    `by_rule_block` is the descriptive view: an entry blocked by two rules
    counts under each name, so those sums overlap by design. `by_sole_block`
    is the causal view and is what the advisories read - only entries a single
    rule change would actually have admitted. The headline total counts every
    replayed entry exactly once.
    """
    outcomes = list(outcomes or ())
    return {
        'total': _aggregate(outcomes),
        'by_rule_block': _group(outcomes, _names),
        'by_sole_block': _group(outcomes, _sole_name),
        'by_direction': _group(outcomes, lambda outcome: [outcome.get('direction', 'NEUTRAL')]),
        'partial': sum(1 for outcome in outcomes if outcome.get('partial')),
    }


# ---------------------------------------------------------------------------
# inputs


def load_candles(database, symbol, start_ms, end_ms):
    """Completed 1m candles in [start_ms, end_ms], read-only; ts_ms are close times."""
    path = _safe_path(database)
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as connection:
        rows = connection.execute(
            "SELECT time_ms,open,high,low,close FROM klines WHERE symbol=? AND interval='1m' "
            "AND time_ms>=? AND time_ms+60000<=? ORDER BY time_ms",
            (symbol, int(start_ms), int(end_ms)))
        return [dict(ts_ms=at + MINUTE_MS, open=o, high=h, low=l, close=c)
                for at, o, h, l, c in rows]


def _usable(record):
    return (isinstance(record, dict) and all(key in record for key in REQUIRED_FIELDS)
            and isinstance(record['spec'], dict)
            and isinstance(record['rule_blocks'], list))


def load_candidates(path):
    """Parse one daily JSONL file; returns (records, unusable line count)."""
    path = Path(path)
    if not path.exists():
        return [], 0
    records, skipped = [], 0
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            skipped += 1
            continue
        if _usable(record):
            records.append(record)
        else:
            skipped += 1
    return records, skipped


def build(database, candidates_dir, date_str, horizon_h=DEFAULT_HORIZON_H, now_ms=None):
    """Replay a day's recorded candidates into the report payload."""
    path = Path(candidates_dir) / (FILE_PREFIX + date_str + FILE_SUFFIX)
    records, skipped = load_candidates(path)
    outcomes = []
    for record in records:
        start = int(record['ts_ms'])
        try:
            candles = load_candles(database, record['symbol'], start,
                                   start + int(horizon_h * HOUR_MS))
            outcomes.append(replay(record, candles, horizon_h))
        except (KeyError, TypeError, ValueError, ZeroDivisionError, sqlite3.Error) as error:
            print('counterfactual replay skipped %s: %s' % (record.get('symbol'),
                                                            type(error).__name__),
                  file=sys.stderr, flush=True)
            skipped += 1
    return {
        'schema_version': SCHEMA_VERSION,
        'date': date_str,
        'generated_at_ms': int(now_ms if now_ms is not None else time.time() * 1000),
        'horizon_hours': horizon_h,
        'candidates': len(records),
        'replayed': len(outcomes),
        'skipped': skipped,
        'outcomes': outcomes,
        'summary': summarize(outcomes),
    }


# ---------------------------------------------------------------------------
# CLI


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog='trader.review.counterfactual',
        description='Replay the decisions the desk skipped for capacity/policy reasons')
    parser.add_argument('--database', required=True, help='market SQLite (read-only)')
    parser.add_argument('--candidates-dir', required=True,
                        help='directory with candidates-YYYY-MM-DD.jsonl')
    parser.add_argument('--date', required=True, help='YYYY-MM-DD (the candidates file day)')
    parser.add_argument('--out', required=True, help='JSON report output path')
    parser.add_argument('--horizon-hours', type=float, default=DEFAULT_HORIZON_H,
                        help='replay horizon per candidate (default: 24)')
    args = parser.parse_args(argv)
    try:
        datetime.strptime(args.date, '%Y-%m-%d')
    except ValueError:
        parser.error('--date must be YYYY-MM-DD, got %r' % args.date)
    if not 0 < args.horizon_hours <= 168:
        parser.error('--horizon-hours must be within (0, 168]')

    report = build(args.database, args.candidates_dir, args.date, args.horizon_hours)
    atomic_write_json(args.out, report)
    total = report['summary']['total']
    print('[counterfactual] {}: candidates={} replayed={} net=${:.2f} partial={} -> {}'.format(
        args.date, report['candidates'], report['replayed'], total['sum_net'],
        report['summary']['partial'], args.out))
    return 0


if __name__ == '__main__':
    sys.exit(main())
