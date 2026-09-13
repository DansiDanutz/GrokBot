"""Read the live circle and report one verdict.

Read-only by design. This tells you where to look; it never restarts, kills or
mutates anything — a doctor that reaches into a running desk mid-decision is a
larger risk than the fault it is fixing.
"""
import argparse, json, shutil, sqlite3, sys, time
from pathlib import Path

from trader.doctor import checks

HOUR_MS = 3_600_000
ROOT = Path.home() / 'Sandbox' / 'grokbot'


def _json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def _last_line_json(path):
    try:
        lines = [l for l in Path(path).read_text().splitlines() if l.strip()]
        return json.loads(lines[-1]) if lines else None
    except (OSError, ValueError, IndexError):
        return None


def gather(now_ms=None, database=None):
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    f = dict(now_ms=now)

    db = Path(database or ROOT / 'market-data' / 'phase-2-20260911' / 'market.sqlite3')
    try:
        con = sqlite3.connect('file:%s?mode=ro' % db, uri=True)
        f['hourly_committed_ms'] = con.execute(
            "select max(time_ms) from klines where interval='1h'").fetchone()[0]
        newest = con.execute("select max(time_ms) from klines where interval='1m'").fetchone()[0]
        f['minute_age_s'] = (now - newest) / 1000 if newest else None
        con.close()
    except sqlite3.Error:
        f['hourly_committed_ms'] = f['minute_age_s'] = None

    radar = _json(ROOT / 'radar' / 'radar.json') or {}
    rows = radar.get('rows') or []
    sections = radar.get('sections') or {}
    f['radar_generated_ms'] = radar.get('generated_at_ms')
    f['radar_rows'] = len(rows)
    f['radar_verified'] = sum(1 for r in rows if r.get('range_verified'))
    f['core_candidates'] = sum(len(v) for k, v in sections.items()
                               if k != 'majors' and isinstance(v, list))

    snap = _json(ROOT / 'autopilot' / 'autopilot.json') or {}
    f['autopilot_tick_age_s'] = snap.get('tick_age_s')
    f['open_bots'] = len(snap.get('open_bots') or [])
    f['max_bots'] = snap.get('max_bots') or 5
    f['hours_since_open'] = snap.get('hours_since_open')

    pub = _json(ROOT / 'vercel-publisher' / 'publisher.json') or {}
    last = pub.get('last_success_at')
    f['publisher_success_age_s'] = (now / 1000 - last) if last else None

    cg = _last_line_json(ROOT / 'market-data' / 'logs' / 'coinglass.out.log') or {}
    f['coinglass_status'] = cg.get('status')
    f['coinglass_symbols_ok'] = cg.get('symbols_ok')
    f['coinglass_symbols_requested'] = cg.get('symbols_requested')

    try:
        f['disk_free_bytes'] = shutil.disk_usage(db.parent if db.parent.exists() else Path.home()).free
    except OSError:
        f['disk_free_bytes'] = None
    return f


MARK = {'ok': 'ok  ', 'warn': 'WARN', 'fail': 'FAIL', 'unknown': '??  '}


def render(report):
    out = []
    for c in report['checks']:
        out.append('  %s  %-11s %s' % (MARK[c['status']], c['name'], c['detail']))
        if c['status'] != 'ok' and c['where']:
            out.append('            -> %s' % c['where'])
    out.append('')
    out.append('  circle: %s' % report['status'].upper())
    return '\n'.join(out)


def main(argv=None):
    p = argparse.ArgumentParser(description='Check whether the whole trading circle is working.')
    p.add_argument('--json', action='store_true', help='machine-readable output')
    p.add_argument('--database', help='market database path')
    args = p.parse_args(argv)
    report = checks.assess(gather(database=args.database))
    print(json.dumps(report, indent=2) if args.json else render(report))
    return report['exit_code']


if __name__ == '__main__':
    sys.exit(main())
