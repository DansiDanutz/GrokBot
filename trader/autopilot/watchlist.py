"""Pure scored watchlist transitions, once per distinct radar scan."""
from copy import deepcopy
from decimal import Decimal, InvalidOperation

from .constants import (BENCH_SIZE, CORE_MIN_HOLD_HOURS, CORE_SIZE,
                        MAX_SWAPS_PER_SCAN, PROMOTION_MARGIN)

DAY_MS = 86_400_000


def initial():
    return {'core': [], 'bench': [], 'last_scan_id': None, 'misses': {},
            'history': [], 'swap_times': [], 'asof_ms': None}


def _order(entry):
    return (-entry['score'], entry['symbol'])


def _entry(row, now_ms, previous=None):
    return {'symbol': row['symbol'], 'direction': row['direction'],
            'score': row['score'], 'score_parts': deepcopy(row['score_parts']),
            'since_ms': previous['since_ms'] if previous else now_ms, 'rank': 0}


def _rank(entries):
    entries.sort(key=_order)
    for rank, entry in enumerate(entries, 1):
        entry['rank'] = rank
    return entries


def _event(kind, entry, now_ms, replaced=None, margin=0):
    return {'ts_ms': now_ms, 'type': kind, 'symbol': entry['symbol'],
            'score': entry['score'],
            'replaced_symbol': replaced['symbol'] if replaced else '',
            'replaced_score': replaced['score'] if replaced else 0,
            'margin': margin}


def is_older(scan_id, previous):
    """Whether two finite numeric scan IDs establish strict regression."""
    if previous is None:
        return False
    try:
        current, old = Decimal(str(scan_id)), Decimal(str(previous))
        return current.is_finite() and old.is_finite() and current < old
    except InvalidOperation:
        return False


def _seen(scan_id, previous):
    if previous is None:
        return False
    if str(scan_id) == str(previous):
        return True
    # Numeric representations such as 100 and "100.0" are the same scan.
    try:
        current, old = Decimal(str(scan_id)), Decimal(str(previous))
        equal = current.is_finite() and old.is_finite() and current == old
    except InvalidOperation:
        equal = False
    return equal or is_older(scan_id, previous)


def update(watchlist, rows, now_ms, scan_id):
    """Return independent state and events; caller supplies qualifying scored rows.

    Numeric scan IDs must increase. Tier age resets on promotion/demotion, while
    score or direction refreshes preserve it. Drops remove every missing coin,
    but at most one seat is filled per scan after cold start. Any drop or
    vacancy fill suppresses discretionary promotion for that scan.
    """
    state = deepcopy(watchlist)
    if _seen(scan_id, state['last_scan_id']):
        return state, []
    cold = state['last_scan_id'] is None
    state['last_scan_id'] = scan_id
    state['asof_ms'] = now_ms
    state['swap_times'] = [ts for ts in state['swap_times']
                           if ts >= now_ms - 30 * DAY_MS]
    # Highest-scoring duplicate wins; direction breaks otherwise equal ties.
    candidates = {}
    for row in sorted(rows, key=lambda row: (*_order(row), row['direction'])):
        candidates.setdefault(row['symbol'], row)
    events = []
    previous_core = {entry['symbol']: entry for entry in state['core']}
    previous_bench = {entry['symbol']: entry for entry in state['bench']}
    dropped = []
    core = []
    for symbol, old in previous_core.items():
        row = candidates.get(symbol)
        if row is None:
            misses = state['misses'].get(symbol, 0) + 1
            state['misses'][symbol] = misses
            if misses >= 2:
                dropped.append(old)
                events.append(_event('DROP', old, now_ms))
                continue
            core.append(old)
        else:
            state['misses'][symbol] = 0
            fresh = _entry(row, now_ms, old)
            core.append(fresh)
            if old['direction'] != fresh['direction']:
                events.append(_event('DIRECTION_CHANGE', fresh, now_ms))

    def bench_for(current_core, previous):
        symbols = {entry['symbol'] for entry in current_core}
        return _rank([_entry(row, now_ms, previous.get(row['symbol']))
                      for row in sorted(candidates.values(), key=_order)
                      if row['symbol'] not in symbols][:BENCH_SIZE])

    # Drop all failed coins immediately; refill at most one seat per scan.
    available = [row for row in sorted(candidates.values(), key=_order)
                 if row['symbol'] not in {entry['symbol'] for entry in core}]
    vacancies = max(0, CORE_SIZE - len(core))
    fill_limit = vacancies if cold else min(vacancies, MAX_SWAPS_PER_SCAN)
    filled = min(fill_limit, len(available))
    for index, row in enumerate(available[:fill_limit]):
        promoted = _entry(row, now_ms)
        core.append(promoted)
        if not cold:
            victim = dropped[index] if index < len(dropped) else None
            margin = promoted['score'] - victim['score'] if victim else 0
            events.append(_event('PROMOTE', promoted, now_ms, victim, margin))
            state['swap_times'].append(now_ms)
    bench = bench_for(core, previous_bench)
    if (not cold and not dropped and not filled and MAX_SWAPS_PER_SCAN > 0 and
            len(core) == CORE_SIZE and bench):
        victim = sorted(core, key=_order)[-1]
        challenger = bench[0]
        margin = challenger['score'] - victim['score']
        held = now_ms - victim['since_ms']
        if margin >= PROMOTION_MARGIN and held >= CORE_MIN_HOLD_HOURS * 3_600_000:
            promoted = _entry(challenger, now_ms)
            core.remove(victim)
            core.append(promoted)
            events.extend([_event('DEMOTE', victim, now_ms, promoted, margin),
                           _event('PROMOTE', promoted, now_ms, victim, margin)])
            state['swap_times'].append(now_ms)
            # A newly demoted coin starts a fresh bench tenure.
            previous_bench.pop(victim['symbol'], None)
            bench = bench_for(core, previous_bench)
    retained = {entry['symbol'] for entry in core + bench}
    for symbol, old in previous_bench.items():
        row = candidates.get(symbol)
        if (symbol in retained and row and
                row['direction'] != old['direction']):
            events.append(_event('DIRECTION_CHANGE', row, now_ms))
    state['core'] = _rank(core)
    state['bench'] = bench
    state['misses'] = {entry['symbol']: state['misses'].get(entry['symbol'], 0)
                       for entry in core}
    state['history'] = (state['history'] + events)[-48:]
    return state, events
