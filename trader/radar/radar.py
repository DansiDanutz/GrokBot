"""Lean KuCoin perpetual radar ported from Dan's working prototype."""

from collections import defaultdict
from pathlib import Path
import math
import sqlite3
import time

from trader.radar.rates import K_STANDARD, K_MAJOR, expected_grids_per_hour


HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS
MIN_TURNOVER_USDT = 3_000_000
MAJOR_TURNOVER_USDT = 50_000_000
MAX_SPREAD_PCT = 0.15
MAX_SNAPSHOT_AGE_MIN = 120
MIN_LISTING_AGE_DAYS = 7
STANDARD_STEP_PCT = 0.8
MAJOR_STEP_PCT = 0.52
SECTION_LIMIT = 8
MAJORS = ("XBTUSDTM", "ETHUSDTM", "SOLUSDTM")


def _ema(values, period):
    weight = 2 / (period + 1)
    value = values[0]
    result = [value]
    for current in values[1:]:
        value = current * weight + value * (1 - weight)
        result.append(value)
    return result


def _atr(rows, period=14):
    ranges = []
    previous = rows[0][3]
    for _, high, low, close in rows[1:]:
        ranges.append(max(high - low, abs(high - previous), abs(low - previous)))
        previous = close
    return sum(ranges[-period:]) / period if len(ranges) >= period else None


def _aggregate(rows, duration_ms):
    buckets = {}
    for timestamp, opened, high, low, close, volume in rows:
        key = timestamp // duration_ms
        if key not in buckets:
            buckets[key] = [opened, high, low, close, volume]
        else:
            item = buckets[key]
            item[1] = max(item[1], high)
            item[2] = min(item[2], low)
            item[3] = close
            item[4] += volume
    return [tuple(buckets[key]) for key in sorted(buckets)]


def _latest(connection, table, columns):
    names = ",".join(f"t.{name}" for name in columns)
    query = (f"SELECT t.symbol,{names} FROM {table} t JOIN "
             f"(SELECT symbol,MAX(time_ms) AS latest FROM {table} GROUP BY symbol) x "
             "ON x.symbol=t.symbol AND x.latest=t.time_ms")
    return {row[0]: row[1:] for row in connection.execute(query)}


def _direction(hourly, price):
    four_hour = _aggregate(hourly, 4 * HOUR_MS)
    daily = _aggregate(hourly, DAY_MS)
    if len(four_hour) < 55 or len(daily) < 12:
        return None, four_hour
    close_4h = [row[3] for row in four_hour]
    close_1d = [row[3] for row in daily]
    ema20_4h, ema50_4h = _ema(close_4h, 20), _ema(close_4h, 50)
    ema20_1d = _ema(close_1d, 20 if len(close_1d) >= 20 else 10)
    ema50_1d = _ema(close_1d, 50 if len(close_1d) >= 50 else 20)
    slope_4h = (ema20_4h[-1] - ema20_4h[-6]) / ema20_4h[-6] * 100
    up_4h = ema20_4h[-1] > ema50_4h[-1] and price > ema20_4h[-1] and slope_4h > 0
    down_4h = ema20_4h[-1] < ema50_4h[-1] and price < ema20_4h[-1] and slope_4h < 0
    up_1d, down_1d = ema20_1d[-1] > ema50_1d[-1], ema20_1d[-1] < ema50_1d[-1]
    if up_4h and up_1d:
        direction = "LONG"
    elif down_4h and down_1d:
        direction = "SHORT"
    elif up_4h and not up_1d:
        direction = "TURNING-UP"
    elif down_4h and not down_1d:
        direction = "TURNING-DOWN"
    else:
        direction = "NEUTRAL"
    return (direction, slope_4h), four_hour


def _sections(rows):
    passed = [row for row in rows if row["passes_liquidity"]]
    groups = {
        "majors": [row for row in rows if row["symbol"] in MAJORS],
        "turning_up": [row for row in passed if row["direction"] == "TURNING-UP"],
        "turning_down": [row for row in passed if row["direction"] == "TURNING-DOWN"],
        "long": [row for row in passed if row["direction"] == "LONG"],
        "short": [row for row in passed if row["direction"] == "SHORT"],
        "neutral": [row for row in passed if row["direction"] == "NEUTRAL"
                    and 0.25 <= row["position_7d"] <= 0.75],
        "movers": [row for row in passed if abs(row["change_24h_pct"]) > 15],
    }
    result = {}
    for name, items in groups.items():
        key = ((lambda row: row["turnover_24h_usdt"]) if name == "majors" else
               (lambda row: abs(row["change_24h_pct"])) if name == "movers" else
               (lambda row: row["rank_score"]))
        result[name] = sorted(items, key=key, reverse=True)[:SECTION_LIMIT]
    return result


def analyse(database, asof_ms=None):
    """Read one SQLite snapshot and return the prototype's deterministic radar."""
    path = Path(database).expanduser().absolute()
    if not path.is_file() or path.is_symlink():
        raise ValueError("database must be a regular non-symlink file")
    now = int(time.time() * 1000) if asof_ms is None else asof_ms
    if type(now) is not int or now < 0:
        raise ValueError("asof_ms must be a nonnegative integer")
    uri = path.as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        tickers = _latest(connection, "ticker_snapshots",
                          ("time_ms", "last", "turnover_24h", "funding_rate"))
        books = _latest(connection, "top_of_book", ("time_ms", "bid", "ask"))
        universe = {row[0]: row[1:] for row in connection.execute(
            "SELECT symbol,first_candle_ms,listed_at_ms,active FROM universe")}
        by_symbol = defaultdict(list)
        since = now - 60 * DAY_MS
        for row in connection.execute(
                "SELECT symbol,time_ms,open,high,low,close,volume FROM klines "
                "WHERE interval='1h' AND time_ms>=? AND time_ms<? ORDER BY symbol,time_ms",
                (since, now)):
            by_symbol[row[0]].append(tuple(row[1:]))

    rows = []
    for symbol, hourly in by_symbol.items():
        if len(hourly) < 24 * 10 or symbol not in tickers or symbol not in books:
            continue
        ticker_time, price, turnover, funding = tickers[symbol]
        book_time, bid, ask = books[symbol]
        identity = universe.get(symbol)
        if not identity or not identity[2]:
            continue
        trend, four_hour = _direction(hourly, price)
        if trend is None:
            continue
        direction, slope_4h = trend
        atr_1h = _atr([(row[1], row[2], row[3], row[4]) for row in hourly[-60:]])
        atr_4h = _atr([row[:4] for row in four_hour[-40:]])
        if not atr_1h or not atr_4h:
            continue
        high_7d = max(row[2] for row in hourly[-168:])
        low_7d = min(row[3] for row in hourly[-168:])
        position = (price - low_7d) / (high_7d - low_7d) if high_7d > low_7d else 0.5
        spread = (ask - bid) / price * 100 if bid and ask and price else math.inf
        listed = identity[1] or identity[0] or now
        age_days = (now - listed) / DAY_MS
        snapshot_age_min = max(0, (now - min(ticker_time, book_time)) / 60_000)
        step = MAJOR_STEP_PCT if turnover >= MAJOR_TURNOVER_USDT else STANDARD_STEP_PCT
        atr_1h_pct, atr_4h_pct = atr_1h / price * 100, atr_4h / price * 100
        if direction in ("LONG", "TURNING-UP"):
            low, high = price - 1.5 * atr_4h, max(high_7d, price + 2 * atr_4h)
        elif direction in ("SHORT", "TURNING-DOWN"):
            low, high = min(low_7d, price - 2 * atr_4h), price + 1.5 * atr_4h
        else:
            low, high = low_7d, high_7d
        expected = round(expected_grids_per_hour(atr_1h_pct, step, turnover), 2)
        rows.append({
            "symbol": symbol, "direction": direction, "price": price,
            "turnover_24h_usdt": turnover, "spread_pct": spread,
            "snapshot_age_min": snapshot_age_min,
            "funding_pct": funding * 100, "listing_age_days": age_days,
            "atr_1h_pct": atr_1h_pct, "atr_4h_pct": atr_4h_pct,
            "slope_4h_pct": slope_4h, "position_7d": position,
            "change_24h_pct": (price / hourly[-25][4] - 1) * 100,
            "low_7d": low_7d, "high_7d": high_7d,
            "range_low": low, "range_high": high, "step_pct": step,
            "grids": int(min(200, max(2, (high - low) / price * 100 / step))),
            "expected_grids_per_hour": expected,
            "rank_score": expected * min(1.0, turnover / 8_000_000),
            "passes_liquidity": turnover >= MIN_TURNOVER_USDT
                                and spread <= MAX_SPREAD_PCT and age_days >= MIN_LISTING_AGE_DAYS
                                and snapshot_age_min <= MAX_SNAPSHOT_AGE_MIN,
        })
    rows.sort(key=lambda row: (-row["rank_score"], row["symbol"]))
    return {"schema_version": 1, "generated_at_ms": int(time.time() * 1000),
            "asof_ms": now, "constants": {"standard_step_pct": STANDARD_STEP_PCT,
            "major_step_pct": MAJOR_STEP_PCT, "k_standard": K_STANDARD, "k_major": K_MAJOR},
            "filters": {"min_turnover_usdt": MIN_TURNOVER_USDT,
            "max_spread_pct": MAX_SPREAD_PCT, "min_listing_age_days": MIN_LISTING_AGE_DAYS,
            "max_snapshot_age_min": MAX_SNAPSHOT_AGE_MIN},
            "rows": rows, "sections": _sections(rows)}
