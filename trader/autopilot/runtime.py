"""Single-pass daemon coordinator; all transport and time are injectable."""
from copy import deepcopy
from pathlib import Path
from sqlite3 import Error as DatabaseError
import time

from trader.autopilot import policy
from trader.autopilot.liquidation import enrich
from trader.autopilot.constants import (MAJORS, DECISION_INTERVAL_S, SNAPSHOT_MAX_INTERVAL_S,
                                        TICK_STALE_ALERT_S, KUCOIN_DOWN_ALERT_S)
from trader.autopilot.market import ingest_open_minutes, candles_after
from trader.autopilot.storage import atomic_json, read_json, EventLog, _safe
from trader.data.kucoin_public import PublicClient
from trader.papergrid.engine import _mark
from trader.autopilot.funding import charge


def guarded_paths(database, state, radar, snapshot):
    if any('..' in Path(p).parts for p in (database, state, radar, snapshot)):
        raise ValueError('parent traversal is not allowed')
    paths = [_safe(Path(p).expanduser()) for p in (database, state, radar, snapshot)]
    protected = [Path.home() / p for p in (
        'Sandbox/grokbot/zmarty-paper-runtime', '.openclaw', '.claude', '.paperclip',
        'Library/LaunchAgents')]
    for path in paths:
        if any(path == root or root in path.parents for root in protected):
            raise ValueError('protected runtime path')
    if len(set(paths)) != 4:
        raise ValueError('input and output paths must differ')
    return paths


def _system(now, kind, code=0):
    return dict(ts_ms=now, bot_id=0, symbol='SYSTEM', type=kind, code=code)


class Runner:
    def __init__(self, database, state, radar, snapshot, *, client=None, now_ms=None,
                 chat_id=None, telegram_state=None, notifier=None):
        self.database, self.state_path, self.radar_path, self.snapshot_path = guarded_paths(
            database, state, radar, snapshot)
        self.now_ms = now_ms or (lambda: int(time.time() * 1000))
        self.client = client
        self.chat_id, self.telegram_state, self.notifier = chat_id, telegram_state, notifier
        if bool(chat_id) != bool(telegram_state):
            raise ValueError('both Telegram arguments are required')
        self.state = (read_json(self.state_path, max_bytes=64 * 1024 * 1024)
                      if self.state_path.exists() else policy.new_state(self.now_ms()))
        allowed = set(policy.new_state(0)) | {'runtime', 'event_seq', 'pending_events', 'pending_notifications', 'pending_watchlists'}
        if self.state.get('schema_version') != 1 or set(self.state) - allowed:
            raise ValueError('unsupported autopilot state')
        for key in ('open_bots', 'closed_bots', 'equity_curve'):
            if not isinstance(self.state.get(key), list):
                raise ValueError('invalid autopilot state list')
        self.state.setdefault('runtime', dict(quotes={}, kucoin_ok=False,
                              kucoin_down_since_ms=None, alert_active=False, last_write_ms=0,
                              last_decision_ms=0, radar_scan_id=None))
        self.state['runtime'].setdefault('last_tick_ms', self.state['started_ms'])
        for key, value in [('event_seq', 0), ('pending_events', []), ('pending_notifications', []), ('pending_watchlists', [])]:
            self.state.setdefault(key, value)
        directory = self.state_path.parent
        self.log = EventLog(directory if directory.name == 'autopilot' else directory / 'autopilot')
        self.radar, self.radar_stamp = None, None
        self.recovered, self.recovery_attempt_ms = not bool(self.state['open_bots']), None
        self.stop = None

    def _transport(self, tick=False):
        return self.client or PublicClient(timeout=8, deadline=time.monotonic() + 8 if tick else None)

    def _reload_radar(self):
        _safe(self.radar_path)
        info = self.radar_path.stat()
        stamp = (info.st_mtime_ns, info.st_size, info.st_ino)
        if stamp == self.radar_stamp:
            return False
        value = read_json(self.radar_path)
        if (value.get('schema_version') != 1 or type(value.get('asof_ms')) is not int
                or not isinstance(value.get('sections'), dict)):
            raise ValueError('invalid radar snapshot')
        self.radar, self.radar_stamp = value, stamp
        return True

    def _charge_funding(self, updates):
        # Split funding valuation by boundary without creating artificial market ticks.
        adjusted = deepcopy(self.state)
        for wrapper in adjusted['open_bots']:
            bot = wrapper['engine']; update = updates.get(bot['symbol'])
            if update is None or update['ts_ms'] <= bot['last_ts_ms']:
                continue
            charge(self.database, bot, update['ts_ms'])
            _mark(bot, bot['last_price'])
        self.state = adjusted

    def _apply(self, updates):
        self._charge_funding(updates)
        self.state, events = policy.advance(self.state, updates)
        return events

    def _recover(self, now):
        symbols = [w['engine']['symbol'] for w in self.state['open_bots']]
        ingest_open_minutes(self.database, symbols, self._transport(), now, stop=self.stop)
        updates = []
        for wrapper in self.state['open_bots']:
            bot = wrapper['engine']
            updates.extend((c['ts_ms'], bot['symbol'], c) for c in
                           candles_after(self.database, bot['symbol'], bot['last_ts_ms'], now))
        events = []
        for at, symbol, candle in sorted(updates, key=lambda row: (row[0], row[1])):
            if self.stop is not None and self.stop.is_set():
                raise RuntimeError('recovery interrupted')
            events.extend(self._apply({symbol: candle}))
            if not self.state['equity_curve'] or at > self.state['equity_curve'][-1][0]:
                self.state = policy.sample(self.state, at)
        self.recovered = True
        return events

    def _health(self, now):
        meta = self.state['runtime']
        # Tick age measures the last successful allTickers pass, not the last trade
        # of the thinnest coin: a quiet contract must not read as a dead feed.
        return dict(heartbeat_ms=now, tick_age_s=max(0, (now - meta['last_tick_ms']) / 1000),
                    kucoin_ok=meta['kucoin_ok'], kucoin_down_since_ms=meta['kucoin_down_since_ms'],
                    radar_age_min=max(0, (now - self.radar['asof_ms']) / 60000) if self.radar else None,
                    recovery_pending=not self.recovered)

    def _persist(self, now):
        self.state['runtime']['last_write_ms'] = now
        # Persist the pending journal first. Log event_id dedup makes restart retry safe.
        atomic_json(self.state_path, self.state)
        self.log.append(self.state['pending_events'])
        self.log.prune(now)
        self.state['pending_events'] = []
        atomic_json(self.state_path, self.state)
        view = enrich(policy.snapshot(self.state, now, self._health(now)), self.database, now)
        atomic_json(self.snapshot_path, view)
        return view

    def _queue(self, events):
        for event in events:
            self.state['event_seq'] += 1
            numbered = dict(event, event_id=self.state['event_seq'])
            self.state['pending_events'].append(numbered)
            if self.chat_id and event['type'] in ('OPEN', 'CLOSE'):
                self.state['pending_notifications'].append(numbered)

    def _notify(self, view, now):
        if not self.chat_id or (self.stop is not None and self.stop.is_set()):
            return
        self.state['pending_watchlists'] = [entry for entry in self.state['pending_watchlists']
            if entry['watchlist_asof_ms'] >= now - 30 * 86400000]
        if self.notifier is None:
            from trader.autopilot.telegram import deliver
            self.notifier = deliver
        # Retain bot details for retries beyond the public snapshot's last20 closed rows.
        private = dict(view, closed_bots=[], pending_watchlists=deepcopy(self.state['pending_watchlists']))
        for wrapper in self.state['closed_bots']:
            bot = wrapper['engine']
            elapsed = max(1, bot['closed_ms'] - bot['opened_ms'])
            private['closed_bots'].append(dict(bot, grids_per_hour=bot['completed_grids'] * 3600000 / elapsed))
        try:
            self.notifier(private, self.state['pending_notifications'], now, self.chat_id, self.telegram_state)
        except (OSError, ValueError, RuntimeError):
            self._queue([_system(now, 'ERROR', 4)])
        else:
            sent = read_json(self.telegram_state).get('sent', {})
            self.state['pending_watchlists'] = [entry for entry in self.state['pending_watchlists']
                if f"WATCHLIST:{entry['watchlist_scan_id']}" not in sent
                and entry['watchlist_asof_ms'] >= now - 30 * 86400000]
            self.state['pending_notifications'] = [e for e in self.state['pending_notifications']
                if f"{e['type']}:{e['bot_id']}:{e['ts_ms']}" not in sent
                and e['ts_ms'] >= now - 30 * 86400000]

    def pass_once(self, *, force_decision=False):
        now = self.now_ms()
        before = deepcopy(self.state)
        events = []
        radar_changed = False
        try:
            radar_changed = self._reload_radar()
        except (OSError, ValueError, TypeError):
            self.radar = None
            self.radar_stamp = None
            events.append(_system(now, 'ERROR', 2))
        if not self.recovered and (self.recovery_attempt_ms is None or
                                   now - self.recovery_attempt_ms >= DECISION_INTERVAL_S * 1000):
            self.recovery_attempt_ms = now
            recover_before = deepcopy(self.state)
            try:
                events.extend(self._recover(now))
            except (OSError, ValueError, RuntimeError, DatabaseError):
                self.state = recover_before
                events.append(_system(now, 'ERROR', 3))
        if self.stop is not None and self.stop.is_set():
            self._queue(events)
            return self._persist(self.now_ms())
        try:
            rows = self._transport(tick=True).all_tickers()
            now = self.now_ms()
            required = set(MAJORS) | {w['engine']['symbol'] for w in self.state['open_bots']}
            quotes = {r['symbol']: r for r in rows if r['symbol'] in required and r['ts_ms'] <= now}
            previous = self.state['runtime']['quotes']
            quotes = {s: r for s, r in quotes.items() if r['ts_ms'] >= previous.get(s, {}).get('ts_ms', 0)}
            if self.recovered:
                events.extend(self._apply(quotes))
            meta = self.state['runtime']
            meta['quotes'] = {s: quotes.get(s, previous.get(s)) for s in required if s in quotes or s in previous}
            meta.update(kucoin_ok=True, kucoin_down_since_ms=None, last_tick_ms=now)
        except (OSError, ValueError, RuntimeError, DatabaseError):
            now = self.now_ms()
            meta = self.state['runtime']
            meta['kucoin_ok'] = False
            if meta['kucoin_down_since_ms'] is None:
                meta['kucoin_down_since_ms'] = now
            events.append(_system(now, 'ERROR', 1))
        meta = self.state['runtime']
        due = force_decision or radar_changed or now - meta['last_decision_ms'] >= DECISION_INTERVAL_S * 1000
        if (due and self.recovered and meta['kucoin_ok']
                and not (self.stop is not None and self.stop.is_set())):
            radar = self.radar
            if radar is not None and now - radar['asof_ms'] > 120 * 60000:
                radar = dict(radar, sections={}, rows=[dict(r, passes_liquidity=False) for r in radar.get('rows', [])])
            # Apply funding through close time even if the last trade preceded a boundary.
            self._charge_funding({w['engine']['symbol']: dict(ts_ms=now)
                                  for w in self.state['open_bots']})
            prices = {r['symbol']: r['price'] for r in rows if 0 <= now-r['ts_ms'] <= 120000}
            scan_id = str(self.radar['asof_ms']) if self.radar else None
            previous_watchlist = deepcopy(self.state.get('watchlist', {}))
            self.state, emitted = policy.decide(self.state, radar, prices, now, scan_id, require_live_prices=True)
            current = self.state['watchlist']
            if (self.chat_id and current['last_scan_id'] != previous_watchlist.get('last_scan_id')
                    and any(current[tier] != previous_watchlist.get(tier, []) for tier in ('core', 'bench'))):
                self.state['pending_watchlists'].append(dict(
                    watchlist={tier: deepcopy(current[tier]) for tier in ('core', 'bench')},
                    watchlist_history=deepcopy(current['history']),
                    watchlist_scan_id=current['last_scan_id'], watchlist_asof_ms=current['asof_ms']))
                self.state['pending_watchlists'] = [entry for entry in self.state['pending_watchlists']
                    if entry['watchlist_asof_ms'] >= now - 30 * 86400000][-720:]
            events.extend(emitted)
            self.state['runtime'].update(last_decision_ms=now, radar_scan_id=scan_id)
        self.state = policy.sample(self.state, now)
        health = self._health(now)
        unhealthy = (health['tick_age_s'] >= TICK_STALE_ALERT_S or
                     (not health['kucoin_ok'] and now - self.state['runtime']['kucoin_down_since_ms'] >= KUCOIN_DOWN_ALERT_S * 1000))
        active = self.state['runtime']['alert_active']
        if unhealthy and not active:
            events.append(_system(now, 'ALERT'))
            self.state['runtime']['alert_active'] = True
        elif active and not unhealthy and health['kucoin_ok']:
            events.append(_system(now, 'RECOVER'))
            self.state['runtime']['alert_active'] = False
        self._queue(events)
        # A fresh tick timestamp alone is a heartbeat, not a state change worth a write.
        before['runtime']['last_tick_ms'] = self.state['runtime']['last_tick_ms']
        changed = before != self.state
        view = policy.snapshot(self.state, now, health)
        if changed or now - self.state['runtime']['last_write_ms'] >= SNAPSHOT_MAX_INTERVAL_S * 1000:
            view = self._persist(now)
        pending_before = deepcopy((self.state['pending_notifications'], self.state['pending_watchlists']))
        self._notify(view, now)
        if pending_before != (self.state['pending_notifications'], self.state['pending_watchlists']) or self.state['pending_events']:
            view = self._persist(now)
        return view
