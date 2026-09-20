"""CLI for the read-only KuCoin grid radar."""

import argparse
import json
import time
from pathlib import Path

from trader.radar import liquidity_levels
from trader.radar.radar import analyse
from trader.radar.entry import VERSION


LABELS = (("majors", "MAJORS READ"), ("turning_up", "TURNING UP"),
          ("turning_down", "TURNING DOWN"), ("long", "LONG"),
          ("short", "SHORT"), ("neutral", "NEUTRAL"), ("movers", "MOVERS"))


def _table(title, rows, directions_only=False):
    if directions_only:
        return ["", title, f"{'symbol':14} direction",
                *(f"{row['symbol']:14} {row['direction']}" for row in rows)]
    lines = ["", title,
             f"{'symbol':14} {'direction':13} {'price':>10} {'turn$M':>8} {'age min':>7} {'ATR1h%':>7} "
             f"{'range':>23} {'step%':>6} {'grids':>5} {'est.grids/h':>11}"]
    for row in rows:
        lines.append(f"{row['symbol']:14} {row['direction']:13} {row['price']:>10.5g} "
                     f"{row['turnover_24h_usdt']/1e6:>8.1f} {row['snapshot_age_min']:>7.1f} "
                     f"{row['atr_1h_pct']:>7.2f} "
                     f"{row['range_low']:.5g}..{row['range_high']:.5g} "
                     f"{row['step_pct']:>6.2f} {row['grids']:>5} "
                     f"{row['expected_grids_per_hour']:>11.2f}")
    return lines


def main(argv=None, printer=print, environ=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--json", required=True)
    parser.add_argument("--asof-ms", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--liquidation-clusters",
                        help="advisory derived liquidation-cluster file; "
                             "omit to refresh and read the default one")
    parser.add_argument("--no-liquidation-clusters", action="store_true",
                        help="skip the advisory annotation entirely")
    parser.add_argument("--telegram-chat-id")
    parser.add_argument("--telegram-state")
    parser.add_argument("--jev-shadow", action="store_true",
                        help="request advisory TypeSafe judgments for up to ten candidates")
    args = parser.parse_args(argv)
    report = analyse(args.database, args.asof_ms, args.liquidation_clusters, entry_policy=VERSION)
    if not args.liquidation_clusters and not args.no_liquidation_clusters:
        now = args.asof_ms if args.asof_ms is not None else int(time.time() * 1000)
        entered = [row["symbol"] for rows in report["sections"].values() for row in rows]
        path = liquidity_levels.refresh(liquidity_levels.DEFAULT_PATH,
                                        args.database, entered, now)
        report = liquidity_levels.annotate(report, path, now)
    if args.jev_shadow:
        from trader.radar.jev import enrich_from_environment
        report = enrich_from_environment(report, environ if environ is not None else __import__('os').environ)
    destination = Path(args.json).expanduser().absolute()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name("." + destination.name + ".pending")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n")
    temporary.replace(destination)
    printer(f"Analysed {len(report['rows'])} symbols; "
            f"{sum(row['passes_liquidity'] for row in report['rows'])} pass liquidity filters.")
    printer(f"Entry policy: {VERSION}; {sum(r.get('entry_viable',0) for r in report['rows'])} confirmed setups.")
    if args.jev_shadow:
        shadow = report['jev_shadow']
        printer(f"JEV shadow: {shadow['evaluated']}/{shadow['attempted']} evaluated; "
                f"{shadow['failed']} failed.")
    for key, title in LABELS:
        for line in _table(title, report["sections"][key], key == "majors"):
            printer(line)
    if args.telegram_chat_id or args.telegram_state:
        if not args.telegram_chat_id or not args.telegram_state:
            parser.error("Telegram delivery requires both --telegram-chat-id and --telegram-state")
        from trader.radar.telegram import deliver
        deliver(report, args.telegram_chat_id, args.telegram_state)
    return 0
