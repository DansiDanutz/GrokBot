"""Turn plain readings into the closed team vocabulary, once each.

Pure. `derive` compares the current facts against the markers the previous cycle
persisted, so a fault that lasts nine hours produces one event, not nine. The
vocabulary is closed on purpose: an event nobody routes is a silent agent.
"""
from trader.papergrid import engine
from trader.team import roster

EVENTS = ('DOCTOR_FAIL', 'DOCTOR_RECOVERED', 'RADAR_SCAN', 'BOT_OPENED',
          'BOT_CLOSED', 'ENTRIES_STALLED', 'STRUCTURE_BLACKOUT',
          'LEARNER_DEFERRED', 'LEARNER_APPLIED', 'COUNTERFACTUAL_READY',
          'LIQ_CLUSTERS_READY', 'RESEARCH_DUE', 'DATA_PHASE_DUE',
          'ENGINEERING_DUE', 'ROLE_IDLE', 'CONTROLLER_RESUMED')

PHASES = ('data', 'research', 'engineering')
PHASE_EVENT = dict(data='DATA_PHASE_DUE', research='RESEARCH_DUE',
                   engineering='ENGINEERING_DUE')
PHASE_DUE_MIN = dict(data=8 * 60 + 15, research=9 * 60 + 15,
                     engineering=10 * 60 + 15)

# A CLOSE event carries the index of engine.CLOSE_REASONS, not a decision code.
CLOSE_REASONS = dict(enumerate(engine.CLOSE_REASONS))
RESUME_GAP_MS = 2 * 3_600_000
HOUR_MS = 3_600_000


def event(name, key, **payload):
    return dict(name=name, key=key, payload=dict(payload))


def _doctor(f, previous):
    report = f.get('doctor') or {}
    failing = sorted(report.get('failing') or [])
    before = sorted(previous.get('doctor_failing') or [])
    detail = {c['name']: c for c in report.get('checks') or []}
    out = []
    for name in failing:
        if name not in before:
            check = detail.get(name) or {}
            out.append(event('DOCTOR_FAIL', name, check=name,
                             where=check.get('where') or '',
                             detail=check.get('detail') or ''))
    if before and not failing:
        out.append(event('DOCTOR_RECOVERED', 'circle'))
    return out


def _radar(f, previous):
    scan = f.get('watchlist_scan_id')
    if scan is None or scan == previous.get('scan_id'):
        return []
    candidates = int(f.get('core_candidates') or 0)
    changed = candidates != previous.get('candidates')
    return [event('RADAR_SCAN', str(scan), scan_id=scan, candidates=candidates,
                  candidates_changed=bool(changed))]


def _bots(f, previous):
    seen = previous.get('event_id') or 0
    directions = f.get('open_directions') or {}
    out = []
    for row in f.get('events') or []:
        if int(row.get('event_id') or 0) <= seen:
            continue
        bot, symbol = int(row.get('bot_id') or 0), str(row.get('symbol') or '')
        if row.get('type') == 'OPEN':
            out.append(event('BOT_OPENED', '%d:%s' % (bot, symbol), bot_id=bot,
                             symbol=symbol,
                             direction=directions.get(bot) or 'UNKNOWN'))
        elif row.get('type') == 'CLOSE':
            reason = CLOSE_REASONS.get(row.get('reason_code'), 'UNKNOWN')
            out.append(event('BOT_CLOSED', '%d:%s' % (bot, symbol), bot_id=bot,
                             symbol=symbol, reason=reason))
    return out


def _flags(f, previous):
    out = []
    for name, key in (('entries_stalled', 'ENTRIES_STALLED'),
                      ('structure_blackout', 'STRUCTURE_BLACKOUT')):
        if f.get(name) and not previous.get(name):
            out.append(event(key, name))
    return out


def _learner(f, previous):
    review = f.get('review') or {}
    run = review.get('last_run_at_ms')
    if run is None or run == previous.get('review_run_ms'):
        return []
    headline = str(review.get('headline') or '')
    if int(review.get('applied') or 0) > 0:
        return [event('LEARNER_APPLIED', str(run))]
    if headline:
        return [event('LEARNER_DEFERRED', str(run), headline=headline[:200])]
    return []


def _artifacts(f, previous):
    out = []
    date = f.get('counterfactual_date')
    if date and date != previous.get('counterfactual_date'):
        out.append(event('COUNTERFACTUAL_READY', str(date), date=str(date)))
    ready = f.get('liq_clusters_ms')
    if ready and ready > (previous.get('liq_clusters_ms') or 0):
        out.append(event('LIQ_CLUSTERS_READY', str(ready)))
    return out


def _phases(f, previous):
    date, minutes = f.get('local_date'), f.get('local_minutes')
    if not date or minutes is None:
        return []
    done = previous.get('phase_dates') or {}
    pending = list(f.get('pending_requests') or ())
    out = []
    for phase in PHASES:                       # data -> research -> engineering
        if minutes < PHASE_DUE_MIN[phase] or done.get(phase) == date:
            continue
        payload = dict(phase=phase, due_local='%02d:15' % (PHASE_DUE_MIN[phase] // 60),
                       overdue_h=round((minutes - PHASE_DUE_MIN[phase]) / 60, 2))
        if phase == 'engineering':
            payload['request_id'] = pending[0] if pending else 'none pending'
        out.append(event(PHASE_EVENT[phase], '%s:%s' % (date, phase), **payload))
    return out


def _idle(f, previous):
    out = []
    for role_id, idle_h in sorted((f.get('role_idle_h') or {}).items()):
        role = roster.BY_ID.get(role_id)
        if role is None or not role['installed'] or idle_h is None:
            continue
        if idle_h > role['max_idle_h']:
            out.append(event('ROLE_IDLE', role_id, role=role['name'],
                             idle_h=round(float(idle_h), 2)))
    return out


def _resumed(f, previous):
    last = previous.get('cycle_at_ms')
    now = f['now_ms']
    if last is not None and now - last <= RESUME_GAP_MS:
        return []
    gap = round((now - last) / HOUR_MS, 2) if last else None
    return [event('CONTROLLER_RESUMED', 'resume',
                  gap_h=gap if gap is not None else 0.0,
                  from_cycle=str(previous.get('cycle_id') or 'none'))]


DERIVERS = (_doctor, _radar, _bots, _flags, _learner, _artifacts, _phases,
            _idle, _resumed)


def derive(facts, previous):
    """Return the events new since `previous`, in a stable order."""
    previous = previous or {}
    out = []
    for source in DERIVERS:
        out.extend(source(facts, previous))
    return out


def advance(previous, facts, fired):
    """Return the markers to persist so these events never fire twice."""
    previous, names = previous or {}, {e['name'] for e in fired}
    review = (facts.get('review') or {})
    ids = [int(r.get('event_id') or 0) for r in facts.get('events') or []]
    markers = dict(previous)
    markers.update(
        doctor_failing=sorted((facts.get('doctor') or {}).get('failing') or []),
        scan_id=facts.get('watchlist_scan_id', previous.get('scan_id')),
        candidates=int(facts.get('core_candidates') or 0),
        event_id=max(ids + [previous.get('event_id') or 0]),
        entries_stalled=bool(facts.get('entries_stalled')),
        structure_blackout=bool(facts.get('structure_blackout')),
        review_run_ms=review.get('last_run_at_ms', previous.get('review_run_ms')),
        counterfactual_date=facts.get('counterfactual_date')
        or previous.get('counterfactual_date'),
        liq_clusters_ms=max(facts.get('liq_clusters_ms') or 0,
                            previous.get('liq_clusters_ms') or 0) or None,
        cycle_at_ms=facts['now_ms'])
    dates = dict(previous.get('phase_dates') or {})
    for phase in PHASES:
        if PHASE_EVENT[phase] in names:
            dates[phase] = facts.get('local_date')
    markers['phase_dates'] = dates
    return markers
