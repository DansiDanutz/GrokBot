"""Pure operator reports reconstructed from explicitly supplied offline snapshots.

Every trade is modeled. This report deliberately cannot authorize live use:
fixture calibration, volatility validation and preregistered holdouts are gates.
"""
import math
import re
from dataclasses import asdict
from trader.research.kucoin_radar import radar, normalize_market, crossing_score
from trader.research.kucoin_tracker import track_bars, track_summary
from trader.research.funded_cycles import funded_cycles
from trader.research.kucoin_replacement import decide_portfolio_replacement, select_funded_entries
from trader.research.kucoin_replay import close_cost_summary, range_policy_parameters
from trader.strategies.grid_features import features
from trader.strategies.grid_setup import build_setup
from trader.strategies.grid_types import GridConfig
from trader.strategies.kucoin_grid import create_bot, preview

HOUR_MS = 3_600_000
MAX_HISTORY_MS = 93 * 24 * HOUR_MS
REQUIRED = {'bot_id', 'pair', 'direction', 'leverage', 'used_margin',
            'reserved_margin', 'entry', 'low', 'high', 'grids', 'stop_loss',
            'start_ms', 'expected_start_gph'}
OPTIONAL = {'stop_loss_high', 'trigger', 'quantity', 'multiplier', 'lot_size', 'tick_size'}
TERMINAL = ('stopped', 'liquidated')


def _config(bot, parameters=None):
    options = range_policy_parameters(parameters)
    return GridConfig(pair=bot['pair'], direction=bot['direction'],
        leverage=bot['leverage'], investment=bot['used_margin'],
        reserved_margin=bot['reserved_margin'], entry_price=bot['entry'],
        low=bot['low'], high=bot['high'], grids=bot['grids'],
        stop_loss=bot['stop_loss'], stop_loss_high=bot.get('stop_loss_high'),
        trigger=bot.get('trigger'), quantity=bot.get('quantity'),
        multiplier=bot.get('multiplier', 1), lot_size=bot.get('lot_size', 1),
        tick_size=bot.get('tick_size'), range_exit_stop_pct=options['range_exit_stop_pct'],
        adaptive_range_stops=options['adaptive_range_stops'],
        adaptive_tight_stop_pct=.01, adaptive_liquidation_clearance_pct=.01)


def _validate_bot(bot, asof_ms, parameters=None):
    income_strategy = (parameters or {}).get('strategy')=='income_chart_v3'
    optional = OPTIONAL | ({'expected_start_income_per_hour'} if income_strategy else set())
    if not isinstance(bot, dict) or not REQUIRED <= bot.keys() or bot.keys()-REQUIRED-optional:
        raise ValueError('running bot has missing or unknown form fields; credentials are not accepted')
    if 'expected_start_income_per_hour' in bot:
        income = bot['expected_start_income_per_hour']
        if type(income) not in (int,float) or not math.isfinite(income) or income < 0:
            raise ValueError('expected_start_income_per_hour must be finite and nonnegative')
    for key in ('bot_id', 'pair'):
        if not isinstance(bot[key], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', bot[key]):
            raise ValueError('invalid bot_id or pair')
    for key, value in bot.items():
        if key in ('bot_id', 'pair', 'direction') or value is None and key in OPTIONAL:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError('form numbers must be finite numeric values')
    started = bot['start_ms']
    if type(started) is not int or started < 0 or started % 60000 or started > asof_ms:
        raise ValueError('start_ms must identify a completed minute boundary no later than asof')
    if asof_ms-started > MAX_HISTORY_MS:
        raise ValueError('offline reconstruction is bounded to 93 days per bot')
    if bot['expected_start_gph'] < 0:
        raise ValueError('expected_start_gph must be nonnegative')
    if bot['leverage'] != 5:
        raise ValueError('running leverage must be exactly 5x')
    if bot['used_margin'] != 1000 or bot['reserved_margin'] != 200:
        raise ValueError('running forms require 1000 USDT used margin plus 200 USDT reserve')
    create_bot(_config(bot, parameters), bot['entry'], started)


def _capital_snapshot(document, asof_ms):
    if 'capital' not in document:
        return dict(known=False, available_cash_usdt=None, asof_ms=None,
                    reason='paper available cash is unknown; funded entry and replacement forms withheld')
    capital = document['capital']
    if not isinstance(capital, dict) or set(capital) != {'available_cash_usdt', 'asof_ms'}:
        raise ValueError('capital requires exactly available_cash_usdt and asof_ms')
    cash, timestamp = capital['available_cash_usdt'], capital['asof_ms']
    if type(cash) not in (int, float) or not math.isfinite(cash) or cash < 0:
        raise ValueError('available cash must be finite and nonnegative')
    if type(timestamp) is not int or timestamp != asof_ms:
        raise ValueError('capital snapshot must match the report asof_ms exactly')
    return dict(known=True, available_cash_usdt=cash, asof_ms=timestamp,
                reason='user-supplied available paper cash at asof; includes already released allocations')


def validate_running(document, asof_ms, parameters=None):
    """Strict public form schema; unknown fields are rejected before data access."""
    if type(asof_ms) is not int or asof_ms < 0 or asof_ms % HOUR_MS:
        raise ValueError('asof must be a UTC hour boundary in milliseconds')
    if (not isinstance(document, dict) or not {'schema_version', 'bots'} <= document.keys()
            or document.keys()-{'schema_version', 'bots', 'capital'}):
        raise ValueError('running document requires schema_version, bots and optional capital only')
    if document['schema_version'] != 1 or not isinstance(document['bots'], list):
        raise ValueError('running document must use schema_version 1 and a bots list')
    if len(document['bots']) > 2:
        raise ValueError('at most two running bot forms are supported')
    _capital_snapshot(document, asof_ms)
    for bot in document['bots']:
        _validate_bot(bot, asof_ms, parameters)
    for field in ('bot_id', 'pair'):
        if len({bot[field] for bot in document['bots']}) != len(document['bots']):
            raise ValueError('duplicate running ' + field)
    return document['bots']


def _direction(snapshot, bot, at, parameters):
    records = snapshot.records(at, pairs=[bot['pair']])
    if not records:
        return dict(known=False, flipped=False, candidate=None, reason='direction observations unavailable')
    record = records[0]
    if parameters.get('strategy')=='income_chart_v3':
        scan = radar(records,at,parameters={'strategy':'income_chart_v3','setup':parameters})
        rows = scan['radar'] or scan['rejected']
        row = rows[0] if rows else {}
        form = row.get('setup',{})
        chart = form.get('chart',{})
        cells = chart.get('five_cells',{})
        names = ('4h',) if chart.get('bias_mode')=='4h-only' else ('1d','4h')
        known = all(cells.get(name,{}).get('available') is True and
                    type(cells[name].get('confidence')) in (int,float) and
                    cells[name]['confidence'] >= chart.get('minimum_confidence',.75) for name in names)
        direction = form.get('direction')
        flipped = known and direction is not None and direction != bot['direction']
        return dict(known=known,flipped=flipped,
            candidate=row if flipped and form.get('can_arm') is True else None,
            reason=form.get('reason',row.get('reason','chart direction unavailable')))
    market = normalize_market(record['market'], at)
    observed = market.get('observed_at_ms')
    if observed is None or not 0 <= at-observed <= HOUR_MS:
        return dict(known=False, flipped=False, candidate=None, reason='direction market observations stale or unavailable')
    rate = market.get('funding_rate_8h')
    if rate is None or len(record['bars']) != 10080:
        return dict(known=False, flipped=False, candidate=None, reason='seven-day direction or funding coverage incomplete')
    market['price'] = record['bars'][-1]['close']
    form = build_setup(bot['pair'], features(record['bars'], rate), market, parameters)
    direction = form.get('direction')
    flipped = direction is not None and direction != bot['direction']
    candidate = None
    if flipped and form.get('eligible'):
        candidate = dict(pair=bot['pair'], direction=direction, setup=form,
                         **crossing_score(record['bars'], form, at))
    return dict(known=direction is not None, flipped=flipped,
                candidate=candidate, reason=form.get('reason'))


def _quote(snapshot, pair, at):
    quote = snapshot.market(pair, at)
    if not quote:
        raise ValueError('missing historical running-bot quote')
    for field in ('observed_at_ms', 'book_observed_at_ms'):
        time = quote.get(field)
        if not isinstance(time, (int, float)) or not 0 <= at-time <= HOUR_MS:
            raise ValueError('stale or missing historical running-bot quote')
    bid, ask = quote.get('bid'), quote.get('ask')
    if any(not isinstance(value, (int, float)) or not math.isfinite(value) for value in (bid, ask)):
        raise ValueError('invalid historical running-bot quote')
    if bid <= 0 or ask < bid:
        raise ValueError('invalid historical running-bot spread')
    source = quote.get('book_time_ms')
    if source is not None and (not isinstance(source, (int, float))
                               or not 0 <= source <= quote['book_observed_at_ms']):
        raise ValueError('invalid historical quote source time')
    latency = quote['book_observed_at_ms']-source if source is not None else None
    return dict(quote, quote_recording_latency_ms=latency,
                quote_observation_age_ms=max(at-quote[field]
                for field in ('observed_at_ms', 'book_observed_at_ms')))


def _hour(snapshot, bot, state, at, ending, parameters):
    if state.status in TERMINAL:
        summary = track_summary(state, bot['start_ms'], ending, bot['expected_start_gph'])
        result = dict(state=state, ledger=[], summary=summary)
    else:
        candles = snapshot.candles(bot['pair'], at, ending)
        if len(candles) != (ending-at)//60000:
            raise ValueError('missing completed minute candles for running bot')
        tracking = {}
        if parameters.get('strategy')=='income_chart_v3':
            tracking = dict(funding_mode='recorded_events',
                            funding_coverage=_funding_coverage(snapshot,bot,ending))
        result = track_bars(state, candles, snapshot.funding(bot['pair'], at, ending),
                            bot['start_ms'], ending, bot['expected_start_gph'],**tracking)
    direction = _direction(snapshot, bot, ending, parameters)
    summary = dict(result['summary'], direction_flip=direction['flipped'],
                   direction_flip_known=direction['known'], direction_reason=direction['reason'])
    return result['state'], result['ledger'], summary, direction['candidate']


def _decision(state, summary, bot_id, quote):
    decision = dict(close_cost_summary(state, summary, quote), bot_id=bot_id,
                    capital_released=state.status in TERMINAL)
    if quote:
        decision['quote_observation_age_ms'] = quote['quote_observation_age_ms']
        decision['quote_recording_latency_ms'] = quote['quote_recording_latency_ms']
    return decision


def _funding_coverage(snapshot, bot, ending):
    reader = getattr(snapshot,'funding_coverage',None)
    value = reader(bot['pair'],bot['start_ms'],ending) if callable(reader) else {}
    return value if isinstance(value,dict) else {}


def _running_income(snapshot, bot, ledger, summary, ending):
    """Attribute full holding history before selecting closes in the last six hours.

    No supplied income totals are accepted. Numeric income remains a recorded
    settlement model, even when its inferred coverage is complete. Verified
    exchange cash stays unknown through funded_cycles(coverage_known=False).
    """
    start = bot['start_ms']
    cut = max(start,ending-6*HOUR_MS)
    proof = _funding_coverage(snapshot,bot,ending)
    if summary.get('funding_mode') != 'recorded_events':
        # Plain initial/terminal track_summary has only model cash. It cannot
        # restore verified funding after recorded-event coverage was unknown.
        summary = dict(summary,funding_mode='recorded_events',funding=None,
            funding_modeled_cash=summary.get('funding'),funding_coverage_complete=False,
            funding_modeled_complete=proof.get('modeled_complete') is True,
            funding_coverage=proof,
            funding_unknown_reasons=['initial/terminal summary preserves model cash without independent funding verification'])
    cycles = funded_cycles(ledger,cut,ending,coverage_known=False)
    measured = cycles['summary']
    bounds = proof.get('start_ms'),proof.get('end_ms')
    spanning = all(type(value) is int for value in bounds) and bounds[0] <= start and ending <= bounds[1]
    reasons = []
    if ending-start < 6*HOUR_MS:
        reasons.append('six-hour warmup is incomplete')
    if proof.get('modeled_complete') is not True or not spanning:
        reasons.append('recorded funding model does not cover the complete holding history')
    if measured['modeled_unknown']:
        reasons.append('one or more completed-cycle model amounts are unknown')
    known = not reasons
    return dict(summary,expected_start_income_per_hour=bot.get('expected_start_income_per_hour'),
        starting_income_basis='immutable user-supplied starting estimate; never recalculated from current radar',
        start_ms=start,asof_ms=ending,income_coverage_6h_known=known,income_6h_estimated=True,
        realized_grid_income_per_hour_6h=measured['modeled_grid_income_per_hour'] if known else None,
        realized_gph_6h=measured['completed_grids']/6 if known else None,
        income_coverage_reason='; '.join(reasons) if reasons else None,
        income_coverage_basis='full reconstructed ledger with inferred recorded-settlement coverage; not independently verified',
        funding_history_coverage=proof,funded_cycle_summary_6h=measured)


def _track_bot(snapshot, bot, asof, pairs, parameters):
    state, at = create_bot(_config(bot, parameters), bot['entry'], bot['start_ms']), bot['start_ms']
    history, flip = [], None
    ledger = [asdict(event) for event in state.fill_events]
    seen = {event.event_id for event in state.fill_events}
    while at < asof:
        ending = min((at//HOUR_MS+1)*HOUR_MS, asof)
        state, events, summary, flip = _hour(snapshot, bot, state, at, ending, parameters)
        for event in events:
            if event['event_id'] not in seen:
                ledger.append(event)
                seen.add(event['event_id'])
        if parameters.get('strategy')=='income_chart_v3':
            summary = _running_income(snapshot,bot,ledger,summary,ending)
        history.append(summary)
        at = ending
    summary = dict(history[-1] if history else
                   track_summary(state, bot['start_ms'], asof, bot['expected_start_gph']),
                   running_pairs=pairs)
    if parameters.get('strategy')=='income_chart_v3' and not history:
        summary = _running_income(snapshot,bot,ledger,summary,asof)
    quote = _quote(snapshot, bot['pair'], asof) if state.status not in TERMINAL else None
    decision = _decision(state, summary, bot['bot_id'], quote)
    estimate = preview(state.config)
    if summary.get('completed_grids') == 0:
        decision.update(actual_net_usdt_per_grid=estimate['profit_per_grid_min'],
                        cash_estimate_basis='modeled setup cash after both fill fees')
    modeled_close = state.stop_reason in ('stop_loss', 'liquidation')
    return dict(bot_id=bot['bot_id'], form=bot, preview=estimate,
                tracker=summary, _decision=decision, ledger=ledger, hourly_history=history,
                execution_cost_coverage=dict(complete=not modeled_close,
                    reason='intrabar bid/ask unavailable; modeled mark-price emergency close' if modeled_close
                    else 'hourly close estimates use historical bid/ask; no live fills asserted'))


def _portfolio(tracked, candidates, parameters, available_cash):
    currents = [bot.pop('_decision') for bot in tracked]
    portfolio = decide_portfolio_replacement(currents, candidates,
                                             dict(parameters, available_cash=available_cash))
    verdicts = {row['bot_id']: row for row in portfolio['verdicts']}
    fields = ('mark_floating_pnl', 'close_spread_cost', 'close_cost_basis',
              'close_quote_bid', 'close_quote_ask', 'close_quote_observed_at_ms',
              'quote_observation_age_ms', 'quote_recording_latency_ms')
    for bot, current in zip(tracked, currents):
        verdict = verdicts[bot['bot_id']]
        verdict['switch_cost'].update({key: current[key] for key in fields if key in current})
        bot['verdict'] = verdict
        if portfolio['worst_bot_id'] == bot['bot_id']:
            portfolio['switch_cost'] = dict(verdict['switch_cost'])
    return portfolio


def _funded_decisions(scan, tracked, parameters, capital):
    horizon = parameters.get('replacement_horizon_hours', parameters.get('horizon_hours', 6))
    candidates = scan['radar']
    if parameters.get('strategy')=='income_chart_v3':
        candidates = [row for row in candidates if row.get('setup',{}).get('can_arm') is True
                      and row['setup'].get('funded_entry_eligible') is True
                      and row['setup'].get('funded_economics_eligible') is True]
    entries = select_funded_entries(candidates, [bot['form']['pair'] for bot in tracked],
                                    capital['available_cash_usdt'], 2, horizon, parameters=parameters)
    proposed_pairs = {row['pair'] for row in entries['selected']}
    challengers = [row for row in candidates if row['pair'] not in proposed_pairs]
    portfolio = _portfolio(tracked, challengers, parameters, entries['remaining_cash'])
    forms = [row['setup'] for row in entries['selected']]
    if portfolio['action'] == 'replace':
        forms.append(portfolio['replacement']['setup'])
    return entries, portfolio, forms


def operator_report(snapshot, asof_ms, running_document, parameters=None):
    """Rebuild each running bot from its declared start, preserving hourly history."""
    parameters = range_policy_parameters(parameters)
    bots = validate_running(running_document, asof_ms, parameters)
    pairs = [bot['pair'] for bot in bots]
    reader = getattr(snapshot, 'iter_records', snapshot.records)
    scan_options = {'setup':parameters,'radar_size':parameters.get('radar_size',10)}
    if parameters.get('strategy')=='income_chart_v3':
        scan_options['strategy'] = 'income_chart_v3'
    scan = radar(reader(asof_ms),asof_ms,pairs,scan_options)
    tracked = [_track_bot(snapshot, bot, asof_ms, pairs, parameters) for bot in bots]
    capital = _capital_snapshot(running_document, asof_ms)
    entries, portfolio, forms = _funded_decisions(scan, tracked, parameters, capital)
    report = dict(schema_version=1, asof_ms=asof_ms,
        interpretation='offline counterfactual OHLC research; fills are modeled, not KuCoin account trades',
        live_use=dict(actionable=False, status='blocked_calibration_and_holdout_validation',
                      reason='All four bot calibration, volatility fixture validation and two disjoint monthly holdouts must pass.'),
        radar=scan, recommended_forms=forms, entry_decision=entries,
        portfolio_decision=portfolio, capital=capital,
        range_policy={key: parameters[key] for key in ('range_exit_stop_pct', 'adaptive_range_stops')},
        capital_policy=dict(used_margin=1000, reserved_margin=200, leverage=5,
                            total_per_bot=1200, total_for_two_bots=2400),
        running=tracked,
        coverage=dict(universe_observed=scan.get('coverage', {}).get('observed', 0),
                      minute_candles='complete reconstruction for supplied running forms',
                      execution_costs_complete=all(bot['execution_cost_coverage']['complete'] for bot in tracked)),
        refresh_radar_after_switch=True,
        instructions='Research proposals only. This command never starts, stops or replaces an exchange bot.')
    if parameters.get('strategy')=='income_chart_v3':
        report.update(strategy='income_chart_v3',offered_forms=[row['setup'] for row in scan['radar']],
            funding_withheld_offers=[dict(pair=row['pair'],reason=row['setup'].get('status',
                'entry signal or funding economics not eligible')) for row in scan['radar']
                if row['setup'].get('can_arm') is not True or row['setup'].get('funded_entry_eligible') is not True
                   or row['setup'].get('funded_economics_eligible') is not True],
            live_use=dict(actionable=False,status='research_only_live_execution_unavailable',
                reason='Estimated paper offers are shown without a calibration gate; this command has no live exchange execution. Quantity and liquidation remain estimates.'))
    return report
