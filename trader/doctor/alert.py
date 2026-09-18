"""Run the doctor and notify only when the circle is actually broken.

Silence is the product. An alarm that speaks every five minutes is one you stop
reading, which is how a seven-hour outage went unnoticed on 2026-09-13 while
every component reported itself healthy.

State lives in a small JSON file so a sustained fault alerts once and recovery
is announced once. Read-only with respect to the trading system.
"""
import argparse, json, os, re, sys, time
from pathlib import Path
from urllib.request import Request, urlopen

from trader.doctor import checks, __main__ as live

TOKEN_ENV = 'DLS_TELEGRAM_BOT_TOKEN'

DEFAULT_STATE = Path.home() / 'Sandbox' / 'grokbot' / 'autopilot' / 'doctor-state.json'


def _load(path):
    try:
        data = json.loads(Path(path).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


REMIND_S = 24 * 3600


def _details(report):
    lines = []
    for check in report['checks']:
        if check['status'] in ('fail', 'unknown'):
            lines.append('· %s — %s' % (check['name'], check['detail']))
            if check['where']:
                lines.append('  %s' % check['where'])
    return lines


def transition(previous, report):
    """Return (should_notify, text, next_state). Edges speak; a sustained
    fault is re-announced once a day, because a single message five days ago
    is its own kind of silence (seen 2026-09-17: autopilot failing for days
    after one alert)."""
    was = previous.get('failing') or []
    now = report['failing']
    at = int(time.time())
    state = dict(failing=now, status=report['status'], at=at,
                 announced_at=previous.get('announced_at'))
    if now and sorted(now) != sorted(was):
        state['announced_at'] = at
        lines = ['GrokBot circle: %s' % report['status'].upper()] + _details(report)
        return True, '\n'.join(lines), state
    if now and was and sorted(now) == sorted(was):
        announced = previous.get('announced_at')
        if announced is None:
            announced = previous.get('at')
        if announced is not None and at - announced >= REMIND_S:
            state['announced_at'] = at
            lines = ['GrokBot circle: still %s (unresolved for over a day)'
                     % report['status'].upper()] + _details(report)
            return True, '\n'.join(lines), state
    if was and not now:
        state['announced_at'] = at
        return True, 'GrokBot circle recovered: all checks ok.', state
    return False, '', state


def send(chat_id, text, *, opener=urlopen, environ=os.environ):
    """Post one message to Telegram, or raise.

    Deliberately the same shape as the radar's sender: the token comes from the
    environment credential-exec.py provides, never from a file on disk or an
    argument that could reach a log.
    """
    token = environ.get(TOKEN_ENV)
    if not token:
        raise RuntimeError('Telegram credential unavailable')
    if not re.fullmatch(r'-?[0-9]{1,20}', str(chat_id)):
        raise ValueError('invalid Telegram chat identifier')
    payload = json.dumps({'chat_id': str(chat_id), 'text': text}).encode()
    request = Request('https://api.telegram.org/bot%s/sendMessage' % token,
                      data=payload, headers={'Content-Type': 'application/json'},
                      method='POST')
    try:
        with opener(request, timeout=15) as response:
            accepted = json.loads(response.read(65536)).get('ok') is True
    except Exception:
        raise RuntimeError('Telegram delivery failed') from None
    if not accepted:
        raise RuntimeError('Telegram delivery rejected')


def main(argv=None):
    p = argparse.ArgumentParser(description='Doctor with edge-triggered notification.')
    p.add_argument('--state', default=str(DEFAULT_STATE))
    p.add_argument('--chat-id')
    p.add_argument('--database')
    p.add_argument('--dry-run', action='store_true', help='print instead of sending')
    args = p.parse_args(argv)

    previous = _load(args.state)
    report = checks.assess(live.gather(database=args.database))
    notify, text, state = transition(previous, report)

    delivery = 'none'
    if notify and text:
        if args.dry_run or not args.chat_id:
            print(text)
            delivery = 'printed'
        else:
            try:
                send(args.chat_id, text)
                delivery = 'sent'
            except Exception as error:  # noqa: BLE001 - the doctor must not die
                delivery = 'failed'
                print('doctor alert delivery failed: ' + type(error).__name__,
                      file=sys.stderr, flush=True)

    # Persist the edge only once it has been spoken. Writing first meant a
    # single failed send marked the fault announced and it was never raised
    # again -- the silence this module exists to prevent.
    if delivery != 'failed':
        Path(args.state).parent.mkdir(parents=True, exist_ok=True)
        Path(args.state).write_text(json.dumps(state, indent=1))

    print(json.dumps(dict(status=report['status'], failing=report['failing'],
                          notified=delivery in ('sent', 'printed'),
                          delivery=delivery)))
    return report['exit_code']


if __name__ == '__main__':
    sys.exit(main())
