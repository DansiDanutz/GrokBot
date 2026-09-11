"""Export bounded, credential-free PAPER DTOs to an isolated static-site directory.

This module never fetches providers, edits runtime state, or copies raw reports.
Public strings are authored here or selected from closed vocabularies. Adding a
runtime field does not make it public: publication requires an explicit DTO edit.
"""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time

from paper_grid import audits, engine, experiment, analytics, public_trade_metrics
from paper_grid.public_autopilot import safe as _autopilot, events as _public_events
from trader.autopilot.storage import read_events
from paper_grid.telemetry_constants import READABLE_BUY_REJECTION_REASONS

MAX_BYTES = 5 * 1024 * 1024
MAX_REPORTS = 100
PUBLIC_INDEX = Path(__file__).parent / 'public' / 'index.html'
RADAR_PAGE = Path(__file__).parent / 'radar.html'
PAPER_PAGE = Path(__file__).parent / 'paper.html'
SYMBOL = re.compile(r'[A-Z0-9]{1,24}USDTM\Z')
REASONS = frozenset(('invalid_contract stale_quote low_turnover wide_spread atr_outside_bounds '
    'not_lower_range no_completed_rebound strong_downtrend insufficient_rebound_room '
    'funding_outside_bounds missing_top_depth coinglass_filter_not_met '
    'coinglass_missing_or_stale liquidation_filter_pass liquidation_burst_below_3x '
    'long_share_below_60pct unspecified_signal_gate').split())
EVENTS = frozenset(('open', 'add', 'close', 'daily_halt', 'deferred_exit', 'buy_rejected'))
ERRORS = frozenset(('coinglass_data', 'cycle_error', 'data_collection', 'unknown'))
ACCOUNT_NUMBERS = ('cash equity realized_pnl unrealized_net_pnl daily_pnl unpaid_liabilities '
    'trade_count total_fees funding_cost max_drawdown max_drawdown_pct initial_equity '
    'experiment_pnl fills').split()
POSITION_NUMBERS = ('contracts quantity cost_basis entry_fees funding_accrued adds multiplier '
    'lot_size average_price last_fill last_bid last_mark score unrealized_net_pnl valuation_bid').split()
AUDIT_NUMBERS = ('start_mark_age_seconds end_mark_age_seconds equity_change closed_trade_net_pnl '
    'non_realized_equity_change_residual fills opens adds closed_trades wins losses breakeven win_rate '
    'expectancy_net_per_close gross_winning_net gross_losing_net fees_paid_during_window '
    'lifetime_funding_on_window_closes period_funding_accrual observed_max_drawdown '
    'observed_max_drawdown_pct risk_halts deferred_exits').split()
LIMITATIONS = tuple(experiment.LIMITATIONS)


def _object(value):
    return value if isinstance(value, dict) else {}


def _array(value):
    return value if isinstance(value, list) else []


def _number(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('invalid public numeric field')
    if not math.isfinite(value) or abs(value) > 1e15:
        raise ValueError('unbounded public numeric field')
    return value


def _stamp(value, required=False):
    if value is None and not required:
        return None
    number = _number(value)
    if number is None or not 0 <= number <= 253402300799:
        raise ValueError('invalid public timestamp')
    datetime.fromtimestamp(number, timezone.utc)
    return number


def _numbers(row, keys):
    return {key: _number(row.get(key)) for key in keys}


def _symbol(value):
    if not isinstance(value, str) or not SYMBOL.fullmatch(value):
        raise ValueError('invalid public symbol')
    return value


def _reason(value):
    return value if isinstance(value, str) and value in REASONS else 'data_unavailable'


def _reasons(values):
    return list(dict.fromkeys(_reason(v) for v in _array(values)[:20]))


def _status(value):
    return value if value in ('running', 'completed', 'frozen', 'unavailable') else 'unavailable'


def _errors(rows):
    count = Counter()
    for row in _array(rows):
        category = _object(row).get('type')
        count[category if isinstance(category, str) and category in ERRORS else 'unknown'] += 1
    return [{'category': key, 'count': value} for key, value in sorted(count.items())]


def _coverage(row):
    row = _object(row)
    result = _numbers(row, ('observed', 'expected_approximately', 'max_gap_seconds', 'observation_ratio'))
    result.update({key: _stamp(row.get(key)) for key in ('first_at', 'last_at')})
    return result


def _account(row):
    row = _object(row)
    if row.get('mode') != 'paper':
        raise ValueError('paper account required')
    result = _numbers(row, ACCOUNT_NUMBERS)
    result.update(mode='paper', equity_is_estimate=row.get('equity_is_estimate') is True,
                  paused=row.get('paused') is True, positions={})
    for key in ('unpriced_positions', 'insufficient_exit_depth'):
        result[key] = [_symbol(s) for s in _array(row.get(key))[:2]]
    for key in ('checked_at', 'last_success_at'):
        result[key] = _stamp(row.get(key))
    positions = _object(row.get('positions'))
    if len(positions) > 2:
        raise ValueError('paper position limit exceeded')
    for symbol, position in positions.items():
        position = _object(position)
        public = _numbers(position, POSITION_NUMBERS)
        public.update({key: _stamp(position.get(key)) for key in ('opened_at', 'last_fill_at', 'quote_time')})
        public['equity_is_estimate'] = position.get('equity_is_estimate') is True
        result['positions'][_symbol(symbol)] = public
    # A boolean expresses a halt without publishing arbitrary persisted strings.
    result['risk_halted'] = bool(row.get('halted_day'))
    return result


def _report(source, published_at):
    source = _object(source)
    exp = _object(source.get('experiment'))
    if exp.get('mode') != 'paper':
        raise ValueError('paper experiment required')
    times = {key: _stamp(exp.get(key), key in ('start_at', 'report_at')) for key in
             ('start_at', 'end_at', 'last_tick_at', 'report_at', 'next_audit_at')}
    if times['end_at'] is not None and times['end_at'] < times['start_at']:
        raise ValueError('invalid experiment window')
    exp_public = dict(times, mode='paper', status=_status(exp.get('status')),
                      continuous=exp.get('continuous') is True,
                      tick_seconds=300, report_seconds=1800)
    accounts = _object(source.get('accounts'))
    if set(accounts) != set(experiment.ARMS):
        raise ValueError('two paper accounts required')
    result = dict(schema=1, mode='paper', published_at=published_at, experiment=exp_public,
                  accounts={arm: _account(accounts[arm]) for arm in experiment.ARMS},
                  history=[], candidates=[], events=[], limitations=list(LIMITATIONS),
                  errors=_errors(source.get('errors')))
    for row in _array(source.get('history'))[-336:]:
        row = _object(row)
        result['history'].append(dict(time=_stamp(row.get('time'), True),
            **_numbers(row, ('baseline_equity', 'liquidation_filter_equity'))))
    for row in _array(source.get('candidates'))[:10]:
        row = _object(row)
        candidate = dict(symbol=_symbol(row.get('symbol')), score=_number(row.get('score')),
                         reasons=_reasons(row.get('reasons')),
                         liquidation_filter_reasons=_reasons(row.get('liquidation_filter_reasons')))
        for key in ('signal_eligible', 'add_signal_eligible', 'baseline_eligible', 'liquidation_filter_eligible'):
            candidate[key] = row.get(key) is True
        result['candidates'].append(candidate)
    cg = _object(source.get('coinglass'))
    result['coinglass'] = dict(fetched_at=_stamp(cg.get('fetched_at')), symbols={})
    for symbol, item in list(_object(cg.get('symbols')).items())[:10]:
        item = _object(item)
        result['coinglass']['symbols'][_symbol(symbol)] = dict(eligible=item.get('eligible') is True,
                                                             reason=_reason(item.get('reason')))
    for row in _array(source.get('events'))[-40:]:
        row = _object(row)
        if row.get('type') not in EVENTS or row.get('account') not in experiment.ARMS:
            continue
        event = dict(type=row['type'], account=row['account'], time=_stamp(row.get('time'), True),
                     **_numbers(row, ('net_pnl', 'fee', 'entry_fees', 'exit_fee', 'funding_model_cost')))
        if row['type'] == 'buy_rejected':
            event.update(reason=row.get('reason') if row.get('reason') in READABLE_BUY_REJECTION_REASONS else 'unknown',
                action=row.get('action') if row.get('action') in ('open', 'add') else 'unknown',
                stage=row.get('stage') if row.get('stage') in ('execution', 'selection', 'rotation_trial') else 'unknown',
                context=_numbers(_object(row.get('context')), ('lots unit_cost budget required_contracts ask_size cost fee cash existing_cost position_cap immediate_net weighted_drop_pct max_position_loss max_price_drop_pct expected_net target_net_profit score min_score elapsed_seconds cooldown_seconds positions max_positions').split()))
        if row.get('symbol') is not None:
            event['symbol'] = _symbol(row['symbol'])
        result['events'].append(event)
    config = _object(source.get('config'))
    result['config'] = _numbers(config, [k for k, v in engine.default_config().items()
                                        if isinstance(v, (float, int))])
    for key in ('tranches', 'add_drop_pct'):
        result['config'][key] = [_number(v) for v in _array(config.get(key))[:3]]
    return result


def _health(source, report, published_at):
    source = _object(source)
    result = dict(mode='paper', published_at=published_at,
                  worker_alive=source.get('worker_alive') if isinstance(source.get('worker_alive'), bool) else None,
                  experiment_status=_status(source.get('experiment_status', report['experiment']['status'])),
                  collection_error=bool(source.get('last_error') or source.get('collection_error')),
                  audit_error=bool(source.get('last_audit_error') or source.get('audit_error')),
                  errors=_errors(source.get('errors')))
    for key in ('last_tick_at', 'report_at', 'last_attempt_at', 'last_poll_at'):
        result[key] = _stamp(source.get(key, report['experiment'].get(key)))
    return result


def _identifier(value):
    if not isinstance(value, str) or not audits.ID.fullmatch(value):
        raise ValueError('unsafe audit identifier')
    datetime.strptime(value.split('-', 1)[1], '%Y%m%dT%H%M%SZ')
    return value


def _audit(source, identifier, published_at):
    source = _object(source)
    if source.get('id') != identifier or source.get('mode') != 'paper' or source.get('kind') not in audits.KINDS:
        raise ValueError('invalid paper audit')
    if not identifier.startswith(source['kind'] + '-'):
        raise ValueError('audit kind mismatch')
    window = _object(source.get('window'))
    start, end = _stamp(window.get('start_at'), True), _stamp(window.get('end_at'), True)
    if end <= start or audits._identifier(source['kind'], end) != identifier:
        raise ValueError('invalid audit window')
    result = dict(schema=1, id=identifier, kind=source['kind'], mode='paper', published_at=published_at,
                  generated_at=_stamp(source.get('generated_at'), True),
                  window=dict(start_at=start, end_at=end, duration_seconds=end-start,
                      boundary_convention=window.get('boundary_convention')
                      if window.get('boundary_convention') in ('[start, end)', '(start, end]') else 'unknown'), accounts={},
                  severity=source.get('severity') if source.get('severity') in ('critical', 'warning', 'info') else 'warning')
    for arm in experiment.ARMS:
        row = _object(_object(source.get('accounts')).get(arm))
        result['accounts'][arm] = _numbers(row, AUDIT_NUMBERS)
        result['accounts'][arm]['performance'] = public_trade_metrics.safe(row.get('performance'),_number,_symbol)
        for key in ('start_mark', 'end_mark'):
            mark = _object(row.get(key))
            result['accounts'][arm][key] = dict(time=_stamp(mark.get('time')), equity=_number(mark.get('equity')))
        result['accounts'][arm]['equity_sample_coverage'] = _coverage(row.get('equity_sample_coverage'))
        rejection = _object(row.get('buy_rejections'))
        safe = _numbers(rejection, ('recorded', 'instrumented_checks', 'legacy_checks'))
        safe['coverage'] = rejection.get('coverage') if rejection.get('coverage') in ('complete', 'partial', 'unavailable') else 'unavailable'
        safe['reasons'] = []
        for entry in _array(rejection.get('reasons'))[:40]:
            entry = _object(entry)
            safe['reasons'].append(dict(reason=entry.get('reason') if entry.get('reason') in READABLE_BUY_REJECTION_REASONS else 'unknown',
                action=entry.get('action') if entry.get('action') in ('open', 'add') else 'unknown',
                stage=entry.get('stage') if entry.get('stage') in ('execution', 'selection', 'rotation_trial') else 'unknown', count=_number(entry.get('count'))))
        result['accounts'][arm]['buy_rejections'] = safe
        sampling = _object(row.get('equity_sampling'))
        result['accounts'][arm]['equity_sampling'] = dict(**_numbers(sampling, ('tick_samples', 'estimated_tick_samples')),
            resolution=sampling.get('resolution') if sampling.get('resolution') in ('mixed', 'per_tick', 'published_only') else 'published_only')
    data = _object(source.get('data'))
    result['data'] = dict(cycle_coverage=_coverage(data.get('cycle_coverage')),
        **_numbers(data, ('observation_records', 'skipped_records', 'future_quote_observations')),
        errors=_errors(data.get('errors')))
    result['comparison'] = _numbers(_object(source.get('comparison')), ('filtered_minus_baseline_equity_change',))
    result['findings'] = []
    subjects = ('Coverage', 'Data collection', 'CoinGlass coverage', 'baseline', 'liquidation_filter', 'Evidence')
    for row in _array(source.get('findings'))[:20]:
        row = _object(row)
        if row.get('subject') in subjects and row.get('severity') in ('critical', 'warning', 'info'):
            result['findings'].append(dict(subject=row['subject'], severity=row['severity']))
    observed = result['data']['cycle_coverage']['observed']
    closed = sum(result['accounts'][a].get('closed_trades') or 0 for a in experiment.ARMS)
    result['summary'] = f"{source['kind']}: {observed if observed is not None else 'unknown'} observed cycles; {closed:g} completed paper trades."
    result['limitations'] = list(LIMITATIONS)
    return result


def _encoded(value):
    return (json.dumps(value, indent=2, allow_nan=False) + '\n').encode()


def _radar(source):
    directions = ('LONG', 'SHORT', 'TURNING-UP', 'TURNING-DOWN', 'NEUTRAL')
    numeric = ('price turnover_24h_usdt spread_pct snapshot_age_min funding_pct listing_age_days atr_1h_pct '
               'atr_4h_pct slope_4h_pct position_7d change_24h_pct low_7d high_7d range_low '
               'range_high step_pct grids expected_grids_per_hour rank_score grid_interval '
               'profit_pct_min profit_pct_max tick_size').split()
    result = dict(schema_version=1, generated_at_ms=_number(source.get('generated_at_ms')),
                  asof_ms=_number(source.get('asof_ms')), sections={})
    for section, rows in _object(source.get('sections')).items():
        if section not in ('majors', 'turning_up', 'turning_down', 'long', 'short', 'neutral', 'movers'):
            continue
        result['sections'][section] = []
        for item in _array(rows)[:8]:
            item = _object(item)
            if item.get('direction') not in directions:
                raise ValueError('invalid radar direction')
            row = dict(symbol=_symbol(item.get('symbol')), direction=item['direction'],
                       passes_liquidity=item.get('passes_liquidity') is True)
            row.update(_numbers(item, numeric))
            result['sections'][section].append(row)
    return result


def _audit_files(report):
    encoded = _encoded(report).decode()
    title = 'DansLabTrader — ' + report['kind'] + ' paper report'
    page = ('<!doctype html><html lang="en"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>' + title + '</title><body><a href="/">Paper dashboard</a><h1>' + title +
            '</h1><p>PAPER ONLY · Simulated results, not investment performance.</p><p>' +
            html.escape(report['summary']) + '</p><pre>' + html.escape(encoded) + '</pre></body></html>\n')
    markdown = '# ' + title + '\n\nPAPER ONLY.\n\n' + report['summary'] + '\n\n```json\n' + encoded + '```\n'
    return {'.json': encoded.encode(), '.html': page.encode(), '.md': markdown.encode()}


def _no_symlinks(path):
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError('symlink paths cannot be published')


def _read_json(path, max_bytes=32 * 1024 * 1024):
    _no_symlinks(path)
    if not path.is_file() or path.stat().st_size > max_bytes:
        raise ValueError('missing or oversized publication source')
    return json.loads(path.read_text())


def _write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    _no_symlinks(path)
    fd, temporary = tempfile.mkstemp(prefix='.publish-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def export_snapshot(runtime, output_dir, now=None, *, health=None,
                    radar_path=None, autopilot_path=None):
    """Write public data and sanitized archives into a caller-owned staging tree.

    ``health`` is an optional local-server response supplied by the publisher;
    absent worker status is unknown, never inferred from a successful file read.
    Caller deploys the staging tree only after this function completes. No runtime
    locks, report acknowledgement or provider access occurs during export.
    Missing default snapshots publish unavailable data. An explicitly supplied
    snapshot path must exist; invalid, oversized or symlink sources fail closed.
    """
    runtime, output = Path(runtime).absolute(), Path(output_dir).absolute()
    _no_symlinks(runtime)
    _no_symlinks(output)
    if output == runtime or runtime in output.parents or output in runtime.parents:
        raise ValueError('publication directory must be isolated from runtime')
    _read_json(runtime / experiment.FILE)  # Bound the source before experiment reads it.
    manifest = runtime / 'audits' / 'manifest.json'
    if manifest.exists() or manifest.is_symlink():
        _read_json(manifest)
    published_at = _stamp(time.time() if now is None else now, True)
    source = experiment.report(runtime, now=published_at)
    report = _report(source, published_at)
    public_health = _health(health if health is not None else source.get('health'), report, published_at)
    report['health'] = public_health
    try:
        learning = analytics.build(runtime, now=published_at)
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        # Missing archive evidence must not hide the last valid account report.
        learning = dict(schema=1, mode='paper', status='unavailable', generated_at=published_at,
                        message='Analytics evidence is unavailable. Portfolio reporting continues independently.')
    payloads = {'data/report.json': _encoded(report), 'data/health.json': _encoded(public_health),
                'data/analytics.json': _encoded(learning)}
    index = PUBLIC_INDEX
    _no_symlinks(index)
    if not index.is_file() or index.stat().st_size > 512 * 1024:
        raise ValueError('missing or oversized public dashboard')
    payloads['control/index.html'] = index.read_bytes()
    for name, page in (('paper', PAPER_PAGE), ('radar', RADAR_PAGE)):
        _no_symlinks(page)
        if not page.is_file() or page.stat().st_size > 512 * 1024:
            raise ValueError('missing or oversized public dashboard')
        payloads[name + '/index.html'] = page.read_bytes()
    payloads['index.html'] = payloads['paper/index.html']
    for name, explicit, clean in (('radar', radar_path, _radar),
                                   ('autopilot', autopilot_path, _autopilot)):
        path = (Path(explicit).absolute() if explicit is not None
                else runtime.parent / name / (name + '.json'))
        _no_symlinks(path)
        if explicit is not None or path.exists():
            source = _read_json(path, max_bytes=2 * 1024 * 1024)
            if not isinstance(source, dict):
                raise ValueError('invalid publication snapshot')
            snapshot = clean(source)
        else:
            snapshot = dict(schema_version=1, status='unavailable')
        if name == 'autopilot' and 'equity' in snapshot:
            try:
                recent = read_events(path.parent, max(0, int(published_at*1000)-30*86400000), limit=500, latest=True)
                snapshot['recent_events'] = _public_events(recent[-50:])
                snapshot['completed_grid_events'] = _public_events([e for e in recent if e['type'] == 'GRID'])
                snapshot['recent_events_available'] = True
            except (OSError, ValueError):
                snapshot['recent_events'] = []
                snapshot['recent_events_available'] = False
        snapshot['published_at_ms'] = int(published_at * 1000)
        payloads['data/' + name + '.json'] = _encoded(snapshot)
    rows = []
    for row in audits.list_reports(runtime)[:MAX_REPORTS]:
        identifier = _identifier(row.get('id'))
        audit = _audit(_read_json(runtime / 'audits' / (identifier + '.json')), identifier, published_at)
        archive = _audit_files(audit)
        for extension, payload in archive.items():
            payloads['reports/' + identifier + extension] = payload
        rows.append(dict(id=identifier, kind=audit['kind'], type=audit['kind'],
            start_at=audit['window']['start_at'], end_at=audit['window']['end_at'],
            generated_at=audit['generated_at'], severity=audit['severity'], summary=audit['summary'],
            url='/reports/' + identifier + '.html', html_url='/reports/' + identifier + '.html',
            json_url='/reports/' + identifier + '.json', markdown_url='/reports/' + identifier + '.md'))
    payloads['data/audits.json'] = _encoded(rows)
    size = sum(len(payload) for payload in payloads.values())
    if size > MAX_BYTES:
        raise ValueError('public snapshot size limit exceeded')
    # Refuse to deploy stale or unrelated assets from a reused staging directory.
    if output.exists() and any(output.iterdir()):
        raise ValueError('publication staging directory must be empty')
    for name, payload in payloads.items():
        _write(output / name, payload)
    return dict(mode='paper', published_at=published_at, file_count=len(payloads), total_bytes=size,
                report_count=len(rows), files={name: hashlib.sha256(data).hexdigest() for name, data in payloads.items()})
