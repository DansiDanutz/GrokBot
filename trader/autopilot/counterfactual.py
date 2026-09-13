"""Record the structurally valid entries the desk declined for policy reasons.

Every scan the policy emits `DECISION action=skip` events. Most of them mean the
setup itself was unusable (no verified range, no liquidity, a layout that cannot
be built) — nothing to replay there. The rest mean the setup was fine and the
desk simply had no room for it: the bot cap, the direction cap, a duplicate
symbol, a cooldown, or one of the learned rules. Those are the decisions worth
second-guessing, so this module rebuilds the exact bot specification the desk
would have opened and appends it to a daily JSONL file. The 07:00 review replays
those records (trader.review.counterfactual) to price what the caps cost or saved.

Pure builders plus one append-only writer. Nothing here reads or mutates trading
state, and the runtime treats every failure as research noise, never as an error.
"""
from copy import deepcopy
from datetime import timedelta
import json
import math
import os
from pathlib import Path
import re

from trader.autopilot import policy
from trader.autopilot.storage import _day, _safe

RETENTION_DAYS = 30
FILE_PREFIX = 'candidates-'
FILE_SUFFIX = '.jsonl'
FILE_GLOB = FILE_PREFIX + '????-??-??' + FILE_SUFFIX
_DATE_RE = re.compile(r'\d{4}-\d{2}-\d{2}')

# The desk's own capacity and policy choices. A skip carrying only these codes
# describes a bot that could have been opened; anything else (range not verified,
# invalid profile/layout, missing liquidity or price) describes a setup that
# could not, so replaying it would invent a trade the desk never had.
POLICY_BLOCK_CODES = frozenset(
    policy.DECISION_RULES[name] for name in (
        'bot_capacity', 'duplicate_symbol', 'cooldown', 'direction_cap', 'major_cap',
        'movers_cap', 'learned_trend_alignment', 'learned_symbol_cooldown',
        'learned_min_hold'))


def _is_policy_skip(event):
    if not isinstance(event, dict) or event.get('type') != 'DECISION':
        return False
    if event.get('action') != 'skip':
        return False
    blocks = event.get('rule_blocks')
    if not isinstance(blocks, list) or not blocks:
        return False
    return set(blocks) <= POLICY_BLOCK_CODES


def _number(value, default=0.0):
    """Finite float or the default: a JSONL line must never carry NaN/Infinity."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return default
    return float(value)


def _record(row, event, spec, scan_id, now_ms):
    """One replayable candidate: the decision's identity plus the exact spec."""
    record = dict(
        ts_ms=int(event.get('ts_ms', now_ms)),
        scan_id=scan_id,
        symbol=spec['symbol'],
        direction=spec['direction'],
        rule_blocks=sorted(set(int(code) for code in event['rule_blocks'])),
        price=_number(row.get('price')),
        range_low=_number(spec['range_low']),
        range_high=_number(spec['range_high']),
        grids=int(spec['grids']),
        leverage=_number(spec['leverage']),
        notional_usdt=_number(spec['notional_usdt']),
        funding_pct=_number(spec['funding_pct']),
        expected_grids_per_hour=_number(event.get('expected_grids_per_hour')),
        rank_score=_number(row.get('rank_score')),
        score=_number(row.get('score'), _number(row.get('rank_score'))),
        spec=deepcopy(spec),
    )
    # Arithmetic layouts carry the interval; anything else carries the percentage.
    if 'grid_interval' in spec:
        record['grid_interval'] = _number(spec['grid_interval'])
    else:
        record['step_pct'] = _number(spec['step_pct'])
    return record


def candidates(rows, events, scan_id, now_ms):
    """Replayable candidates from one decision pass, deduped per scan/symbol/direction."""
    by_symbol = {row['symbol']: row for row in rows or ()
                 if isinstance(row, dict) and isinstance(row.get('symbol'), str)}
    seen, built = set(), []
    for event in events or ():
        if not _is_policy_skip(event):
            continue
        key = (scan_id, event.get('symbol'), event.get('direction'))
        row = by_symbol.get(event.get('symbol'))
        if row is None or key in seen:
            continue
        try:
            spec, _ = policy.profile(row, event.get('direction'), 0)
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            continue
        seen.add(key)
        built.append(_record(row, event, spec, scan_id, now_ms))
    return built


def file_name(now_ms):
    return FILE_PREFIX + _day(now_ms).isoformat() + FILE_SUFFIX


def append(directory, records, now_ms):
    """Append records to the UTC daily file in one O_APPEND write."""
    directory = _safe(Path(directory))
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / file_name(now_ms)
    payload = ''.join(json.dumps(record, allow_nan=False, sort_keys=True) + '\n'
                      for record in records).encode()
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'ab') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    return path


def retain(paths, now_ms, days=RETENTION_DAYS):
    """Return the daily files whose day falls outside the retention window."""
    cutoff = (_day(now_ms) - timedelta(days=days - 1)).isoformat()
    expired = []
    for path in paths:
        name = Path(path).name
        stamp = name[len(FILE_PREFIX):len(FILE_PREFIX) + 10]
        if not name.startswith(FILE_PREFIX) or not name.endswith(FILE_SUFFIX):
            continue
        if _DATE_RE.fullmatch(stamp) and stamp < cutoff:
            expired.append(path)
    return expired


def prune(directory, now_ms, days=RETENTION_DAYS):
    """Delete expired daily files; returns the paths removed."""
    directory = Path(directory)
    if not directory.is_dir():
        return []
    expired = retain(sorted(directory.glob(FILE_GLOB)), now_ms, days)
    for path in expired:
        _safe(path)
        path.unlink()
    return expired


def record(directory, rows, events, scan_id, now_ms, *, days=RETENTION_DAYS):
    """Build and persist this pass's candidates, then enforce retention."""
    built = candidates(rows, events, scan_id, now_ms)
    if built:
        append(directory, built, now_ms)
    prune(directory, now_ms, days)
    return built
