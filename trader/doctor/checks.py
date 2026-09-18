"""Outcome checks across the whole circle, from plain facts.

Pure: callers gather readings, this decides. That keeps the judgement testable
without a database, a clock or a running desk.

Severity: 'fail' means the circle cannot do its job and someone must look now;
'warn' means degraded but still trading; 'unknown' means the reading itself is
missing, which is never treated as healthy.
"""
HOUR_MS = 3_600_000
MIN_FREE_BYTES = 5 * 1024 ** 3          # the collector's own write reserve
TICK_STALE_S = 180
RADAR_MAX_AGE_MIN = 90                  # hourly job plus generous slack
ENTRY_STALL_H = 3
PUBLISHER_MAX_AGE_S = 900               # three missed five-minute cycles
BLACKOUT_MIN_ROWS = 50
TEAM_STALE_S = 2 * 3600                 # hourly :15 controller plus one miss

_RANK = {'ok': 0, 'warn': 1, 'unknown': 2, 'fail': 3}


def _check(name, status, detail, where=''):
    return dict(name=name, status=status, detail=detail, where=where)


def _collector(f):
    committed, now = f.get('hourly_committed_ms'), f['now_ms']
    if committed is None:
        return _check('collector', 'unknown', 'no hourly commit reading',
                      'is com.danslab.trader-market-data running?')
    behind = ((now // HOUR_MS) * HOUR_MS - committed) // HOUR_MS
    if behind > 2:
        return _check('collector', 'fail',
                      'newest committed hour is %d hours behind' % behind,
                      'market-data logs; check disk and the KuCoin feed')
    age = f.get('minute_age_s')
    if age is not None and age > 600:
        return _check('collector', 'warn', 'minute candles %ds old' % age,
                      'collector is running but falling behind')
    return _check('collector', 'ok', 'hourly current, minute feed live')


def _radar(f):
    gen, now = f.get('radar_generated_ms'), f['now_ms']
    if gen is None:
        return _check('radar', 'unknown', 'no radar snapshot',
                      'has com.danslab.trader-radar ever run?')
    age_min = (now - gen) / 60000
    if age_min > RADAR_MAX_AGE_MIN:
        return _check('radar', 'fail', 'last scan %.0f minutes ago' % age_min,
                      'com.danslab.trader-radar did not fire')
    rows, verified = f.get('radar_rows', 0), f.get('radar_verified', 0)
    if rows >= BLACKOUT_MIN_ROWS and verified == 0:
        # Never a market condition: a full scan that admits nothing is plumbing.
        return _check('radar', 'fail', 'verified structure for 0 of %d symbols' % rows,
                      'radar is asking for an hour the collector has not written')
    return _check('radar', 'ok', '%d of %d symbols verified, %d candidates'
                  % (verified, rows, f.get('core_candidates', 0)))


def _autopilot(f):
    age = f.get('autopilot_tick_age_s')
    if age is None:
        return _check('autopilot', 'unknown', 'no tick reading',
                      'is com.danslab.trader-autopilot running?')
    if age >= TICK_STALE_S:
        return _check('autopilot', 'fail', 'last tick %ds ago' % age,
                      'autopilot is alive but not ticking; check its log')
    held = ((f.get('recovery_reconciliation_pending') or 0)
            + (f.get('funding_reconciliation_pending') or 0))
    if held:
        # Name the gate, not just its symptom: this exact silence ran for a
        # day as an anonymous entry stall before 2026-09-18.
        return _check('autopilot', 'fail',
                      '%d unresolved reconciliation(s) hold the decision gate' % held,
                      'no new entries until pending reconciliation resolves')
    free = max(0, f.get('max_bots', 0) - f.get('open_bots', 0))
    # How long the SLOT has stood empty, not how long since the last entry. A
    # desk that runs full for five hours and then closes a bot has a brand-new
    # vacancy and a five-hour-old last entry; judging it on the latter fails a
    # desk that is behaving perfectly, which is how this check first fired.
    waiting = f.get('vacancy_age_h')
    if waiting is None:
        waiting = f.get('hours_since_open')
    if free and f.get('core_candidates', 0) and waiting is not None \
            and waiting >= ENTRY_STALL_H:
        return _check('autopilot', 'fail',
                      '%d free slot(s) and %d candidate(s), nothing opened in %.1fh'
                      % (free, f['core_candidates'], waiting),
                      'entries are blocked; read the DECISION rule_blocks')
    return _check('autopilot', 'ok', '%d of %d slots in use, ticking'
                  % (f.get('open_bots', 0), f.get('max_bots', 0)))


def _publisher(f):
    age = f.get('publisher_success_age_s')
    if age is None:
        return _check('publisher', 'unknown', 'no publish reading',
                      'check the vercel-publisher status file')
    if age > PUBLISHER_MAX_AGE_S:
        return _check('publisher', 'warn', 'last successful deploy %.0f min ago' % (age / 60),
                      'publisher.stdout.log; the desk still trades without it')
    return _check('publisher', 'ok', 'deployed %.0fs ago' % age)


def _coinglass(f):
    status = f.get('coinglass_status')
    if status is None:
        return _check('coinglass', 'unknown', 'no collector reading',
                      'check coinglass.out.log')
    ok, asked = f.get('coinglass_symbols_ok', 0), f.get('coinglass_symbols_requested', 0)
    if status == 'fail' or ok == 0:
        return _check('coinglass', 'warn', 'no symbols collected',
                      'key reads from ~/.openclaw-secrets; not on the trading path')
    if asked and ok < asked:
        return _check('coinglass', 'warn', '%d of %d symbols' % (ok, asked),
                      'usually a symbol the aggregate cannot serve')
    return _check('coinglass', 'ok', '%d of %d symbols' % (ok, asked))


def _disk(f):
    free = f.get('disk_free_bytes')
    if free is None:
        return _check('disk', 'unknown', 'no disk reading', 'df on the data volume')
    gib = free / 1024 ** 3
    if free < MIN_FREE_BYTES:
        return _check('disk', 'fail', '%.1f GiB free, below the %.0f GiB reserve'
                      % (gib, MIN_FREE_BYTES / 1024 ** 3),
                      'the collector refuses to write and backups stop verifying')
    if gib < 10:
        return _check('disk', 'warn', '%.1f GiB free' % gib, 'reclaim before it bites')
    return _check('disk', 'ok', '%.1f GiB free' % gib)


def _team(f):
    age = f.get('team_cycle_age_s')
    if age is None:
        return _check('team', 'unknown', 'no controller reading',
                      'has com.danslab.trader-team-controller ever run?')
    if age > TEAM_STALE_S:
        return _check('team', 'fail', 'last controller cycle %.1f hours ago'
                      % (age / 3600),
                      'com.danslab.trader-team-controller did not fire; '
                      'the whole team is stalled and nobody is being dispatched')
    idle, blocked = f.get('team_idle_roles') or [], f.get('team_blocked') or []
    if idle or blocked:
        parts = []
        if idle:
            parts.append('idle: ' + ', '.join(idle))
        if blocked:
            parts.append('blocked: ' + ', '.join(blocked))
        return _check('team', 'warn', '; '.join(parts),
                      "dispatch.json; the role's room in the Grok Bot app")
    return _check('team', 'ok', 'controller cycled %.0fs ago, no idle role' % age)


CHECKS = (_collector, _radar, _autopilot, _publisher, _coinglass, _disk, _team)


def assess(facts):
    """Return one verdict for the whole circle."""
    results = [check(facts) for check in CHECKS]
    worst = max((r['status'] for r in results), key=lambda s: _RANK[s])
    return dict(status=worst, checks=results,
                failing=[r['name'] for r in results if r['status'] in ('fail', 'unknown')],
                exit_code=1 if worst == 'fail' else 0)
