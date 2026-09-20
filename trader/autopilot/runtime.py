"""Single-pass daemon coordinator; all transport and time are injectable."""
from copy import deepcopy
from pathlib import Path
from sqlite3 import Error as DatabaseError
import sys
import time

from trader.autopilot import policy, evidence_archive, counterfactual, history_archive
from trader.autopilot.liquidation import enrich
from trader.autopilot.constants import (MAJORS, DECISION_INTERVAL_S, SNAPSHOT_MAX_INTERVAL_S,
                                        TICK_STALE_ALERT_S, KUCOIN_DOWN_ALERT_S, LOCAL_ERROR,
                                        BLACKOUT_MIN_ROWS, ENTRY_STALL_ALERT_H, MAX_BOTS)
from trader.autopilot.market import ingest_open_minutes, candles_after, minute_times
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
                 chat_id=None, telegram_state=None, notifier=None, entry_policy=None):
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
        # States written before the hourly candle aggregate get an empty list.
        self.state.setdefault('equity_hourly', [])
        self.state.setdefault('runtime', dict(quotes={}, kucoin_ok=False,
                              kucoin_down_since_ms=None, alert_active=False, last_write_ms=0,
                              last_decision_ms=0, radar_scan_id=None))
        from trader.radar.entry import VERSION
        entry_policy = entry_policy or self.state['runtime'].get('entry_policy_version')
        if entry_policy not in (None, VERSION):
            raise ValueError('unknown entry policy')
        self.entry_policy = entry_policy
        if entry_policy:
            meta = self.state['runtime']
            if meta.get('entry_policy_version') != entry_policy:
                meta['entry_policy_activated_ms'] = self.now_ms()
            meta['entry_policy_version'] = entry_policy
        self.state['runtime'].setdefault('last_tick_ms', self.state['started_ms'])
        self.state['runtime'].setdefault('pending_funding_reconciliation', {})
        self.state['runtime'].setdefault('pending_recovery_reconciliation', {})
        for key, value in [('event_seq', 0), ('pending_events', []), ('pending_notifications', []), ('pending_watchlists', [])]:
            self.state.setdefault(key, value)
        directory = self.state_path.parent
        self.log = EventLog(directory if directory.name == 'autopilot' else directory / 'autopilot')
        self.radar, self.radar_stamp = None, None
        self.recovered, self.recovery_attempt_ms = not bool(self.state['open_bots']), None
        self.stop = None
        self.history_imported = False

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

    def _pending_close_funding(self, bot, through_ms, error):
        # Retain the pre-close exposure and cursor even after the closed bot ages
        # out of the public ledger. A later reconciliation must use settlement
        # evidence; retrying charge() blindly would advance past missing rates.
        fields = ('symbol', 'position_contracts', 'last_price', 'opened_ms',
                  'last_ts_ms', 'last_funding_ts_ms', 'funding_checked_through_ms',
                  'funding_paid', 'funding_interval_ms', 'next_funding_ms')
        pending = self.state['runtime']['pending_funding_reconciliation']
        pending[str(bot['bot_id'])] = dict(
            bot_id=bot['bot_id'], through_ms=through_ms,
            status='PENDING_RECONCILIATION', error_type=type(error).__name__,
            recovery_pending=not self.recovered,
            pre_close={key: bot[key] for key in fields if key in bot},
            hedge_positions=[dict(position_contracts=book['position_contracts'],
                                  last_price=book['last_price'])
                             for book in bot.get('hedge_books', [])])
        bot['funding_managed'] = True
        bot['funding_schedule_status'] = 'PENDING_RECONCILIATION'
        bot['accounting_status'] = 'PENDING_FUNDING_RECONCILIATION'

    def _apply(self, updates, *, boundary_only=False):
        events, remaining = [], dict(updates)
        # Observed boundary exits take priority over both historical recovery and
        # accounting availability. Process each independently so one symbol's
        # funding failure cannot veto another symbol's safety close.
        for bot_id in [w['engine']['bot_id'] for w in self.state['open_bots']]:
            bot = next(w['engine'] for w in self.state['open_bots']
                       if w['engine']['bot_id'] == bot_id)
            update = remaining.get(bot['symbol'])
            if update is None or update['ts_ms'] <= bot['last_ts_ms']:
                continue
            if policy._boundary_price(bot, update) is None:
                continue
            remaining.pop(bot['symbol'])
            try:
                self._charge_funding({bot['symbol']: update})
            except (OSError, ValueError, RuntimeError, DatabaseError) as error:
                # _charge_funding commits only a complete copy. Preserve the
                # unchanged funding ledger and an explicit unresolved obligation.
                self._pending_close_funding(bot, update['ts_ms'], error)
                events.append(_system(update['ts_ms'], 'ERROR', LOCAL_ERROR))
            if not self.recovered:
                current = next(w['engine'] for w in self.state['open_bots']
                               if w['engine']['bot_id'] == bot_id)
                current['recovery_incomplete_at_close'] = True
                # The recovery reader does not prove contiguous market coverage.
                # Preserve this uncertainty beyond closed-history retention even
                # when funding was successfully booked and recovery later ends.
                self.state['runtime']['pending_recovery_reconciliation'][str(bot_id)] = dict(
                    bot_id=bot_id, symbol=current['symbol'], status='PENDING_RECONCILIATION',
                    reason='RECOVERY_COVERAGE_UNVERIFIED',
                    last_processed_ms=bot['last_ts_ms'], observed_ms=update['ts_ms'],
                    boundary_price=policy._boundary_price(current, update),
                    update_kind='tick' if 'price' in update else 'candle')
            self.state, emitted = policy.advance(self.state, {bot['symbol']: update})
            events.extend(emitted)
        if not boundary_only and remaining:
            try:
                self._charge_funding(remaining)
                self.state, emitted = policy.advance(self.state, remaining)
                events.extend(emitted)
            except (OSError, ValueError, RuntimeError, DatabaseError) as error:
                if not self.recovered:
                    raise
                # Return already-completed safety events to the journal even if
                # ordinary accounting/stepping fails later in this pass.
                print('autopilot local error: ' + type(error).__name__,
                      file=sys.stderr, flush=True)
                events.append(_system(self.now_ms(), 'ERROR', LOCAL_ERROR))
        return events

    def _recover(self, now):
        symbols = [w['engine']['symbol'] for w in self.state['open_bots']]
        ingest_open_minutes(self.database, symbols, self._transport(), now, stop=self.stop)
        updates = []
        for wrapper in self.state['open_bots']:
            bot = wrapper['engine']
            updates.extend((c['ts_ms'], bot['symbol'], c) for c in
                           candles_after(self.database, bot['symbol'], bot['last_ts_ms'], now))
        for at, symbol, candle in sorted(updates, key=lambda row: (row[0], row[1])):
            if self.stop is not None and self.stop.is_set():
                raise RuntimeError('recovery interrupted')
            # Each successfully processed candle is a committed transition.
            # Keep its journal alongside state before attempting another symbol:
            # a later failure must not resurrect a bot already closed for safety.
            self._queue(self._apply({symbol: candle}))
            if not self.state['equity_curve'] or at > self.state['equity_curve'][-1][0]:
                self._sample(at)
        self.recovered = True
        return []  # Committed recovery events are already in the pending journal.

    def _reconcile_recovery(self, now):
        """Resolve boundary closes taken during recovery once the public candle
        record proves contiguous coverage of the unverified window. Without this
        a single such close gated all future entries forever (seen 2026-09-17,
        bot 56): the pending map had a writer but no reader."""
        pending = self.state['runtime']['pending_recovery_reconciliation']
        for key in sorted(pending):
            entry = pending[key]
            try:
                # Verify the enclosing minutes, so even a sub-minute tick gap
                # needs its committed candle before the desk trades again.
                start = int(entry['last_processed_ms']) // 60000 * 60000
                cutoff = -(-int(entry['observed_ms']) // 60000) * 60000
                expected = set(range(start, cutoff, 60000))
                covered = minute_times(self.database, entry['symbol'], start, cutoff)
            except (OSError, ValueError, TypeError, KeyError, DatabaseError):
                continue
            if expected - covered:
                continue
            resolved = dict(entry, status='RESOLVED', reason='COVERAGE_VERIFIED',
                            resolved_ms=now)
            for wrapper in self.state['closed_bots']:
                if wrapper['engine'].get('bot_id') == entry.get('bot_id'):
                    wrapper['recovery_reconciliation'] = resolved
            del pending[key]
            print('autopilot recovery reconciliation resolved: bot %s %s'
                  % (entry.get('bot_id'), entry.get('symbol')), flush=True)

    def _productivity(self, now):
        """Is the daemon still doing its job, not merely still running?"""
        radar = self.radar or {}
        rows = radar.get('rows') or []
        verified = sum(1 for entry in rows if entry.get('range_verified'))
        sections = radar.get('sections') or {}
        candidates = sum(len(value) for key, value in sections.items()
                         if key != 'majors' and isinstance(value, list))
        opened = [wrapper['engine']['opened_ms'] for key in ('open_bots', 'closed_bots')
                  for wrapper in self.state[key]]
        since_open = (now - max(opened)) / 3_600_000 if opened else None
        free = max(0, MAX_BOTS - len(self.state['open_bots']))
        return dict(radar_rows=len(rows), radar_verified=verified,
                    core_candidates=candidates, free_slots=free,
                    hours_since_open=since_open,
                    structure_blackout=len(rows) >= BLACKOUT_MIN_ROWS and verified == 0,
                    entries_stalled=bool(free and candidates and since_open is not None
                                         and since_open >= ENTRY_STALL_ALERT_H))

    def _health(self, now):
        meta = self.state['runtime']
        # Tick age measures the last successful allTickers pass, not the last trade
        # of the thinnest coin: a quiet contract must not read as a dead feed.
        return dict(entry_policy_version=meta.get('entry_policy_version'),
                    entry_policy_activated_ms=meta.get('entry_policy_activated_ms'),
                    heartbeat_ms=now, tick_age_s=max(0, (now - meta['last_tick_ms']) / 1000),
                    kucoin_ok=meta['kucoin_ok'], kucoin_down_since_ms=meta['kucoin_down_since_ms'],
                    radar_age_min=max(0, (now - self.radar['asof_ms']) / 60000) if self.radar else None,
                    recovery_pending=not self.recovered,
                    funding_reconciliation_pending=len(meta['pending_funding_reconciliation']),
                    funding_reconciliation_status=('PENDING' if meta['pending_funding_reconciliation']
                                                   else 'NO_RECORDED_FAILURE'),
                    recovery_reconciliation_pending=len(meta['pending_recovery_reconciliation']),
                    recovery_reconciliation_status=('PENDING' if meta['pending_recovery_reconciliation']
                                                    else 'NO_RECORDED_FAILURE'),
                    setup_evidence_archive_status=meta.get('setup_evidence_archive_status', 'PENDING'),
                    history_archive_status=meta.get('history_archive_status', 'PENDING'),
                    **self._productivity(now))

    def _archive_evidence(self, *, flush=False):
        try:
            return (evidence_archive.flush(self.state, self.state_path.parent) if flush
                    else evidence_archive.stage(self.state))
        except (OSError, ValueError, TypeError, RuntimeError):
            # Archive errors must never veto the trading-state/event checkpoint.
            self.state['runtime']['setup_evidence_archive_status'] = 'BLOCKED'
            return dict(status='BLOCKED', pending=1, failed=1)

    def _archive_history(self):
        try:
            history_archive.checkpoint(self.log.directory, self.state,
                                       import_logs=not self.history_imported)
            self.history_imported = True
            self.state['runtime']['history_archive_status'] = 'COMPLETE'
            return True
        except (OSError, ValueError, TypeError, DatabaseError):
            self.state['runtime']['history_archive_status'] = 'BLOCKED'
            return False

    def _record_counterfactual(self, radar, prices, emitted, scan_id, now):
        """Research-only recorder: it must never affect trading, state or the pass."""
        try:
            rows = [dict(row, price=prices.get(row.get('symbol'), row.get('price')))
                    for row in (radar or {}).get('rows') or []]
            counterfactual.record(self.log.directory / 'counterfactual', rows, emitted,
                                  scan_id, now)
        except Exception as error:  # noqa: BLE001 - fail-safe by contract
            print('autopilot counterfactual error: ' + type(error).__name__,
                  file=sys.stderr, flush=True)

    def _sample(self, now):
        history_ok = self._archive_history()
        self._archive_evidence()
        pending = self.state['runtime'].get('pending_setup_evidence', {})
        # If a full or invalid archive queue cannot retain a separate copy, keep
        # that closed wrapper until staging succeeds instead of losing its dossier.
        protected = {}
        closed = self.state['closed_bots']
        for wrapper in closed:
            dossier = wrapper.get('setup_evidence')
            if not history_ok:
                protected[wrapper['engine']['bot_id']] = wrapper
            if dossier is None:
                continue
            identifier = dossier.get('evidence_id') if isinstance(dossier, dict) else None
            archived = isinstance(identifier, str) and wrapper.get('setup_evidence_archived_id') == identifier
            queued = isinstance(identifier, str) and isinstance(pending, dict) and identifier in pending
            if not archived and not queued:
                protected[wrapper['engine']['bot_id']] = wrapper
        sampled = policy.sample(self.state, now)
        retained = {w['engine']['bot_id'] for w in sampled['closed_bots']}
        restore = {key: value for key, value in protected.items() if key not in retained}
        if restore:
            sampled['archived_net'] -= sum(policy.net(w['engine']) for w in restore.values())
            sampled['closed_bots'] = [w for w in closed if w['engine']['bot_id'] in retained | restore.keys()]
        if not history_ok:
            # Keep evidence available for retry without vetoing risk processing.
            prior = self.state['equity_curve']
            last = prior[-1][0] if prior else -1
            sampled['equity_curve'] = prior + [p for p in sampled['equity_curve'] if p[0] > last]
        self.state = sampled

    def _persist(self, now):
        self.state['runtime']['last_write_ms'] = now
        self._archive_evidence()
        # Persist the pending journal first. Log event_id dedup makes restart retry safe.
        atomic_json(self.state_path, self.state)
        history_ok = self._archive_history()
        self.log.append(self.state['pending_events'], prune=False)
        if history_ok:
            self.log.prune(now)
        else:
            self.history_imported = False
        self.state['pending_events'] = []
        self._archive_evidence(flush=True)
        atomic_json(self.state_path, self.state)
        view = policy.snapshot(self.state, now, self._health(now))
        try:
            view = enrich(view, self.database, now)
        except (OSError, ValueError, RuntimeError, DatabaseError) as error:
            # Enrichment reads the shared market DB; a transient lock must not
            # kill the daemon or veto the state checkpoint already written.
            print('autopilot enrich error: ' + type(error).__name__,
                  file=sys.stderr, flush=True)
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
        self._archive_evidence()
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
            try:
                events.extend(self._recover(now))
            except (OSError, ValueError, RuntimeError, DatabaseError):
                # Recovery commits valid transitions incrementally; preserve both
                # their state and pending events while leaving recovery blocked.
                events.append(_system(now, 'ERROR', 3))
        if self.stop is not None and self.stop.is_set():
            self._queue(events)
            return self._persist(self.now_ms())
        try:
            rows = self._transport(tick=True).all_tickers()
        except (OSError, ValueError, RuntimeError, DatabaseError):
            now = self.now_ms()
            meta = self.state['runtime']
            meta['kucoin_ok'] = False
            if meta['kucoin_down_since_ms'] is None:
                meta['kucoin_down_since_ms'] = now
            events.append(_system(now, 'ERROR', 1))
        else:
            now = self.now_ms()
            required = set(MAJORS) | {w['engine']['symbol'] for w in self.state['open_bots']}
            quotes = {r['symbol']: r for r in rows if r['symbol'] in required and r['ts_ms'] <= now}
            complete = required <= quotes.keys()
            if (complete and self.state['runtime']['kucoin_down_since_ms'] is not None
                    and self.state['open_bots']):
                # Resume through the existing candle recovery path before new
                # fills/entries; immediate boundary exits remain available.
                self.recovered = False
                self.recovery_attempt_ms = None
            previous = self.state['runtime']['quotes']
            quotes = {s: r for s, r in quotes.items() if r['ts_ms'] >= previous.get(s, {}).get('ts_ms', 0)}
            meta = self.state['runtime']
            if complete:
                meta.update(kucoin_ok=True, kucoin_down_since_ms=None, last_tick_ms=now)
            else:
                meta['kucoin_ok'] = False
                if meta['kucoin_down_since_ms'] is None:
                    meta['kucoin_down_since_ms'] = now
                events.append(_system(now, 'ERROR', 1))
            try:
                if self.recovered and complete:
                    events.extend(self._apply(quotes))
                else:
                    events.extend(self._apply(quotes, boundary_only=True))
            except (ValueError, RuntimeError, DatabaseError) as error:
                # Local policy/engine failures are not exchange outages: leave
                # kucoin_ok set from the successful pass and continue the tick.
                # Only the exception type is logged; messages may carry secrets.
                print('autopilot local error: ' + type(error).__name__,
                      file=sys.stderr, flush=True)
                events.append(_system(now, 'ERROR', LOCAL_ERROR))
            meta = self.state['runtime']
            meta['quotes'] = {s: quotes.get(s, previous.get(s)) for s in required if s in quotes or s in previous}
        meta = self.state['runtime']
        due = force_decision or radar_changed or now - meta['last_decision_ms'] >= DECISION_INTERVAL_S * 1000
        if (due and self.recovered and meta['kucoin_ok']
                and not (self.stop is not None and self.stop.is_set())):
            radar = self.radar
            if radar is not None and now - radar['asof_ms'] > 120 * 60000:
                radar = dict(radar, sections={}, rows=[dict(r, passes_liquidity=False) for r in radar.get('rows', [])])
            # Apply funding through close time even if the last trade preceded a boundary.
            self._reconcile_recovery(now)
            funding_ready = not (meta['pending_funding_reconciliation']
                                 or meta['pending_recovery_reconciliation']
                                 or meta.get('setup_evidence_archive_status') in {'BLOCKED', 'CAPACITY_BLOCKED'})
            try:
                self._charge_funding({w['engine']['symbol']: dict(ts_ms=now)
                                      for w in self.state['open_bots']})
            except (OSError, ValueError, RuntimeError, DatabaseError):
                funding_ready = False
                events.append(_system(now, 'ERROR', LOCAL_ERROR))
            prices = {r['symbol']: r['price'] for r in rows if 0 <= now-r['ts_ms'] <= 120000}
            scan_id = str(self.radar['asof_ms']) if self.radar else None
            previous_watchlist = deepcopy(self.state.get('watchlist', {}))
            if funding_ready:
                self.state, emitted = policy.decide(self.state, radar, prices, now, scan_id, require_live_prices=True, entry_policy=self.entry_policy)
            else:
                emitted = []
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
            self._record_counterfactual(radar, prices, emitted, scan_id, now)
            self.state['runtime'].update(last_decision_ms=now, radar_scan_id=scan_id)
        self._sample(now)
        health = self._health(now)
        unhealthy = (health['tick_age_s'] >= TICK_STALE_ALERT_S or
                     health['structure_blackout'] or health['entries_stalled'] or
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
