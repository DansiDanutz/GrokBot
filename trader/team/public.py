"""The public projection of the team: what a native bot is allowed to read.

Native bots can only fetch public URLs, so team.json is how they receive work.
It therefore leaves this machine: no local path, no host, no credential, and no
free-form runtime text that could smuggle one.
"""
FORBIDDEN = ('/Users', 'Sandbox', 'ZCodeProject', 'localhost', '127.0.0.1',
             '.sqlite3', 'ssh-', 'BEGIN ')
MAX_TEXT = 600
MAX_DISPATCHES = 50
MAX_ROSTER = 40
DISPATCH_FIELDS = ('dispatch_id', 'role_name', 'room', 'event', 'instruction',
                   'created_at', 'due_at', 'status')
ROSTER_FIELDS = ('name', 'kind', 'status', 'last_seen_at')


def text(value, limit=MAX_TEXT):
    """A bounded string, replaced wholesale if it carries anything local."""
    if value is None:
        return None
    value = str(value)[:limit]
    return 'redacted' if any(bad in value for bad in FORBIDDEN) else value


def _value(value):
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    return text(value, 200)


def _dispatch(row):
    out = {field: text(row.get(field)) for field in DISPATCH_FIELDS}
    out['payload'] = {text(k, 40): _value(v)
                      for k, v in (row.get('payload') or {}).items()}
    return out


def _roster(row):
    out = {field: text(row.get(field), 80) for field in ROSTER_FIELDS}
    out.update(installed=bool(row.get('installed')), idle_h=row.get('idle_h'),
               max_idle_h=row.get('max_idle_h'), rooms=[text(r, 80) for r in
                                                        row.get('rooms') or []])
    return out


def snapshot(summary, dispatches, now_ms):
    """Build the file the native bots poll every cycle."""
    rows = [r for r in dispatches if r.get('status') in ('PENDING', 'BLOCKED')]
    recent = [r for r in dispatches if r.get('status') not in ('PENDING', 'BLOCKED')]
    return dict(
        schema_version=2, generated_at_ms=int(now_ms),
        cycle_id=text(summary.get('cycle_id'), 40), last_cycle_at=text(summary.get('at'), 40),
        scheduler='hourly :15 Europe/Bucharest',
        doctor=dict(status=text((summary.get('doctor') or {}).get('status'), 20),
                    failing=[text(name, 40) for name in
                             (summary.get('doctor') or {}).get('failing') or []]),
        open_dispatches=len(rows),
        dispatches=[_dispatch(r) for r in (rows + recent)[:MAX_DISPATCHES]],
        roster=[_roster(r) for r in (summary.get('roster') or [])[:MAX_ROSTER]])


def safe(source):
    """Bound an already-public team file before serving or publishing it."""
    if not isinstance(source, dict):
        raise ValueError('invalid team snapshot')
    doctor = source.get('doctor') if isinstance(source.get('doctor'), dict) else {}
    return dict(
        schema_version=2, generated_at_ms=source.get('generated_at_ms'),
        cycle_id=text(source.get('cycle_id'), 40),
        last_cycle_at=text(source.get('last_cycle_at'), 40),
        scheduler=text(source.get('scheduler'), 60),
        doctor=dict(status=text(doctor.get('status'), 20),
                    failing=[text(n, 40) for n in (doctor.get('failing') or [])[:20]]),
        open_dispatches=source.get('open_dispatches'),
        dispatches=[_dispatch(r) for r in (source.get('dispatches') or [])[:MAX_DISPATCHES]
                    if isinstance(r, dict)],
        roster=[_roster(r) for r in (source.get('roster') or [])[:MAX_ROSTER]
                if isinstance(r, dict)])


def unavailable():
    return dict(schema_version=2, status='unavailable')
