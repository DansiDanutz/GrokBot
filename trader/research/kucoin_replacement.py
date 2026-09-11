"""Pure hourly switch decisions; monetary outputs are signed USDT cash effects."""
import math

HOUR_MS = 3_600_000


def _rate(row):
    value = row.get('realized_gph_6h')
    if value is None or not math.isfinite(value) or value < 0:
        return None
    return value


def _held(current, history, count, predicate):
    now = current['asof_ms']
    rows = {row['asof_ms']: row for row in history if row['asof_ms'] < now}
    rows[now] = current
    return all(now - i * HOUR_MS in rows and predicate(rows[now - i * HOUR_MS])
               for i in range(count))


def _triggers(current, history, minimum, count):
    result = []
    if current.get('stop_loss_hit') or current.get('stop_reason') == 'stop_loss':
        result.append('stop_loss')
    if current['price'] < current['low'] or current['price'] > current['high']:
        result.append('range_exit')
    if _held(current, history, count, lambda row: _rate(row) is not None
             and _rate(row) < minimum):
        result.append('low_grid_rate')
    if _held(current, history, count, lambda row: row.get('direction_flip') is True):
        result.append('direction_flip')
    return result


def _candidate(current, radar, triggers, options):
    running = set(current.get('running_pairs', ())) | {current['pair']}
    candidates = list(radar)
    flip = options.get('direction_flip_candidate')
    if flip and 'direction_flip' in triggers:
        if flip['pair'] == current['pair'] and flip.get('direction') != current['direction']:
            candidates = [flip] + candidates
    rate = _rate(current)
    if rate is None:
        return None
    for row in candidates:
        same_flip = row is flip and 'direction_flip' in triggers
        if row['pair'] in running and not same_flip:
            continue
        score = row.get('score', row.get('expected_gph'))
        if row.get('eligible', True) and score is not None and math.isfinite(score):
            if score > options.get('replacement_factor', 2) * rate:
                return row
    return None


def _switch_cost(current):
    floating, fee = current.get('floating_pnl'), current.get('close_fee')
    realized = current.get('status') in ('stopped', 'liquidated')
    if realized:
        floating = current.get('switch_close_gross_pnl', floating)
        fee = current.get('switch_close_fee_paid', fee)
    if floating is None or fee is None:
        return {'known': False, 'floating_pnl': floating, 'close_fees': fee,
                'net_realized_on_close': None, 'loss_cost': None}
    if not math.isfinite(floating) or not math.isfinite(fee) or fee < 0:
        raise ValueError('switch cost must contain finite PnL and nonnegative close fees')
    net = floating - fee
    return {'known': True, 'floating_pnl': floating, 'close_fees': fee,
            'already_realized': realized,
            'net_realized_on_close': net, 'loss_cost': max(0, -net)}


def decide_replacement(current, radar, hourly_history, parameters=None):
    """History contains prior hourly observations; gaps reset held signals."""
    options = parameters or {}
    count = options.get('consecutive_hours', 2)
    factor = options.get('replacement_factor', 2)
    if not isinstance(count, int) or count < 1 or not math.isfinite(factor) or factor <= 0:
        raise ValueError('positive consecutive_hours and replacement_factor required')
    rate_factor = options.get('minimum_rate_factor', .5)
    if not math.isfinite(rate_factor) or rate_factor <= 0:
        raise ValueError('minimum_rate_factor must be positive and finite')
    minimum = max(2, options.get('minimum_gph', current['expected_start_gph'] * rate_factor))
    if not math.isfinite(minimum):
        raise ValueError('minimum grid rate must be finite')
    triggers = _triggers(current, hourly_history, minimum, count)
    replacement = _candidate(current, radar, triggers, options) if triggers else None
    closed = current.get('status') in ('stopped', 'liquidated')
    action = 'replace' if replacement else ('waiting' if closed else 'keep')
    if not triggers:
        reason = 'hourly replacement conditions have not held'
    elif replacement:
        reason = ', '.join(triggers) + '; qualifying replacement found; refresh radar after switch'
    else:
        reason = ', '.join(triggers) + '; no qualifying replacement'
    return dict(action=action, reason=reason, triggers=triggers,
                replacement=replacement, minimum_gph=minimum,
                current_realized_gph=_rate(current), replacement_factor=factor,
                switch_cost=_switch_cost(current), refresh_radar=bool(replacement))
