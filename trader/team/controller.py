"""One hourly cycle of the team: reconcile, derive, dispatch, report.

Pure. `cycle` takes the previous state, plain facts and a clock, and returns a
new state, the dispatches to publish and a summary. Nothing here reads a file,
sends a message or runs a subprocess: those belong to __main__, which is the
only place an effect can happen and therefore the only place one can go wrong.
"""
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from trader.team import events, roster, routing

ZONE = ZoneInfo('Europe/Bucharest')
SCHEMA = 2
SCHEDULER_OWNER = 'com.danslab.trader-team-controller'
LEGACY_STALL_REASON = 'codex heartbeat lost'
MAX_OPEN = 12
MAX_DEFERRED = 256
MAX_DEFERRED_BYTES = 1_048_576
MAX_STATE_BYTES = 3 * 1024 * 1024
HOUR_MS = 3_600_000
DUE_MS = 2 * HOUR_MS
PHASE_DUE_MS = 3 * HOUR_MS
DISCOVERY_MIN = 10 * 60 + 30
KEEP_CYCLES = 48
KEEP_DISPATCHES = 60
PHASE_OF = {name: phase for phase, name in events.PHASE_EVENT.items()}
OPEN = 'PENDING'


class DeferredEventOverflow(ValueError):
    """The controller state cannot be persisted within its safety bounds."""


def _check_deferred(events_):
    size = len(json.dumps(events_, separators=(',', ':'), allow_nan=False)
               .encode('utf-8'))
    if len(events_) > MAX_DEFERRED or size > MAX_DEFERRED_BYTES:
        raise DeferredEventOverflow(
            'deferred event queue overflow: %d events, %d bytes '
            '(limits: %d events, %d bytes)'
            % (len(events_), size, MAX_DEFERRED, MAX_DEFERRED_BYTES))


def _check_state(state):
    encoded = (json.dumps(state, indent=1, allow_nan=False, sort_keys=False)
               + '\n').encode('utf-8')
    size = len(encoded)
    if size > MAX_STATE_BYTES:
        raise DeferredEventOverflow(
            'controller state overflow: %d bytes (limit: %d bytes)'
            % (size, MAX_STATE_BYTES))


def _iso(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat().replace(
        '+00:00', 'Z')


def _local(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).astimezone(ZONE)


def _parse(value):
    try:
        text = str(value).replace('Z', '+00:00')
        return int(datetime.fromisoformat(text).timestamp() * 1000)
    except (TypeError, ValueError):
        return None


def cycle_id(now_ms):
    local = _local(now_ms)
    return 'CTRL-%s-%02d' % (local.strftime('%Y%m%d'), local.hour)


def migrate(state):
    """Carry a schema-1 controller state forward without losing its history."""
    state = state or {}
    if state.get('schema_version') == SCHEMA:
        return dict(state)
    cycles = {}
    for name, row in (state.get('cycles') or {}).items():
        row = dict(row)
        if row.get('status') in ('RUNNING', 'PENDING'):
            row.update(status='MISSED', reason=LEGACY_STALL_REASON)
        cycles[name] = row
    markers = dict(state.get('markers') or {})
    markers.setdefault('cycle_at_ms', _parse(state.get('last_wake_at')))
    return dict(state, schema_version=SCHEMA, scheduler_owner=SCHEDULER_OWNER,
                cycles=cycles, native_timer=state.get('native_timer', 'PAUSED'),
                cadence='hourly :15 Europe/Bucharest',
                daily_phase_outcomes=dict(state.get('daily_phase_outcomes') or {}),
                dispatches=list(state.get('dispatches') or []), markers=markers,
                migrated_from=state.get('schema_version', 0))


def _seen_ms(row):
    for value in (row.get('answered_at_ms'), row.get('created_at_ms')):
        if isinstance(value, (int, float)):
            return int(value)
    return None


def idle_hours(state, facts, now_ms):
    """Hours since each role last answered, or since we first asked it."""
    asked = {}
    for row in state.get('dispatches') or []:
        if row.get('status') == OPEN:
            at = _seen_ms(row) or now_ms
            asked[row['role']] = min(asked.get(row['role'], at), at)
    out = {}
    for role in roster.ROLES:
        seen = (facts.get('role_seen_ms') or {}).get(role['id'])
        base = seen if seen else asked.get(role['id'])
        out[role['id']] = None if not base else round((now_ms - base) / HOUR_MS, 3)
    return out


def _resolve(row, facts, now_ms):
    receipt = (facts.get('receipts') or {}).get(row['dispatch_id'])
    if receipt:
        return dict(row, status='DONE', answered_at=receipt.get('answered_at'),
                    answered_at_ms=receipt.get('answered_at_ms'),
                    room=receipt.get('room') or row.get('room'))
    request = (row.get('payload') or {}).get('request_id')
    if request and request in (facts.get('engineering_results') or ()):
        return dict(row, status='DONE', answered_at=_iso(now_ms),
                    answered_at_ms=now_ms, receipt=str(request))
    if not roster.BY_ID[row['role']]['installed']:
        return dict(row, status='NOT_INSTALLED')
    if now_ms > (row.get('due_at_ms') or 0):
        return dict(row, status='BLOCKED', blocked_at=_iso(now_ms))
    return dict(row)


def reconcile(state, facts, now_ms):
    """Return (dispatches, done, blocked) after checking receipts and due times."""
    rows, done, blocked = [], [], []
    receipts = facts.get('receipts') or {}
    for row in state.get('dispatches') or []:
        if row.get('status') != OPEN:
            # A dispatch recorded NOT_INSTALLED before Dan added the bot must
            # still close once that role answers: the receipt is proof the answer
            # exists, and leaving it would have the board deny work that was done.
            # BLOCKED stays terminal on purpose - a missed deadline is a fact,
            # and a late answer should not quietly erase that it was late.
            if row.get('status') == 'NOT_INSTALLED' and row['dispatch_id'] in receipts:
                after = _resolve(dict(row, status=OPEN), facts, now_ms)
                rows.append(after)
                done.append(after)
                continue
            rows.append(dict(row))
            continue
        after = _resolve(row, facts, now_ms)
        rows.append(after)
        if after['status'] == 'DONE':
            done.append(after)
        elif after['status'] == 'BLOCKED':
            blocked.append(after)
    return rows, done, blocked


def _dispatch(name, role_id, payload, now_ms, index):
    local = _local(now_ms)
    role = roster.BY_ID[role_id]
    due = now_ms + (PHASE_DUE_MS if name in PHASE_OF else DUE_MS)
    return dict(
        dispatch_id='D-%s-%02d-%s-%s-%d' % (local.strftime('%Y%m%d'), local.hour,
                                            role_id, name, index),
        cycle_id=cycle_id(now_ms), role=role_id, role_name=role['name'],
        room=roster.room_of(role_id), event=name, payload=dict(payload),
        instruction=routing.instruction(name, payload), created_at=_iso(now_ms),
        created_at_ms=now_ms, due_at=_iso(due), due_at_ms=due, status=OPEN)


def _unique_dispatch(name, role_id, payload, now_ms, index, used_ids):
    while True:
        row = _dispatch(name, role_id, payload, now_ms, index)
        if row['dispatch_id'] not in used_ids:
            used_ids.add(row['dispatch_id'])
            return row, index
        index += 1


def _reserve(fired, state, facts):
    """Slots the standing dispatches will need, so the cap is never exceeded."""
    count = 1                                            # Lead synthesis
    if any(i['name'] in routing.USER_FACING for i in fired):
        count += 1                                       # Secretary final answer
    markers = state.get('markers') or {}
    if (facts['local_minutes'] >= DISCOVERY_MIN
            and markers.get('discovery_date') != facts['local_date']):
        count += 1                                       # daily discovery audit
    return count


def _route(fired, open_keys, now_ms, budget, used_ids=None):
    """One dispatch per (role, event, key) that is not already open."""
    out, index = [], {}
    used_ids = set() if used_ids is None else used_ids
    for item in routing.ranked(fired):
        payload = item['payload']
        if item['name'] == 'ENGINEERING_DUE' and payload.get('request_id') == 'none pending':
            continue
        roles = routing.roles_for(item['name'], payload)
        missing = [role for role in roles
                   if (role, item['name'], item['key']) not in open_keys]
        if len(missing) > budget:
            continue  # Admit all required owners together or defer the event.
        for role_id in roles:
            key = (role_id, item['name'], item['key'])
            if key in open_keys or budget <= 0:
                continue
            count = index.get((role_id, item['name']), 0) + 1
            observed = dict(payload, event_observed_at=item.get('observed_at') or _iso(now_ms))
            row, count = _unique_dispatch(item['name'], role_id, observed,
                                          now_ms, count, used_ids)
            index[(role_id, item['name'])] = count
            row['key'] = item['key']
            out.append(row)
            open_keys.add(key)
            budget -= 1
    return out


def gaps(state, facts, status_rows):
    """What the controller can already see is missing, for the daily audit."""
    out = []
    missing = [r['name'] for r in status_rows if r['status'] == 'NOT_INSTALLED']
    if missing:
        out.append('roles not installed: ' + ', '.join(missing))
    unknown = [c['name'] for c in (facts.get('doctor') or {}).get('checks') or []
               if c.get('status') == 'unknown']
    if unknown:
        out.append('doctor checks unknown: ' + ', '.join(unknown))
    stale = [r['name'] for r in status_rows
             if r['kind'] == 'deterministic' and r['status'] == 'IDLE']
    if stale:
        out.append('data sources past their cadence: ' + ', '.join(stale))
    stuck = sorted({r['role_name'] for r in state.get('dispatches') or []
                    if r.get('status') == 'BLOCKED'})
    if stuck:
        out.append('blocked dispatches: ' + ', '.join(stuck))
    return out or ['none the controller can compute']


def _standing(fired, created, open_keys, facts, state, status_rows, now_ms,
              used_ids=None):
    """Lead synthesis, Secretary final response and the daily discovery audit."""
    out, date = [], facts['local_date']
    used_ids = set() if used_ids is None else used_ids
    cycle_key = cycle_id(now_ms)
    if created and ('grid_desk_lead', 'SYNTHESIS', cycle_key) not in open_keys:
        out.append(_unique_dispatch('SYNTHESIS', 'grid_desk_lead',
                                    dict(count=len(created)), now_ms, 1,
                                    used_ids)[0])
    outcomes = sorted({i['name'] for i in fired
                       if i['name'] in routing.USER_FACING})
    if (outcomes and
            ('paper_desk_secretary', 'FINAL_RESPONSE', cycle_key) not in open_keys):
        out.append(_unique_dispatch('FINAL_RESPONSE', 'paper_desk_secretary',
                                    dict(outcomes=', '.join(outcomes)), now_ms,
                                    1, used_ids)[0])
    markers = state.get('markers') or {}
    if (facts['local_minutes'] >= DISCOVERY_MIN
            and markers.get('discovery_date') != date
            and ('discovery_auditor', 'DISCOVERY', date) not in open_keys):
        out.append(_unique_dispatch(
            'DISCOVERY', 'discovery_auditor',
            dict(gaps='; '.join(gaps(state, facts, status_rows))), now_ms, 1,
            used_ids)[0])
    for row in out:
        row['key'] = date if row['event'] == 'DISCOVERY' else row['cycle_id']
        open_keys.add((row['role'], row['event'], row['key']))
    return out


def _new_phase(phase, date, now_ms):
    due = datetime.fromisoformat('%sT%02d:15:00' % (date, events.PHASE_DUE_MIN[phase] // 60))
    return dict(due_at=due.replace(tzinfo=ZONE).isoformat(), status='PENDING',
                request_id=None, started_at=None, updated_at=_iso(now_ms),
                completed_at=None, receipt=None, blocker=None,
                attempt_id=None, dispatch_ids=[])


def phase_outcomes(state, facts, fired, dispatches, now_ms):
    """Persist one record per phase per local date, per controller.md."""
    out = {d: {p: dict(r) for p, r in rows.items()}
           for d, rows in (state.get('daily_phase_outcomes') or {}).items()}
    date, stamp = facts['local_date'], _iso(now_ms)
    today = out.setdefault(date, {})
    for phase in events.PHASES:
        today.setdefault(phase, _new_phase(phase, date, now_ms))
    for older, rows in out.items():
        if older >= date:
            continue
        for phase, row in rows.items():
            if row.get('status') in ('PENDING', 'RUNNING'):
                rows[phase] = dict(row, status='MISSED', updated_at=stamp,
                                   blocker=row.get('blocker') or LEGACY_STALL_REASON)
    names = {i['name'] for i in fired}
    pending = list(facts.get('pending_requests') or ())
    for phase in events.PHASES:
        name = events.PHASE_EVENT[phase]
        rows = [r for r in dispatches if r.get('event') == name
                and r.get('key') == date + ':' + phase]
        if (phase == 'engineering' and not pending and name in names
                and not any((r.get('payload') or {}).get('request_id') not in (None, 'none pending')
                            for r in rows)):
            today[phase] = dict(today[phase], status='NO_ELIGIBLE_NEED',
                                updated_at=stamp, blocker=None)
            continue
        if not rows:
            if today[phase].get('status') == 'RUNNING':
                today[phase] = dict(today[phase], status='PENDING',
                                    updated_at=stamp, blocker='no dispatch evidence')
            continue
        required = set(routing.roles_for(name, rows[0].get('payload')))
        present = {r['role'] for r in rows}
        statuses = {r['status'] for r in rows}
        status = ('BLOCKED' if statuses & {'BLOCKED', 'NOT_INSTALLED'} else
                  'PENDING' if not required <= present else
                  'COMPLETED' if statuses == {'DONE'} else 'RUNNING')
        finished = max((r.get('answered_at_ms') or _parse(r.get('answered_at')) or 0
                        for r in rows), default=0)
        today[phase] = dict(today[phase], status=status, updated_at=stamp,
                            attempt_id=date + ':' + phase,
                            dispatch_ids=[r['dispatch_id'] for r in rows],
                            started_at=min(r.get('created_at') or _iso(r.get('created_at_ms') or now_ms)
                                           for r in rows),
                            request_id=(rows[0].get('payload') or {}).get('request_id'),
                            completed_at=_iso(finished) if status == 'COMPLETED' and finished else None,
                            receipt=rows[-1]['dispatch_id'] if status == 'COMPLETED' else None,
                            blocker=('required dispatch blocked' if status == 'BLOCKED' else
                                     'required dispatch not admitted' if status == 'PENDING' else None))
    return out


def status_of(role, idle_h, blocked_roles):
    """A deterministic member that never produced a reading counts as idle."""
    if not role['installed']:
        return 'NOT_INSTALLED'
    if role['id'] in blocked_roles:
        return 'BLOCKED'
    if idle_h is None:
        return 'IDLE' if role['kind'] == 'deterministic' else 'UNOBSERVED'
    return 'IDLE' if idle_h > role['max_idle_h'] else 'OK'


def roster_status(dispatches, facts, idle, now_ms):
    """One row per participant: installed, last seen, idle hours, status."""
    blocked = {r['role'] for r in dispatches if r.get('status') == 'BLOCKED'
               and ((facts.get('role_seen_ms') or {}).get(r['role']) or 0) <=
               (_parse(r.get('blocked_at')) or r.get('due_at_ms') or r.get('created_at_ms') or 0)}
    rows = []
    for role in roster.ROLES:
        seen = (facts.get('role_seen_ms') or {}).get(role['id'])
        rows.append(dict(role=role['id'], name=role['name'], kind=role['kind'],
                         rooms=list(role['rooms']), installed=role['installed'],
                         last_seen_at=_iso(seen) if seen else None,
                         idle_h=idle.get(role['id']), max_idle_h=role['max_idle_h'],
                         status=status_of(role, idle.get(role['id']), blocked)))
    return rows


def _record_cycle(state, now_ms, fired, created, summary_id):
    cycles = dict(state.get('cycles') or {})
    cycles[summary_id] = dict(status='COMPLETED', trigger='launchd',
                              observed_utc=_iso(now_ms),
                              scheduled_local_hour=_local(now_ms).isoformat(),
                              events=[i['name'] for i in fired],
                              dispatched=[r['dispatch_id'] for r in created],
                              updated_at=_iso(now_ms))
    return dict(sorted(cycles.items())[-KEEP_CYCLES:])


def cycle(state, facts, now_ms):
    """Return (new_state, dispatches, summary) for this wake."""
    state = migrate(state)
    local = _local(now_ms)
    facts = dict(facts, now_ms=now_ms, local_date=local.strftime('%Y-%m-%d'),
                 local_minutes=local.hour * 60 + local.minute)
    rows, done, blocked = reconcile(state, facts, now_ms)
    phases = phase_outcomes(state, facts, [], rows, now_ms)
    markers = dict(state.get('markers') or {})
    phase_dates = dict(markers.get('phase_dates') or {})
    for phase, outcome in phases[facts['local_date']].items():
        if (outcome['status'] == 'PENDING' and outcome.get('blocker')
                and phase_dates.get(phase) == facts['local_date']):
            phase_dates.pop(phase)
    state = dict(state, markers=dict(markers, phase_dates=phase_dates),
                 daily_phase_outcomes=phases)
    idle = idle_hours(dict(state, dispatches=rows), facts, now_ms)
    facts = dict(facts, role_idle_h=idle)
    fired = events.derive(facts, state.get('markers') or {})
    fired += [events.event('ROLE_IDLE', r['role'], role=r['role_name'],
                           idle_h=idle.get(r['role']) or 0.0) for r in blocked]
    queued = {}
    for item in list(state.get('deferred_events') or []) + fired:
        if item['name'] in PHASE_OF and not str(item['key']).startswith(facts['local_date'] + ':'):
            continue  # Prior-date phase outcomes are retained as MISSED.
        queued.setdefault((item['name'], item['key']),
                          dict(item, observed_at=item.get('observed_at') or _iso(now_ms)))
    fired = list(queued.values())
    open_keys = {(r['role'], r['event'], r.get('key'))
                 for r in rows if r['status'] == OPEN}
    open_keys.update((r['role'], r['event'], r.get('key')) for r in rows
                     if r['event'] in PHASE_OF and
                     str(r.get('key', '')).startswith(facts['local_date'] + ':'))
    used_ids = ({r['dispatch_id'] for r in rows}
                | set((facts.get('receipts') or {}).keys()))
    budget = (MAX_OPEN - sum(1 for r in rows if r['status'] == OPEN)
              - _reserve(fired, state, facts))
    created = _route(fired, open_keys, now_ms, budget, used_ids)
    deferred, accepted = [], []
    for item in fired:
        if item['name'] == 'ENGINEERING_DUE' and item['payload'].get('request_id') == 'none pending':
            accepted.append(item)
            continue
        required = routing.roles_for(item['name'], item['payload'])
        if not all((role, item['name'], item['key']) in open_keys for role in required):
            deferred.append(item)
        else:
            accepted.append(item)
    _check_deferred(deferred)
    state = dict(state, deferred_events=deferred)
    status = roster_status(rows + created, facts, idle, now_ms)
    standing = _standing(accepted, created, open_keys, facts, state, status,
                         now_ms, used_ids)
    room = max(0, MAX_OPEN - sum(r['status'] == OPEN for r in rows) - len(created))
    created += standing[:room]
    result = _finish(state, facts, rows, created, fired, done, blocked, status,
                     now_ms)
    _check_state(result[0])
    return result


def _keep(rows):
    """Never drop an open dispatch; trim only the settled tail."""
    settled = sorted((r for r in rows if r['status'] != OPEN),
                     key=lambda r: (r.get('created_at_ms') or 0,
                                    r.get('answered_at_ms') or 0, r['dispatch_id']))
    return [r for r in rows if r['status'] == OPEN] + settled[-KEEP_DISPATCHES:]


def _finish(state, facts, rows, created, fired, done, blocked, status, now_ms):
    identifier = cycle_id(now_ms)
    dispatches = _keep(rows + created)
    outcomes = phase_outcomes(state, facts, fired, rows + created, now_ms)
    accepted_phases = {events.PHASE_EVENT[p] for p, r in outcomes[facts['local_date']].items()
                       if r['status'] in ('RUNNING', 'COMPLETED', 'BLOCKED', 'NO_ELIGIBLE_NEED')}
    accepted_idle = {r.get('key') for r in created if r['event'] == 'ROLE_IDLE'}
    markers = events.advance(state.get('markers') or {}, facts, fired,
                             accepted_phase_names=accepted_phases,
                             accepted_idle_roles=accepted_idle)
    markers['cycle_id'] = identifier
    if any(r['event'] == 'DISCOVERY' for r in created):
        markers['discovery_date'] = facts['local_date']
    new_state = dict(state, schema_version=SCHEMA, scheduler_owner=SCHEDULER_OWNER,
                     dispatches=dispatches, markers=markers,
                     last_wake_at=_iso(now_ms), last_cycle_at_ms=now_ms,
                     cycles=_record_cycle(state, now_ms, fired, created, identifier),
                     daily_phase_outcomes=outcomes)
    summary = dict(
        cycle_id=identifier, at=_iso(now_ms), local_time=_local(now_ms).isoformat(),
        events=['%s(%s)' % (i['name'], i['key']) for i in fired],
        dispatched=[r['dispatch_id'] for r in created],
        done=[r['dispatch_id'] for r in done],
        blocked=[r['dispatch_id'] for r in blocked],
        open=sum(1 for r in dispatches if r['status'] == OPEN), roster=status,
        doctor=dict(status=(facts.get('doctor') or {}).get('status', 'unknown'),
                    failing=sorted((facts.get('doctor') or {}).get('failing') or [])),
        phases=new_state['daily_phase_outcomes'].get(facts['local_date'], {}),
        engineering_request=next((r['payload'].get('request_id') for r in created
                                  if r['event'] == 'ENGINEERING_DUE'), None),
        migrated_from=state.get('migrated_from'),
        missed_cycles=['%s (%s)' % (name, row.get('reason') or 'missed')
                       for name, row in sorted(new_state['cycles'].items())
                       if row.get('status') == 'MISSED'],
        gaps=gaps(state, facts, status))
    return new_state, created, summary


def record_engineering(state, request_id, result):
    """Fold a bridge receipt back into the engineering phase, immutably."""
    out = {d: {p: dict(r) for p, r in rows.items()}
           for d, rows in (state.get('daily_phase_outcomes') or {}).items()}
    for rows in out.values():
        row = rows.get('engineering')
        if not row or row.get('request_id') != request_id:
            continue
        status = str(result.get('status') or 'UNKNOWN')
        rows['engineering'] = dict(row, status='COMPLETED' if status not in
                                   ('REJECTED', 'BLOCKED') else 'BLOCKED',
                                   completed_at=result.get('completed_at'),
                                   receipt=str(request_id),
                                   blocker=None if status not in ('REJECTED', 'BLOCKED')
                                   else status)
    return dict(state, daily_phase_outcomes=out)
