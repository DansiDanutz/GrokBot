"""Manual/scheduled delivery of changed radar leaders through Telegram."""

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.request import Request, urlopen


TOKEN_ENV = 'DLS_TELEGRAM_BOT_TOKEN'
SECTION_LABELS = {'majors': 'Majors', 'turning_up': 'Turning up',
                  'turning_down': 'Turning down', 'long': 'Long', 'short': 'Short',
                  'neutral': 'Neutral', 'movers': 'Movers'}


def _leaders(report):
    sections = report.get('sections') if isinstance(report, dict) else None
    if not isinstance(sections, dict):
        raise ValueError('invalid radar report')
    return {key: [row for row in sections.get(key, [])[:3] if isinstance(row, dict)]
            for key in SECTION_LABELS}


def _message(leaders):
    lines = ['KuCoin grid radar · top 3 per section']
    for key, label in SECTION_LABELS.items():
        rows = leaders[key]
        if not rows:
            continue
        lines.append('\n' + label)
        for row in rows:
            lines.append(f"{row.get('symbol', '—')} · {row.get('direction', '—')} · "
                         f"{row.get('expected_grids_per_hour', 0):.2f} grids/h · "
                         f"{row.get('grids', 0)} grids · {row.get('step_pct', 0):.2f}% step")
    return '\n'.join(lines)


def deliver(report, chat_id, state_path, *, opener=urlopen, environ=os.environ):
    """Post changed leader identities; return False when the previous top three match."""
    token = environ.get(TOKEN_ENV)
    if not token:
        raise ValueError('Telegram credential unavailable')
    if not re.fullmatch(r'-?[0-9]{1,20}', str(chat_id)):
        raise ValueError('invalid Telegram chat identifier')
    leaders = _leaders(report)
    signature = hashlib.sha256(json.dumps({key: [row.get('symbol') for row in rows]
        for key, rows in leaders.items()}, sort_keys=True).encode()).hexdigest()
    state = Path(state_path).expanduser().absolute()
    if state.is_symlink() or state.parent.is_symlink():
        raise ValueError('Telegram state path contains a symlink')
    if state.is_file():
        try:
            if json.loads(state.read_text()).get('signature') == signature:
                return False
        except (OSError, ValueError, AttributeError):
            raise ValueError('invalid Telegram delivery state') from None
    payload = json.dumps({'chat_id': str(chat_id), 'text': _message(leaders)}).encode()
    request = Request(f'https://api.telegram.org/bot{token}/sendMessage', data=payload,
                      headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with opener(request, timeout=15) as response:
            accepted = json.loads(response.read(65536)).get('ok') is True
    except Exception:
        raise RuntimeError('Telegram delivery failed') from None
    if not accepted:
        raise RuntimeError('Telegram delivery rejected')
    state.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, pending = tempfile.mkstemp(prefix='.telegram-', dir=state.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, 'w') as handle:
            json.dump({'signature': signature}, handle)
        os.replace(pending, state)
    finally:
        if os.path.exists(pending):
            os.unlink(pending)
    return True
