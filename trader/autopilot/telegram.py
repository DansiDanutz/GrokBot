"""Pure notification planning and explicit, checkpointed Telegram delivery."""
import copy
from datetime import datetime
import hashlib
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
    swaps = sum(datetime.fromtimestamp(ts / 1000, ZONE).date() == today
                for ts in snapshot.get('watchlist_swap_times', []))
    lines.append(f"{swaps} watchlist swaps today")
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


def _watchlist_fingerprint(watchlist):
    fields = ('symbol', 'direction', 'score', 'score_parts', 'rank')
    values = {tier: [{key: entry[key] for key in fields}
                     for entry in watchlist.get(tier, [])]
              for tier in ('core', 'bench')}
    return hashlib.sha256(json.dumps(values, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def _watchlist_text(snapshot):
    def row(entry):
        positive = [part for part in entry['score_parts'] if part['points'] > 0]
        reason = (min(positive, key=lambda part: (-part['points'], part['code']))['code']
                  if positive else 'NONE')
        return f"{entry['symbol']} {entry['direction']} {entry['score']:.6g} ({reason})"

    lines = [tier.title() + ': ' + ('; '.join(row(entry) for entry in
             snapshot['watchlist'].get(tier, [])) or 'none') for tier in ('core', 'bench')]
    for event in snapshot.get('watchlist_history', []):
        if event['ts_ms'] != snapshot.get('watchlist_asof_ms'):
            continue
        if event['type'] == 'PROMOTE':
            lines.append(f"PROMOTE {event['symbol']} over {event['replaced_symbol'] or 'vacancy'}"
                         f" · {event['score']:.6g} vs {event['replaced_score']:.6g}"
                         f" · margin {event['margin']:.6g}")
        elif event['type'] in ('DROP', 'DIRECTION_CHANGE'):
            lines.append(f"{event['type']} {event['symbol']} · score {event['score']:.6g}")
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
    blackout = bool(snapshot.get('structure_blackout'))
    stalled = bool(snapshot.get('entries_stalled'))
    unhealthy = unhealthy or blackout or stalled
    active = state.get('health_active', False)
    if unhealthy and not active:
        # Name which kind of silence this is: a dead feed, a gate admitting
        # nothing, and idle slots need different first moves.
        if blackout:
            text = ('Paper health alert: radar verified structure for 0 of '
                    f'{snapshot.get("radar_rows", 0)} symbols — no bot can open.')
        elif stalled:
            hours = snapshot.get('hours_since_open')
            text = (f'Paper health alert: {snapshot.get("free_slots", 0)} free slot(s) and '
                    f'{snapshot.get("core_candidates", 0)} candidate(s), but no bot opened in '
                    f'{hours:.1f}h.' if hours is not None else
                    f'Paper health alert: {snapshot.get("free_slots", 0)} free slot(s) and '
                    f'{snapshot.get("core_candidates", 0)} candidate(s), but no bot has opened.')
        else:
            text = 'Paper health alert: public market ticks unavailable.'
        queue(f'ALERT:{now_ms}', text, remember=False, health_active=True)
    elif active and not unhealthy and snapshot.get('kucoin_ok', False):
        queue(f'RECOVER:{now_ms}', 'Paper health recovered: the desk is opening bots again.',
              remember=False, health_active=False)

    pending = snapshot.get('pending_watchlists', [snapshot])
    for payload in pending:
        scan_id = payload.get('watchlist_scan_id')
        identity = f'WATCHLIST:{scan_id}'
        if (scan_id is None or scan_id == state.get('watchlist_scan_id')
                or identity in state['sent']):
            continue
        fingerprint = _watchlist_fingerprint(payload.get('watchlist', {}))
        if fingerprint != state.get('watchlist_fingerprint'):
            queue(identity, _watchlist_text(payload),
                  watchlist_scan_id=scan_id, watchlist_fingerprint=fingerprint)
        else:
            state['watchlist_scan_id'] = scan_id
            if 'pending_watchlists' in snapshot:
                state['sent'][identity] = now_ms

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
