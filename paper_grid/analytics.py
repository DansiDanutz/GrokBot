"""Bounded, read-only learning metrics from the complete local paper ledger.

This module has no provider calls or write path. Published strings are authored
here or validated contract symbols; arbitrary logs and provider payloads never
become public output. All-time totals fail closed if evidence is unavailable.
"""
from collections import Counter
from datetime import datetime, timezone
import json
import math
import os
import stat
from pathlib import Path
import re
import time

from paper_grid import calendar_day, audits, coinglass, retention, telemetry_metrics, trade_metrics

ARMS = ('baseline', 'liquidation_filter')
MAX_BYTES = 200 * 1024 * 1024
MAX_DAYS = 400
DAY = 86400
SYMBOL = re.compile(r'[A-Z0-9]{1,24}USDTM\Z')
REASONS = ('net_profit_target', 'position_loss_limit', 'price_stop',
           'daily_loss_limit', 'rotation', 'unknown')
DEFINITIONS = [
    'Checks are persisted decision attempts; successful checks exclude skipped cycles.',
    'Analytics windows are [start, end]: both boundaries, including closes, are included.',
    'Distinct scan snapshots include cached scans. New discovery scans count distinct scan timestamps inside the window.',
    'Coins found means distinct shortlisted symbols, not the sum of a repeatedly scanned universe.',
    'Eligible symbol-observations are research signals, not orders or executable entry guarantees.',
    'Entries are initial position opens. Adds are recorded averaging-down buys; fills include entries, adds and closes.',
    'Good trades are closes with positive net PnL. Profit and loss include entry fees, exit fees and modeled funding already.',
    'Fees paid counts entry/add fees and exit fees paid inside the window; it must not be subtracted again from net PnL.',
    'Add cohorts classify completed position lifecycles. Unknown means the opening history is unavailable.',
    'Daily chart buckets and halt days use Europe/Bucharest midnight boundaries; archive files retain UTC dates.',
]
LIMITATIONS = [
    'Paper results are simulated and do not establish future profitability or live execution quality.',
    'Drawdown combines per-tick equity after instrumentation with older published marks; moves between marks are not observed.',
    'Rejected buys were not logged before instrumentation. Recorded zero is not a complete historical rejection total.',
    'Funding on closes covers the lifetime of those positions, not funding accrued only during the selected window.',
    'The latest 200 closed trades, 90 daily buckets and 100 most-observed coins are shown; totals use all available evidence.',
    'Unrecorded attempts cannot be reconstructed. Coverage is approximate and gaps may include intentional pauses.',
    'A favorable add cohort is an association, not proof that averaging down improved outcomes.',
    'No-loss profit factor is undefined and displayed without an invented infinity or zero.',
]


def _number(value, *, nonnegative=False):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or (nonnegative and value < 0)):
        raise ValueError('invalid analytics number')
    return value


def _optional(value):
    return None if value is None else _number(value)


def _symbol(value):
    if not isinstance(value, str) or not SYMBOL.fullmatch(value):
        raise ValueError('invalid analytics symbol')
    return value


def _read(path, remaining):
    if path.is_symlink() or not path.is_file():
        raise ValueError('analytics evidence is missing or unsafe')
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise ValueError('analytics evidence is missing or unsafe')
        payload = handle.read(remaining + 1)
    if len(payload) > remaining:
        raise ValueError('analytics evidence size limit exceeded')
    try:
        value = json.loads(payload, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError):
        raise ValueError('invalid analytics evidence') from None
    return value, len(payload)


def _records(rows, kind):
    if not isinstance(rows, list):
        raise ValueError('invalid analytics records')
    # The archive primitive validates timestamps and exact-deduplicates records.
    result = retention._merge([], rows, kind)
    identities = {}
    if kind == 'events':
        for row in result:
            identity = (row['time'], row.get('account'), row.get('type'), row.get('symbol'))
            key = retention._key(row)
            if identity in identities and identities[identity] != key:
                raise ValueError('conflicting analytics events')
            identities[identity] = key
    return result


def _load(runtime, now):
    runtime = Path(runtime)
    if any(p.is_symlink() for p in (runtime, *runtime.parents)) or not runtime.is_dir():
        raise ValueError('analytics runtime is missing or unsafe')
    source = runtime / 'experiment.json'
    directory = retention._directory(runtime)
    paths = [source]
    if directory.exists():
        paths += sorted(p for p in directory.iterdir() if retention.FILENAME.fullmatch(p.name))
    if len(paths) > MAX_DAYS + 1:
        raise ValueError('analytics archive day limit exceeded')
    total = 0
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise ValueError('analytics evidence is missing or unsafe')
        total += path.stat().st_size
        if total > MAX_BYTES:
            raise ValueError('analytics evidence size limit exceeded')
    doc, consumed = _read(source, MAX_BYTES)
    if not isinstance(doc, dict) or doc.get('mode') != 'paper':
        raise ValueError('analytics requires a paper experiment')
    start = _number(doc.get('start_at'), nonnegative=True)
    if now < start or now - start > MAX_DAYS * DAY:
        raise ValueError('analytics time window exceeds supported evidence range')
    declared = doc.get('archive_files', [])
    if not isinstance(declared, list):
        raise ValueError('invalid declared analytics archives')
    available = {p.name for p in paths[1:]}
    for name in declared:
        if not isinstance(name, str) or not retention.FILENAME.fullmatch(name) or name not in available:
            raise ValueError('declared analytics archive is missing or unsafe')
    rows = {kind: list(doc.get(kind, [])) for kind in retention.RECORDS}
    for path in paths[1:]:
        archive, size = _read(path, MAX_BYTES-consumed)
        consumed += size
        if not isinstance(archive, dict) or archive.get('schema') != 1 or archive.get('day') != path.stem:
            raise ValueError('invalid analytics archive schema')
        try:
            datetime.strptime(path.stem, '%Y-%m-%d')
        except ValueError:
            raise ValueError('invalid analytics archive day') from None
        for kind in retention.RECORDS:
            incoming = _records(archive.get(kind, []), kind)
            if any(retention._day(r['time']) != path.stem for r in incoming):
                raise ValueError('analytics archive timestamp does not match day')
            rows[kind].extend(incoming)
    # Account histories preserve inherited opens that predate experiment start.
    for arm in ARMS:
        account = doc.get('accounts', {}).get(arm)
        if not isinstance(account, dict):
            raise ValueError('missing analytics account')
        if account.get('state', {}).get('mode', 'paper') != 'paper':
            raise ValueError('analytics account is not paper')
        for event in account.get('events', []):
            rows['events'].append(dict(event, account=arm))
    for kind in retention.RECORDS:
        doc[kind] = _records(rows[kind], kind)
    history = _records(doc.get('history', []), 'observations')
    doc['history'] = history
    for row in history:
        for arm in ARMS:
            _optional(row.get(arm + '_equity'))
    for arm in ARMS:
        _optional(doc['accounts'][arm].get('statistics', {}).get('initial_equity'))
    return doc


_trades = trade_metrics.closed_trades


def _counts():
    return dict(entries=0, adds=0, closes=0, wins=0, losses=0, net_closed_pnl=0.0)


def _increment(target, event):
    kind = event['type']
    field = {'open': 'entries', 'add': 'adds', 'close': 'closes'}.get(kind)
    if field is None:
        return
    target[field] += 1
    if kind == 'close':
        value = _number(event.get('net_pnl'))
        target['net_closed_pnl'] += value
        target['wins'] += value > 0
        target['losses'] += value < 0


def _account(doc, arm, start, end, events, trades):
    events = [e for e in events if e.get('account') == arm]
    closes = [t for t in trades if start <= t['closed_at'] <= end]
    result = _counts()
    fees = 0.0
    for event in events:
        _increment(result, event)
        if event.get('type') in ('open', 'add'):
            fees += _number(event.get('fee'), nonnegative=True)
        elif event.get('type') == 'close':
            fees += _number(event.get('exit_fee'), nonnegative=True)
    profit = sum(t['net_pnl'] for t in closes if t['net_pnl'] > 0)
    loss = sum(t['net_pnl'] for t in closes if t['net_pnl'] < 0)
    # The audit helper's mark calculations are independent of event boundaries.
    marked = audits._arm_metrics(doc, arm, start, end)
    reasons, cohorts = [], []
    for reason in REASONS:
        items = [t for t in closes if t['reason'] == reason]
        if items:
            reasons.append(dict(reason=reason, closes=len(items), losses=sum(t['net_pnl'] < 0 for t in items),
                                net_pnl=sum(t['net_pnl'] for t in items)))
    for adds in (0, 1, 2, None):
        items = [t for t in closes if (t['adds'] if t['adds'] in (0, 1, 2) else None) == adds]
        cohorts.append(dict(adds=adds, closes=len(items), wins=sum(t['net_pnl'] > 0 for t in items),
                            losses=sum(t['net_pnl'] < 0 for t in items), net_pnl=sum(t['net_pnl'] for t in items)))
    # Without boundary closes, the cached audit metric has the same inclusive result.
    performance = (
        trade_metrics.report(doc, arm, start, end, include_start=True)
        if any(t["closed_at"] == start for t in trades)
        else marked["performance"]
    )
    excursions = {(t["symbol"], t["closed_at"]): t for t in performance["trades"]}
    closes = [
        dict(
            t,
            **{
                k: v
                for k, v in excursions.get((t["symbol"], t["closed_at"]), {}).items()
                if k.startswith("observed_") or k.startswith("excursion_")
            },
        )
        for t in closes
    ]
    first, last = marked['start_mark'], marked['end_mark']
    result.update(fills=result['entries']+result['adds']+result['closes'],
        breakeven=len(closes)-result['wins']-result['losses'],
        win_rate=result['wins']/len(closes) if closes else None,
        positive_profit=profit, negative_loss=loss,
        profit_factor=profit/-loss if loss < 0 else None,
        profit_factor_state='finite' if loss < 0 else 'no_losses' if closes else 'no_closes',
        fees_paid=fees, modeled_funding_on_closes=sum(t['funding_cost'] for t in closes),
        marked_equity_change=marked['equity_change'],
        start_equity=first['equity'] if first else None, end_equity=last['equity'] if last else None,
        end_mark_age_seconds=marked['end_mark_age_seconds'],
        observed_max_drawdown=marked['observed_max_drawdown'],
        observed_max_drawdown_pct=marked['observed_max_drawdown_pct'],
        expectancy=result['net_closed_pnl']/len(closes) if closes else None,
        close_reasons=reasons, add_cohorts=cohorts,
        buy_rejections=telemetry_metrics.rejections(events, [o for o in doc['observations'] if start <= o['time'] <= end], arm),
        equity_sampling=marked['equity_sampling'], performance=performance,
        journal=list(reversed(closes[-200:])), journal_total=len(closes))
    return result


def _window(doc, start, end, trades):
    observations = [o for o in doc['observations'] if start <= o['time'] <= end]
    success = [o for o in observations if not o.get('skipped')]
    events = [e for e in doc['events'] if start <= e['time'] <= end]
    days, coins, shortlist, scans = {}, {}, set(), {}

    def day(at):
        stamp = calendar_day.day_label(at)
        return days.setdefault(stamp, dict(day=stamp, checks=0, **{a: _counts() for a in ARMS}))

    def coin(symbol):
        _symbol(symbol)
        return coins.setdefault(symbol, dict(symbol=symbol, observations=0, baseline_eligible=0,
                                             filtered_eligible=0, **{a: _counts() for a in ARMS}))

    # Preserve zero-count days for honest chart spacing, bounded to the run.
    for at in calendar_day.day_samples(start, end):
        day(at)
    for obs in success:
        day(obs['time'])['checks'] += 1
        market = obs.get('market', {})
        scan = obs.get('scan', {})
        if not isinstance(market, dict) or not isinstance(scan, dict):
            raise ValueError('invalid analytics observation')
        names = scan.get('top5', scan.get('top_five', []))
        if not isinstance(names, list):
            raise ValueError('invalid analytics shortlist')
        shortlist.update(_symbol(s) for s in names)
        scanned = scan.get('scanned_at')
        if scanned is not None:
            _number(scanned, nonnegative=True)
            if scanned > obs['time']:
                raise ValueError('future analytics scan')
            liquid = scan.get('liquid_contracts')
            if liquid is not None:
                _number(liquid, nonnegative=True)
                if int(liquid) != liquid:
                    raise ValueError('invalid analytics contract count')
            scans[scanned] = liquid
        safe_quotes = {}
        for symbol, quote in market.items():
            _symbol(symbol)
            if not isinstance(quote, dict):
                raise ValueError('invalid analytics quote')
            safe_quotes[symbol] = dict(symbol=symbol, eligible=quote.get('eligible') is True,
                                       add_eligible=quote.get('add_eligible') is True, reasons=[])
        filtered = coinglass.apply_filter(safe_quotes, obs.get('coinglass', {}), obs['time'])
        for symbol in set(safe_quotes) | set(names):
            row = coin(symbol)
            row['observations'] += 1
            row['baseline_eligible'] += safe_quotes.get(symbol, {}).get('eligible') is True
            row['filtered_eligible'] += filtered.get(symbol, {}).get('eligible') is True
    for event in events:
        if event.get('account') not in ARMS or event.get('type') not in ('open', 'add', 'close'):
            continue
        _increment(day(event['time'])[event['account']], event)
        _increment(coin(event.get('symbol'))[event['account']], event)
    stamps = sorted({o['time'] for o in success})
    tick_seconds = _number(doc.get('tick_seconds', 300), nonnegative=True)
    if tick_seconds <= 0:
        raise ValueError('invalid analytics check interval')
    expected = math.floor((end-start)/tick_seconds)+1
    latest_scan = max(scans) if scans else None
    return dict(start_at=start, end_at=end,
        checks=dict(attempts=len(observations), successful=len(success), skipped=len(observations)-len(success),
                    expected_approximately=expected, coverage_ratio=min(1, len(success)/expected),
                    max_gap_seconds=max((b-a for a,b in zip([start]+stamps, stamps+[end])), default=0)),
        discovery=dict(distinct_shortlist_coins=len(shortlist), shortlist_symbols=sorted(shortlist),
            distinct_scan_snapshots=len(scans), new_discovery_scans=sum(start <= at <= end for at in scans),
            latest_scan_at=latest_scan, latest_liquid_contracts=scans.get(latest_scan),
            symbol_observations=sum(c['observations'] for c in coins.values()),
            baseline_eligible_observations=sum(c['baseline_eligible'] for c in coins.values()),
            filtered_eligible_observations=sum(c['filtered_eligible'] for c in coins.values())),
        accounts={a: _account(doc,a,start,end,events,trades[a]) for a in ARMS},
        daily=[days[d] for d in sorted(days)[-90:]],
        coins=sorted(coins.values(), key=lambda c:(-c['observations'],c['symbol']))[:100])


def build(runtime, now=None):
    """Return an allowlisted DTO; never change the source or hide missing evidence."""
    at = _number(time.time() if now is None else now, nonnegative=True)
    doc = _load(runtime, at)
    start = doc['start_at']
    trades = _trades(doc['events'], at)
    result = dict(schema=1, mode='paper', status='ok', generated_at=at,
        source_last_tick_at=_optional(doc.get('last_tick_at')), start_at=start,
        windows={name: _window(doc,begin,at,trades) for name,begin in
                 (('all',start),('24h',max(start,at-DAY)),('7d',max(start,at-7*DAY)))},
        definitions=DEFINITIONS[:], limitations=LIMITATIONS[:])
    # Overflow in arithmetic must never leak non-finite JSON as a valid report.
    try:
        json.dumps(result, allow_nan=False)
    except ValueError:
        raise ValueError('non-finite analytics result') from None
    return result
