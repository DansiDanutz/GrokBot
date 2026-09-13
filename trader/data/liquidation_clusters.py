"""Implied liquidation clusters derived from our own open-interest history.

Zero-cost stand-in for a paid liquidation heatmap. Between two consecutive
open-interest snapshots a positive change means contracts were opened near the
mark price; that notional is spread across standard leverage tiers and turned
into the prices at which those positions would be liquidated. A negative change
closes positions and removes mass proportionally from every existing bin. Mass
decays with a 48 hour half life. The output is an inference about where
leverage probably sits, never an observation of resting orders or ground truth.

Read-only against the public market database; no credentials, no network.
"""

import argparse
import json
import math
import os
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

SCHEMA_VERSION = 1
METHOD = 'oi_delta_implied_v1'
HOUR_MS = 3_600_000
WINDOW_HOURS = 168
HALF_LIFE_HOURS = 48
TIER_WEIGHTS = {5: .25, 10: .30, 20: .25, 25: .10, 50: .10}
FUNDING_TILT = .60
BIN_PCT = .0025
MIN_CLUSTER_SHARE = .005
TOP_CLUSTERS = 12
DEFAULT_LIMIT = 60
MAX_RAW_JSON = 65536
MAX_RADAR_BYTES = 32_000_000
MIN_SCALE = 1e-9
OUTPUT_MODE = 0o644
CONTRACT_FIELDS = ('multiplier', 'tick_size', 'maintain_margin', 'max_leverage')
RAW_CONTRACT_KEYS = (('multiplier', 'multiplier'), ('tick_size', 'tickSize'),
                     ('maintain_margin', 'maintainMargin'),
                     ('max_leverage', 'maxLeverage'))


def _number(value):
    """Accept only real finite numbers; booleans and strings are not numbers."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('expected a finite number')
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('expected a finite number')
    return number


def contract(values):
    """Return a validated copy of the contract terms the model needs."""
    if not isinstance(values, dict):
        raise ValueError('contract must be a mapping')
    terms = {name: _number(values.get(name)) for name in CONTRACT_FIELDS}
    if terms['multiplier'] <= 0 or terms['tick_size'] < 0 \
            or not 0 <= terms['maintain_margin'] < 1 \
            or terms['max_leverage'] < 1:
        raise ValueError('contract terms out of range')
    return terms


def tier_weights(max_leverage):
    """Standard tiers the contract actually allows, renormalised to one."""
    allowed = {tier: weight for tier, weight in TIER_WEIGHTS.items()
               if tier <= max_leverage}
    total = sum(allowed.values())
    return {tier: weight / total for tier, weight in allowed.items()} \
        if total > 0 else {}


def bin_width(price, tick_size):
    """Bins are never finer than the tick nor than 0.25% of the price."""
    return max(tick_size, price * BIN_PCT) if price > 0 else 0.0


def levels(price, maintain_margin, leverage):
    """Long and short liquidation prices for one leverage tier."""
    edge = 1 / leverage - maintain_margin
    return price * (1 - edge), price * (1 + edge)


def funding_split(funding_rate):
    """Positive funding means longs pay: assume the crowd is long-heavy."""
    if funding_rate > 0:
        return FUNDING_TILT, 1 - FUNDING_TILT
    if funding_rate < 0:
        return 1 - FUNDING_TILT, FUNDING_TILT
    return .5, .5


def _snapshot(item):
    try:
        stamp, open_interest, price, funding = item
    except (TypeError, ValueError):
        return None
    try:
        values = (_number(open_interest), _number(price), _number(funding))
    except ValueError:
        return None
    if isinstance(stamp, bool) or not isinstance(stamp, int) or stamp < 0 \
            or values[0] < 0 or values[1] <= 0:
        return None
    return (stamp, *values)


def clean(symbol_rows):
    """Ordered, deduplicated, finite snapshots; unusable rows are dropped."""
    kept = {}
    for item in symbol_rows or ():
        row = _snapshot(item)
        if row is not None:
            kept[row[0]] = row
    return [kept[stamp] for stamp in sorted(kept)]


def _decay(elapsed_ms):
    return .5 ** (elapsed_ms / (HALF_LIFE_HOURS * HOUR_MS))


def _open(bins, weights, terms, width, event):
    """Add one opening event to the local bin accumulator; return mass added."""
    scale, stamp, usd, price, funding = event
    long_share, short_share = funding_split(funding)
    added = 0.0
    for leverage, weight in weights.items():
        long_level, short_level = levels(price, terms['maintain_margin'],
                                         leverage)
        for side, share, level in (('long', long_share, long_level),
                                   ('short', short_share, short_level)):
            index = int(round(level / width))
            amount = usd * share * weight / scale
            if index <= 0 or not math.isfinite(amount) or amount <= 0:
                continue
            previous_usd, previous_time = bins.get((side, index), (0.0, 0.0))
            bins[(side, index)] = (previous_usd + amount,
                                   previous_time + amount * stamp)
            added += amount
    return added


def _renormalise(bins, scale):
    return {key: (usd * scale, stamp * scale)
            for key, (usd, stamp) in bins.items()}


def _walk(rows, weights, terms, width, now_ms):
    """Replay the snapshots once; `scale` carries decay and closures for all."""
    bins, scale, mass = {}, 1.0, 0.0
    previous = rows[0]
    for row in rows[1:]:
        stamp, open_interest, price, funding = row
        if stamp > previous[0]:
            scale *= _decay(stamp - previous[0])
        delta = open_interest - previous[1]
        notional = abs(delta) * terms['multiplier'] * price
        if delta > 0 and notional > 0:
            mass += _open(bins, weights, terms, width,
                          (scale, stamp, notional, price, funding))
        elif delta < 0 and mass * scale > 0:
            current = mass * scale
            scale = 0.0 if notional >= current else scale * (1 - notional / current)
        if scale <= 0:
            bins, scale, mass = {}, 1.0, 0.0
        elif scale < MIN_SCALE:
            bins, mass, scale = _renormalise(bins, scale), mass * scale, 1.0
        previous = row
    if now_ms > previous[0]:
        scale *= _decay(now_ms - previous[0])
    return bins, scale


def _materialise(bins, scale, width, now_ms):
    clusters = []
    for (side, index), (usd, weighted) in bins.items():
        amount = usd * scale
        if not math.isfinite(amount) or amount <= 0 or usd <= 0:
            continue
        age_hours = max(0.0, (now_ms - weighted / usd) / HOUR_MS)
        clusters.append(dict(price=round(index * width, 12), side=side,
                             usd=round(amount, 2),
                             age_h=round(age_hours, 3)))
    return sorted(clusters, key=lambda item: item['price'])


def _nearest(clusters, price, above):
    side = [item for item in clusters
            if (item['price'] > price if above else item['price'] < price)]
    if not side or price <= 0:
        return None
    best = min(side, key=lambda item: abs(item['price'] - price))
    return dict(price=best['price'], side=best['side'], usd=best['usd'],
                distance_pct=round(abs(best['price'] - price) / price * 100, 6))


def derive(symbol_rows, contract_terms, now_ms):
    """Pure core: snapshots plus contract terms in, one symbol entry out."""
    terms = contract(contract_terms)
    rows = clean(symbol_rows)
    price = rows[-1][2] if rows else 0.0
    width = bin_width(price, terms['tick_size'])
    weights = tier_weights(terms['max_leverage'])
    bins, scale = ({}, 1.0)
    if len(rows) >= 2 and weights and width > 0:
        bins, scale = _walk(rows, weights, terms, width, now_ms)
    clusters = _materialise(bins, scale, width, now_ms)
    total = math.fsum(item['usd'] for item in clusters)
    significant = [item for item in clusters
                   if item['usd'] >= total * MIN_CLUSTER_SHARE]
    return dict(price=price, snapshots_used=len(rows),
                total_usd=round(total, 2),
                clusters=sorted(significant, key=lambda item: -item['usd'])[:TOP_CLUSTERS],
                nearest_below=_nearest(significant, price, above=False),
                nearest_above=_nearest(significant, price, above=True))


def read_symbol(connection, symbol, start_ms, end_ms):
    """Open interest joined to the mark price and funding of the same tick."""
    query = ("SELECT o.time_ms, o.open_interest, t.mark_price, t.funding_rate "
             "FROM open_interest AS o JOIN ticker_snapshots AS t "
             "ON t.symbol=o.symbol AND t.time_ms=o.time_ms "
             "WHERE o.symbol=? AND o.time_ms>=? AND o.time_ms<=? "
             "ORDER BY o.time_ms")
    return [tuple(row) for row in
            connection.execute(query, (symbol, start_ms, end_ms))]


def contract_for(connection, symbol):
    """Contract terms from the newest stored ticker snapshot of the symbol."""
    row = connection.execute(
        "SELECT raw_json FROM ticker_snapshots WHERE symbol=? "
        "ORDER BY time_ms DESC LIMIT 1", (symbol,)).fetchone()
    raw = row[0] if row else None
    if not isinstance(raw, str) or len(raw) > MAX_RAW_JSON:
        raise ValueError('no usable contract snapshot')
    try:
        values = json.loads(raw)
    except ValueError:
        raise ValueError('unreadable contract snapshot') from None
    if not isinstance(values, dict):
        raise ValueError('unreadable contract snapshot')
    return contract({name: values.get(key) for name, key in RAW_CONTRACT_KEYS})


def _readable_path(path):
    target = Path(path).expanduser().absolute()
    if not target.is_file() or target.is_symlink():
        raise ValueError('path must be a regular non-symlink file')
    return target


def build(database, symbols, now_ms):
    """Derive every requested symbol; unusable symbols are simply absent."""
    if not isinstance(now_ms, int) or isinstance(now_ms, bool) or now_ms < 0:
        raise ValueError('now_ms must be a nonnegative integer')
    uri = _readable_path(database).as_uri() + '?mode=ro'
    entries = {}
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        for symbol in dict.fromkeys(symbols):
            try:
                terms = contract_for(connection, symbol)
                rows = read_symbol(connection, symbol,
                                   now_ms - WINDOW_HOURS * HOUR_MS, now_ms)
                entry = derive(rows, terms, now_ms)
            except (ValueError, TypeError, sqlite3.Error):
                continue
            if entry['snapshots_used'] >= 2:
                entries[symbol] = entry
    return dict(schema_version=SCHEMA_VERSION, method=METHOD,
                generated_at_ms=now_ms, window_hours=WINDOW_HOURS,
                symbols=entries)


def radar_symbols(path, limit):
    """Every row symbol of a radar report, in radar order, deduplicated."""
    target = _readable_path(path)
    if target.stat().st_size > MAX_RADAR_BYTES:
        raise ValueError('radar report too large')
    report = json.loads(target.read_text())
    rows = report.get('rows') if isinstance(report, dict) else None
    if not isinstance(rows, list):
        raise ValueError('radar report has no rows')
    names = [row.get('symbol') for row in rows if isinstance(row, dict)]
    ordered = [name for name in dict.fromkeys(names) if isinstance(name, str) and name]
    return ordered[:limit] if limit and limit > 0 else ordered


def write_report(path, report):
    """Atomic replace of a world-readable file; readers never see a partial."""
    destination = Path(path).expanduser().absolute()
    destination.parent.mkdir(parents=True, exist_ok=True)
    pending = destination.with_name('.' + destination.name + '.pending')
    pending.write_text(json.dumps(report, indent=2, sort_keys=True,
                                 allow_nan=False) + '\n')
    os.chmod(pending, OUTPUT_MODE)
    pending.replace(destination)
    return destination


def _status(symbols_ok, symbols_requested):
    if symbols_ok == 0:
        return 'fail'
    return 'pass' if symbols_ok == symbols_requested else 'warn'


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    parser.add_argument('--out', required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--symbols', help='comma-separated symbol override')
    source.add_argument('--from-radar', help='radar report whose rows select '
                        'the symbols to derive')
    parser.add_argument('--limit', type=int, default=DEFAULT_LIMIT,
                        help='maximum --from-radar symbols')
    parser.add_argument('--now-ms', type=int, help=argparse.SUPPRESS)
    return parser


def main(argv=None, printer=print):
    args = _parser().parse_args(argv)
    try:
        now_ms = args.now_ms if args.now_ms is not None else int(time.time() * 1000)
        symbols = ([name for name in args.symbols.split(',') if name]
                   if args.symbols else radar_symbols(args.from_radar, args.limit))
        if not symbols:
            raise ValueError('no symbols to derive')
        report = build(args.database, symbols, now_ms)
        write_report(args.out, report)
    except Exception as error:
        print('liquidation cluster build failed: ' + type(error).__name__,
              file=sys.stderr)
        return 1
    status = _status(len(report['symbols']), len(symbols))
    printer(json.dumps(dict(status=status, symbols_ok=len(report['symbols']),
                            symbols_requested=len(symbols),
                            generated_at_ms=now_ms), sort_keys=True))
    return 0 if status != 'fail' else 1


if __name__ == '__main__':
    raise SystemExit(main())
