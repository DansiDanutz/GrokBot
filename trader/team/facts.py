"""Read the live circle for the controller. Read-only, bounded, offline.

Every reading is optional: a missing file becomes a missing fact, never an
exception and never a healthy default. The controller decides what absence
means; this module only reports it.
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from trader.doctor import checks, __main__ as doctor
from trader.team import roster

BY_NAME = {role['name']: role['id'] for role in roster.ROLES}
EVENT_TAIL_BYTES = 200_000
MAX_JSON = 4 * 1024 * 1024
COUNTERFACTUAL = re.compile(r'counterfactual-(\d{4}-\d{2}-\d{2})\.json\Z')
IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z')


def read_json(path, limit=MAX_JSON):
    try:
        path = Path(path)
        if not path.is_file() or path.stat().st_size > limit:
            return None
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def mtime_ms(path):
    try:
        return int(Path(path).stat().st_mtime * 1000)
    except OSError:
        return None


def _ms(value):
    try:
        return int(datetime.fromisoformat(str(value).replace('Z', '+00:00'))
                   .astimezone(timezone.utc).timestamp() * 1000)
    except (ValueError, TypeError):
        return None


def event_tail(path):
    """The last events of the active log, newest last, without loading it all."""
    try:
        with open(path, 'rb') as handle:
            handle.seek(0, 2)
            handle.seek(max(0, handle.tell() - EVENT_TAIL_BYTES))
            lines = handle.read().decode('utf-8', 'ignore').splitlines()[1:]
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get('event_id'), int):
            out.append(row)
    return out


def receipts(directory):
    """dispatch_id -> the steward's minimal receipt for that dispatch."""
    out = {}
    for path in sorted(Path(directory).glob('*.json'))[-400:]:
        row = read_json(path, 64 * 1024)
        if not isinstance(row, dict) or not IDENTIFIER.fullmatch(str(row.get('dispatch_id', ''))):
            continue
        out[row['dispatch_id']] = dict(
            dispatch_id=row['dispatch_id'], role=str(row.get('role') or ''),
            answered_at=str(row.get('answered_at') or ''),
            answered_at_ms=_ms(row.get('answered_at')) or mtime_ms(path),
            room=str(row.get('room') or ''),
            summary=str(row.get('summary') or '')[:600])
    return out


def experiments(root):
    """(request ids with a result, request ids still waiting in the inbox)."""
    root = Path(root)
    done = sorted(path.parent.name for path in root.glob('results/*/result.json')
                  if IDENTIFIER.fullmatch(path.parent.name))
    pending = [path.stem for path in sorted(root.glob('inbox/*.json'))
               if IDENTIFIER.fullmatch(path.stem) and path.stem not in done]
    return done, pending


def counterfactual(directory):
    """(newest replay date, its mtime) from reports/counterfactual-<date>.json."""
    dates = sorted((COUNTERFACTUAL.match(p.name).group(1), mtime_ms(p))
                   for p in Path(directory).glob('counterfactual-*.json')
                   if COUNTERFACTUAL.match(p.name))
    return dates[-1] if dates else (None, None)


def _role_seen(paths, snapshot, review, replay_ms, receipt_rows, state):
    seen = {name: mtime_ms(path) for name, path in paths.items()}
    seen['daily_review'] = (review or {}).get('last_run_at_ms') or seen.get('daily_review')
    seen['autopilot'] = (snapshot or {}).get('heartbeat_ms') or seen.get('autopilot')
    seen['counterfactual'] = replay_ms
    seen['controller'] = ((state or {}).get('last_cycle_at_ms')
                          or _ms((state or {}).get('last_wake_at')))
    for row in receipt_rows.values():
        role = BY_NAME.get(row['role'], row['role'])
        if role in roster.BY_ID:
            seen[role] = max(seen.get(role) or 0, row['answered_at_ms'] or 0) or None
    return {name: value for name, value in seen.items() if value}


def sources(runtime, radar, database, evidence):
    """Where each deterministic member leaves its proof of life."""
    home = Path(runtime).parent
    return dict(market_data=database, radar=radar,
                autopilot=Path(runtime) / 'autopilot.json',
                publisher=home / 'vercel-publisher' / 'publisher.json',
                coinglass=home / 'market-data' / 'logs' / 'coinglass.out.log',
                liq_clusters=home / 'market-data' / 'liquidation-clusters.json',
                daily_review=Path(runtime) / 'review-status.json',
                counterfactual=home / 'reports',
                doctor=Path(runtime) / 'doctor-state.json',
                controller=Path(evidence) / 'controller-state.json')


def gather(now_ms, *, runtime, evidence, experiments_root, radar, database, state=None):
    """One dict of plain readings; the controller turns it into events."""
    snapshot = read_json(Path(runtime) / 'autopilot.json') or {}
    review = read_json(Path(runtime) / 'review-status.json') or {}
    paths = sources(runtime, radar, database, evidence)
    replay_date, replay_ms = counterfactual(paths['counterfactual'])
    receipt_rows = receipts(Path(evidence) / 'receipts')
    done, pending = experiments(experiments_root)
    report = checks.assess(doctor.gather(now_ms=now_ms, database=database))
    evidence_root = Path(evidence)
    phase_artifacts = {
        'data': mtime_ms(evidence_root / 'supabase-market-context.json'),
        'engineering': mtime_ms(evidence_root / 'engineering-status.json'),
        'research': max((row.get('answered_at_ms') or 0 for row in receipt_rows.values()),
                        default=None),
    }
    return dict(
        now_ms=now_ms, doctor=report,
        watchlist_scan_id=snapshot.get('watchlist_scan_id'),
        core_candidates=snapshot.get('core_candidates') or 0,
        entries_stalled=bool(snapshot.get('entries_stalled')),
        structure_blackout=bool(snapshot.get('structure_blackout')),
        open_directions={int(b.get('bot_id') or 0): str(b.get('direction') or '')
                         for b in snapshot.get('open_bots') or []},
        events=event_tail(Path(runtime) / 'events.jsonl'),
        review=dict(headline=review.get('evidence_headline') or '',
                    applied=(review.get('proposals') or {}).get('applied') or 0,
                    last_run_at_ms=review.get('last_run_at_ms')),
        counterfactual_date=replay_date,
        liq_clusters_ms=mtime_ms(paths['liq_clusters']),
        receipts=receipt_rows, engineering_results=done, pending_requests=pending,
        phase_artifacts=phase_artifacts,
        role_seen_ms=_role_seen(paths, snapshot, review, replay_ms, receipt_rows, state))
