"""Persistent, read-only portfolio audits; scheduling writes only the audit archive.

48-hour windows are elapsed UTC time from the original experiment start. Daily
and weekly windows end at 09:00 Europe/Bucharest (Monday for weekly). Reporting
never changes accounts, fetches data, enables trading, or changes strategy rules.
"""
import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import html
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from paper_grid import cli, retention

KINDS = ('audit48h', 'daily', 'weekly')
ARMS = ('baseline', 'liquidation_filter')
ZONE = ZoneInfo('Europe/Bucharest')
MAX_CATCHUP = 8
ID = re.compile(r'^(audit48h|daily|weekly)-[0-9]{8}T[0-9]{6}Z$')
LIMITATIONS = [
    'Paper only. No real orders, account transfers, or strategy changes are performed by this report.',
    'Returns are experimental and do not establish profitability or statistical significance.',
    'Equity and drawdown use observed publication samples, normally 30 minutes apart; intratick losses may be larger.',
    'Event windows are (start, end]. Equity uses the last available mark at or before each boundary, with its timestamp disclosed.',
    'Closed-trade PnL includes lifetime entry/exit fees and modeled funding, including costs incurred before this window.',
    'Fees paid during the window and funding attached to closes are different accounting views; do not subtract them again from net PnL.',
    'Window funding accrual and historical open-position inventory are unavailable in legacy snapshots; no invented period funding total is shown.',
    'Equity change minus closed-trade net PnL is a residual: it includes open-position changes, cost timing and boundary mark gaps, not an exact unrealized-PnL ledger.',
    'Polling fills have no queue, partial-fill, intratick, maintenance-margin or liquidation simulation; stops can slip or remain unfilled.',
    'CoinGlass coverage refers to completed liquidations on other exchanges, not future liquidation clusters or executable KuCoin liquidity.',
    'Signal rejections are quote-observation counts, not attempted orders. Historical filtered eligibility is reconstructed from saved inputs.',
    'Runtime tests are not run by this audit. Software test status is unknown unless reviewed separately.',
]


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _clock(value=None):
    value = time.time() if value is None else value
    if not _number(value) or value < 0:
        raise ValueError('invalid audit time')
    # Ensure a representable calendar date before writing any files.
    datetime.fromtimestamp(value, timezone.utc)
    return value


def _iso(at):
    return datetime.fromtimestamp(at, timezone.utc).isoformat()


def _local(at):
    return datetime.fromtimestamp(at, ZONE).isoformat()


def _next(kind, after):
    if kind == 'audit48h':
        return after + 48 * 3600
    local = datetime.fromtimestamp(after, ZONE)
    day = local.date()
    if kind == 'weekly':
        day += timedelta(days=(0-day.weekday()) % 7)
    boundary = datetime(day.year, day.month, day.day, 9, tzinfo=ZONE)
    if boundary.timestamp() <= after:
        day += timedelta(days=7 if kind == 'weekly' else 1)
        boundary = datetime(day.year, day.month, day.day, 9, tzinfo=ZONE)
    return boundary.timestamp()


def _identifier(kind, end):
    return kind + '-' + datetime.fromtimestamp(end, timezone.utc).strftime('%Y%m%dT%H%M%SZ')


@contextmanager
def _locked(directory):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / '.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _manifest(directory, start=None):
    path = directory / 'manifest.json'
    if not path.exists():
        return dict(schema=1, run_start_at=start, cursors={}, reports=[])
    state = json.loads(path.read_text())
    if (state.get('schema') != 1 or not isinstance(state.get('reports'), list)
            or not isinstance(state.get('cursors'), dict)
            or (start is not None and state.get('run_start_at') != start)):
        raise ValueError('audit archive mismatch; preserve and review')
    seen = set()
    for row in state['reports']:
        if (not isinstance(row, dict) or not ID.fullmatch(row.get('id', ''))
                or row['id'] in seen or row.get('kind') not in KINDS):
            raise ValueError('invalid audit manifest')
        seen.add(row['id'])
    for kind, cursor in state['cursors'].items():
        if kind not in KINDS or not _number(cursor) or cursor < state['run_start_at']:
            raise ValueError('invalid audit cursor')
    return state


def _atomic_text(path, content):
    fd, name = tempfile.mkstemp(prefix='.'+path.name, dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _window(rows, start, end):
    return [r for r in rows if _number(r.get('time')) and start < r['time'] <= end]


def _last(points, at):
    matches = [p for p in points if p['time'] <= at]
    return matches[-1] if matches else None


def _coverage(times, start, end, expected_seconds):
    times = sorted(set(t for t in times if start < t <= end))
    gaps = [b-a for a, b in zip([start]+times, times+[end])]
    expected = max(0, math.floor((end-start)/expected_seconds))
    return dict(observed=len(times), expected_approximately=expected,
                first_at=times[0] if times else None, last_at=times[-1] if times else None,
                max_gap_seconds=max(gaps, default=end-start),
                observation_ratio=min(1, len(times)/expected) if expected else None,
                note='Distinct timestamps; expected count is approximate. Gaps include both window edges.')


def _arm_metrics(doc, arm, start, end):
    points = []
    initial = doc['accounts'][arm].get('statistics', {}).get('initial_equity')
    if _number(initial):
        points.append(dict(time=doc['start_at'], equity=initial))
    for row in doc.get('history', []):
        value = row.get(arm+'_equity')
        if _number(row.get('time')) and _number(value) and doc['start_at'] <= row['time'] <= end:
            points.append(dict(time=row['time'], equity=value))
    # A real publication wins over the synthetic initial mark at the same time.
    points = sorted({p['time']: p for p in points}.values(), key=lambda p:p['time'])
    first, last = _last(points, start), _last(points, end)
    marks = ([first] if first else []) + [p for p in points if start < p['time'] <= end]
    peak, dd, dd_pct = None, 0.0, 0.0
    for p in marks:
        peak = p['equity'] if peak is None else max(peak, p['equity'])
        dd = max(dd, peak-p['equity'])
        if peak > 0:
            dd_pct = max(dd_pct, 100*(peak-p['equity'])/peak)
    events = [e for e in _window(doc.get('events', []), start, end) if e.get('account') == arm]
    closes = [e for e in events if e.get('type') == 'close' and _number(e.get('net_pnl'))]
    pnl = [e['net_pnl'] for e in closes]
    realized = sum(pnl)
    fees = sum(e.get('fee', 0) for e in events if e.get('type') in ('open', 'add') and _number(e.get('fee', 0)))
    fees += sum(e.get('exit_fee', 0) for e in closes if _number(e.get('exit_fee', 0)))
    delta = last['equity']-first['equity'] if first and last else None
    counts = Counter(e.get('type', 'unknown') for e in events)
    return dict(start_mark=first, end_mark=last,
        start_mark_age_seconds=start-first['time'] if first else None,
        end_mark_age_seconds=end-last['time'] if last else None,
        equity_change=delta, closed_trade_net_pnl=realized,
        non_realized_equity_change_residual=delta-realized if delta is not None else None,
        fills=sum(counts[t] for t in ('open', 'add', 'close')), opens=counts['open'], adds=counts['add'],
        closed_trades=len(pnl), wins=sum(v>0 for v in pnl), losses=sum(v<0 for v in pnl),
        breakeven=sum(v==0 for v in pnl), win_rate=sum(v>0 for v in pnl)/len(pnl) if pnl else None,
        expectancy_net_per_close=realized/len(pnl) if pnl else None,
        gross_winning_net=sum(v for v in pnl if v>0), gross_losing_net=sum(v for v in pnl if v<0),
        fees_paid_during_window=fees,
        lifetime_funding_on_window_closes=sum(e.get('funding_model_cost', 0) for e in closes if _number(e.get('funding_model_cost', 0))),
        period_funding_accrual=None, observed_max_drawdown=dd if len(marks) > 1 else None,
        observed_max_drawdown_pct=dd_pct if len(marks) > 1 else None,
        equity_sample_coverage=_coverage([p['time'] for p in points], start, end, doc.get('report_seconds', 1800)),
        risk_halts=counts['daily_halt'], deferred_exits=counts['deferred_exit'], event_counts=dict(counts),
        close_reasons=dict(Counter(e.get('reason', 'unknown') for e in closes)),
        cumulative=dict(initial_equity=initial, marked_equity=last['equity'] if last else None,
            marked_pnl_since_start=last['equity']-initial if last and _number(initial) else None,
            closed_trade_net_since_start=None, closed_trades_since_start=None))


def _data_metrics(doc, start, end):
    observations = _window(doc.get('observations', []), start, end)
    executed = [o for o in observations if not o.get('skipped')]
    rejected, filtered_rejected, coins = Counter(), Counter(), {}
    quote_ages, future_quotes = [], 0
    for obs in executed:
        at = obs['time']
        features = obs.get('coinglass', {})
        fetched = features.get('fetched_at')
        fresh = _number(fetched) and 0 <= at-fetched < 1800
        market = obs.get('market', {})
        symbols = set(market) | set(obs.get('scan', {}).get('top5', obs.get('scan', {}).get('top_five', [])))
        for symbol in sorted(symbols):
            quote = market.get(symbol, {})
            if not isinstance(quote, dict):
                quote = {}
            counts = coins.setdefault(symbol, dict(observations=0, missing_or_stale_coinglass=0,
                baseline_eligible=0, filtered_signal_eligible=0, stale_or_invalid_quote=0))
            counts['observations'] += 1
            stamp = quote.get('quote_time')
            valid_quote = _number(stamp) and 0 <= at-stamp <= doc.get('config', {}).get('max_quote_age', 90)
            if _number(stamp):
                quote_ages.append(at-stamp)
                future_quotes += stamp > at
            counts['stale_or_invalid_quote'] += not valid_quote
            eligible = quote.get('eligible') is True
            counts['baseline_eligible'] += eligible
            if not eligible:
                rejected.update(quote.get('reasons') or ['unspecified_signal_gate'])
            feature = features.get('symbols', {}).get(symbol, {})
            latest = feature.get('latest_hour')
            available = (fresh and _number(latest) and 0 <= at-(latest+3600) <= 7200
                and all(_number(feature.get(k)) for k in ('burst_ratio', 'long_share', 'total_usd')))
            counts['missing_or_stale_coinglass'] += not available
            allowed = (available and feature.get('eligible') is True and feature['burst_ratio'] >= 3
                       and .60 <= feature['long_share'] <= 1 and feature['total_usd'] > 0)
            counts['filtered_signal_eligible'] += eligible and allowed
            if not allowed:
                filtered_rejected['coinglass_filter_not_met' if available else 'coinglass_missing_or_stale'] += 1
    for row in coins.values():
        row['coinglass_missing_rate'] = row['missing_or_stale_coinglass']/row['observations']
    return dict(cycle_coverage=_coverage([o['time'] for o in executed], start, end, doc.get('tick_seconds', 300)),
        observation_records=len(observations), skipped_records=sum(bool(o.get('skipped')) for o in observations),
        skipped_reasons=dict(Counter(str(o.get('skipped')) for o in observations if o.get('skipped'))),
        coins=coins, baseline_signal_rejections=dict(rejected), additional_filter_rejections=dict(filtered_rejected),
        maximum_quote_age_seconds=max(quote_ages) if quote_ages else None, future_quote_observations=future_quotes,
        errors=_window(doc.get('errors', []), start, end))


def _build(doc, kind, start, end, generated, prior=None):
    accounts = {arm:_arm_metrics(doc, arm, start, end) for arm in ARMS}
    for arm, metrics in accounts.items():
        previous = prior['accounts'][arm]['cumulative'] if prior else {}
        if start == doc['start_at'] or prior is not None:
            metrics['cumulative']['closed_trade_net_since_start'] = previous.get('closed_trade_net_since_start', 0) + metrics['closed_trade_net_pnl']
            metrics['cumulative']['closed_trades_since_start'] = previous.get('closed_trades_since_start', 0) + metrics['closed_trades']
        metrics['cumulative']['ledger_method'] = 'Original start plus consecutive committed windows of this report cadence; excludes inherited pre-experiment trades.'
    data = _data_metrics(doc, start, end)
    findings = []
    coverage = data['cycle_coverage']
    if coverage['observed'] == 0:
        findings.append(dict(severity='critical', subject='Coverage', detail='No successful observations in this window; performance is not assessable.'))
    elif coverage['max_gap_seconds'] > 2*doc.get('tick_seconds', 300):
        findings.append(dict(severity='warning', subject='Coverage', detail='Observation gaps exceed two scheduled cycles; inspect service availability and data errors.'))
    if data['errors'] or data['skipped_records']:
        findings.append(dict(severity='warning', subject='Data collection', detail=f"{len(data['errors'])} recorded errors and {data['skipped_records']} skipped observations."))
    missing = sum(c['missing_or_stale_coinglass'] for c in data['coins'].values())
    if missing:
        findings.append(dict(severity='warning', subject='CoinGlass coverage', detail=f'{missing} symbol-observations had missing or stale features; filtered entries are restricted.'))
    for arm, metrics in accounts.items():
        if metrics['end_mark_age_seconds'] is None or metrics['end_mark_age_seconds'] > doc.get('report_seconds', 1800):
            findings.append(dict(severity='warning', subject=arm, detail='The closing equity mark is absent or older than one publication interval.'))
        if metrics['risk_halts'] or metrics['deferred_exits']:
            findings.append(dict(severity='warning', subject=arm, detail=f"{metrics['risk_halts']} risk halts; {metrics['deferred_exits']} deferred exit attempts. Stops are not guaranteed fills."))
        if metrics['equity_change'] is not None and metrics['equity_change'] < 0:
            findings.append(dict(severity='warning', subject=arm, detail='Observed account equity declined in this window, including open exposure.'))
    total_closes = sum(a['closed_trades'] for a in accounts.values())
    findings.append(dict(severity='info', subject='Evidence', detail=f'{total_closes} completed paper trades across two correlated simulations. This is not proof of a predictive edge.'))
    severity = 'critical' if any(f['severity']=='critical' for f in findings) else 'warning' if any(f['severity']=='warning' for f in findings) else 'info'
    delta = [accounts[a]['equity_change'] for a in ARMS]
    report = dict(schema=1, id=_identifier(kind, end), kind=kind, mode='paper', severity=severity,
        generated_at=generated, run_start_at=doc['start_at'], window=dict(start_at=start, end_at=end,
            start_utc=_iso(start), end_utc=_iso(end), start_local=_local(start), end_local=_local(end),
            duration_seconds=end-start, boundary_convention='(start, end]'),
        accounts=accounts, data=data, findings=findings,
        comparison=dict(filtered_minus_baseline_equity_change=delta[1]-delta[0] if all(v is not None for v in delta) else None,
            interpretation='Descriptive paired comparison only; not statistical significance, a causal claim, or a recommendation for live trading.'),
        strategy_config=doc.get('config', {}), provenance=dict(source_file='experiment.json',
            source_last_tick_at=doc.get('last_tick_at'), source_report_at=doc.get('report_at'),
            recorded_code_hashes=doc.get('code_hashes', {}), config_hash=doc.get('config_hash'),
            test_status='unknown; tests not executed by report generation'), limitations=LIMITATIONS,
        summary=f"{kind}: {coverage['observed']} observed cycles, {total_closes} closed paper trades; {severity}. No strategy changes.")
    return report


def _render(report):
    title = f"Zmarty paper {'48-hour audit' if report['kind']=='audit48h' else report['kind']+' summary'}"
    rows = []
    metrics = [('Equity change', 'equity_change'), ('Closed-trade net PnL', 'closed_trade_net_pnl'),
        ('Other equity change (residual)', 'non_realized_equity_change_residual'), ('Fills', 'fills'),
        ('Closed trades', 'closed_trades'), ('Wins', 'wins'), ('Losses', 'losses'),
        ('Net expectancy / close', 'expectancy_net_per_close'), ('Fees paid in window', 'fees_paid_during_window'),
        ('Observed sample drawdown', 'observed_max_drawdown'), ('Risk halts', 'risk_halts'), ('Deferred exits', 'deferred_exits')]
    def fmt(value):
        return 'Unavailable' if value is None else f'{value:,.4f}' if isinstance(value, float) else str(value)
    for label, key in metrics:
        rows.append('<tr><th>'+html.escape(label)+'</th>'+''.join('<td>'+html.escape(fmt(report['accounts'][a][key]))+'</td>' for a in ARMS)+'</tr>')
    findings = ''.join('<tr><td>'+html.escape(f['severity'])+'</td><th>'+html.escape(f['subject'])+'</th><td>'+html.escape(f['detail'])+'</td></tr>' for f in report['findings'])
    encoded = json.dumps(report, indent=2, allow_nan=False)
    doc = '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+html.escape(title)+'</title>'
    doc += '<style>body{font:16px/1.5 system-ui;background:#101827;color:#e5edf5;max-width:1100px;margin:2rem auto;padding:0 1rem}a{color:#86c9ff}table{border-collapse:collapse;width:100%;margin:1rem 0}th,td{text-align:left;border-bottom:1px solid #39465a;padding:.65rem}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#182438;padding:1rem}h1,h2{line-height:1.2}.badge{background:#294663;padding:.5rem 1rem;border-radius:1rem;display:inline-block}</style>'
    doc += '<body><a href="/">← Paper dashboard</a><h1>'+html.escape(title)+'</h1><p class="badge">PAPER ONLY · '+html.escape(report['severity'].upper())+'</p>'
    doc += '<p>'+html.escape(report['window']['start_local'])+' → '+html.escape(report['window']['end_local'])+'</p><p>'+html.escape(report['summary'])+'</p>'
    doc += '<p>All monetary values are modeled USDT. Two independent accounts; do not add their returns as one portfolio.</p><h2>Account results</h2><table><tr><th>Metric</th><th>Baseline</th><th>Liquidation filter</th></tr>'+''.join(rows)+'</table>'
    doc += '<h2>Audit findings</h2><table><tr><th>Severity</th><th>Area</th><th>Finding</th></tr>'+findings+'</table>'
    for heading, value in [('Data coverage and rejected signals', report['data']), ('Equity marks and cumulative context', report['accounts']), ('Comparison', report['comparison']), ('Configuration and provenance', dict(config=report['strategy_config'], provenance=report['provenance']))]:
        doc += '<h2>'+html.escape(heading)+'</h2><pre>'+html.escape(json.dumps(value, indent=2))+'</pre>'
    doc += '<h2>Interpretation limits</h2><ul>'+''.join('<li>'+html.escape(s)+'</li>' for s in report['limitations'])+'</ul><details><summary>Complete machine-readable report</summary><pre>'+html.escape(encoded)+'</pre></details></body></html>\n'
    md = '# '+title+'\n\nPAPER ONLY. '+report['summary']+'\n\n'+report['window']['start_local']+' → '+report['window']['end_local']+'\n\n'
    md += '\n'.join('- '+html.escape(f['severity']+': '+f['subject']+' — '+f['detail']) for f in report['findings'])
    md += '\n\nFull audit data (unknown fields remain null):\n\n```json\n'+encoded.replace('```', '\\u0060\\u0060\\u0060')+'\n```\n'
    return doc, md


def _public(row):
    keys = ('id', 'kind', 'start_at', 'end_at', 'generated_at', 'severity', 'summary', 'delivered_at')
    result = {k:row.get(k) for k in keys}
    result.update(type=row['kind'], url='/audits/'+row['id']+'.html', html_url='/audits/'+row['id']+'.html',
                  json_url='/audits/'+row['id']+'.json', markdown_url='/audits/'+row['id']+'.md', md_url='/audits/'+row['id']+'.md')
    return result


def generate_due(runtime=cli.DEFAULT_RUNTIME, now=None):
    """Archive up to eight oldest completed windows; never mutate the experiment.

    Atomic report files precede the atomic manifest commit. Failed writes leave a
    window due for retry; deterministic IDs make retries overwrite orphan files.
    """
    runtime, at = Path(runtime), _clock(now)
    directory = runtime/'audits'
    with _locked(directory):
        doc = json.loads((runtime/'experiment.json').read_text())
        if doc.get('mode') != 'paper' or set(doc.get('accounts', {})) != set(ARMS):
            raise ValueError('paper experiment required')
        if not _number(doc.get('start_at')):
            raise ValueError('experiment start time required')
        start = _clock(doc['start_at'])
        state = _manifest(directory, start)
        cursors = dict(state['cursors'])
        due = []
        for _ in range(MAX_CATCHUP):
            end, kind = min((_next(kind, cursors.get(kind, start)), kind) for kind in KINDS)
            if end > at:
                break
            begin = cursors.get(kind, start)
            due.append((kind, begin, end))
            cursors[kind] = end
        if not due:
            return []
        earliest, latest = min(w[1] for w in due), max(w[2] for w in due)
        first_day = datetime.fromtimestamp(earliest, timezone.utc).strftime('%Y-%m-%d')
        last_day = datetime.fromtimestamp(latest, timezone.utc).strftime('%Y-%m-%d')
        declared = doc.get('archive_files', [])
        if not isinstance(declared, list):
            raise ValueError('invalid declared archive files')
        for name in declared:
            if not isinstance(name, str) or not retention.FILENAME.fullmatch(name):
                raise ValueError('invalid declared archive filename')
            if first_day <= name[:-5] <= last_day:
                archive_path = runtime/retention.DIRECTORY/name
                if archive_path.is_symlink() or not archive_path.is_file():
                    raise ValueError('required observation archive is missing or unsafe')
        archived = retention.read_archives(runtime, earliest, latest)
        for key in ('observations', 'events', 'errors'):
            # Use retention's exact identity and conflicting timestamp validation.
            doc[key] = retention._merge(archived.get(key, []), _window(doc.get(key, []), earliest, latest), key)
        created = []
        for kind, begin, end in due:
            prior = None
            if begin != start:
                previous_path = directory/(_identifier(kind, begin)+'.json')
                prior = json.loads(previous_path.read_text())
                if (prior.get('kind') != kind or prior.get('run_start_at') != start
                        or prior.get('window', {}).get('end_at') != begin):
                    raise ValueError('previous report chain mismatch')
            report = _build(doc, kind, begin, end, at, prior)
            identifier = report['id']
            if any(row['id'] == identifier for row in state['reports']):
                raise ValueError('audit cursor conflicts with committed report')
            page, markdown = _render(report)
            cli.atomic_json(directory/(identifier+'.json'), report)
            _atomic_text(directory/(identifier+'.html'), page)
            _atomic_text(directory/(identifier+'.md'), markdown)
            row = dict(id=identifier, kind=kind, start_at=begin, end_at=end, generated_at=at,
                       severity=report['severity'], summary=report['summary'], delivered_at=None)
            state['reports'].append(row)
            state['cursors'][kind] = end
            cli.atomic_json(directory/'manifest.json', state)
            created.append(_public(row))
        return created


def list_reports(runtime=cli.DEFAULT_RUNTIME):
    """Read public archive records, newest first; never create files."""
    directory = Path(runtime)/'audits'
    state = _manifest(directory)
    return [_public(r) for r in sorted(state['reports'], key=lambda r:(r['end_at'], r['kind']), reverse=True)]


def pending(runtime=cli.DEFAULT_RUNTIME):
    """Undelivered archive records with local paths for the native Grok reader."""
    directory = (Path(runtime)/'audits').resolve()
    result = []
    for row in reversed(list_reports(runtime)):
        if row['delivered_at'] is None:
            row.update(paths={ext:str(directory/(row['id']+'.'+ext)) for ext in ('html', 'json', 'md')})
            result.append(row)
    return result


def acknowledge(runtime, ids):
    """Mark explicitly delivered reports; reject unknown/unsafe IDs atomically."""
    ids = list(dict.fromkeys(ids))
    if not ids or any(not isinstance(value, str) or not ID.fullmatch(value) for value in ids):
        raise ValueError('valid report IDs required')
    directory = Path(runtime)/'audits'
    with _locked(directory):
        state = _manifest(directory)
        if set(ids)-{r['id'] for r in state['reports']}:
            raise ValueError('unknown audit report')
        at = _clock()
        for row in state['reports']:
            if row['id'] in ids and row['delivered_at'] is None:
                row['delivered_at'] = at
        cli.atomic_json(directory/'manifest.json', state)
        return ids


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('generate', 'pending', 'ack'))
    parser.add_argument('--runtime', type=Path, default=cli.DEFAULT_RUNTIME)
    parser.add_argument('--paper', action='store_true')
    parser.add_argument('--id', action='append', default=[])
    args = parser.parse_args(argv)
    if args.command in ('generate', 'ack') and not args.paper:
        parser.error('archive writes require --paper; live mode does not exist')
    try:
        result = generate_due(args.runtime) if args.command=='generate' else pending(args.runtime) if args.command=='pending' else acknowledge(args.runtime, args.id)
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0
    except Exception as error:
        print(json.dumps(dict(error_type=type(error).__name__, error='audit operation failed; accounts unchanged')), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
