"""Reproducible two-arm PAPER experiment; never submits exchange orders.

The account pair, observations, costs and publication snapshot commit in one
atomic file under the original CLI lock. Report reads never extend the run.
"""
import argparse
from copy import deepcopy
import json
import hashlib
import math
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from paper_grid import cli, engine, market

ARMS = ('baseline', 'liquidation_filter')
TICK_SECONDS = 300
REPORT_SECONDS = 1800
FILE = 'experiment.json'


class _DeadlineReached(Exception):
    pass
LIMITATIONS = [
    'PAPER ONLY: two independent simulations of the same starting account, not pooled capital.',
    'Periodic audits measure observed paper results; they do not establish future profitability.',
    'Polling fills use displayed full depth, modeled fees, adverse slippage and positive funding.',
    'No intratick fills, partial fills, queue simulation, maintenance margin or liquidation model.',
    'Stops can exceed their thresholds or be deferred when quotes or depth are unavailable.',
    'CoinGlass completed liquidations are historical cross-exchange context, not future clusters.',
    'Audit boundaries do not close positions or reset balances; open exposure stays in total equity.',
]


def _now(now):
    value = time.time() if now is None else now
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError('invalid experiment time')
    return value


def _code_hashes():
    directory = Path(__file__).resolve().parent
    return {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
            for name in ('engine.py', 'market.py', 'coinglass.py', 'experiment.py')}


def _load(runtime):
    doc = json.loads((runtime / FILE).read_text())
    expected = engine.default_config()
    if (doc.get('schema') != 1 or doc.get('mode') != 'paper'
            or doc.get('config') != expected
            or doc.get('config_hash') != cli.config_fingerprint(expected)
            or set(doc.get('accounts', {})) != set(ARMS)
            or doc.get('tick_seconds') != TICK_SECONDS
            or doc.get('report_seconds') != REPORT_SECONDS
            or doc.get('status') not in ('running', 'completed', 'frozen')):
        raise ValueError('experiment schema/config mismatch; preserve and review state')
    if not isinstance(doc.get('start_at'), (int, float)) or isinstance(doc.get('start_at'), bool):
        raise ValueError('invalid experiment start')
    start_at = _now(doc['start_at'])
    if doc.get('continuous') is True:
        if doc.get('end_at') is not None:
            raise ValueError('continuous experiment cannot have a deadline')
    else:
        if not isinstance(doc.get('end_at'), (int, float)) or isinstance(doc.get('end_at'), bool):
            raise ValueError('invalid experiment end')
        end_at = _now(doc.get('end_at'))
        if not 0 < end_at - start_at <= 48 * 3600:
            raise ValueError('invalid experiment timeframe')
    for account in doc['accounts'].values():
        if account.get('config') != expected or account.get('config_hash') != doc['config_hash']:
            raise ValueError('account config mismatch')
        engine._validate_state(account['state'], expected)
    json.dumps(doc, allow_nan=False)
    for key in ('history', 'events', 'errors', 'observations'):
        if not isinstance(doc.get(key), list):
            raise ValueError('invalid experiment ' + key)
    if not isinstance(doc.get('published'), dict):
        raise ValueError('missing published report')
    return doc


def _metrics(account, at):
    result = cli.summary(account, at)
    stats = account['statistics']
    for symbol, position in result['positions'].items():
        quote = account.get('market', {}).get(symbol)
        fresh = engine._quote(quote, at, account['config'])
        record = quote if fresh else dict(bid=position['last_bid'])
        position['unrealized_net_pnl'] = engine._net(position, record, account['config'])
        position['equity_is_estimate'] = not fresh or not engine._full_depth(quote, 'bid', position['contracts'])
        position['valuation_bid'] = record['bid']
        position['quote_time'] = quote.get('quote_time') if isinstance(quote, dict) else None
    funding = stats['closed_funding'] + sum(p['funding_accrued'] for p in account['state']['positions'].values())
    result.update(trade_count=stats['trade_count'], total_fees=stats['total_fees'],
                  funding_cost=funding, max_drawdown=stats['max_drawdown'],
                  max_drawdown_pct=stats['max_drawdown_pct'],
                  initial_equity=stats['initial_equity'],
                  experiment_pnl=result['equity'] - stats['initial_equity'],
                  fills=stats['fills'],
                  statistics_scope='Trade and cost totals include available inherited account history; drawdown and experiment PnL begin at experiment start.')
    return result


def _publish(doc, at, force=False):
    if not force and at - doc.get('report_at', doc['start_at']) < REPORT_SECONDS:
        return
    results = {name: _metrics(doc['accounts'][name], at) for name in ARMS}
    if doc['status'] != 'running':
        for result in results.values():
            # A held position is never represented as realized cash at the deadline.
            result['equity_is_estimate'] = bool(result['positions']) or result['equity_is_estimate']
    point = dict(time=at, baseline_equity=results['baseline']['equity'],
                 liquidation_filter_equity=results['liquidation_filter']['equity'])
    if not doc['history'] or doc['history'][-1]['time'] != at:
        doc['history'].append(point)
    doc['report_at'] = at
    baseline = results['baseline']
    filtered = {r['symbol']: r for r in results['liquidation_filter']['candidate_analysis']}
    candidates = []
    for row in baseline['candidate_analysis']:
        candidate = deepcopy(row)
        candidate['baseline_eligible'] = row['signal_eligible']
        candidate['liquidation_filter_eligible'] = filtered.get(row['symbol'], {}).get('signal_eligible', False)
        candidate['liquidation_filter_reasons'] = filtered.get(row['symbol'], {}).get('reasons', [])
        candidates.append(candidate)
    doc['published'] = dict(
        experiment={k: doc.get(k) for k in ('status', 'start_at', 'end_at', 'continuous', 'last_tick_at', 'report_at', 'mode', 'frozen_at', 'freeze_reason')},
        accounts=results, history=deepcopy(doc['history']), candidates=candidates,
        coinglass=deepcopy(doc.get('coinglass', {})), events=deepcopy(doc['events'][-100:]),
        config=deepcopy(doc['config']), errors=deepcopy(doc['errors'][-30:]),
        limitations=LIMITATIONS[:])
    doc['published']['experiment'].update(tick_seconds=TICK_SECONDS, report_seconds=REPORT_SECONDS)


def _response(doc, at):
    result = deepcopy(doc['published'])
    continuous = doc.get('continuous') is True
    result['experiment']['remaining_seconds'] = None if continuous else (max(0, doc['end_at'] - at) if doc['status'] == 'running' else 0)
    result['experiment']['next_audit_at'] = doc['start_at'] + (max(0, math.floor((at-doc['start_at'])/(48*3600)))+1)*(48*3600)
    # Health is live metadata; portfolio numbers deliberately retain report_at.
    result['health'] = dict(last_tick_at=doc.get('last_tick_at'), last_attempt_at=doc.get('last_attempt_at'),
                            status=doc['status'], errors=deepcopy(doc['errors'][-5:]),
                            overdue=doc['status'] == 'running' and _deadline(doc, at))
    return result


def _freeze(doc, at, reason):
    doc.update(status='completed' if reason == 'deadline' else 'frozen',
               frozen_at=at, freeze_reason=reason)
    _publish(doc, at, force=True)


def _deadline(doc, at):
    return doc.get('continuous') is not True and at >= doc['end_at']


def continue_running(runtime=cli.DEFAULT_RUNTIME, now=None):
    """Explicitly migrate the existing account pair; never reset or unfreeze a safety halt."""
    runtime = Path(runtime)
    with cli.locked(runtime):
        doc = _load(runtime)
        at = _now(now)
        if doc['status'] not in ('running', 'completed') or (doc['status'] == 'completed' and doc.get('freeze_reason') != 'deadline'):
            raise ValueError('manual or integrity freeze requires separate review')
        if doc.get('continuous') is True:
            return _response(doc, at)
        doc.update(continuous=True, original_end_at=doc['end_at'], end_at=None,
                   continued_at=at, status='running', frozen_at=None, freeze_reason=None,
                   code_hashes=_code_hashes())
        _publish(doc, at, force=True)
        cli.atomic_json(runtime / FILE, doc)
        return _response(doc, at)


def start(runtime=cli.DEFAULT_RUNTIME, hours=48, now=None):
    runtime = Path(runtime)
    at = _now(now)
    if isinstance(hours, bool) or not isinstance(hours, (int, float)) or not math.isfinite(hours) or not 0 < hours <= 48:
        raise ValueError('duration must be positive and no more than 48 hours')
    with cli.locked(runtime):
        if (runtime / FILE).exists():
            raise ValueError('experiment already exists; it cannot be reset or overwritten')
        config = engine.default_config()
        source = cli.load(runtime, config)
        if source is None:
            raise ValueError('existing paper account required; no automatic balance creation')
        if source.get('config') != config:
            raise ValueError('source account config mismatch')
        engine._validate_state(source['state'], config)
        if source['state'].get('last_run') is not None and at < source['state']['last_run']:
            raise ValueError('experiment start predates source account')
        doc = dict(schema=1, mode='paper', status='running', config=config,
                   config_hash=cli.config_fingerprint(config), code_hashes=_code_hashes(), start_at=at, end_at=at+hours*3600,
                   tick_seconds=TICK_SECONDS, report_seconds=REPORT_SECONDS,
                   last_tick_at=None, last_attempt_at=None, report_at=at,
                   accounts={}, history=[], events=[], errors=[], observations=[], coinglass={})
        for name in ARMS:
            account = deepcopy(source)
            account.update(paused=False, current_events=[])
            equity = cli.summary(account, at)['equity']
            past = account.get('events', [])
            account['statistics'] = dict(initial_equity=equity, peak_equity=equity,
                max_drawdown=0.0, max_drawdown_pct=0.0,
                trade_count=sum(e.get('type') == 'close' for e in past),
                total_fees=sum(e.get('fee', 0) + e.get('exit_fee', 0) for e in past),
                closed_funding=sum(e.get('funding_model_cost', 0) for e in past),
                fills=sum(e.get('type') in ('open', 'add', 'close') for e in past))
            doc['accounts'][name] = account
        _publish(doc, at, force=True)
        cli.atomic_json(runtime / FILE, doc)
        return _response(doc, at)


def _advance(account, quotes, scan, at, config):
    state, events = engine.step(account['state'], quotes, at, config)
    result = deepcopy(account)
    result.update(state=state, market=deepcopy(quotes), scan=deepcopy(scan), checked_at=at, current_events=events)
    result['events'] = result.get('events', []) + events
    if any(engine._quote(r, at, config) for r in quotes.values()):
        result['last_success_at'] = at
    stats = result['statistics']
    stats['trade_count'] += sum(e['type'] == 'close' for e in events)
    stats['total_fees'] += sum(e.get('fee', 0) + e.get('exit_fee', 0) for e in events)
    stats['closed_funding'] += sum(e.get('funding_model_cost', 0) for e in events)
    stats['fills'] += sum(e['type'] in ('open', 'add', 'close') for e in events)
    equity = engine.status(state, quotes, at, config)['equity']
    stats['peak_equity'] = max(stats['peak_equity'], equity)
    drawdown = stats['peak_equity'] - equity
    stats['max_drawdown'] = max(stats['max_drawdown'], drawdown)
    stats['max_drawdown_pct'] = max(stats['max_drawdown_pct'], 100*drawdown/stats['peak_equity'] if stats['peak_equity'] > 0 else 0)
    return result, events


def tick(runtime=cli.DEFAULT_RUNTIME, now=None, collector=None, feature_collector=None):
    runtime = Path(runtime)
    with cli.locked(runtime):
        doc = _load(runtime)
        at = _now(now)
        if doc['status'] != 'running' or at < doc['start_at']:
            return _response(doc, at)
        if _deadline(doc, at):
            _freeze(doc, at, 'deadline')
            cli.atomic_json(runtime / FILE, doc)
            return _response(doc, at)
        if doc.get('code_hashes') != _code_hashes():
            doc['errors'].append(dict(time=at, type='code_changed', reason='algorithm files changed; both paper accounts frozen for review'))
            _freeze(doc, at, 'code_changed')
            cli.atomic_json(runtime / FILE, doc)
            return _response(doc, at)
        if doc['last_attempt_at'] is not None and at - doc['last_attempt_at'] < TICK_SECONDS:
            return _response(doc, at)
        previous = deepcopy(doc)
        doc['last_attempt_at'] = at
        quotes, scan, features = {}, {}, {}
        try:
            from paper_grid import coinglass
            previous_scan = doc['accounts']['baseline'].get('scan', {})
            held = sorted(set().union(*(set(a['state']['positions']) for a in doc['accounts'].values())))
            symbols = sorted(set(previous_scan.get('top5', previous_scan.get('top_five', []))) | set(held))
            cached = doc.get('coinglass', {})
            fetched = cached.get('fetched_at')
            if isinstance(fetched, (int, float)) and 0 <= at - fetched < REPORT_SECONDS:
                features = deepcopy(cached)
            else:
                try:
                    features = (feature_collector or coinglass.collect)(symbols, at, cache=None)
                except Exception as error:
                    features = dict(fetched_at=at, symbols={}, errors=[dict(type=type(error).__name__, reason='CoinGlass collection failed')])
            # A long feature request must not start another API request beyond the deadline.
            market_at = _now(now)
            if _deadline(doc, market_at):
                _freeze(doc, market_at, 'deadline')
                cli.atomic_json(runtime / FILE, doc)
                return _response(doc, market_at)
            quotes, scan = (collector or market.collect)(previous_scan, held, market_at, force_scan=False)
            decision_at = _now(now)
            if _deadline(doc, decision_at):
                # Preserve the unprocessed input for audit, never create deadline fills.
                doc['observations'].append(dict(time=decision_at, skipped='deadline', market=quotes, scan=scan, coinglass=features))
                _freeze(doc, decision_at, 'deadline')
                cli.atomic_json(runtime / FILE, doc)
                return _response(doc, decision_at)
            # Daily discovery can introduce names absent from the prior feature
            # cache. Fetch only those names, then refresh the SAME market for
            # both arms so slow feature requests cannot age their fill quotes.
            uncovered = sorted((set(quotes) | set(scan.get('top5', [])) | set(held))
                               - set(features.get('symbols', {})))
            if uncovered:
                try:
                    extra = (feature_collector or coinglass.collect)(uncovered, decision_at, cache=features)
                except Exception as error:
                    extra = dict(fetched_at=decision_at, symbols={}, errors=[dict(type=type(error).__name__, reason='CoinGlass new-symbol collection failed')])
                old_has_symbols = bool(features.get('symbols'))
                combined = deepcopy(features)
                combined.setdefault('symbols', {}).update(extra.get('symbols', {}))
                combined.setdefault('errors', []).extend(extra.get('errors', []))
                # Never extend old entries' TTL when adding newly discovered names.
                combined['fetched_at'] = min(features['fetched_at'], extra['fetched_at']) if old_has_symbols else extra['fetched_at']
                features = combined
                refresh_at = _now(now)
                if _deadline(doc, refresh_at):
                    raise _DeadlineReached()
                quotes, scan = (collector or market.collect)(scan, held, refresh_at, force_scan=False)
                decision_at = _now(now)
                if _deadline(doc, decision_at):
                    raise _DeadlineReached()
            filtered = coinglass.apply_filter(quotes, features, decision_at)
            next_accounts, events = {}, []
            for name, inputs in (('baseline', quotes), ('liquidation_filter', filtered)):
                if _deadline(doc, _now(now)):
                    raise _DeadlineReached()
                next_accounts[name], arm_events = _advance(doc['accounts'][name], inputs, scan, decision_at, doc['config'])
                events.extend(dict(event, account=name) for event in arm_events)
            if _deadline(doc, _now(now)):
                raise _DeadlineReached()
            doc.update(accounts=next_accounts, coinglass=features, last_tick_at=decision_at)
            doc['events'].extend(events)
            doc['observations'].append(dict(time=decision_at, market=quotes, scan=scan, coinglass=features, events=events))
            if scan.get('errors'):
                doc['errors'].append(dict(time=decision_at, type='market_data', details=scan['errors']))
            if features.get('errors'):
                doc['errors'].append(dict(time=decision_at, type='coinglass_data', details=features['errors']))
            _publish(doc, decision_at, force=previous.get('last_tick_at') is None)
        except _DeadlineReached:
            doc = previous
            doc['last_attempt_at'] = at
            final_at = _now(now)
            doc['observations'].append(dict(time=final_at, skipped='deadline', market=quotes, scan=scan, coinglass=features))
            _freeze(doc, final_at, 'deadline')
        except Exception as error:
            # Neither arm settles when either arm fails. Do not leak exception text/keys.
            doc = previous
            doc['last_attempt_at'] = at
            doc['errors'].append(dict(time=at, type=type(error).__name__, reason='cycle aborted; both account states preserved'))
            doc['observations'].append(dict(time=at, skipped='cycle_error', market=quotes, scan=scan, coinglass=features))
            _publish(doc, at, force=True)
        if doc.get('continuous') is True:
            from paper_grid.retention import archive_history
            doc = archive_history(runtime, doc, _now(now))
        cli.atomic_json(runtime / FILE, doc)
        return _response(doc, _now(now))


def report(runtime=cli.DEFAULT_RUNTIME, now=None):
    """Read the last published portfolio snapshot without fetching or mutating."""
    return _response(_load(Path(runtime)), _now(now))


def freeze(runtime=cli.DEFAULT_RUNTIME, now=None):
    runtime = Path(runtime)
    with cli.locked(runtime):
        doc = _load(runtime)
        at = _now(now)
        if doc['status'] == 'running':
            _freeze(doc, at, 'manual')
            cli.atomic_json(runtime / FILE, doc)
        return _response(doc, at)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['start', 'tick', 'report', 'freeze', 'continue-running'])
    parser.add_argument('--paper', action='store_true')
    parser.add_argument('--hours', type=float, default=48)
    parser.add_argument('--runtime', type=Path, default=cli.DEFAULT_RUNTIME)
    args = parser.parse_args(argv)
    if args.command != 'report' and not args.paper:
        parser.error('state changes require --paper; live mode does not exist')
    try:
        result = start(args.runtime, args.hours) if args.command == 'start' else globals()[args.command.replace('-', '_')](args.runtime)
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0
    except Exception as error:
        print(json.dumps(dict(mode='paper', error_type=type(error).__name__, error='operation failed; existing accounts preserved')), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
