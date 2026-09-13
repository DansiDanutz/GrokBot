"""Run the doctor and notify only when the circle is actually broken.

Silence is the product. An alarm that speaks every five minutes is one you stop
reading, which is how a seven-hour outage went unnoticed on 2026-09-13 while
every component reported itself healthy.

State lives in a small JSON file so a sustained fault alerts once and recovery
is announced once. Read-only with respect to the trading system.
"""
import argparse, json, sys, time
from pathlib import Path

from trader.doctor import checks, __main__ as live

DEFAULT_STATE = Path.home() / 'Sandbox' / 'grokbot' / 'autopilot' / 'doctor-state.json'


def _load(path):
    try:
        data = json.loads(Path(path).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def transition(previous, report):
    """Return (should_notify, text, next_state). Only edges speak."""
    was = previous.get('failing') or []
    now = report['failing']
    state = dict(failing=now, status=report['status'], at=int(time.time()))
    if now and sorted(now) != sorted(was):
        lines = ['GrokBot circle: %s' % report['status'].upper()]
        for check in report['checks']:
            if check['status'] in ('fail', 'unknown'):
                lines.append('· %s — %s' % (check['name'], check['detail']))
                if check['where']:
                    lines.append('  %s' % check['where'])
        return True, '\n'.join(lines), state
    if was and not now:
        return True, 'GrokBot circle recovered: all checks ok.', state
    return False, '', state


def main(argv=None):
    p = argparse.ArgumentParser(description='Doctor with edge-triggered notification.')
    p.add_argument('--state', default=str(DEFAULT_STATE))
    p.add_argument('--chat-id')
    p.add_argument('--database')
    p.add_argument('--dry-run', action='store_true', help='print instead of sending')
    args = p.parse_args(argv)

    report = checks.assess(live.gather(database=args.database))
    notify, text, state = transition(_load(args.state), report)
    Path(args.state).parent.mkdir(parents=True, exist_ok=True)
    Path(args.state).write_text(json.dumps(state, indent=1))

    if notify and text:
        if args.dry_run or not args.chat_id:
            print(text)
        else:
            from trader.autopilot import telegram
            telegram.send(args.chat_id, text)
    print(json.dumps(dict(status=report['status'], failing=report['failing'],
                          notified=bool(notify))))
    return report['exit_code']


if __name__ == '__main__':
    sys.exit(main())
