"""Pure notification planning and explicit, checkpointed Telegram delivery."""
import copy
from datetime import datetime
import json
import os
from pathlib import Path
import re
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

from .constants import KUCOIN_DOWN_ALERT_S, TICK_STALE_ALERT_S
from .storage import atomic_json, read_json


ZONE = ZoneInfo('Europe/Bucharest')
DIRECTIONS = ('LONG', 'SHORT', 'NEUTRAL')


def _net(bot):
    return (bot.get('realized_pnl', 0) + bot.get('unrealized_pnl', 0)
            - bot.get('fees_paid', 0) - bot.get('funding_paid', 0))


def _row(bot):
    return (f"{bot['symbol']} · {bot['direction']} · "
            f"range {bot['range_low']:.8g}..{bot['range_high']:.8g} · "
            f"{bot.get('completed_grids', 0)} grids · "
            f"{bot.get('grids_per_hour', 0):.2f} grids/h · net {_net(bot):.2f} USDT")


def _summary(snapshot, now_ms):
    today = datetime.fromtimestamp(now_ms / 1000, ZONE).date()
    opened = snapshot.get('open_bots', [])
    closed = [bot for bot in snapshot.get('closed_bots', []) if
              datetime.fromtimestamp(bot['closed_ms'] / 1000, ZONE).date() == today]
    lines = [f"Paper daily summary {today} · equity {snapshot.get('equity', 0):.2f} USDT"
             f" · {len(opened)} open bots"]
    for direction in DIRECTIONS:
        bots = [bot for bot in opened + closed if bot['direction'] == direction]
        totals = {key: sum(bot.get(key, 0) for bot in bots) for key in
                  ('completed_grids', 'grid_profit', 'unrealized_pnl',
                   'fees_paid', 'funding_paid')}
        rate = sum(bot.get('grids_per_hour', 0) for bot in bots)
        lines.append(f"{direction}: {len(bots)} bots · {totals['completed_grids']} grids"
                     f" · {rate:.2f} grids/h · grid profit {totals['grid_profit']:.2f}"
                     f" · unrealized {totals['unrealized_pnl']:.2f}"
                     f" · fees {totals['fees_paid']:.2f} · funding {totals['funding_paid']:.2f}"
                     f" · net {sum(_net(bot) for bot in bots):.2f} USDT")
    for label, chooser in [('Best', max), ('Worst', min)]:
        lines.append(f"{label}: " + (_row(chooser(closed, key=_net)) if closed else 'none'))
    return '\n'.join(lines)


def notifications(snapshot, events, now_ms, delivery_state):
    """Return message dictionaries with success checkpoints and the final state.

    Checkpoints contain only deduplication metadata, never message text or tokens.
    Callers commit a checkpoint only after that message is accepted.
    """
    state = copy.deepcopy(delivery_state)
    state['sent'] = {key: ts for key, ts in state.get('sent', {}).items()
                     if ts >= now_ms - 30 * 86400000}
    messages = []

    def queue(identity, text, timestamp=None, remember=True, **changes):
        if identity in state['sent']:
            return
        if remember:
            state['sent'][identity] = now_ms if timestamp is None else timestamp
        state.update(changes)
        messages.append({'id': identity, 'text': text[:4096],
                         'state': copy.deepcopy(state)})

    down_since = snapshot.get('kucoin_down_since_ms')
    unhealthy = (snapshot.get('tick_age_s', float('inf')) >= TICK_STALE_ALERT_S or
                 (not snapshot.get('kucoin_ok', False) and down_since is not None
                  and now_ms - down_since >= KUCOIN_DOWN_ALERT_S * 1000))
    active = state.get('health_active', False)
    if unhealthy and not active:
        queue(f'ALERT:{now_ms}', 'Paper health alert: public market ticks unavailable.',
              remember=False, health_active=True)
    elif active and not unhealthy and snapshot.get('kucoin_ok', False):
        queue(f'RECOVER:{now_ms}', 'Paper health recovered: public market ticks available.',
              remember=False, health_active=False)

    bots = {bot['bot_id']: bot for bot in
            snapshot.get('open_bots', []) + snapshot.get('closed_bots', [])}
    for event in events:
        kind = event.get('type')
        bot = bots.get(event.get('bot_id'))
        if kind not in ('OPEN', 'CLOSE') or bot is None:
            continue
        if event['ts_ms'] < now_ms - 30 * 86400000:
            continue
        identity = f"{kind}:{event['bot_id']}:{event['ts_ms']}"
        reason = bot.get('reason', 'OPEN') if kind == 'CLOSE' else 'OPEN'
        queue(identity, f"Paper {kind} · {_row(bot)} · {reason}", event['ts_ms'])
    local = datetime.fromtimestamp(now_ms / 1000, ZONE)
    if local.hour >= 8:
        queue(f'DAILY:{local.date()}', _summary(snapshot, now_ms))
    return messages, state


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _open(request, timeout):
    return build_opener(_NoRedirect()).open(request, timeout=timeout)


def deliver(snapshot, events, now_ms, chat_id, state_path, *, opener=_open,
            environ=os.environ, max_messages=1):
    """Explicit opt-in only: deliver queued messages and persist each success."""
    token = environ.get('DLS_TELEGRAM_BOT_TOKEN')
    if not token:
        raise ValueError('Telegram credential unavailable')
    if not re.fullmatch(r'-?[0-9]{1,20}', str(chat_id)):
        raise ValueError('invalid Telegram chat identifier')
    if type(max_messages) is not int or not 1 <= max_messages <= 20:
        raise ValueError('invalid Telegram batch limit')
    path = Path(state_path)
    previous = read_json(path) if path.exists() else {}
    messages, final = notifications(snapshot, events, now_ms, previous)
    if not messages and final != previous:
        atomic_json(path, final)
    count = 0
    for message in messages[:max_messages]:
        payload = json.dumps({'chat_id': str(chat_id), 'text': message['text']}).encode()
        request = Request(f'https://api.telegram.org/bot{token}/sendMessage', data=payload,
                          headers={'Content-Type': 'application/json'}, method='POST')
        try:
            with opener(request, timeout=5) as response:
                accepted = json.loads(response.read(65536)).get('ok') is True
        except Exception:
            raise RuntimeError('Telegram delivery failed') from None
        if not accepted:
            raise RuntimeError('Telegram delivery rejected')
        atomic_json(path, message['state'])
        count += 1
    return count
