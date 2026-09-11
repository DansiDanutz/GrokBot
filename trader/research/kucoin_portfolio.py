"""Replay accounting and preregistered walk-forward gates, without live IO."""
from datetime import datetime, timezone
import itertools
import json
from pathlib import Path
import subprocess

from trader.strategies.kucoin_grid import FEE, floating_pnl, net_equity

ROOT = Path(__file__).resolve().parents[2]
HOUR_MS = 3600000
DAY_MS = 24 * HOUR_MS
METRICS = ('completed_grids', 'completed_grids_per_hour', 'completed_grids_per_day',
    'completed_grids_per_hour_at_net_1_usdt', 'completed_at_target', 'completed_below_target',
    'grid_profit', 'grid_net_profit', 'seed_pnl', 'floating_pnl', 'realized_switch_pnl',
    'realized_close_pnl', 'funding', 'fees', 'net', 'max_drawdown', 'max_drawdown_fraction_of_margin',
    'max_unrealized_loss', 'max_unrealized_loss_conservative_bound',
    'max_drawdown_conservative_bound', 'max_drawdown_conservative_fraction_of_margin', 'closest_liquidation_range_pct', 'stop_loss_hits',
    'switches_per_day', 'time_outside_range_hours', 'liquidations')


def _net_completion(event):
    entry = event['price'] + event['gross_pnl'] / (event['side'] * event['quantity'])
    return event['gross_pnl'] - event['quantity'] * (entry+event['price']) * FEE


def _drawdown(report, bots):
    ids = {bot['bot_id'] for bot in bots}
    marks = sorted((row for row in report['equity_timeline'] if row['bot_id'] in ids),
                   key=lambda row: row['timestamp_ms'])
    capital = report['initial_capital']
    values, peak, maximum = {}, capital, 0.
    for row in marks:
        values[row['bot_id']] = row['equity']-row['margin']
        value = capital + sum(values.values())
        peak = max(peak, value)
        maximum = max(maximum, peak-value)
    return maximum


def _floating_loss(report, bots):
    ids = {bot['bot_id'] for bot in bots}
    marks = sorted((row for row in report['equity_timeline'] if row['bot_id'] in ids),
                   key=lambda row: row['timestamp_ms'])
    current, maximum = {}, 0.
    for row in marks:
        current[row['bot_id']] = row.get('floating_pnl', 0.)
        maximum = max(maximum, -sum(current.values()))
    return maximum


def _risk_bound(report, bots, floating=False):
    ids = {bot['bot_id'] for bot in bots}
    marks = sorted((row for row in report['equity_timeline'] if row['bot_id'] in ids),
                   key=lambda row: row['timestamp_ms'])
    current, peak, maximum = {}, 0., 0.
    for timestamp, rows in itertools.groupby(marks, key=lambda row: row['timestamp_ms']):
        changes = {}
        for row in rows:
            identifier = row['bot_id']
            value = row['floating_pnl'] if floating else row['equity']-row['margin']
            changes.setdefault(identifier, [current.get(identifier, 0.)]).append(value)
        low = sum(current.values()) + sum(min(values)-current.get(key, 0.) for key, values in changes.items())
        high = sum(current.values()) + sum(max(values)-current.get(key, 0.) for key, values in changes.items())
        peak = max(peak, high)
        maximum = max(maximum, -low if floating else peak-low)
        current.update({key: values[-1] for key, values in changes.items()})
    return maximum


def _metrics(report, bots):
    states = [bot['state'] for bot in bots]
    ids = {bot['bot_id'] for bot in bots}
    fills = [row for row in report['ledger'] if row['bot_id'] in ids and row.get('completed_grid')]
    target = sum(_net_completion(row) >= 1-1e-9 for row in fills)
    hours = (report['end_ms']-report['start_ms'])/HOUR_MS
    closest = [bot['closest'] for bot in bots if bot['closest'] is not None]
    switches = sum(row['bot_id'] in ids and row.get('replacement_executed', False) for row in report['switches'])
    drawdown = _drawdown(report, bots)
    return dict(completed_grids=len(fills), completed_grids_per_hour=len(fills)/hours,
        completed_grids_per_day=len(fills)/hours*24,
        completed_grids_per_hour_at_net_1_usdt=target/hours,
        completed_at_target=target, completed_below_target=len(fills)-target,
        grid_profit=sum(s.grid_profit for s in states), grid_net_profit=sum(s.grid_net_profit for s in states),
        seed_pnl=sum(s.seed_pnl for s in states), floating_pnl=sum(floating_pnl(s, s.price) for s in states),
        realized_switch_pnl=sum(row['gross_pnl'] for row in report['ledger']
            if row['bot_id'] in ids and row['kind'] == 'replacement'),
        realized_close_pnl=sum(s.close_pnl for s in states), funding=sum(s.funding for s in states),
        fees=sum(s.fees for s in states), net=sum(net_equity(s, s.price)-s.config.total_margin for s in states),
        max_drawdown=drawdown, max_drawdown_fraction_of_margin=drawdown/report['initial_capital'],
        max_unrealized_loss=_floating_loss(report, bots),
        max_unrealized_loss_conservative_bound=_risk_bound(report, bots, True),
        max_drawdown_conservative_bound=_risk_bound(report, bots),
        max_drawdown_conservative_fraction_of_margin=_risk_bound(report, bots)/report['initial_capital'],
        closest_liquidation_range_pct=min(closest) if closest else None,
        stop_loss_hits=sum(s.stop_reason == 'stop_loss' for s in states), switches_per_day=switches/hours*24,
        time_outside_range_hours=sum(bot['outside_ms'] for bot in bots)/HOUR_MS,
        liquidations=sum(s.liquidated for s in states))


def _bot_record(bot):
    state = bot['state']
    return dict(bot_id=bot['bot_id'], pair=state.config.pair, direction=state.config.direction,
        start_ms=bot['start_ms'], end_ms=state.timestamp_ms, form=bot['form'],
        status=state.status, stop_reason=state.stop_reason, total_margin=state.config.total_margin,
        expected_start_gph=bot['expected'], completed_grids=state.completed_grids,
        grid_profit=state.grid_profit, grid_net_profit=state.grid_net_profit,
        funding=state.funding, fees=state.fees, close_pnl=state.close_pnl,
        net=net_equity(state, state.price)-state.config.total_margin,
        exposure_hours=bot['exposure_hours'], liquidated=state.liquidated)


def summarize(report):
    """Unknown coverage invalidates performance, while retaining observed diagnostics."""
    bots = report['bots']
    measured = _metrics(report, bots)
    report['partial_metrics'] = measured if bots else None
    report['metrics'] = measured if report['coverage']['complete'] else dict.fromkeys(METRICS)
    report['status'] = ('failed_liquidation' if measured['liquidations'] else
                        'complete' if report['coverage']['complete'] else 'partial_coverage')
    for pair in sorted({bot['state'].config.pair for bot in bots}):
        selected = [bot for bot in bots if bot['state'].config.pair == pair]
        report['per_coin'][pair] = _metrics(report, selected) if report['coverage']['complete'] else dict.fromkeys(METRICS)
    for direction in ('long', 'short', 'neutral'):
        selected = [bot for bot in bots if bot['state'].config.direction == direction]
        hours = sum(bot['exposure_hours'] for bot in selected)
        values = _metrics(report, selected)
        report['direction_mix'][direction] = dict(bot_count=len(selected), exposure_hours=hours,
            grids_per_hour=values['completed_grids']/hours if hours else None,
            net=values['net'] if report['coverage']['complete'] else None)
    report['bots'] = [_bot_record(bot) for bot in bots]
    return report


def _registered(registration):
    """Require byte-identical preregistration in this module's repository HEAD."""
    path = Path(registration).resolve()
    try:
        relative = path.relative_to(ROOT)
        current = path.read_bytes()
        committed = subprocess.run(['git', '-C', str(ROOT), 'show', 'HEAD:'+relative.as_posix()],
            check=True, capture_output=True).stdout
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        raise ValueError('preregistration must already be committed in repository HEAD') from error
    if current != committed:
        raise ValueError('preregistration differs from committed HEAD; sweep forbidden')
    document = json.loads(current)
    if document.get('id') not in ('grid-kucoin', 'grid-kucoin-policy-v2') or not document.get('sweep'):
        raise ValueError('invalid grid-kucoin preregistration')
    return document


def _time(value):
    return int(datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()*1000)


def _combinations(registration):
    names = sorted(registration['sweep'])
    return [dict(zip(names, values)) for values in itertools.product(
            *(registration['sweep'][name] for name in names))]


def _accepted(result):
    metrics = result.get('metrics', {})
    return (result.get('coverage', {}).get('complete') is True
        and metrics.get('liquidations') == 0
        and metrics.get('completed_grids_per_hour_at_net_1_usdt') is not None
        and metrics.get('net') is not None)


def _skip(start, end, reason):
    return dict(start_ms=start, end_ms=end, status='not_run_coverage',
                coverage=dict(complete=False, reasons=[reason]), metrics=dict.fromkeys(METRICS))


def _month_valid(window, metrics, margin_threshold):
    reasons = []
    if window.get('coverage', {}).get('complete') is not True:
        reasons.append('incomplete historical coverage')
    if metrics.get('net') is None or metrics['net'] <= 0:
        reasons.append('monthly net is unknown or not positive')
    below_target = metrics.get('completed_below_target')
    if type(below_target) is not int or below_target != 0:
        reasons.append('completed grids below +1 USDT are unknown or nonzero')
    qualified = metrics.get('completed_at_target')
    if type(qualified) is not int or qualified <= 0:
        reasons.append('completed grids at +1 USDT are unknown or not positive')
    if metrics.get('liquidations') != 0:
        reasons.append('liquidations are unknown or nonzero')
    closest = metrics.get('closest_liquidation_range_pct')
    if closest is None or closest < 10:
        reasons.append('closest liquidation approach is unknown or below 10 percent of range')
    drawdown = metrics.get('max_drawdown_conservative_fraction_of_margin',
                           metrics.get('max_drawdown_fraction_of_margin'))
    if drawdown is None or drawdown >= margin_threshold:
        reasons.append('drawdown is unknown or at least 15 percent of margin')
    return reasons


def _full_months(windows):
    previous = None
    for window in windows:
        try:
            start = datetime.fromtimestamp(window['start_ms']/1000, timezone.utc)
            end = datetime.fromtimestamp(window['end_ms']/1000, timezone.utc)
        except (KeyError, TypeError, ValueError):
            return False
        following = datetime(start.year+(start.month == 12), start.month%12+1, 1, tzinfo=timezone.utc)
        if start.day != 1 or any((start.hour, start.minute, start.second, start.microsecond)) or end != following:
            return False
        if previous is not None and start < previous:
            return False
        previous = end
    return len(windows) == 2


def decision(windows, calibration, volatility, registration=None):
    """All conditions are required; profitable simulations never waive parity."""
    reasons = []
    if calibration.get('calibrated') is not True:
        reasons.append('four-bot predictive calibration has not passed')
    if calibration.get('neutral_calibrated') is not True:
        reasons.append('neutral predictive calibration has not passed')
    if volatility.get('validated') is not True:
        reasons.append('KuCoin volatility definition and fixture validation have not passed')
    if not _full_months(windows):
        reasons.append('two disjoint complete months are required')
    for index, window in enumerate(windows):
        reasons.extend('month '+str(index+1)+': '+reason for reason in
                       _month_valid(window, window.get('metrics', {}), .15))
    return dict(decision='shelve' if reasons else 'eligible_for_separate_hourly_recommender_review',
                reasons=reasons, live_orders_authorized=False)


def _train(snapshot, registration, holdout, runner, trials):
    from trader.research.kucoin_replay import window_coverage
    start, end = holdout['start_ms']-registration['training_days']*DAY_MS, holdout['start_ms']
    available = (window_coverage(snapshot, start, end)['complete'] and
                 window_coverage(snapshot, holdout['start_ms'], holdout['end_ms'])['complete'])
    accepted = []
    for parameters in _combinations(registration):
        result = runner(snapshot, start, end, parameters=parameters) if available else _skip(
            start, end, 'preceding seven-day training window lacks historical ticker/book coverage')
        row = dict(holdout_start_ms=holdout['start_ms'], parameters=parameters,
                   status=result.get('status', 'complete'), coverage=result['coverage'], metrics=result['metrics'])
        trials.append(row)
        if _accepted(result):
            accepted.append(row)
    accepted.sort(key=lambda row: (-row['metrics']['completed_grids_per_hour_at_net_1_usdt'],
        -row['metrics']['net'], json.dumps(row['parameters'], sort_keys=True)))
    return accepted[0]['parameters'] if accepted else None


def _holdout(snapshot, registration, span, parameters, runner, fixtures):
    from trader.research.kucoin_replay import window_coverage
    start, end = span['start_ms'], span['end_ms']
    if parameters is None or not window_coverage(snapshot, start, end)['complete']:
        result = _skip(start, end, 'no fully covered training selection or holdout quote history')
        result['baselines'] = {mode: _skip(start, end, 'historical coverage unavailable')
                               for mode in registration.get('baselines', [])}
        return result
    result = runner(snapshot, start, end, parameters=parameters)
    result.update(start_ms=start, end_ms=end, selected_parameters=parameters)
    result['baselines'] = {}
    for mode in registration.get('baselines', []):
        if mode != 'random_radar_identical_rules' and fixtures is None:
            result['baselines'][mode] = _skip(start, end, 'four observed forms not supplied')
        else:
            result['baselines'][mode] = runner(snapshot, start, end, parameters=parameters,
                mode=mode, fixtures=fixtures, seed=registration.get('random_seed', 20260911))
    return result


def sweep(snapshot, registration, calibration, volatility, runner=None, fixtures=None):
    """Train on prior seven days, freeze choices, then evaluate untouched months."""
    document = _registered(registration)
    if runner is None and document['id'] != 'grid-kucoin-policy-v2':
        raise ValueError('archived registration cannot authorize a sweep of the revised portfolio policy')
    if runner is None:
        from trader.research.kucoin_replay import run_window
        runner = run_window
    spans = [dict(start_ms=_time(span['start']), end_ms=_time(span['end']))
             for span in document['holdouts']]
    if len(spans) != 2 or spans[0]['end_ms'] > spans[1]['start_ms']:
        raise ValueError('two chronologically disjoint holdouts required')
    report = dict(registration=document, training_trials=[], holdouts=[],
        calibration=calibration, volatility=volatility, mode='offline_preregistered_model')
    for span in spans:
        selected = _train(snapshot, document, span, runner, report['training_trials'])
        report['holdouts'].append(_holdout(snapshot, document, span, selected, runner, fixtures))
    report['decision'] = decision(report['holdouts'], calibration, volatility, document)
    return report
