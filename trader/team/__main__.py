"""Run one controller cycle: read the circle, dispatch work, publish the team file.

The only place in this package that touches the world. It writes three files
atomically — the controller state, the local dispatch board the doctor reads,
and the sanitized public team.json the native bots poll — and never board.json,
which belongs to the steward alone.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from trader.team import controller, facts as readings, public

ROOT = Path.home() / 'Sandbox' / 'grokbot'
EVIDENCE = ROOT / 'team-evidence'
EXPERIMENTS = ROOT / 'team-experiments'
RUNTIME = ROOT / 'autopilot'
RADAR = ROOT / 'radar' / 'radar.json'
DATABASE = ROOT / 'market-data' / 'phase-2-20260911' / 'market.sqlite3'
BRIDGE_TIMEOUT_S = 180
PYTHON = '/opt/homebrew/bin/python3'


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix='.team-', dir=path.parent)
    try:
        with os.fdopen(handle, 'w') as out:
            json.dump(value, out, indent=1, allow_nan=False, sort_keys=False)
            out.write('\n')
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def run_bridge(evidence, request_id):
    """Ask the installed operator bridge for one receipt. Never raises."""
    operator = Path(evidence) / 'operator'
    worker, config = operator / 'paper-team-bridge.py', operator / 'bridge-config.json'
    if not worker.is_file() or not config.is_file():
        return dict(status='BLOCKED', reason='operator bridge not installed')
    try:
        done = subprocess.run([PYTHON, str(worker), '--config', str(config),
                               'run', '--request', request_id],
                              capture_output=True, text=True, check=False,
                              timeout=BRIDGE_TIMEOUT_S)
        return json.loads(done.stdout or '{}') or dict(status='BLOCKED',
                                                       reason='empty receipt')
    except (OSError, ValueError, subprocess.SubprocessError):
        return dict(status='BLOCKED', reason='bridge run failed')


def dispatch_board(state, summary, now_ms):
    return dict(schema_version=controller.SCHEMA, generated_at_ms=now_ms,
                cycle_id=summary['cycle_id'], last_cycle_at_ms=now_ms,
                doctor=summary['doctor'], open=summary['open'],
                gaps=summary['gaps'], roster=summary['roster'],
                dispatches=state['dispatches'])


def render(summary):
    out = ['team cycle %s at %s (local %s)'
           % (summary['cycle_id'], summary['at'], summary['local_time']),
           '  doctor: %s %s' % (summary['doctor']['status'],
                                ','.join(summary['doctor']['failing']) or '-'),
           '  events: %s' % (', '.join(summary['events']) or 'none')]
    if summary.get('migrated_from') is not None:
        out.append('  migrated controller-state schema %s -> %d'
                   % (summary['migrated_from'], controller.SCHEMA))
    for missed in summary.get('missed_cycles') or []:
        out.append('  cycle MISSED %s' % missed)
    for phase, row in sorted((summary['phases'] or {}).items()):
        out.append('  phase %-12s %-16s due %s' % (phase, row['status'], row['due_at']))
    for row in summary['roster']:
        if row['status'] != 'OK':
            out.append('  role  %-24s %-14s idle %s h (max %s)'
                       % (row['name'], row['status'],
                          '-' if row['idle_h'] is None else row['idle_h'],
                          row['max_idle_h']))
    for identifier in summary['done']:
        out.append('  done    %s' % identifier)
    for identifier in summary['blocked']:
        out.append('  blocked %s' % identifier)
    out.append('  open dispatches: %d' % summary['open'])
    for gap in summary['gaps']:
        out.append('  gap: %s' % gap)
    return '\n'.join(out)


def render_dispatches(created):
    return '\n'.join('  -> %s  %s  [%s]\n     %s'
                     % (row['dispatch_id'], row['role_name'], row['event'],
                        row['instruction']) for row in created) or '  -> none'


def parse(argv):
    p = argparse.ArgumentParser(description='Run one paper-team controller cycle.')
    p.add_argument('--dry-run', action='store_true', help='print, write nothing')
    p.add_argument('--now-ms', type=int, help='clock override, for replay')
    p.add_argument('--evidence-dir', type=Path, default=EVIDENCE)
    p.add_argument('--experiments-dir', type=Path, default=EXPERIMENTS)
    p.add_argument('--runtime-dir', type=Path, default=RUNTIME)
    p.add_argument('--radar', type=Path, default=RADAR)
    p.add_argument('--database', type=Path, default=DATABASE)
    p.add_argument('--json', action='store_true', help='machine-readable summary')
    return p.parse_args(argv)


def _engineering(state, summary, evidence, dry_run):
    request = summary.get('engineering_request')
    if dry_run or not request or request == 'none pending':
        return state, None
    receipt = run_bridge(evidence, request)
    return controller.record_engineering(state, request, receipt), receipt


def main(argv=None):
    args = parse(argv)
    now = args.now_ms if args.now_ms is not None else int(time.time() * 1000)
    previous = readings.read_json(args.evidence_dir / 'controller-state.json') or {}
    facts = readings.gather(now, runtime=args.runtime_dir, evidence=args.evidence_dir,
                            experiments_root=args.experiments_dir, radar=args.radar,
                            database=args.database, state=previous)
    state, created, summary = controller.cycle(previous, facts, now)
    state, receipt = _engineering(state, summary, args.evidence_dir, args.dry_run)
    if receipt is not None:
        summary['engineering_receipt'] = receipt.get('status')
    if not args.dry_run:
        write_json(args.evidence_dir / 'controller-state.json', state)
        write_json(args.evidence_dir / 'dispatch.json', dispatch_board(state, summary, now))
        write_json(args.runtime_dir / 'team.json',
                   public.snapshot(summary, state['dispatches'], now))
    if args.json:
        print(json.dumps(summary, indent=1, allow_nan=False))
    else:
        print(render(summary))
        print('  dry run: nothing written' if args.dry_run else '  written: controller-state.json, dispatch.json, team.json')
        print(render_dispatches(created))
    return 0


if __name__ == '__main__':
    sys.exit(main())
