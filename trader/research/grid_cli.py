"""Offline commands only. Output JSON to stdout; no scheduler or account APIs."""
import argparse
import json
import sys
import sqlite3

from .grid_snapshot import Snapshot, SnapshotError, _checked_path
from .grid_runner import run_window
from .grid_scanner import scan
from .grid_replay import sweep, registered_document
from .grid_operator import operator_output
from trader.strategies.grid_types import GridConfig, GridState, Position, Order


def read_state(path):
    path = _checked_path(path)
    if path.stat().st_size > 4000000:
        raise ValueError('state input is too large')
    raw = json.loads(path.read_text())
    raw['config'] = GridConfig(**raw['config'])
    raw['positions'] = tuple(Position(**p) for p in raw.get('positions', []))
    raw['orders'] = tuple(Order(**o) for o in raw.get('orders', []))
    raw['completion_times'] = tuple(raw.get('completion_times', []))
    return GridState(**raw)


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=('scan', 'operator', 'replay', 'sweep'))
    p.add_argument('--snapshot', required=True)
    p.add_argument('--at-ms', type=int)
    p.add_argument('--start-ms', type=int)
    p.add_argument('--end-ms', type=int)
    p.add_argument('--state')
    p.add_argument('--started-ms', type=int)
    p.add_argument('--lookback-hours', type=float, default=4)
    p.add_argument('--margin-gph', type=float, default=.25)
    p.add_argument('--payback-hours', type=float, default=4)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        with Snapshot(args.snapshot) as data:
            result = execute(data, args)
        print(json.dumps(result, allow_nan=False, sort_keys=True))
        return 0
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as error:
        print(json.dumps({'status': 'rejected', 'error_type': type(error).__name__,
                          'reason': 'invalid or unavailable offline input; no execution performed'}),
              file=sys.stderr)
        return 2


def execute(data, args):
    if args.command == 'sweep':
        return sweep(data)
    if args.command == 'replay':
        registered_document()
        if args.start_ms is None or args.end_ms is None:
            raise ValueError('replay needs start and end')
        return run_window(data, args.start_ms, args.end_ms,
                          dict(lookback_hours=args.lookback_hours,
                               margin_gph=args.margin_gph,
                               payback_hours=args.payback_hours))
    if args.at_ms is None:
        raise ValueError('scanner needs an as-of timestamp')
    if args.command == 'scan':
        result = scan(data, args.at_ms)
        result.pop('eligible_candidates', None)
        return result
    return operator_output(data, args.at_ms, read_state(args.state) if args.state else None,
                           args.started_ms, args.lookback_hours, args.margin_gph,
                           args.payback_hours)


if __name__ == '__main__':
    sys.exit(main())
