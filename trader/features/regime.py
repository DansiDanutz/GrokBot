"""Pure new-entry direction gate from causal BTC/ETH and optional SOL chart reads.

This function neither calculates chart indicators nor closes existing positions.
Unknown ecosystem membership retains the macro gate and records the omitted SOL
check. Membership comes from an explicit maintained registry, never symbol text.
"""
from copy import deepcopy
import math

TIMEFRAMES = {'4h': 14_400_000, '1d': 86_400_000}
DIRECTIONS = ('long', 'short', 'neutral')
ALLOWED = {'up': ('long', 'neutral'), 'down': ('short', 'neutral'),
           'neutral': ('neutral',), 'unknown': ('neutral',)}
SOL_SOURCE = 'https://www.kucoin.com/learn/glossary/solana-sol'


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _evidence(value):
    if type(value) is float and not math.isfinite(value):
        return {'invalid_numeric': str(value)}
    if isinstance(value, dict):
        return {key: _evidence(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_evidence(item) for item in value]
    return deepcopy(value)


def _read(raw, symbol, timeframe, asof_ms, minimum):
    row = dict(symbol=symbol, timeframe=timeframe, known=False, direction='unknown',
               raw_read=_evidence(raw), reason='chart read is missing')
    if not isinstance(raw, dict):
        return row
    confidence, closed = raw.get('confidence'), raw.get('last_closed_ms')
    reason = None
    if raw.get('available') is not True:
        reason = 'chart read unavailable: '+str(raw.get('reason', 'unknown history'))
    elif raw.get('timeframe') != timeframe:
        reason = 'chart timeframe does not match requested gate timeframe'
    elif type(raw.get('asof_ms')) is not int or raw['asof_ms'] != asof_ms:
        reason = 'chart read does not match gate asof time'
    elif type(closed) is not int or closed != asof_ms//TIMEFRAMES[timeframe]*TIMEFRAMES[timeframe]:
        reason = 'latest closed chart bucket is missing, stale or in the future'
    elif not _number(confidence) or not minimum <= confidence <= 1:
        reason = 'chart confidence is unknown or below the required threshold'
    elif raw.get('direction') not in ('up', 'down', 'neutral'):
        reason = 'chart direction is unknown'
    elif raw.get('regime') != {'up': 'up', 'down': 'down', 'neutral': 'range'}[raw['direction']]:
        reason = 'chart regime and direction disagree or are unknown'
    row.update(known=reason is None, direction=raw['direction'] if reason is None else 'unknown',
               reason=reason or 'causal closed chart read meets confidence threshold')
    return row


def _asset_read(chart_reads, symbol, asof_ms, minimum):
    supplied = chart_reads.get(symbol, {}) if isinstance(chart_reads, dict) else {}
    cells = supplied.get('five_cells', supplied) if isinstance(supplied, dict) else {}
    if not isinstance(cells, dict):
        cells = {}
    reads = [_read(cells.get(timeframe), symbol, timeframe, asof_ms, minimum) for timeframe in TIMEFRAMES]
    directions = {row['direction'] for row in reads}
    known = all(row['known'] for row in reads)
    direction = next(iter(directions)) if known and len(directions) == 1 else 'neutral' if known else 'unknown'
    reason = (symbol+' 4h/1d agree on '+direction if known and len(directions) == 1 else
              symbol+' 4h/1d disagree; Neutral only' if known else symbol+' chart read unknown; Neutral only')
    return dict(symbol=symbol, direction=direction, known=known, reason=reason, reads=reads)


def _macro(chart_reads, asof_ms, minimum):
    assets = [_asset_read(chart_reads, symbol, asof_ms, minimum) for symbol in ('XBTUSDTM', 'ETHUSDTM')]
    known = all(asset['known'] for asset in assets)
    directions = {asset['direction'] for asset in assets}
    direction = next(iter(directions)) if known and len(directions) == 1 else 'neutral' if known else 'unknown'
    reason = ('BTC and ETH agree on '+direction if direction in ('up', 'down') else
              'BTC/ETH reads are mixed or ranging; Neutral only' if known else
              'BTC/ETH reads are missing or low confidence; Neutral only')
    return dict(direction=direction, known=known, allowed_directions=list(ALLOWED[direction]),
                reason=reason, assets=assets)


def classify_membership(pair, registry=None):
    """An absent listing is unclassified, not evidence of non-membership."""
    document = registry if isinstance(registry, dict) else {}
    result = dict(status='unclassified', known=False, basis='maintained list has no sourced classification',
        sources=[], coverage_complete=document.get('coverage_complete') is True,
        reviewed_at=document.get('reviewed_at'), historical_basis=document.get('historical_basis',
        'membership is not a point-in-time exchange category observation'))
    if pair == 'SOLUSDTM':
        return dict(result, status='member', known=True, basis='SOL is the native asset of the Solana ecosystem', sources=[SOL_SOURCE])
    if type(document.get('schema_version')) is not int or document['schema_version'] != 1:
        return dict(result, basis='membership registry missing or schema unknown')
    members, nonmembers = document.get('members'), document.get('non_members')
    if not isinstance(members, dict) or not isinstance(nonmembers, dict):
        return dict(result, basis='membership registry requires explicit member and non-member mappings')
    if pair in members and pair in nonmembers:
        return dict(result, basis='conflicting membership classifications')
    entry = members.get(pair, nonmembers.get(pair))
    if entry is None:
        return result
    if (not isinstance(entry, dict) or not isinstance(entry.get('basis'), str) or not entry['basis']
            or not isinstance(entry.get('sources'), list) or not entry['sources']
            or any(not isinstance(source, str) or not source for source in entry['sources'])):
        return dict(result, basis='membership classification lacks source evidence')
    return dict(result, status='member' if pair in members else 'non_member', known=True,
                basis=entry['basis'], sources=list(entry['sources']))


def _sol_gate(chart_reads, asof_ms, minimum, membership):
    if membership['status'] == 'member':
        asset = _asset_read(chart_reads, 'SOLUSDTM', asof_ms, minimum)
        return dict(applied=True, direction=asset['direction'], known=asset['known'],
            allowed_directions=list(ALLOWED[asset['direction']]), reason=asset['reason'], reads=asset['reads'])
    reason = ('explicit non-member; SOL overlay not required' if membership['known'] else
              'SOL gate not applied: ecosystem membership is unclassified; macro gate retained')
    return dict(applied=False, direction=None, known=membership['known'],
                allowed_directions=list(DIRECTIONS), reason=reason, reads=[])


def gate_entry(pair, chart_reads, asof_ms, membership=None, minimum_confidence=.75, requested_direction=None):
    """Return a direction allowlist and complete decision evidence for a new entry.

    ``chart_reads`` maps contract IDs to ``{'4h': read, '1d': read}`` or a
    ``read_chart`` report containing ``five_cells``. The caller loads the optional
    registry JSON; this module has no file, database, network, or order access.
    """
    if not isinstance(pair, str) or not pair:
        raise ValueError('pair must be a nonempty contract symbol')
    if type(asof_ms) is not int or asof_ms < 0:
        raise ValueError('asof_ms must be a nonnegative integer')
    if not _number(minimum_confidence) or not 0 <= minimum_confidence <= 1:
        raise ValueError('minimum_confidence must be finite and between zero and one')
    if requested_direction is not None and requested_direction not in DIRECTIONS:
        raise ValueError('requested_direction must be long, short or neutral')
    macro = _macro(chart_reads, asof_ms, minimum_confidence)
    classification = classify_membership(pair, membership)
    sol = _sol_gate(chart_reads, asof_ms, minimum_confidence, classification)
    allowed = [direction for direction in DIRECTIONS
               if direction in macro['allowed_directions'] and direction in sol['allowed_directions']]
    reads = [row for asset in macro['assets'] for row in asset['reads']]+sol['reads']
    unknown = [row['symbol']+' '+row['timeframe']+': '+row['reason'] for row in reads if not row['known']]
    if not classification['known']:
        unknown.append('membership unclassified: '+classification['basis'])
    reasons = [asset['reason'] for asset in macro['assets']]+[macro['reason'], sol['reason']]
    return dict(schema_version=1, pair=pair, asof_ms=asof_ms, scope='new_entries_only',
        existing_positions_action='none', allowed_directions=allowed, requested_direction=requested_direction,
        requested_allowed=None if requested_direction is None else requested_direction in allowed,
        minimum_confidence=minimum_confidence, confidence_basis='heuristic chart confidence; not calibrated probability',
        macro_gate=macro, sol_gate=sol, membership=classification, reads=reads, reasons=reasons,
        unknown=bool(unknown), unknown_reasons=unknown,
        warnings=[sol['reason']] if not classification['known'] else [],
        neutral_is_direction_permission_only=True)
