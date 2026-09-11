"""Offline universe registry with no network or execution surface."""

import argparse
import json
import math
import time

from trader.data.constants import MAX_TIMESTAMP_MS
from trader.data.kucoin_public import number, select_contracts
from trader.data.store import Store
from trader.data.universe_constants import (
    ATR_PERIODS,
    ATR_REQUIRED_CANDLES,
    HOUR_MS,
    MILLISECONDS_PER_SECOND,
    MINUTE_MS,
    PERCENT,
    TURNOVER_EXPECTED_CANDLES,
    TURNOVER_WINDOW_MS,
)


def _timestamp(value):
    if (isinstance(value, bool) or not isinstance(value, int)
            or not 0 <= value <= MAX_TIMESTAMP_MS):
        raise ValueError("expected bounded UTC milliseconds")
    return value


def _metadata(rows, now_ms):
    selected = select_contracts(rows)
    result = []
    for raw in selected:
        lot_size = number(raw["lotSize"], positive=True)
        if not lot_size.is_integer() or lot_size > MAX_TIMESTAMP_MS:
            raise ValueError("lotSize must be a positive bounded integer")
        listed = _timestamp(raw["firstOpenDate"])
        if listed > now_ms:
            raise ValueError("listing date is later than registry update")
        result.append({
            "symbol": raw["symbol"], "multiplier": number(
                raw["multiplier"], positive=True),
            "lot_size": int(lot_size), "listed_at_ms": listed,
        })
    return result


def _snapshot_epoch(store, now_ms):
    epoch = store.query(
        "SELECT MAX(observed_at_ms) AS at FROM ticker_snapshots "
        "WHERE observed_at_ms <= ? AND time_ms <= ?", (now_ms, now_ms)
    )[0]["at"]
    if epoch is None:
        raise ValueError("no stored contracts snapshot available")
    return epoch


def stored_contracts(store, *, now_ms):
    """Read a single latest stored epoch, never infer universe completeness."""
    _timestamp(now_ms)
    epoch = _snapshot_epoch(store, now_ms)
    rows = store.query(
        "SELECT symbol, raw_json FROM ticker_snapshots "
        "WHERE observed_at_ms = ? AND time_ms <= ? ORDER BY symbol",
        (epoch, now_ms),
    )
    result = []
    for row in rows:
        raw = json.loads(row["raw_json"])
        if not isinstance(raw, dict) or raw.get("symbol") != row["symbol"]:
            raise ValueError("stored contract metadata symbol mismatch")
        result.append(raw)
    _metadata(result, now_ms)
    return result


def _first_candle(store, symbol, now_ms):
    rows = store.query(
        "SELECT MIN(time_ms) AS first_ms FROM klines WHERE symbol = ? "
        "AND ((interval = '1m' AND time_ms <= ?) "
        "OR (interval = '1h' AND time_ms <= ?))",
        (symbol, now_ms - MINUTE_MS, now_ms - HOUR_MS),
    )
    return rows[0]["first_ms"]


def _turnover(store, symbol, now_ms):
    end_ms = now_ms // MINUTE_MS * MINUTE_MS
    start_ms = max(0, end_ms - TURNOVER_WINDOW_MS)
    row = store.query(
        "SELECT COUNT(*) AS count, SUM(turnover) AS turnover, "
        "MIN(time_ms) AS first_ms, MAX(time_ms) AS last_ms "
        "FROM klines WHERE symbol = ? AND interval = '1m' "
        "AND time_ms >= ? AND time_ms < ?", (symbol, start_ms, end_ms)
    )[0]
    count = row["count"]
    status = ("complete" if count == TURNOVER_EXPECTED_CANDLES
              else "partial" if count else "unavailable")
    coverage = {
        "source": "stored_completed_1m_quote_turnover",
        "start_ms": start_ms, "end_ms_exclusive": end_ms,
        "observed_candles": count,
        "expected_candles": TURNOVER_EXPECTED_CANDLES,
        "fraction": count / TURNOVER_EXPECTED_CANDLES,
        "first_observed_ms": row["first_ms"],
        "last_observed_ms": row["last_ms"], "status": status,
        "partial_sum": status == "partial",
    }
    return row["turnover"], coverage


def _atr(store, symbol, now_ms):
    end_ms = now_ms // HOUR_MS * HOUR_MS
    start_ms = end_ms - ATR_REQUIRED_CANDLES * HOUR_MS
    rows = store.query(
        "SELECT time_ms, high, low, close FROM klines "
        "WHERE symbol = ? AND interval = '1h' AND time_ms >= ? "
        "AND time_ms < ? ORDER BY time_ms", (symbol, start_ms, end_ms)
    )
    expected = list(range(start_ms, end_ms, HOUR_MS))
    complete = [row["time_ms"] for row in rows] == expected
    coverage = {
        "source": "stored_completed_1h_true_range",
        "periods": ATR_PERIODS, "normalization": "last_close_percent",
        "start_ms": max(0, start_ms), "end_ms_exclusive": end_ms,
        "observed_candles": len(rows),
        "expected_candles": ATR_REQUIRED_CANDLES,
        "status": "complete" if complete else "unavailable",
    }
    if not complete:
        return None, coverage
    ranges = [max(current["high"] - current["low"],
                  abs(current["high"] - previous["close"]),
                  abs(current["low"] - previous["close"]))
              for previous, current in zip(rows, rows[1:])]
    value = math.fsum(ranges) / ATR_PERIODS / rows[-1]["close"] * PERCENT
    return value, coverage


def _record(store, metadata, now_ms, source):
    symbol = metadata["symbol"]
    first = _first_candle(store, symbol, now_ms)
    turnover, turnover_coverage = _turnover(store, symbol, now_ms)
    atr, atr_coverage = _atr(store, symbol, now_ms)
    coverage = {
        "metrics_as_of_ms": now_ms, "contract_metadata": source,
        "listing": {"source": "kucoin_contract_firstOpenDate",
                    "status": "reported"},
        "first_candle": {
            "source": "stored_completed_1m_or_1h",
            "meaning": "oldest_observed_completed_candle",
            "is_actual_first_ever": False,
        },
        "turnover_30d": turnover_coverage, "atr": atr_coverage,
    }
    return {
        **metadata, "updated_at_ms": now_ms, "first_candle_ms": first,
        "turnover_30d": turnover, "atr_pct": atr, "active": 1,
        "coverage_json": json.dumps(coverage, allow_nan=False, sort_keys=True),
    }


def _inactive(store, active_symbols, now_ms):
    result = []
    for row in store.query("SELECT * FROM universe ORDER BY symbol"):
        if row["symbol"] in active_symbols:
            continue
        coverage = json.loads(row["coverage_json"])
        coverage = {**coverage, "inactive_observed_at_ms": now_ms,
                    "inactive_reason": "absent_from_complete_active_universe"}
        result.append({
            **row, "active": 0, "updated_at_ms": now_ms,
            "coverage_json": json.dumps(coverage, allow_nan=False,
                                        sort_keys=True),
        })
    return result


def refresh_registry(store, contracts=None, *, now_ms, complete=False):
    """Atomically refresh; complete=True requires full-response evidence.

    Direct inputs are raw metadata validated by select_contracts.
    Omitted inputs use one stored snapshot and cannot prove completeness.
    """
    _timestamp(now_ms)
    if not isinstance(complete, bool):
        raise ValueError("complete must be boolean")
    with store.transaction():
        latest = store.query(
            "SELECT MAX(updated_at_ms) AS at FROM universe"
        )[0]["at"]
        if latest is not None and latest > now_ms:
            raise ValueError("refresh would regress newer registry state")
        source = {"source": "supplied_contract_metadata",
                  "observed_at_ms": None}
        if contracts is None:
            if complete:
                raise ValueError("stored snapshots cannot prove completeness")
            contracts = stored_contracts(store, now_ms=now_ms)
            source = {"source": "stored_contracts_ticker_snapshot",
                      "observed_at_ms": _snapshot_epoch(store, now_ms)}
        metadata = _metadata(contracts, now_ms)
        if complete and not metadata:
            raise ValueError("empty complete universe requires review")
        rows = [_record(store, row, now_ms, source) for row in metadata]
        if complete:
            symbols = {row["symbol"] for row in rows}
            rows += _inactive(store, symbols, now_ms)
        store.upsert("universe", rows)
    return registry_report(store, now_ms=now_ms)


def _age(at, now_ms):
    return now_ms - at if at is not None and 0 <= at <= now_ms else None


def _age_bound(row, now_ms):
    first, listed = row["first_candle_ms"], row["listed_at_ms"]
    if (_age(first, now_ms) is None or _age(listed, now_ms) is None):
        return None
    return first >= listed


def registry_report(store, *, now_ms):
    """Return registry facts with explicit observed-history age bounds."""
    _timestamp(now_ms)
    rows = store.query("SELECT * FROM universe ORDER BY active DESC, symbol")
    contracts = []
    for row in rows:
        coverage = json.loads(row["coverage_json"])
        contracts.append({
            **row, "coverage": coverage,
            "listing_age_ms": _age(row["listed_at_ms"], now_ms),
            "observed_history_age_ms": _age(row["first_candle_ms"], now_ms),
            "observed_history_is_listing_age_lower_bound": _age_bound(
                row, now_ms),
        })
    stats = {
        "contracts": len(rows), "active": sum(row["active"] for row in rows),
        "inactive": sum(not row["active"] for row in rows),
        "listing_dates_reported": sum(row["listed_at_ms"] is not None
                                      for row in rows),
        "observed_histories": sum(row["first_candle_ms"] is not None
                                  for row in rows),
        "atr_available": sum(row["atr_pct"] is not None for row in rows),
    }
    for status in ("complete", "partial", "unavailable"):
        stats["turnover_" + status] = sum(
            row["coverage"].get("turnover_30d", {}).get("status") == status
            for row in contracts
        )
    return {"reported_at_ms": now_ms, "stats": stats, "contracts": contracts}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--refresh", action="store_true",
                        help="refresh from stored contract ticker metadata")
    parser.add_argument("--now-ms", type=int,
                        help="UTC milliseconds for offline checks")
    args = parser.parse_args(argv)
    now_ms = (int(time.time() * MILLISECONDS_PER_SECOND)
              if args.now_ms is None else args.now_ms)
    try:
        with Store(args.database) as store:
            result = (refresh_registry(store, now_ms=now_ms) if args.refresh
                      else registry_report(store, now_ms=now_ms))
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print(json.dumps(result, allow_nan=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
