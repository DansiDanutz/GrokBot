"""Versioned paper entry evidence; funding scenarios are not realized profits."""
import math
from copy import deepcopy
from trader.radar.spacing import funding_stress

VERSION = 'funding4h_retest1h_v1'
HOUR_MS = 3_600_000
FUNDING_HOURS = 4
MIN_GRIDS = 70
MAX_EVIDENCE_AGE_MS = 2 * HOUR_MS


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def funding_window(row, now_ms):
    rate, interval, observed = (row.get(k) for k in
                               ('funding_pct', 'funding_interval_ms', 'funding_asof_ms'))
    if (not all(_number(v) for v in (rate, interval, observed, now_ms)) or
            not 0 < interval <= 24 * HOUR_MS or
            not 0 <= now_ms - observed <= MAX_EVIDENCE_AGE_MS):
        return dict(status='REJECTED', reason='UNKNOWN_FUNDING_METADATA')
    return dict(status='READY', horizon_hours=FUNDING_HOURS,
                settlements=math.ceil(FUNDING_HOURS * HOUR_MS / interval),
                interval_ms=interval, rate_pct=rate, observed_ms=observed,
                timing='worst_case_settlement_phase')


def _retest_reason(proof, row, direction, now_ms):
    try:
        side = proof['side']
        t, o, h, l, c, volume = proof['candle']
        pt, po, ph, pl, pc, pv = proof['previous']
        level, atr, extreme = proof['level'], proof['atr_1h'], proof['prior_24h_extreme']
        price = row['price']
        values = (t, o, h, l, c, pt, po, ph, pl, pc, level, atr, extreme, price)
        if (not all(_number(v) for v in values) or min(o,h,l,c,po,ph,pl,pc,level,atr,price) <= 0
                or side not in ('LONG','SHORT') or direction not in ('LONG','SHORT','NEUTRAL')
                or (direction != 'NEUTRAL' and side != direction)):
            return 'INVALID_RETEST_EVIDENCE'
        if (t % HOUR_MS or pt != t-HOUR_MS or
                not 0 <= now_ms-(t+HOUR_MS) <= MAX_EVIDENCE_AGE_MS):
            return 'STALE_OR_INCOMPLETE_RETEST'
        pivots, confirmed = proof['pivot_times_ms'], proof['confirmed_at_ms']
        if (len(pivots) < 2 or len(pivots) != len(confirmed)
                or not all(_number(x) for x in pivots+confirmed)
                or max(pivots)-min(pivots) < 3*HOUR_MS
                or any(p >= q or q > t for p,q in zip(pivots,confirmed))):
            return 'LEVEL_NOT_CONFIRMED_BEFORE_RETEST'
        tolerance = min(atr*.25, proof['analysis_price']*.005)
        if not _number(tolerance) or tolerance <= 0:
            return 'INVALID_RETEST_EVIDENCE'
        if side == 'LONG':
            held = pc > level+tolerance and level-tolerance <= l <= level+tolerance and c > level+tolerance and c > o
            inside = price > level+tolerance and price < extreme
        else:
            held = pc < level-tolerance and level-tolerance <= h <= level+tolerance and c < level-tolerance and c < o
            inside = price < level-tolerance and price > extreme
        if not held:
            return 'WAIT_1H_RETEST'
        if not inside or abs(price-c) > atr*.25:
            return 'RETEST_PRICE_MOVED'
        if not row['range_low'] < level < row['range_high']:
            return 'RETEST_OUTSIDE_RANGE'
        return ''
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return 'INVALID_RETEST_EVIDENCE'


def retest(hourly, basis, row, direction, now_ms):
    closed = [r for r in hourly if r[0]+HOUR_MS <= now_ms]
    if len(closed) < 25 or any(b[0]-a[0] != HOUR_MS for a,b in zip(closed[-25:],closed[-24:])):
        return dict(status='REJECTED', reason='INCOMPLETE_RETEST_HISTORY')
    prior, candle = closed[-2], closed[-1]
    sides = ('LONG','SHORT') if direction == 'NEUTRAL' else (direction,)
    reason = 'WAIT_1H_RETEST'
    for side in sides:
        points = basis.get('supports' if side == 'LONG' else 'resistances', [])
        for point in sorted(points, key=lambda p: abs(row['price']-p['price'])):
            pairs = [(p,c) for p,c in zip(point['pivot_times_ms'], point['confirmed_at_ms']) if c <= candle[0]]
            proof = dict(side=side, candle=list(candle), previous=list(prior),
                         level=point['price'], analysis_price=row['price'],
                         atr_1h=row['atr_1h_pct']*row['price']/100,
                         pivot_times_ms=[p for p,c in pairs], confirmed_at_ms=[c for p,c in pairs],
                         prior_24h_extreme=(max(r[2] for r in closed[-25:-1]) if side == 'LONG'
                                           else min(r[3] for r in closed[-25:-1])))
            reason = _retest_reason(proof,row,direction,now_ms)
            if not reason:
                return dict(proof,status='CONFIRMED',confirmed_close_ms=candle[0]+HOUR_MS)
    return dict(status='REJECTED',reason=reason)


def admission(row, direction, now_ms, *, required_version=None):
    version = row.get('entry_policy_version')
    if version is None and required_version is None:
        return ''
    if version != VERSION or required_version not in (None,VERSION):
        return 'ENTRY_POLICY_MISMATCH'
    try:
        if type(row['grids']) is not int or not MIN_GRIDS <= row['grids'] <= 200:
            return 'INSUFFICIENT_GRID_ROOM'
        window = funding_window(row,now_ms)
        if window['status'] != 'READY':
            return window['reason']
        scenario = funding_stress(row['range_low'],row['range_high'],row['grids'],
                                  rate_pct=row['funding_pct'],settlements=window['settlements'],
                                  tick_size=row.get('tick_size',0),direction=direction)
        if not scenario.get('passes_floor'):
            return 'FUNDING_RETURN_BELOW_1_PERCENT'
        proof = row['entry_retests'][direction]
        if proof.get('status') != 'CONFIRMED':
            return proof.get('reason') or 'WAIT_1H_RETEST'
        return _retest_reason(proof,row,direction,now_ms)
    except (KeyError,TypeError,ValueError):
        return 'INVALID_ENTRY_EVIDENCE'


def receipt(row, direction, now_ms):
    window = funding_window(row,now_ms)
    return dict(version=VERSION, checked_at_ms=now_ms, minimum_grids=MIN_GRIDS,
                funding=window, funding_scenario=funding_stress(
                    row['range_low'],row['range_high'],row['grids'],
                    tick_size=row.get('tick_size',0),direction=direction,
                    rate_pct=row['funding_pct'],settlements=window['settlements']),
                retest=deepcopy(row['entry_retests'][direction]))
