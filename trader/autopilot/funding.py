"""Funding settlements from stored evidence; rates are fractional, not percentages.

Exact settlement history wins. A fresh pre-settlement contract snapshot is only
an estimate for its explicitly announced next settlement, never a rate to carry
backward or forward across unobserved historical boundaries.
"""
from contextlib import closing
import json
import math
import sqlite3
from trader.data.store import _safe_path

MAX_AGE_MS = 120 * 60000
MAX_RAW_BYTES = 65536


def settlements(database, symbol, after_ms, through_ms):
    path = _safe_path(database)
    found, latest = {}, None
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)) as db:
        rows = db.execute(
            'SELECT time_ms, observed_at_ms, funding_rate, raw_json FROM ticker_snapshots '
            'WHERE symbol=? AND observed_at_ms>=? AND observed_at_ms<=? '
            'AND length(CAST(raw_json AS BLOB))<=? ORDER BY observed_at_ms, time_ms',
            (symbol, max(0, after_ms-MAX_AGE_MS), through_ms, MAX_RAW_BYTES))
        for at, observed, rate, raw in rows:
            try:
                meta = json.loads(raw)
                interval = meta['fundingRateGranularity']
                boundary = meta['nextFundingRateDateTime']
                if (type(interval) is not int or not 0 < interval <= 24*3600000
                        or type(boundary) is not int or boundary < observed
                        or not 0 <= at <= observed or observed-at > MAX_AGE_MS
                        or not math.isfinite(rate)):
                    continue
                if through_ms-min(at, observed) <= MAX_AGE_MS:
                    latest = dict(interval_ms=interval, next_ms=boundary, rate=rate)
                    margin, limit = meta.get('maintainMargin'), meta.get('minRiskLimit')
                    if (type(margin) in (int, float) and math.isfinite(margin) and 0 < margin < 1
                            and type(limit) in (int, float) and math.isfinite(limit) and limit > 0):
                        latest.update(maintain_margin=margin, risk_limit=limit,
                                      risk_metadata_at_ms=observed)
                if (after_ms < boundary <= through_ms
                        and boundary-min(at, observed) <= MAX_AGE_MS):
                    found[boundary] = dict(ts_ms=boundary, rate=rate, interval_ms=interval,
                                           estimated=True)
            except (ValueError, TypeError, KeyError, RecursionError):
                continue
        for at, rate, interval in db.execute(
                'SELECT time_ms, rate, period_ms FROM funding WHERE symbol=? '
                'AND time_ms>? AND time_ms<=? ORDER BY time_ms',
                (symbol, after_ms, through_ms)):
            if math.isfinite(rate):
                found[at] = dict(ts_ms=at, rate=rate, interval_ms=interval, estimated=False)
    return [found[at] for at in sorted(found)], latest


def charge(database, bot, through_ms):
    """Mutate the engine ledger once through this time using its pre-tick position.

The cursor is persisted with the ledger. Missing evidence is marked unavailable;
we do not fabricate a retroactive rate or silently apply the engine's 8h default.
"""
    bot['funding_managed'] = True
    after = max(bot.get('funding_checked_through_ms', bot['last_ts_ms']),
                bot['opened_ms'], bot['last_funding_ts_ms'])
    if through_ms <= after:
        return
    entries, metadata = settlements(database, bot['symbol'], after, through_ms)
    for entry in entries:
        bot['funding_paid'] += bot['position_contracts'] * bot['last_price'] * entry['rate']
        bot['last_funding_ts_ms'] = entry['ts_ms']
        if entry['interval_ms'] is not None:
            bot['funding_interval_ms'] = entry['interval_ms']
    if metadata:
        bot['funding_interval_ms'] = metadata['interval_ms']
        bot['next_funding_ms'] = metadata['next_ms']
        bot['funding_pct'] = metadata['rate'] * 100
        if 'risk_metadata_at_ms' in metadata:
            for key in ('maintain_margin', 'risk_limit', 'risk_metadata_at_ms'):
                bot[key] = metadata[key]
    bot['funding_schedule_status'] = ('ESTIMATED' if metadata or any(e['estimated'] for e in entries)
                                       else 'RECORDED' if entries else 'UNAVAILABLE')
    bot['funding_checked_through_ms'] = through_ms
