"""Validate market records before any part of a batch reaches SQLite."""

from collections.abc import Mapping
import json
import math

from trader.data.constants import (
    INTERVAL_MS,
    MAX_JSON_LENGTH,
    MAX_TEXT_LENGTH,
    MAX_TIMESTAMP_MS,
    QUALITY_INTERVALS,
    QUALITY_STATUSES,
    TABLE_COLUMNS,
)


def _integer(value, minimum=0):
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= MAX_TIMESTAMP_MS
    ):
        raise ValueError("expected a bounded integer")


def _number(value, kind):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("expected a finite number")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError("expected a finite number")
    if kind == "positive" and value <= 0:
        raise ValueError("expected a positive number")
    if kind == "nonnegative" and value < 0:
        raise ValueError("expected a nonnegative number")


def _json(value):
    if not isinstance(value, str) or len(value) > MAX_JSON_LENGTH:
        raise ValueError("expected bounded JSON object text")
    try:
        decoded = json.loads(value)
        json.dumps(decoded, allow_nan=False)
    except (ValueError, TypeError, RecursionError) as error:
        raise ValueError("invalid or nonfinite JSON") from error
    if not isinstance(decoded, dict):
        raise ValueError("expected JSON object")


def _value(value, kind):
    if kind.endswith("?"):
        if value is None:
            return
        kind = kind[:-1]
    if kind == "timestamp":
        _integer(value)
    elif kind == "positive_integer":
        _integer(value, minimum=1)
    elif kind in ("finite", "positive", "nonnegative"):
        _number(value, kind)
    elif kind == "json":
        _json(value)
    elif kind == "flag":
        _integer(value)
        if value not in (0, 1):
            raise ValueError("expected flag 0 or 1")
    elif kind == "interval":
        if not isinstance(value, str) or value not in INTERVAL_MS:
            raise ValueError("unsupported interval")
    elif kind == "quality_interval":
        if not isinstance(value, str) or value not in QUALITY_INTERVALS:
            raise ValueError("unsupported quality interval")
    elif kind == "status":
        if value not in QUALITY_STATUSES:
            raise ValueError("unsupported quality status")
    elif (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > MAX_TEXT_LENGTH
        or not value.isprintable()
    ):
        raise ValueError("expected bounded printable text")


def validate(table, row):
    """Return an independent validated row in the documented column order."""
    if table not in TABLE_COLUMNS:
        raise ValueError("unsupported storage table")
    columns = TABLE_COLUMNS[table]
    expected = {name for name, _ in columns}
    if not isinstance(row, Mapping) or set(row) != expected:
        raise ValueError("record columns do not match table schema")
    result = dict(row)
    for name, kind in columns:
        try:
            _value(result[name], kind)
        except ValueError as error:
            raise ValueError(f"{table}.{name}: {error}") from error
    _relationships(table, result)
    return tuple(result[name] for name, _ in columns)


def _relationships(table, row):
    if table == "klines":
        if (
            not row["low"]
            <= min(row["open"], row["close"])
            <= max(row["open"], row["close"])
            <= row["high"]
        ):
            raise ValueError("invalid OHLC range")
        if row["time_ms"] % INTERVAL_MS[row["interval"]]:
            raise ValueError("candle timestamp must align with interval")
    elif table == "top_of_book" and row["bid"] > row["ask"]:
        raise ValueError("inverted top of book")
    elif table == "coinglass_liquidations":
        if row["time_ms"] % INTERVAL_MS["1h"]:
            raise ValueError("liquidation timestamp must align with hour")
    elif table == "data_quality" and row["end_ms"] < row["start_ms"]:
        raise ValueError("invalid quality window")
    elif table == "universe":
        for field in ("first_candle_ms", "listed_at_ms"):
            if row[field] is not None and row[field] > row["updated_at_ms"]:
                raise ValueError(field + " is later than registry update")
