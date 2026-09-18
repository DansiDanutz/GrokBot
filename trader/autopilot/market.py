"""Public minute recovery and read-only historical funding lookups."""
from contextlib import closing
import sqlite3

from trader.data.store import Store, _safe_path
from trader.data.kucoin_public import MAX_CANDLES, number

MINUTE_MS = 60_000


def ingest_open_minutes(database, symbols, client, now_ms, *, stop=None):
    """Fetch only missing completed minutes, at most the last six hours per symbol."""
    end = now_ms // MINUTE_MS * MINUTE_MS
    start = max(0, end - 360 * MINUTE_MS)
    with Store(database) as store:
        for symbol in sorted(set(symbols)):
            existing = {r['time_ms'] for r in store.query(
                "SELECT time_ms FROM klines WHERE symbol=? AND interval='1m' AND time_ms>=? AND time_ms<?",
                (symbol, start, end))}
            for lower in range(start, end, MAX_CANDLES * MINUTE_MS):
                if stop is not None and stop.is_set():
                    raise RuntimeError('recovery interrupted')
                upper = min(end, lower + MAX_CANDLES * MINUTE_MS)
                if all(at in existing for at in range(lower, upper, MINUTE_MS)):
                    continue
                rows = client.klines(symbol, '1m', lower, upper)
                # The collector can write while the request runs. Never replace its evidence.
                with store.transaction():
                    found = {r['time_ms'] for r in store.query(
                        "SELECT time_ms FROM klines WHERE symbol=? AND interval='1m' AND time_ms>=? AND time_ms<?",
                        (symbol, lower, upper))}
                    store.upsert('klines', [r for r in rows if r['time_ms'] not in found])


def candles_after(database, symbol, last_ms, now_ms):
    """Use only complete, wholly unseen candles; timestamps denote their closes."""
    path = _safe_path(database)
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)) as connection:
        rows = connection.execute(
            "SELECT time_ms,open,high,low,close FROM klines WHERE symbol=? AND interval='1m' "
            "AND time_ms>=? AND time_ms+60000<=? ORDER BY time_ms", (symbol, last_ms, now_ms))
        return [dict(ts_ms=at + MINUTE_MS, open=o, high=h, low=l, close=c) for at, o, h, l, c in rows]


def minute_times(database, symbol, start_ms, end_ms):
    """Committed 1m candle start times in [start_ms, end_ms), read-only."""
    path = _safe_path(database)
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)) as connection:
        return {row[0] for row in connection.execute(
            "SELECT time_ms FROM klines WHERE symbol=? AND interval='1m' "
            "AND time_ms>=? AND time_ms<?", (symbol, start_ms, end_ms))}


def rates_at(database, symbol, boundaries):
    """Return fractional stored rates as percentages, with no future-rate lookahead."""
    if not boundaries:
        return {}
    path = _safe_path(database)
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)) as connection:
        result = {}
        for at in boundaries:
            row = connection.execute('SELECT rate FROM funding WHERE symbol=? AND time_ms<=? '
                                     'ORDER BY time_ms DESC LIMIT 1', (symbol, at)).fetchone()
            if row is not None:
                result[at] = number(row[0]) * 100
        return result
