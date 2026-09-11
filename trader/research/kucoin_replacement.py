"""Pure hourly switch decisions; monetary outputs are signed USDT cash effects."""
import math
from trader.strategies.grid_setup import minimum_grid_net_usdt

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


def _finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _cash_requirement(form, parameters):
    declared = [minimum_grid_net_usdt({'minimum_grid_net_usdt': form[key]})
                for key in ('minimum_grid_net_usdt', 'target_profit_per_grid') if key in form]
    if parameters is not None and 'minimum_grid_net_usdt' in parameters:
        declared.append(minimum_grid_net_usdt(parameters))
    if declared and any(value != declared[0] for value in declared):
        raise ValueError('contradictory minimum_grid_net_usdt requirements')
    return declared[0] if declared else 0.


def candidate_economics(candidate, horizon_hours=6, parameters=None):
    """Transparent forecast using the crossing proxy, net floor and entry budget."""
    form = candidate.get('setup', {})
    score = candidate.get('score', candidate.get('expected_gph'))
    floor = form.get('preview', {}).get('profit_per_grid_min')
    fee = form.get('opening_fee_budget')
    if fee is None and all(key in form for key in ('quantity', 'grids', 'entry')):
        fee = form['quantity'] * form['grids'] * form['entry'] * .0006
    try:
        minimum, reason = _cash_requirement(form, parameters), None
    except ValueError as error:
        minimum, reason = None, str(error)
    if reason is not None:
        pass
    elif candidate.get('eligible', True) is not True or form.get('eligible') is not True:
        reason = 'setup is not eligible and safe'
    elif (form.get('used_margin'), form.get('reserved_margin'), form.get('leverage')) != (1000, 200, 5):
        reason = 'setup must use 1000 margin plus 200 reserve at exactly 5x'
    elif not _finite_number(floor) or floor <= 0 or floor < minimum:
        reason = 'actual fee-net grid profit must be known, strictly positive and meet the declared cash floor'
    elif not _finite_number(score) or score <= 0 or not _finite_number(fee) or fee < 0:
        reason = 'positive crossing rate and nonnegative entry fee budget must be known'
    gross = score*floor*horizon_hours if reason is None else None
    return dict(eligible=reason is None, reason=reason, horizon_hours=horizon_hours,
                expected_gph=score, net_profit_floor=floor, minimum_grid_net_usdt=minimum, opening_fee_budget=fee,
                projected_grid_income=gross, projected_net_income=None if gross is None else gross-fee,
                forecast_basis='crossing proxy times per-grid net floor; not observed completed grids')


def _current_economics(row, horizon):
    rate, cost = _rate(row), _switch_cost(row)
    completed, grid_net = row.get('completed_grids', 0), row.get('grid_net_profit', 0)
    observed = _finite_number(completed) and completed > 0 and _finite_number(grid_net)
    profit = grid_net/completed if observed else row.get('actual_net_usdt_per_grid') if completed == 0 else None
    if rate is None or not cost['known'] or not _finite_number(profit):
        return None
    income = rate*profit*horizon
    return dict(current_realized_gph=rate, current_net_per_grid=profit,
                current_projected_income=income, switch_cost=cost,
                worst_score=income+cost['net_realized_on_close'],
                profit_basis='observed mean grid net' if observed else 'supplied actual setup cash estimate; no completed history')


def _emergencies(currents):
    rows = []
    for current in currents:
        distance = current.get('distance_liquidation_pct')
        if current.get('status') == 'liquidated' or (_finite_number(distance) and distance <= 5):
            rows.append(dict(bot_id=current['bot_id'], pair=current['pair'],
                distance_liquidation_pct=distance, action='verify tightened 1% range protection or stop; no additional capital beyond 200 reserve',
                reason='liquidation early warning within 5 percent of liquidation price; separate from the 1 percent outside-range barrier'))
    return rows


def _comparison(current, candidate, economics, horizon, options):
    proposed = candidate_economics(candidate, horizon, options)
    if not proposed['eligible']:
        return None, proposed['reason']
    income, cost = economics['current_projected_income'], economics['switch_cost']
    loss = max(0, -cost['net_realized_on_close'])
    advantage = proposed['projected_net_income']-loss-income
    result = dict(horizon_hours=horizon, current_projected_income=income,
        current_net_per_grid=economics['current_net_per_grid'], current_profit_basis=economics['profit_basis'],
        current_realized_gph=economics['current_realized_gph'], challenger_expected_gph=proposed['expected_gph'],
        challenger_projected_income=proposed['projected_grid_income'], opening_fee_budget=proposed['opening_fee_budget'],
        close_loss_cost=loss, net_advantage_usdt=advantage,
        forecast_basis=proposed['forecast_basis'], worst_incumbent_score=economics['worst_score'])
    if proposed['expected_gph'] <= economics['current_realized_gph']:
        return result, 'challenger must have strictly more expected grids per hour'
    if advantage <= 0:
        return result, 'closing loss and opening fees exceed the projected grid-income improvement'
    available, equity = options.get('available_cash'), current.get('net_equity')
    if not _finite_number(available) or available < 0:
        return result, 'available capital is unknown; replacement form withheld'
    mark = current.get('mark_floating_pnl', current.get('floating_pnl'))
    realizable = equity-mark+cost['net_realized_on_close'] if _finite_number(equity) and _finite_number(mark) else None
    if current.get('status') in ('stopped', 'liquidated'):
        realizable = 0 if current.get('capital_released') else equity
    if not _finite_number(realizable) or available+realizable < 1200-1e-9:
        return result, 'capital cannot fund the next 1200-USDT allocation without an external deposit'
    return result, None


def _portfolio_verdicts(currents, result):
    rows = []
    for current in currents:
        selected = current['bot_id'] == result.get('worst_bot_id') and result['action'] == 'replace'
        emergency = any(row['bot_id'] == current['bot_id'] for row in result['emergency_actions'])
        action = 'emergency' if emergency else 'replace' if selected else (
            'waiting' if current.get('status') in ('stopped', 'liquidated') else 'keep')
        rows.append(dict(bot_id=current['bot_id'], pair=current['pair'], action=action,
            reason=result['reason'] if selected or emergency else 'not selected for the single hourly replacement',
            replacement=result['replacement'] if selected else None, switch_cost=_switch_cost(current),
            triggers=result['triggers'] if selected else [], refresh_radar=selected))
    return rows


def _rank_challengers(currents, radar, current, economics, horizon, options):
    running = {row['pair'] for row in currents if row.get('status') not in ('stopped', 'liquidated')}
    accepted, rejected = [], []
    for candidate in radar:
        if candidate['pair'] in running:
            rejected.append(dict(pair=candidate['pair'], reason='already running', comparison=None))
            continue
        comparison, reason = _comparison(current, candidate, economics, horizon, options)
        if reason:
            rejected.append(dict(pair=candidate['pair'], reason=reason, comparison=comparison))
        else:
            accepted.append((candidate, comparison))
    if not options.get('random_pick_order', False):
        accepted.sort(key=lambda row: (-row[1]['net_advantage_usdt'], -row[1]['challenger_expected_gph'], row[0]['pair']))
    return accepted, rejected


def decide_portfolio_replacement(currents, radar, parameters=None):
    """Choose at most one worst incumbent; opportunity does not require a held trigger.

    Worst = six-hour realized-rate grid-income forecast + executable close net.
    A challenger must improve GPH and recover closing loss plus entry fee budget
    over the horizon. Positive inventory profit never subsidizes replacement.
    """
    options, currents = parameters or {}, list(currents)
    horizon = options.get('replacement_horizon_hours', options.get('horizon_hours', 6))
    if not _finite_number(horizon) or horizon <= 0 or len(currents) > 2:
        raise ValueError('positive horizon and at most two incumbent slots required')
    if len({row['bot_id'] for row in currents}) != len(currents):
        raise ValueError('unique incumbent bot IDs required')
    result = dict(action='keep', worst_bot_id=None, replacement=None, switch_cost=None, comparison=None,
        rejected_candidates=[], triggers=[], emergency_actions=_emergencies(currents),
        reason='no qualifying cost-adjusted improvement', refresh_radar=False, horizon_hours=horizon)
    evaluated = [(row, _current_economics(row, horizon)) for row in currents]
    if result['emergency_actions']:
        result.update(action='emergency', reason='resolve liquidation emergency before an ordinary replacement')
    elif not evaluated or any(value is None for row, value in evaluated):
        result['reason'] = 'incumbent rates, per-grid income or executable close costs are unknown'
    else:
        evaluated.sort(key=lambda item: (item[0].get('status') not in ('stopped', 'liquidated'),
                                         item[1]['worst_score'], item[0]['bot_id']))
        current, economics = evaluated[0]
        result.update(worst_bot_id=current['bot_id'], switch_cost=economics['switch_cost'])
        accepted, rejected = _rank_challengers(currents, radar, current, economics, horizon, options)
        result['rejected_candidates'] = rejected
        if accepted:
            result.update(action='replace', replacement=accepted[0][0], comparison=accepted[0][1],
                triggers=['better_cost_adjusted_grid_income'], refresh_radar=True,
                reason='higher grid rate recovers closing loss and entry fee budget over the forecast horizon')
    result['verdicts'] = _portfolio_verdicts(currents, result)
    return result



def _entry_ranking(candidates, occupied, horizon_hours, parameters):
    seen, accepted, rejected = set(), [], []
    for row in candidates:
        pair = row['pair']
        if pair in occupied or pair in seen:
            rejected.append(dict(pair=pair, reason='already occupied or duplicate candidate'))
            continue
        seen.add(pair)
        estimate = candidate_economics(row, horizon_hours, parameters)
        if not estimate['eligible'] or estimate['projected_net_income'] <= 0:
            rejected.append(dict(pair=pair, reason=estimate['reason'] or 'projected income does not cover entry fee budget'))
        else:
            accepted.append(dict(row, economics=estimate))
    return accepted, rejected


def select_funded_entries(candidates, occupied_pairs=(), available_cash=None, slots=2,
                          horizon_hours=6, random_order=False, parameters=None):
    """Rank research opportunities separately from funded startup/vacancy forms.

    Caller supplies fresh available cash, and reserves ``required_cash`` before
    another portfolio action. Each selected form consumes exactly 1200 USDT.
    """
    if type(slots) is not int or not 0 <= slots <= 2:
        raise ValueError('at most two portfolio slots are allowed')
    if not _finite_number(horizon_hours) or horizon_hours <= 0:
        raise ValueError('positive forecast horizon required')
    occupied = set(occupied_pairs)
    accepted, rejected = _entry_ranking(candidates, occupied, horizon_hours, parameters)
    ranked = sorted(accepted, key=lambda row: (-row['economics']['projected_net_income'], -row['economics']['expected_gph'], row['pair']))
    ordered = accepted if random_order else ranked
    known = _finite_number(available_cash) and available_cash >= 0
    remaining, selected = available_cash if known else None, []
    for row in ordered:
        if not known:
            reason = 'available capital is unknown; entry form withheld'
        elif len(occupied)+len(selected) >= slots:
            reason = 'no vacant portfolio slot'
        elif remaining < 1200-1e-9:
            reason = 'available capital cannot fund another 1200-USDT allocation'
        else:
            selected.append(row)
            remaining -= 1200
            continue
        rejected.append(dict(pair=row['pair'], reason=reason))
    return dict(selected=selected, research_ranking=ranked, rejected=rejected,
                available_cash_known=known, remaining_cash=remaining,
                required_cash=1200*len(selected), horizon_hours=horizon_hours)
