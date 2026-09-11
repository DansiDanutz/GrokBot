"""Read-only isolated-margin estimates for the paper engine's net base quantity.

KuCoin: https://www.kucoin.com/support/26694703491737
The 0.06% liquidation fee is an explicit assumption from its worked example.
Reserved collateral is excluded; its second price is a hypothetical allocation.
"""
from contextlib import closing
from copy import deepcopy
import json
import math
import sqlite3

from trader.data.store import _safe_path

FEE_RATE = 0.0006
MAX_AGE_MS = 120 * 60_000
MAX_RAW_BYTES = 65_536


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('invalid number')
    return value


def _result(status, *, at=None, mmr=None, price=None, reserve=None):
    return dict(status=status, metadata_at_ms=at, mmr=mmr, fee_rate=FEE_RATE,
                price=price, with_reserve_price=reserve)


def estimate(bot, metadata, metadata_at_ms, now_ms):
    """Estimate from current inventory; never alter position or reserve economics."""
    try:
        q = _number(bot['position_contracts'])
        entry = _number(bot['avg_entry'])
        margin = _number(bot['notional_usdt'])
        reserve = _number(bot.get('reserve_usdt', 0))
        collateral = (margin + _number(bot['realized_pnl'])
                      - _number(bot['fees_paid']) - _number(bot['funding_paid']))
        if margin <= 0 or reserve < 0 or entry < 0 or (q and entry <= 0):
            raise ValueError('invalid position')
        _number(collateral)
    except (KeyError, TypeError, ValueError, OverflowError):
        return _result('INVALID_POSITION')
    if q == 0:
        return _result('FLAT')
    try:
        if (type(metadata_at_ms) is not int or type(now_ms) is not int
                or not 0 <= metadata_at_ms <= now_ms):
            raise ValueError('invalid metadata timestamp')
        mmr = _number(metadata['maintainMargin'])
        cap = _number(metadata['minRiskLimit'])
        if metadata.get('symbol') != bot['symbol'] or not 0 < mmr < 1-FEE_RATE or cap <= 0:
            raise ValueError('invalid metadata')
    except (KeyError, TypeError, ValueError, OverflowError):
        return _result('INVALID_METADATA')
    if now_ms - metadata_at_ms > MAX_AGE_MS:
        return _result('STALE_METADATA', at=metadata_at_ms)
    denominator = q - abs(q) * (mmr + FEE_RATE)
    price = (q * entry - collateral) / denominator
    with_reserve = (q * entry - collateral - reserve) / denominator
    notionals = [abs(q) * entry, abs(q) * max(0, price), abs(q) * max(0, with_reserve)]
    if not all(math.isfinite(v) for v in (price, with_reserve, *notionals)):
        return _result('INVALID_POSITION')
    if max(notionals) > cap:
        return _result('TIER_UNAVAILABLE', at=metadata_at_ms, mmr=mmr)
    if price <= 0:
        return _result('NO_POSITIVE_PRICE', at=metadata_at_ms, mmr=mmr)
    return _result('ESTIMATED', at=metadata_at_ms, mmr=mmr, price=price,
                   reserve=with_reserve if with_reserve > 0 else None)


def enrich(snapshot, database, now_ms):
    """Return enriched open rows; bounded metadata reads use SQLite mode=ro.

    Applies to both top-level and per-direction rows. Database failure leaves an
    explicit unavailable status; it never blocks paper accounting or creates DBs.
    """
    result = deepcopy(snapshot)
    rows = list(result.get('open_bots', []))
    for group in result.get('groups', {}).values():
        rows.extend(group.get('open_bots', []))
    for row in rows:
        row['liquidation'] = _result('FLAT' if row.get('position_contracts') == 0
                                     else 'METADATA_UNAVAILABLE')
    try:
        path = _safe_path(database)
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=.2)) as connection:
            cache = {}
            for row in rows:
                symbol = row.get('symbol')
                if not isinstance(symbol, str) or len(symbol) > 100:
                    continue
                if symbol not in cache:
                    found = connection.execute(
                        'SELECT time_ms, observed_at_ms, length(CAST(raw_json AS BLOB)), '
                        'CASE WHEN length(CAST(raw_json AS BLOB))<=? THEN raw_json ELSE NULL END '
                        'FROM ticker_snapshots WHERE symbol=? '
                        'ORDER BY observed_at_ms DESC, time_ms DESC LIMIT 1',
                        (MAX_RAW_BYTES, symbol)).fetchone()
                    cache[symbol] = found
                found = cache[symbol]
                if found is None:
                    continue
                at, observed, size, raw = found
                try:
                    if (type(at) is not int or type(observed) is not int
                            or not 0 <= at <= now_ms or not 0 <= observed <= now_ms
                            or not isinstance(size, int) or not 0 < size <= MAX_RAW_BYTES):
                        raise ValueError('invalid metadata')
                    metadata = json.loads(raw)
                    if not isinstance(metadata, dict):
                        raise ValueError('invalid metadata')
                    row['liquidation'] = estimate(row, metadata, min(at, observed), now_ms)
                except (TypeError, ValueError, RecursionError):
                    row['liquidation'] = _result('INVALID_METADATA')
    except (OSError, ValueError, sqlite3.Error):
        pass
    return result
