"""Did an agent's influence help or hurt, in USDT? (T8 audit trail.)

The desk logs every applied influence as a `DECISION action='influence'` event.
The 07:00 counterfactual replay (trader.review.counterfactual) prices every
entry the desk skipped, and an agent VETO is now one of those skips. Joining the
two answers the only question worth asking about the influence channel.

The join key is (ts_ms, symbol, direction): every event a single decision pass
emits shares that pass's now_ms, and the recorded counterfactual candidate
carries the same ts_ms, so the timestamp identifies the scan.

Accounting, stated honestly:

- A matched VETO priced at replayed net N contributes -N. Vetoing an entry that
  would have made money is a cost; vetoing one that would have lost money is a
  saving.
- A BOOST has no counterfactual by construction: if the boost worked, the bot
  opened and its real P&L is in the ordinary ledger. Boosts are therefore
  counted, never priced, and a matched boost means the boost FAILED -- the
  candidate was skipped for some other reason anyway.

Pure stdlib, pure functions, no I/O.
"""
from trader.autopilot.policy import DECISION_RULES

VETO = 'VETO'
BOOST = 'BOOST'


def _is_influence(event):
    return (isinstance(event, dict) and event.get('type') == 'DECISION'
            and event.get('action') == 'influence'
            and isinstance(event.get('agent_id'), str))


def _key(item):
    return (int(item['ts_ms']), item.get('symbol'), item.get('direction'))


def _verb(event):
    """VETO or BOOST, read from the rule code the event carries."""
    blocks = event.get('rule_blocks') or []
    return VETO if DECISION_RULES['influence_veto'] in blocks else BOOST


def _blank():
    return {'influences': 0, 'boosts': 0, 'vetoes': 0, 'matched_vetoes': 0,
            'clamped': 0, 'net_usdt': 0.0}


def _fold(bucket, verb, clamped, net):
    bucket['influences'] += 1
    bucket['clamped'] += clamped
    if verb == VETO:
        bucket['vetoes'] += 1
        if net is not None:
            bucket['matched_vetoes'] += 1
            bucket['net_usdt'] = round(bucket['net_usdt'] - net, 4)
    else:
        bucket['boosts'] += 1
    return bucket


def summarize(events, outcomes):
    """Per-agent influence ledger for one day.

    Returns {'agents': {agent_id: bucket}, 'total': bucket, 'records': [...]}
    where each record names the influence and the replayed net it is joined to
    (None when nothing was replayed for it). `net_usdt` is negative when the
    agent's influence cost the desk money.
    """
    priced = {}
    for outcome in outcomes or ():
        try:
            priced[_key(outcome)] = float(outcome['net'])
        except (KeyError, TypeError, ValueError):
            continue
    agents, total, records = {}, _blank(), []
    for event in events or ():
        if not _is_influence(event):
            continue
        verb = _verb(event)
        net = priced.get(_key(event)) if verb == VETO else None
        clamped = 1 if event.get('influence_clamped') else 0
        agent = event['agent_id']
        _fold(agents.setdefault(agent, _blank()), verb, clamped, net)
        _fold(total, verb, clamped, net)
        records.append({'agent_id': agent, 'symbol': event.get('symbol'),
                        'direction': event.get('direction'), 'verb': verb,
                        'reason_code': int(event.get('reason_code', 0) or 0),
                        'delta': float(event.get('influence_delta', 0.0) or 0.0),
                        'replayed_net': None if net is None else round(net, 4),
                        'net_usdt': 0.0 if net is None else round(-net, 4)})
    return {'agents': dict(sorted(agents.items())), 'total': total,
            'records': records}
