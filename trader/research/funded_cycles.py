"""Attribute settlement cash to completed grid inventory without changing PnL.

Supply the full opening history, not a prefiltered six-hour ledger. Output closes
use (start_ms, end_ms]; funding and opening inventory before that window still
belong to those cycles. Stable timestamp ordering preserves each replay's
funding/fill convention at equal timestamps. This is modeled fill accounting,
not evidence of exchange executions or complete funding observations.
"""
from decimal import Decimal, InvalidOperation

HOUR_MS = 3_600_000
OPEN_KINDS = ('seed', 'grid_open')
CLOSE_KINDS = ('grid_close', 'seed_close', 'stop_loss', 'replacement', 'liquidation')
FUNDING_KINDS = ('funding', 'funding_estimated', 'funding_unknown')


def _number(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def _sum_known(values):
    values = list(values)
    return None if any(value is None for value in values) else sum(values, Decimal(0))


def _float(value):
    return None if value is None else float(value)


def _events(ledger, end_ms):
    seen, rows = {}, []
    for event in ledger:
        if event.get('kind') not in OPEN_KINDS + CLOSE_KINDS + FUNDING_KINDS:
            continue
        if 'event_id' not in event:
            raise ValueError('ledger event_id is required')
        key = (event.get('bot_id', ''), event['event_id'])
        if key in seen:
            if seen[key] != event:
                raise ValueError(f'conflicting duplicate ledger event: {key}')
            continue
        seen[key] = event
        timestamp = event.get('timestamp_ms')
        if type(timestamp) is not int:
            raise ValueError('ledger timestamp_ms must be an integer')
        if timestamp <= end_ms:
            rows.append(event)
    return sorted(rows, key=lambda row: row['timestamp_ms'])


def _position(event):
    side, quantity = _number(event.get('side')), _number(event.get('quantity'))
    if side not in (Decimal(-1), Decimal(1)) or quantity is None or quantity <= 0:
        raise ValueError('opening fill needs a signed side and positive quantity')
    return dict(opening=event, side=side, quantity=quantity, funding=Decimal(0),
                estimated=False, funding_reasons=[])


def _settle(position, event):
    rate, mark = _number(event.get('rate')), _number(event.get('mark'))
    unknown = (event['kind'] == 'funding_unknown' or event.get('observed_rate') is False
               or rate is None or mark is None or mark <= 0)
    position['estimated'] |= (event['kind'] == 'funding_estimated'
                              or bool(event.get('mark_carried_forward')))
    if unknown:
        position['funding'] = None
        if 'unknown_funding' not in position['funding_reasons']:
            position['funding_reasons'].append('unknown_funding')
    elif position['funding'] is not None:
        # Net bot cash cannot allocate opposite Neutral legs correctly.
        position['funding'] -= position['side'] * position['quantity'] * mark * rate


def _close(event, position, coverage_known):
    opening = position['opening'] if position else {}
    reasons = list(position['funding_reasons']) if position else ['missing_opening']
    if position and (_number(event.get('side')) != -position['side']
                     or _number(event.get('quantity')) != position['quantity']):
        raise ValueError('closing fill must match the held quantity and opposite side')
    opening_fee, closing_fee = _number(opening.get('fee')), _number(event.get('fee'))
    gross = _number(event.get('gross_pnl'))
    opening_price, closing_price = _number(opening.get('price')), _number(event.get('price'))
    if opening_price is None or opening_price <= 0:
        reasons.append('unknown_opening_price')
    if closing_price is None or closing_price <= 0:
        reasons.append('unknown_closing_price')
    funding = position['funding'] if position else None
    estimated = bool(position and position['estimated'])
    if opening_fee is None:
        reasons.append('unknown_opening_fee')
    if closing_fee is None:
        reasons.append('unknown_closing_fee')
    if gross is None:
        reasons.append('unknown_gross_pnl')
    modeled_net = None if reasons else gross-opening_fee-closing_fee+funding
    if not coverage_known:
        reasons.append('funding_coverage_unknown')
    if estimated:
        reasons.append('estimated_funding')
    seed = opening.get('kind') == 'seed' or event['kind'] == 'seed_close'
    return dict(bot_id=event.get('bot_id', ''), pair=event.get('pair', opening.get('pair')),
        slot=event['slot'], opening_event_id=opening.get('event_id'),
        closing_event_id=event['event_id'], opening_timestamp_ms=opening.get('timestamp_ms'),
        timestamp_ms=event['timestamp_ms'], closing_kind=event['kind'], seed=seed,
        completed_grid=event['kind'] == 'grid_close' and not seed and event.get('completed_grid', True),
        side=opening.get('side'), quantity=event.get('quantity'),
        opening_price=_float(opening_price), closing_price=_float(closing_price),
        gross_pnl=_float(gross), opening_fee=_float(opening_fee), closing_fee=_float(closing_fee),
        allocated_funding=_float(funding), net=_float(modeled_net) if not reasons else None,
        modeled_net=_float(modeled_net), estimated=estimated, unknown_reasons=reasons)


def funded_cycles(ledger, start_ms, end_ms, coverage_known=False):
    """Return completed grid rows, all window closures, open lots and summary.

    ``coverage_known`` attests funding coverage over the complete holding history
    of the supplied lots, including before start_ms. False leaves verified net
    unknown. ``modeled_net`` retains calculable recorded/estimated settlements,
    but remains unknown for absent opening fees, settlement rates or marks.
    Serialized fee amounts are authoritative; no inferred fee rate is applied.
    """
    if type(start_ms) is not int or type(end_ms) is not int or end_ms < start_ms:
        raise ValueError('cycle window needs integer timestamps with end >= start')
    held, closed, all_funding = {}, [], []
    for event in _events(ledger, end_ms):
        bot_id, kind = event.get('bot_id', ''), event['kind']
        if kind in FUNDING_KINDS:
            for (owner, _), position in held.items():
                if owner == bot_id:
                    _settle(position, event)
            continue
        key = (bot_id, event['slot'])
        if kind in OPEN_KINDS:
            if key in held:
                raise ValueError('opening fill overlaps an already held slot')
            held[key] = _position(event)
        else:
            position = held.pop(key, None)
            row = _close(event, position, coverage_known)
            all_funding.append(_number(row['allocated_funding']))
            if start_ms < event['timestamp_ms'] <= end_ms:
                closed.append(row)
    opened = [dict(bot_id=bot_id, slot=slot, seed=p['opening']['kind'] == 'seed',
                   opening_event_id=p['opening']['event_id'],
                   opening_timestamp_ms=p['opening']['timestamp_ms'],
                   allocated_funding=_float(p['funding']), estimated=p['estimated'])
              for (bot_id, slot), p in held.items()]
    all_funding.extend(p['funding'] for p in held.values())
    completed = [row for row in closed if row['completed_grid']]
    nets = [_number(row['net']) for row in completed]
    modeled = [_number(row['modeled_net']) for row in completed]
    income = _sum_known(nets) if coverage_known else None
    model_income = _sum_known(modeled)
    hours = (end_ms-start_ms)/HOUR_MS
    summary = dict(completed_grids=len(completed), coverage_known=bool(coverage_known),
        completed_positive_net=sum(value is not None and value > 0 for value in nets),
        completed_nonpositive_net=sum(value is not None and value <= 0 for value in nets),
        completed_unknown_net=sum(value is None for value in nets),
        completed_estimated=sum(row['estimated'] for row in completed),
        modeled_positive=sum(value is not None and value > 0 for value in modeled),
        modeled_nonpositive=sum(value is not None and value <= 0 for value in modeled),
        modeled_unknown=sum(value is None for value in modeled),
        grid_income=_float(income), modeled_grid_income=_float(model_income),
        grid_income_per_hour=_float(income)/hours if income is not None and hours else None,
        modeled_grid_income_per_hour=_float(model_income)/hours if model_income is not None and hours else None,
        allocated_funding=_float(_sum_known(all_funding)))
    return dict(start_ms=start_ms, end_ms=end_ms, completed=completed, closed=closed,
                open=opened, summary=summary)
