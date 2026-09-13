"""Deterministic repeated-pivot structure from completed hourly candles."""
import hashlib
import json
from statistics import median

HOUR_MS = 3600000
WINDOW_HOURS = 168
METHOD = 'repeated_hourly_pivots_v1'
# The collector commits each closed hour a few minutes after it closes, so the
# newest stored hour normally trails the wall clock by one. Anchor the window to
# the data and bound how far it may trail, rather than demanding an hour the
# collector has not written yet.
MAX_COLLECTION_LAG_HOURS = 2


def _candidates(hourly, price, atr):
    pivots = []
    rows = hourly[-WINDOW_HOURS:]
    tolerance = min(atr * .25, price * .005)
    for i in range(2, len(rows)-2):
        neighbors = rows[i-2:i] + rows[i+1:i+3]
        low, high = rows[i][3], rows[i][2]
        confirmed = rows[i+2][0] + HOUR_MS
        if low < min(r[3] for r in neighbors):
            pivots.append((low, rows[i][0], 'low', confirmed))
        if high > max(r[2] for r in neighbors):
            pivots.append((high, rows[i][0], 'high', confirmed))
    clusters = []
    for pivot in sorted(pivots):
        if not clusters or pivot[0]-clusters[-1][0][0] > tolerance:
            clusters.append([])
        clusters[-1].append(pivot)
    supports, resistances = [], []
    for cluster in clusters:
        times = sorted(set(p[1] for p in cluster))
        if len(times) < 2 or times[-1]-times[0] < 3*HOUR_MS:
            continue
        level = median(p[0] for p in cluster)
        proof = dict(price=level, touches=len(times), pivot_times_ms=times,
                     confirmed_at_ms=sorted(set(p[3] for p in cluster)))
        if level < price and (any(p[2] == 'low' for p in cluster)
                              or all(r[4] > level for r in rows[-2:])):
            supports.append(proof)
        if level > price and (any(p[2] == 'high' for p in cluster)
                               or all(r[4] < level for r in rows[-2:])):
            resistances.append(proof)
    return (sorted(supports, key=lambda item: item['price']),
            sorted(resistances, key=lambda item: item['price']))


def candidates(hourly, price, atr):
    """Compatibility diagnostics; production admission uses assess's coverage gate."""
    supports, resistances = _candidates(hourly, price, atr)
    return ([(p['price'], p['touches']) for p in supports],
            [(p['price'], p['touches']) for p in resistances])


def assess(hourly, price, atr, asof_ms):
    """Require the complete latest 168-hour window before confirming structure.

    "Latest" means the newest completed hour actually stored, not the newest the
    clock implies; `MAX_COLLECTION_LAG_HOURS` bounds how far behind that may fall.
    """
    clock_end = asof_ms // HOUR_MS * HOUR_MS
    stored = [row[0] for row in hourly if row[0] < clock_end]
    end = min(clock_end, max(stored) + HOUR_MS) if stored else clock_end
    lag_hours = (clock_end - end) // HOUR_MS
    start = end - WINDOW_HOURS*HOUR_MS
    rows = [row for row in hourly if start <= row[0] < end]
    expected = list(range(start, end, HOUR_MS))
    observed = [row[0] for row in rows]
    contiguous = observed == expected
    coverage = len(set(observed) & set(expected))/WINDOW_HOURS
    evidence = dict(version=1, method=METHOD, venue='KuCoin', timeframe='1h',
                    analysis_asof_ms=asof_ms, candle_asof_ms=max(observed)+HOUR_MS if observed else None,
                    window_start_ms=start, window_end_ms=end, expected_candles=WINDOW_HOURS,
                    observed_candles=len(rows), coverage_ratio=coverage, contiguous=contiguous,
                    collection_lag_hours=lag_hours,
                    status='REJECTED', reason='INCOMPLETE_RECENT_HOURLY_HISTORY',
                    candles_sha256=hashlib.sha256(json.dumps(rows,separators=(',',':'),allow_nan=False).encode()).hexdigest(),
                    tolerance_price=min(atr*.25,price*.005), pivot_confirmation_candles=2,
                    supports=[], resistances=[])
    if lag_hours > MAX_COLLECTION_LAG_HOURS:
        evidence.update(reason='STALE_HOURLY_HISTORY')
        return evidence
    if not contiguous:
        return evidence
    supports, resistances = _candidates(rows,price,atr)
    evidence.update(supports=supports,resistances=resistances,
                    status='VERIFIED' if supports and resistances else 'MISSING_STRUCTURE',
                    reason='' if supports and resistances else 'MISSING_STRUCTURE')
    return evidence


def levels(hourly, price, atr):
    """Nearest confirmed pair, retained for structure diagnostics."""
    supports, resistances = candidates(hourly, price, atr)
    support = max(supports, default=(None, 0))
    resistance = min(resistances, default=(None, 0))
    return dict(support=support[0], support_touches=support[1],
                resistance=resistance[0], resistance_touches=resistance[1])
