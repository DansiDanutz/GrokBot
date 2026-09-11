"""Explicit paper-only daemon and single-pass commands."""
import argparse
import math
import signal
from sqlite3 import Error as DatabaseError
import sys
import threading
import time
from pathlib import Path

from trader.autopilot.constants import TICK_INTERVAL_S
from trader.autopilot.runtime import Runner, guarded_paths
from trader.autopilot.storage import InstanceLock


def run_loop(runner, stop, *, monotonic=time.monotonic):
    anchor = monotonic()
    slot = 0
    while not stop.is_set():
        if stop.wait(max(0, anchor + slot * TICK_INTERVAL_S - monotonic())):
            break
        runner.pass_once()
        slot = max(slot + 1, math.ceil((monotonic() - anchor) / TICK_INTERVAL_S))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('run', 'once'))
    for flag in ('database', 'state', 'radar', 'snapshot'):
        parser.add_argument('--' + flag, required=True, type=Path)
    parser.add_argument('--telegram-chat-id')
    parser.add_argument('--telegram-state', type=Path)
    args = parser.parse_args(argv)
    if bool(args.telegram_chat_id) != bool(args.telegram_state):
        parser.error('both Telegram arguments are required')
    stop = threading.Event()
    previous = {}
    try:
        paths = guarded_paths(args.database, args.state, args.radar, args.snapshot)
        if args.telegram_state is not None:
            guarded_paths(args.database, args.telegram_state, args.radar, args.snapshot)
            if args.telegram_state.absolute() == args.state.absolute():
                raise ValueError('Telegram state must be separate')
        with InstanceLock(str(paths[1]) + '.lock'):
            for sig in (signal.SIGTERM, signal.SIGINT):
                previous[sig] = signal.getsignal(sig)
                signal.signal(sig, lambda *_: stop.set())
            runner = Runner(*paths, chat_id=args.telegram_chat_id, telegram_state=args.telegram_state)
            runner.stop = stop
            if args.command == 'once':
                runner.pass_once(force_decision=True)
            else:
                run_loop(runner, stop)
            runner._persist(runner.now_ms())
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, DatabaseError):
        print('Autopilot stopped: unsafe/unavailable input or failed operation; details omitted.', file=sys.stderr)
        return 1
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == '__main__':
    sys.exit(main())
