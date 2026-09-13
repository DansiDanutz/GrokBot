"""Bounded agent influence over entry ordering (T8, owner decision 2026-09-13).

The ten native assistants in the Grok Bot desktop app cannot write to this
engine. Their operations steward writes ONE sanitized JSON file; the desk reads
it and nothing else. There is no network call and no model in the trading loop.

An agent may do exactly two things to a candidate the deterministic policy has
ALREADY admitted:

- BOOST: add a bounded delta to the candidate's ordering score inside its own
  radar section. It cannot move a row into another section, and it never
  touches a number the profile, sizing or eligibility maths reads.
- VETO: remove the candidate from consideration for this scan.

An agent can never make an ineligible coin eligible. Every structural and risk
gate (range_not_verified, insufficient_equity, missing_liquidity,
invalid_profile, invalid_layout, missing_live_price, sizing, the liquidation
buffer, the direction/major/movers caps) stays sovereign and is re-evaluated
after influence, exactly as before.

Fail-closed everywhere: a missing, unreadable, oversized, wrong-schema, stale,
future-dated or self-contradicting file is ignored WHOLE and the desk behaves
exactly as it does today. Nothing here raises into the trading loop.

Pure: every function returns new objects; the caller's rows are never mutated.
See docs/agent-influence.md.
"""
import json
import math
import os
from pathlib import Path

from trader.autopilot.constants import (INFLUENCE_MAX_AGE_MIN, INFLUENCE_MAX_DELTA,
                                        INFLUENCE_MAX_TOTAL_DELTA)

SCHEMA_VERSION = 1
DEFAULT_PATH = str(Path.home() / 'Sandbox' / 'grokbot' / 'team-evidence' / 'influence.json')
MAX_BYTES = 65_536
MAX_RECORDS = 40
EVIDENCE_REF_MAX = 120
MINUTE_MS = 60_000

# Closed roster: the eight review roles that may speak, plus the steward who
# owns sanitized team-evidence/ and is the only writer of the file. The
# user-facing Paper Desk Secretary is deliberately absent -- it holds no
# analytical opinion and makes no board writes.
ROSTER = (
    'grid_desk_lead',
    'strategy_manager',
    'data_and_structure',
    'technical_interpreter',
    'risk_sentinel',
    'performance_analyst',
    'x_setup_researcher',
    'research_scout',
    'dans_senior_developer',  # operations steward, sole writer of the file
)

VERBS = ('BOOST', 'VETO')
DIRECTIONS = ('LONG', 'SHORT', 'NEUTRAL')

# Closed reason vocabulary. Free text never reaches the engine.
REASON_CODES = {
    1: 'x_setup_evidence',            # an original X/social setup read
    2: 'structure_doubt',             # the range or level looks unconvincing
    3: 'risk_objection',              # exposure, correlation or funding objection
    4: 'performance_history',         # this coin/profile has a measured record
    5: 'liquidation_cluster_proximity',  # a cluster sits against the entry
    6: 'research_corroboration',      # papers/docs/video research agrees
}

# Slot direction each radar section feeds. Mirrors policy._candidates; the pair
# is asserted equal in trader/tests/test_agent_influence.py so it cannot drift.
SECTION_DIRECTIONS = {
    'turning_up': 'LONG', 'long': 'LONG',
    'turning_down': 'SHORT', 'short': 'SHORT',
    'neutral': 'NEUTRAL', 'movers': 'NEUTRAL',
}

_RECORD_KEYS = frozenset({'agent_id', 'symbol', 'direction', 'verb', 'delta',
                          'reason_code', 'evidence_ref'})
_FILE_KEYS = frozenset({'schema_version', 'generated_at_ms', 'records'})
_SYMBOL_CHARS = set('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789')
_EVIDENCE_CHARS = set('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'
                      '0123456789-_.:#@+=,() ')


def _is_number(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _symbol_ok(value):
    return (isinstance(value, str) and 1 <= len(value) <= 32
            and set(value) <= _SYMBOL_CHARS)


def _evidence_ok(value):
    """Bounded opaque reference: no path separators, no whitespace but single spaces."""
    if not isinstance(value, str) or not 1 <= len(value) <= EVIDENCE_REF_MAX:
        return False
    if set(value) - _EVIDENCE_CHARS or '  ' in value:
        return False
    return value == value.strip()


def _record_problems(record, index, known_symbols):
    """Schema violations of one record, each as a short stable code."""
    if not isinstance(record, dict):
        return ['record_%d_not_an_object' % index]
    problems = []
    if set(record) != _RECORD_KEYS:
        problems.append('record_%d_keys' % index)
        return problems
    if record['agent_id'] not in ROSTER:
        problems.append('record_%d_unknown_agent' % index)
    if not _symbol_ok(record['symbol']):
        problems.append('record_%d_invalid_symbol' % index)
    elif known_symbols is not None and record['symbol'] not in known_symbols:
        problems.append('record_%d_unknown_symbol' % index)
    if record['direction'] not in DIRECTIONS:
        problems.append('record_%d_invalid_direction' % index)
    if record['verb'] not in VERBS:
        problems.append('record_%d_invalid_verb' % index)
    # A VETO carries no delta: it removes the candidate, it does not reorder it.
    if (not _is_number(record['delta']) or record['delta'] < 0
            or (record['verb'] == 'VETO' and record['delta'] != 0)):
        problems.append('record_%d_invalid_delta' % index)
    if record['reason_code'] not in REASON_CODES or isinstance(record['reason_code'], bool):
        problems.append('record_%d_invalid_reason_code' % index)
    if not _evidence_ok(record['evidence_ref']):
        problems.append('record_%d_invalid_evidence_ref' % index)
    return problems


def _payload_problems(payload, now_ms):
    """Envelope violations, including expiry. Empty list means the envelope is usable."""
    if not isinstance(payload, dict):
        return ['not_an_object']
    problems = []
    if set(payload) != _FILE_KEYS:
        return ['file_keys']
    if payload['schema_version'] != SCHEMA_VERSION or isinstance(payload['schema_version'], bool):
        problems.append('schema_version')
    generated = payload['generated_at_ms']
    if not _is_number(generated):
        problems.append('generated_at_ms')
    elif generated > now_ms:
        problems.append('future')
    elif now_ms - generated > INFLUENCE_MAX_AGE_MIN * MINUTE_MS:
        problems.append('stale')
    if not isinstance(payload['records'], list):
        problems.append('records')
    elif len(payload['records']) > MAX_RECORDS:
        problems.append('too_many_records')
    return problems


def _read(path):
    """(payload, problems). Never raises: an unreadable file is just a problem."""
    try:
        if os.path.getsize(path) > MAX_BYTES:
            return None, ['too_large']
        with open(path, 'r', encoding='utf-8') as handle:
            return json.loads(handle.read()), []
    except (OSError, ValueError, UnicodeDecodeError):
        return None, ['unreadable']


def load(path, now_ms, known_symbols=None):
    """Validate the steward's file. Returns (records, problems).

    Fail-closed: ANY problem yields ([], problems) -- the whole file is ignored
    and the desk behaves exactly as it does today. `known_symbols`, when given,
    is the radar universe; a record naming a symbol outside it is a schema
    error, because the steward wrote about a coin the desk cannot see.

    Records carry no timestamp of their own: the envelope's generated_at_ms is
    their age, so one stale file expires every record in it at once.
    """
    payload, problems = _read(path)
    if problems:
        return [], problems
    problems = _payload_problems(payload, now_ms)
    if problems:
        return [], problems
    seen = set()
    for index, record in enumerate(payload['records']):
        problems.extend(_record_problems(record, index, known_symbols))
        if isinstance(record, dict) and isinstance(record.get('symbol'), str):
            key = (record.get('agent_id'), record['symbol'])
            if key in seen:
                problems.append('record_%d_duplicate' % index)
            seen.add(key)
    if problems:
        return [], problems
    return [dict(record) for record in payload['records']], []


def _index(candidates):
    """{(symbol, slot direction): section name} over the admitted candidates.

    A symbol listed in two sections that feed the same slot direction keeps the
    first section name in sorted order, so the mapping is deterministic.
    """
    index = {}
    for name in sorted(candidates or {}):
        direction = SECTION_DIRECTIONS.get(name)
        if direction is None:
            continue
        for row in candidates[name] or ():
            index.setdefault((row['symbol'], direction), name)
    return index


def _entry(record, section, delta, granted, now_ms):
    """One audit entry: what an agent asked for and what the bounds allowed."""
    requested, allowed = round(float(delta), 6), round(float(granted), 6)
    return dict(ts_ms=int(now_ms), agent_id=record['agent_id'], symbol=record['symbol'],
                direction=record['direction'], verb=record['verb'],
                section=section, reason_code=int(record['reason_code']),
                evidence_ref=record['evidence_ref'],
                requested_delta=requested, delta=allowed,
                clamped=1 if requested != allowed else 0)


def apply(candidates, records, now_ms):
    """Apply validated influence to admitted candidates. Returns (adjusted, applied).

    `candidates` is policy's section mapping {section: [row]}. `adjusted` is a
    NEW mapping of NEW rows: boosted rows carry `influence_delta`, vetoed rows
    are gone. `applied` is the audit trail, one entry per influence that took
    effect. An influence naming a candidate the policy did not admit is a
    silent no-op -- it is not applied, so it is not audited as applied.

    Vetoes resolve first, so a boost can never spend the scan's delta budget on
    a candidate another agent removed.
    """
    index = _index(candidates)
    records = list(records or ())
    applied, vetoed = [], set()
    for record in records:
        key = (record['symbol'], record['direction'])
        if record['verb'] != 'VETO' or key not in index or key in vetoed:
            continue
        vetoed.add(key)
        applied.append(_entry(record, index[key], 0.0, 0.0, now_ms))
    budget, granted = INFLUENCE_MAX_TOTAL_DELTA, {}
    for record in records:
        key = (record['symbol'], record['direction'])
        if record['verb'] != 'BOOST' or key not in index or key in vetoed:
            continue
        room = min(INFLUENCE_MAX_DELTA - granted.get(key, 0.0), budget)
        allowed = max(0.0, min(float(record['delta']), room))
        granted[key] = granted.get(key, 0.0) + allowed
        budget -= allowed
        applied.append(_entry(record, index[key], record['delta'], allowed, now_ms))
    return _adjust(candidates, granted, vetoed), applied


def _adjust(candidates, granted, vetoed):
    """New sections: vetoed rows dropped, boosted rows carrying influence_delta."""
    adjusted = {}
    for name, rows in (candidates or {}).items():
        direction = SECTION_DIRECTIONS.get(name)
        kept = []
        for row in rows or ():
            key = (row['symbol'], direction)
            if key in vetoed:
                continue
            delta = granted.get(key, 0.0)
            kept.append(dict(row, influence_delta=delta) if delta else dict(row))
        adjusted[name] = kept
    return adjusted


def vetoed_rows(candidates, applied):
    """[(section, row, direction)] for every veto that removed an admitted candidate.

    The caller replays these through its own eligibility check so a veto that
    turned away a fillable entry is reported as a skip, and priced by the
    counterfactual replay like any other refusal.
    """
    removed = []
    for entry in applied or ():
        if entry['verb'] != 'VETO':
            continue
        for row in (candidates or {}).get(entry['section'], ()):
            if row['symbol'] == entry['symbol']:
                removed.append((entry['section'], dict(row), entry['direction']))
                break
    return removed
