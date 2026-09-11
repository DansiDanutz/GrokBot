"""Pure offline hourly orchestration. All fills are OHLC-model estimates.

The snapshot protocol reads detached observations supplied by the caller. No
exchange clients, databases, credentials, schedules or file writes live here.
"""
from dataclasses import asdict, replace
import random
import math
from time import perf_counter

from trader.research.kucoin_radar import radar, normalize_market, crossing_score
from trader.research.kucoin_replacement import (decide_replacement, decide_portfolio_replacement, select_funded_entries)
from trader.research.kucoin_tracker import track_bars, track_summary
from trader.strategies.grid_features import features
from trader.strategies.grid_setup import build_setup
from trader.strategies.grid_calibration import fixture_config
from trader.strategies.kucoin_grid import create_bot, stop, net_equity, floating_pnl, preview, FEE
from trader.strategies.grid_types import GridConfig

HOUR_MS = 3600000
DAY_MS = 24 * HOUR_MS
TERMINAL = ('stopped', 'liquidated')
MODES = ('system', 'four_observed_long_forms_unchanged',
         'same_four_symbols_system_setup', 'random_radar_identical_rules')


def window_coverage(snapshot, start, end, parameters=None):
    """Candle-only research can execute without pretending books were observed."""
    historical = bool((parameters or {}).get('historical_candle_only'))
    lower, upper = snapshot.bounds() if historical else snapshot.market_bounds()
    available = lower is not None and upper is not None and lower <= start and end <= upper
    reasons = [] if available else ['historical '+('candles' if historical else 'ticker/book observations')+' do not cover the requested window']
    if historical:
        reasons.append('candle-only filters; retrospective metadata, unknown funding and modeled spread are not verified coverage')
    return dict(complete=available and not historical, window_available=available,
                market_bounds=[lower, upper], bounds_basis='candles' if historical else 'quotes/books',
                candidate_minimum_coverage=.95 if historical else 1., reasons=reasons,
                execution_missing_minutes=0, execution_observed_minutes=0, unknown_funding_settlements=0)


def _report(snapshot, start, end, mode, parameters):
    return dict(mode=mode, start_ms=start, end_ms=end, parameters=parameters,
                initial_capital=2400., coverage=window_coverage(snapshot, start, end, parameters),
                filter_mode='candle-only filters' if parameters.get('historical_candle_only') else 'quote/book filters',
                model_executed=False, completed_hours=0, execution_assumptions=[],
                manifest=dict(snapshot.manifest), bots=[], ledger=[], hourly_radar=[],
                hourly_tracker=[], switches=[], capital_events=[], equity_timeline=[], portfolio_decisions=[], entry_decisions=[],
                metrics={}, per_coin={}, direction_mix={},
                interpretation='counterfactual OHLC model; not observed KuCoin app history',
                limitations=['Adverse-first minute OHLC ordering cannot recover actual trades.',
                             'Critical-price marks share their model path vertex timestamp; the decision uses the conservative drawdown bound.',
                             'Open inventory is marked at the end; no terminal profit stop.',
                             'Outside-range duration uses minute-close sampling, not reconstructed intraminute residence.'])


def range_policy_parameters(parameters=None):
    """Make the operational edge policy explicit; preserve requested research buffers."""
    options = dict(parameters or {})
    fraction = options.setdefault('range_exit_stop_pct', 0.)
    if type(fraction) not in (int, float) or not math.isfinite(fraction) or not 0 <= fraction < 1:
        raise ValueError('range_exit_stop_pct must be finite, at least zero and less than one')
    adaptive = options.setdefault('adaptive_range_stops', fraction >= .01)
    if type(adaptive) is not bool:
        raise ValueError('adaptive_range_stops must be boolean')
    if adaptive and fraction < .01:
        raise ValueError('adaptive stops require an enabled wider initial range stop')
    return options


def _config(form):
    return GridConfig(pair=form['pair'], low=form['low'], high=form['high'],
        grids=form['grids'], leverage=form['leverage'], direction=form['direction'],
        investment=form['used_margin'], reserved_margin=form.get('reserved_margin', 0),
        entry_price=form['entry'], quantity=form.get('quantity'),
        multiplier=form.get('multiplier', 1), lot_size=form.get('lot_size', 1),
        trigger=form.get('trigger'), stop_loss=form.get('stop_loss'),
        stop_loss_high=form.get('stop_loss_high'), tick_size=form.get('tick_size'),
        range_exit_stop_pct=form.get('range_exit_stop_pct', 0.),
        adaptive_range_stops=form.get('adaptive_range_stops', False),
        adaptive_tight_stop_pct=form.get('adaptive_tight_stop_pct', .01),
        adaptive_liquidation_clearance_pct=form.get('adaptive_liquidation_clearance_pct', .01))


def _pairs(bots):
    return {bot['state'].config.pair for bot in bots if bot['active'] and bot['state'].status not in TERMINAL}


def _append_events(report, bot, rows):
    for row in rows:
        if row['event_id'] not in bot['seen']:
            report['ledger'].append(dict(row, bot_id=bot['bot_id'], pair=bot['state'].config.pair))
            bot['seen'].add(row['event_id'])


def _mark(report, bot, timestamp, equity, floating=None):
    report['equity_timeline'].append(dict(bot_id=bot['bot_id'], timestamp_ms=timestamp,
        equity=equity, margin=bot['state'].config.total_margin,
        floating_pnl=floating_pnl(bot['state'], bot['state'].price) if floating is None else floating))


def _open(report, candidate, at, cash, price=None, config=None):
    observed_form = config is not None
    config = config or _config(candidate['setup'])
    if not observed_form and (config.investment, config.reserved_margin, config.leverage) != (1000, 200, 5):
        raise ValueError('generated bot must use 1000 USDT plus 200 reserve at exactly 5x')
    if cash + 1e-9 < config.total_margin:
        report['capital_events'].append(dict(asof_ms=at, pair=config.pair, action='cash_wait',
            available_cash=cash, required_margin=config.total_margin,
            reason='fixed margin cannot be funded; no external deposits'))
        return cash
    price = candidate['setup']['entry'] if price is None else price
    state = create_bot(config, price, at)
    bot = dict(bot_id='bot-' + str(len(report['bots'])+1), state=state, active=True,
        start_ms=at, expected=candidate.get('score', 0), form=candidate['setup'],
        history=[], seen=set(), exposure_hours=0., closest=None, outside_ms=0)
    if report['parameters'].get('strategy') == 'income_chart_v3':
        bot.update(expected=candidate.get('expected_gph', candidate['setup'].get('expected_gph')),
            expected_start_income_per_hour=candidate.get('grid_income_per_hour',
                candidate['setup'].get('grid_income_per_hour')), funding_modeled_complete=True)
    report['bots'].append(bot)
    _append_events(report, bot, [asdict(event) for event in state.fill_events])
    _mark(report, bot, at, net_equity(state, state.price))
    return cash-config.total_margin


def _scan(snapshot, report, at, options, allowed=None):
    running = _pairs(report['bots'])
    key = at, tuple(sorted(running)), None if allowed is None else tuple(sorted(allowed))
    previous = report.get('_scan_cache')
    statistics = report.setdefault('scan_statistics', {'scans': 0, 'cache_hits': 0})
    if previous is not None and previous[0] == key:
        statistics['cache_hits'] += 1
        return list(previous[1]['radar']), previous[2]
    statistics['scans'] += 1
    radar_options = {'setup': options, 'radar_size': options.get('radar_size', 10)}
    if options.get('strategy') == 'income_chart_v3':
        radar_options['strategy'] = 'income_chart_v3'
    if hasattr(snapshot, 'iter_records'):
        result = radar(snapshot.iter_records(at, pairs=allowed), at, running, radar_options)
        records = snapshot.records(at, pairs=running)
    else:
        records = snapshot.records(at, pairs=allowed)
        records = [dict(row, bars=row.get('bars', row.get('bars7days', []))) for row in records]
        result = radar(records, at, running, radar_options)
    report['hourly_radar'].append(result)
    if not result.get('coverage', {}).get('observed', len(records)):
        _gap(report, 'no historical market membership observations at ' + str(at))
    for row in result.get('rejected', []):
        reason = row['reason']
        if row.get('coverage_issue', False):
            _gap(report, row['pair'] + ': ' + reason)
    report['_scan_cache'] = key, result, records
    return list(result['radar']), records


def _gap(report, reason):
    report['coverage']['complete'] = False
    if reason not in report['coverage']['reasons']:
        report['coverage']['reasons'].append(reason)


def _price(snapshot, pair, at, fallback):
    market = snapshot.market(pair, at) or {}
    if market.get('assumed') and market.get('candle_observed_at_ms') != at:
        return None
    bid, ask = market.get('bid'), market.get('ask')
    return (bid+ask)/2 if bid is not None and ask is not None else fallback


def _fill_slots(snapshot, report, candidates, at, cash, rng, maximum=2):
    rows = list(candidates)
    random_order = report['mode'] == 'random_radar_identical_rules'
    if random_order:
        rng.shuffle(rows)
    occupied = {bot['state'].config.pair for bot in report['bots'] if bot['active']}
    options = report['parameters']
    horizon = options.get('replacement_horizon_hours', options.get('horizon_hours', 6))
    selection = select_funded_entries(rows, occupied, cash, maximum, horizon, random_order, options)
    report['entry_decisions'].append(dict(selection, asof_ms=at))
    for row in selection['selected']:
        price = _price(snapshot, row['pair'], at, row['setup']['entry'])
        if price is None:
            report['capital_events'].append(dict(asof_ms=at, pair=row['pair'], action='await_observed_price'))
            continue
        cash = _open(report, row, at, cash, price)
    return cash


def _flip(records, bot, at, options):
    record = next((row for row in records if row['pair'] == bot['state'].config.pair), None)
    if record is None:
        return None
    market = normalize_market(record['market'], at)
    rate = market.get('funding_rate_8h')
    if rate is None:
        return None
    form = build_setup(record['pair'], features(record['bars'], rate), market, options)
    if not form.get('direction') or form['direction'] == bot['state'].config.direction:
        return None
    if not form.get('eligible'):
        return dict(pair=record['pair'], direction=form['direction'], eligible=False, setup=form, score=0)
    score = crossing_score(record['bars'], form, at)
    return dict(pair=record['pair'], direction=form['direction'], setup=form, **score)


def _track(snapshot, report, bot, at, end):
    state = bot['state']
    bars = snapshot.candles(state.config.pair, at, end)
    historical = bool(report['parameters'].get('historical_candle_only'))
    if not historical:
        _require_quote(snapshot, state.config.pair, end)
    if not historical and len(bars) != (end-at)//60000:
        raise ValueError('missing completed minute candles for ' + state.config.pair)
    tracker_options = dict(historical_candle_only=historical)
    if report['parameters'].get('strategy') == 'income_chart_v3':
        coverage = (snapshot.funding_coverage(state.config.pair, bot['start_ms'], end)
                    if hasattr(snapshot, 'funding_coverage') else {})
        tracker_options.update(funding_mode='recorded_events', funding_coverage=coverage)
        if coverage.get('coverage_verified', coverage.get('verified')) is not True:
            _gap(report, 'recorded funding cadence/coverage is inferred, not independently verified')
    result = track_bars(state, bars, snapshot.funding(state.config.pair, at, end),
                       bot['start_ms'], end, bot['expected'], **tracker_options)
    if report['parameters'].get('strategy') == 'income_chart_v3':
        stopped_at = result['state'].timestamp_ms if result['state'].status in TERMINAL else None
        _record_execution_coverage(bot, bars, at, end, closed_at=stopped_at)
        actual_coverage = (snapshot.funding_coverage(state.config.pair, bot['start_ms'],
            min(end, stopped_at) if stopped_at is not None else end)
            if hasattr(snapshot, 'funding_coverage') else {})
        bot['funding_modeled_complete'] &= actual_coverage.get('modeled_complete') is True
    bot['state'] = result['state']
    _append_events(report, bot, result['ledger'])
    if any(row['kind'] in ('stop_loss', 'liquidation') for row in result['ledger']):
        _gap(report, 'intrabar market-close spread is unobserved; stop/liquidation execution-cost coverage incomplete')
    timeline = result.get('equity_timeline', [])
    for mark in timeline:
        _mark(report, bot, mark['timestamp_ms'], mark['equity'], mark.get('floating_pnl'))
    _mark(report, bot, end, net_equity(bot['state'], bot['state'].price))
    elapsed_end = min(end, bot['state'].timestamp_ms)
    bot['exposure_hours'] += (elapsed_end-at)/HOUR_MS
    bot['outside_ms'] += sum(60000 for bar in bars if bar['timestamp_ms' if 'timestamp_ms' in bar else 'time_ms'] < elapsed_end
        and (bar['close'] < state.config.low or bar['close'] > state.config.high))
    summary = result['summary']
    if historical:
        _execution_coverage(report, bot, result)
    closest = summary.get('closest_liquidation_range_pct')
    if closest is not None:
        bot['closest'] = closest if bot['closest'] is None else min(bot['closest'], closest)
    return summary



def _execution_coverage(report, bot, result):
    summary, coverage = result['summary'], report['coverage']
    coverage['execution_missing_minutes'] += summary.get('missing_minutes', 0)
    coverage['execution_observed_minutes'] += summary.get('observed_minutes', 0)
    coverage['unknown_funding_settlements'] += len(summary.get('unknown_funding_settlements', []))
    if result.get('last_observed_ms') is not None:
        bot['last_observed_ms'] = result['last_observed_ms']
    for assumption in summary.get('assumptions', []):
        if assumption not in report['execution_assumptions']:
            report['execution_assumptions'].append(assumption)


def _income_summary(snapshot, report, bot, summary, end):
    """Match full opening history, then select closes in the trailing six hours."""
    if report.get('parameters', {}).get('strategy') != 'income_chart_v3':
        return summary
    from trader.research.funded_cycles import funded_cycles
    start = bot['start_ms']
    beginning = max(start, end-6*HOUR_MS)
    holding_end = min(end, bot['state'].timestamp_ms) if bot['state'].status in TERMINAL else end
    coverage = (snapshot.funding_coverage(bot['state'].config.pair, start, holding_end)
                if hasattr(snapshot, 'funding_coverage') else {})
    ledger = [row for row in report['ledger'] if row['bot_id'] == bot['bot_id']]
    cycles = funded_cycles(ledger, beginning, end, coverage_known=False)
    measured = cycles['summary']
    execution = _income_execution_coverage(bot, beginning, end)
    spanning = (type(coverage.get('start_ms')) is int and type(coverage.get('end_ms')) is int
                and coverage['start_ms'] <= start and coverage['end_ms'] >= holding_end)
    known = (end-start >= 6*HOUR_MS and spanning and coverage.get('modeled_complete') is True
             and measured['modeled_unknown'] == 0 and execution['eligible'])
    return dict(summary, expected_start_income_per_hour=bot['expected_start_income_per_hour'],
        income_coverage_6h_known=known, income_6h_estimated=True,
        income_execution_coverage_6h=execution,
        income_coverage_basis='inferred recorded-settlement model; not independently verified',
        realized_grid_income_per_hour_6h=measured['modeled_grid_income_per_hour'] if known else None,
        realized_gph_6h=measured['completed_grids']/6 if known else None,
        funded_cycle_summary_6h=measured)


def _record_execution_coverage(bot, bars, start, end, closed=False, closed_at=None):
    expected = (end-start)//60000
    observed = {row.get('timestamp_ms', row.get('time_ms')) for row in bars
        if not row.get('synthetic') and not row.get('indicator_only') and row.get('observed') is not False
        and start <= row.get('timestamp_ms', row.get('time_ms', -1)) < end}
    if closed_at is not None:
        observed.update(range(max(start, (closed_at+59999)//60000*60000), end, 60000))
    actual = expected if closed else len(observed)
    previous = [row for row in bot.get('income_execution_windows', [])
                if row['end_ms'] > end-6*HOUR_MS and row['start_ms'] != start]
    previous.append(dict(start_ms=start, end_ms=end, observed_minutes=actual,
                         expected_minutes=expected, closed_flat=closed))
    bot['income_execution_windows'] = previous


def _income_execution_coverage(bot, start, end):
    windows = [row for row in bot.get('income_execution_windows', [])
               if start <= row['start_ms'] and row['end_ms'] <= end]
    expected = (end-start)//60000
    actual = sum(row['observed_minutes'] for row in windows)
    covered = sum(row['expected_minutes'] for row in windows)
    return dict(observed_minutes=actual, expected_minutes=expected,
        missing_minutes=expected-actual, fraction=actual/expected if expected else None,
        minimum_fraction=.95, eligible=bool(expected and covered == expected and actual*100 >= expected*95),
        basis='actual minute observations or already-closed flat time; gaps do not create cycles')


def _require_quote(snapshot, pair, at):
    quote = snapshot.market(pair, at) or {}
    for field in ('observed_at_ms', 'book_observed_at_ms'):
        observed = quote.get(field, quote.get('observed_at_ms'))
        if observed is None or not 0 <= at-observed <= HOUR_MS:
            raise ValueError('missing or stale historical book for ' + pair)
    if quote.get('bid') is None or quote.get('ask') is None:
        raise ValueError('missing historical close book for ' + pair)
    return quote


def close_cost_summary(state, summary, quote):
    """Quote-aware decision copy; the caller validates quote age and causality.

    Keep mark PnL explicit. The replacement rule consumes executable gross PnL
    and closing fees. Already-closed positions retain their modeled actual cost.
    """
    result = dict(summary, mark_floating_pnl=summary.get('mark_floating_pnl', summary['floating_pnl']))
    if state.status in TERMINAL:
        return dict(result, close_cost_basis='already_realized_model_close')
    bid, ask = quote.get('bid'), quote.get('ask')
    if any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0
           for value in (bid, ask)) or bid > ask:
        raise ValueError('valid bid and ask required for executable close cost')
    gross = sum(p.side*p.quantity*((bid if p.side == 1 else ask)-p.entry) for p in state.positions)
    fee = sum(p.quantity*(bid if p.side == 1 else ask)*FEE for p in state.positions)
    return dict(result, floating_pnl=gross, close_fee=fee, quoted_close_gross_pnl=gross,
        quoted_close_fee=fee, quoted_close_net_pnl=gross-fee,
        close_spread_cost=result['mark_floating_pnl']-gross, close_cost_basis='historical_bid_ask',
        close_quote_bid=bid, close_quote_ask=ask,
        close_quote_observed_at_ms=quote.get('book_observed_at_ms', quote.get('observed_at_ms')),
        close_quote_assumed=bool(quote.get('assumed')), close_quote_assumption=quote.get('assumption'))


def _actual_switch_cost(report, bot, forecast):
    fills = [row for row in report['ledger'] if row['bot_id'] == bot['bot_id']
             and row['kind'] in ('replacement', 'stop_loss', 'liquidation')]
    gross, fees = sum(row['gross_pnl'] for row in fills), sum(row['fee'] for row in fills)
    net = gross-fees
    return dict(forecast_switch_cost=dict(forecast), actual_switch_gross_pnl=gross,
                actual_switch_close_fees=fees, actual_switch_net_pnl=net,
                forecast_actual_net_difference=net-forecast['net_realized_on_close'],
                actual_switch_cost_basis='model fill ledger; not exchange execution')


def _close(snapshot, report, bot, at, quote=None):
    if bot.get('released'):
        bot['active'] = False
        return 0.
    state = bot['state']
    if state.status not in TERMINAL:
        quote = _require_quote(snapshot, state.config.pair, at) if quote is None else quote
        state = stop(state, state.price, at, quote['bid'], quote['ask'])
        bot['state'] = state
        _append_events(report, bot, [asdict(event) for event in state.fill_events])
        _mark(report, bot, at, net_equity(state, state.price))
    bot['active'] = False
    bot['released'] = True
    return net_equity(state, state.price)


def _legacy_hour_decisions(snapshot, report, end, options, cash, allowed, rng):
    candidates, records = _scan(snapshot, report, end, options, allowed)
    for bot in list(report['bots']):
        if not bot['active']:
            continue
        summary = dict(bot['latest'], asof_ms=end, running_pairs=sorted(_pairs(report['bots'])))
        quote = {} if bot['state'].status in TERMINAL else _require_quote(snapshot, bot['state'].config.pair, end)
        summary = close_cost_summary(bot['state'], summary, quote)
        flip = _flip(records, bot, end, options)
        summary['direction_flip'] = flip is not None
        choices = list(candidates)
        if report['mode'] == 'random_radar_identical_rules':
            rng.shuffle(choices)
        verdict = decide_replacement(summary, choices, bot['history'],
                                     dict(options, direction_flip_candidate=flip))
        bot['history'].append(summary)
        report['hourly_tracker'].append(dict(summary, bot_id=bot['bot_id'], verdict=verdict))
        if verdict['action'] == 'replace':
            cash += _close(snapshot, report, bot, end, quote)
            actual_cost = _actual_switch_cost(report, bot, verdict['switch_cost'])
            selected = verdict['replacement']
            before = len(report['bots'])
            cash = _open(report, selected, end, cash,
                         _price(snapshot, selected['pair'], end, selected['setup']['entry']))
            report['switches'].append(dict(asof_ms=end, bot_id=bot['bot_id'],
                pair=bot['state'].config.pair, replacement_executed=len(report['bots']) > before, **verdict, **actual_cost))
            candidates, records = _scan(snapshot, report, end, options, allowed)
        elif bot['state'].status in TERMINAL:
            cash += _close(snapshot, report, bot, end)
            bot['active'] = True  # Closed slot waits for a qualifying replacement.
    return cash



def _decision_summaries(snapshot, report, end):
    rows, quotes = [], {}
    for bot in report['bots']:
        if not bot['active']:
            continue
        state = bot['state']
        quote = {} if state.status in TERMINAL else _require_quote(snapshot, state.config.pair, end)
        summary = dict(bot['latest'], bot_id=bot['bot_id'], asof_ms=end,
                       capital_released=bool(bot.get('released')))
        summary = _income_summary(snapshot, report, bot, summary, end)
        if summary.get('completed_grids') == 0:
            summary.update(actual_net_usdt_per_grid=preview(state.config)['profit_per_grid_min'],
                           cash_estimate_basis='modeled setup cash after both fill fees')
        rows.append(close_cost_summary(state, summary, quote))
        quotes[bot['bot_id']] = quote
    return rows, quotes


def _execute_portfolio_switch(snapshot, report, end, cash, decision, quotes):
    bot = next(bot for bot in report['bots'] if bot['bot_id'] == decision['worst_bot_id'])
    cash += _close(snapshot, report, bot, end, quotes[bot['bot_id']])
    actual_cost = _actual_switch_cost(report, bot, decision['switch_cost'])
    selected, before = decision['replacement'], len(report['bots'])
    cash = _open(report, selected, end, cash,
                 _price(snapshot, selected['pair'], end, selected['setup']['entry']))
    report['switches'].append(dict(asof_ms=end, bot_id=bot['bot_id'], pair=bot['state'].config.pair,
        replacement_executed=len(report['bots']) > before, **decision, **actual_cost))
    return cash


def _hour_decisions(snapshot, report, end, options, cash, allowed, rng):
    if options.get('replacement_policy') == 'legacy_held':
        if options.get('historical_candle_only'):
            raise ValueError('candle-only replay requires the portfolio replacement policy')
        return _legacy_hour_decisions(snapshot, report, end, options, cash, allowed, rng)
    candidates, _ = _scan(snapshot, report, end, options, allowed)
    summaries, quotes = _decision_summaries(snapshot, report, end)
    if report['mode'] == 'random_radar_identical_rules':
        rng.shuffle(candidates)
    decision = decide_portfolio_replacement(summaries, candidates, dict(options, available_cash=cash,
        random_pick_order=report['mode'] == 'random_radar_identical_rules'))
    if options.get('historical_candle_only'):
        _withhold_missing_execution(snapshot, decision, summaries, end)
    report['portfolio_decisions'].append(dict(decision, asof_ms=end))
    verdicts = {row['bot_id']: row for row in decision['verdicts']}
    for summary in summaries:
        report['hourly_tracker'].append(dict(summary, verdict=verdicts[summary['bot_id']]))
    if decision['action'] == 'replace':
        cash = _execute_portfolio_switch(snapshot, report, end, cash, decision, quotes)
        _scan(snapshot, report, end, options, allowed)
    for bot in report['bots']:
        if bot['active'] and bot['state'].status in TERMINAL:
            cash += _close(snapshot, report, bot, end)
            bot['active'] = True
    return cash



def _withhold_missing_execution(snapshot, decision, summaries, at):
    if decision['action'] != 'replace':
        return
    current = next(row for row in summaries if row['bot_id'] == decision['worst_bot_id'])
    proposed = decision['replacement']
    prices = [_price(snapshot, current['pair'], at, None), _price(snapshot, proposed['pair'], at, None)]
    if any(price is None for price in prices):
        decision.update(action='keep', replacement=None, refresh_radar=False, triggers=[],
                        reason='await actual observed prices; missing minutes do not create market-close fills')
        for verdict in decision['verdicts']:
            if verdict['action'] == 'replace':
                verdict.update(action='keep', replacement=None, refresh_radar=False, triggers=[], reason=decision['reason'])


def _fixture_rows(fixtures):
    if fixtures is None:
        raise ValueError('baseline requires the four observed forms explicitly')
    rows = fixtures.get('running_bots', []) if isinstance(fixtures, dict) else list(fixtures)
    if len(rows) != 4:
        raise ValueError('baseline requires exactly four observed bots')
    return rows


def _actual_start(snapshot, report, fixtures, start):
    configs = [fixture_config(row) for row in _fixture_rows(fixtures)]
    configs = [replace(config, pair=config.pair+'M' if config.pair.endswith('USDT') else config.pair)
               for config in configs]
    report['initial_capital'] = sum(config.total_margin for config in configs)
    cash = report['initial_capital']
    report['limitations'].extend(['Actual baseline capital follows the documented fixture_config interpretation of Margin and Reserved Margin.',
        'Original stop-loss and initial inventory are unknown; no baseline stop-loss is invented.'])
    prices = [_price(snapshot, config.pair, start, None) for config in configs]
    for config, price in zip(configs, prices):
        if price is None:
            raise ValueError('baseline historical starting price unavailable for '+config.pair+' at '+str(start))
        if not config.low <= price <= config.high:
            raise ValueError('baseline original inventory unavailable when start price is outside its fixed range')
    for config, price in zip(configs, prices):
        form = dict(asdict(config), used_margin=config.investment, entry=config.entry_price)
        row = dict(pair=config.pair, setup=form, score=0)
        cash = _open(report, row, start, cash, price, config)
    return cash


def _track_hour(snapshot, report, at, end):
    for bot in report['bots']:
        if bot['active']:
            if bot['state'].status in TERMINAL:
                bot['latest'] = track_summary(bot['state'], bot['start_ms'], end, bot['expected'])
                if report['parameters'].get('strategy') == 'income_chart_v3':
                    _record_execution_coverage(bot, [], at, end, closed=True)
            else:
                bot['latest'] = _track(snapshot, report, bot, at, end)
            bot['latest'] = _income_summary(snapshot, report, bot, bot['latest'], end)
            if bot['state'].liquidated:
                report['failed_liquidation'] = True
    return report.get('failed_liquidation', False)


def _execution_start(snapshot, report, fixtures, seed):
    start, mode = report['start_ms'], report['mode']
    rng, cash, allowed = random.Random(seed), report['initial_capital'], None
    actual = mode == 'four_observed_long_forms_unchanged'
    if mode == 'same_four_symbols_system_setup':
        report['limitations'].append('Rules baseline: two 1200-USDT slots (1000 used plus 200 reserve) restricted to the four observed symbols.')
        allowed = [row['symbol']+'M' if row['symbol'].endswith('USDT') else row['symbol']
                   for row in _fixture_rows(fixtures)]
    if actual:
        cash = _actual_start(snapshot, report, fixtures, start)
    return dict(cursor_ms=start, cash=cash, allowed=allowed, rng_state=rng.getstate(), complete=False)


def _execute(snapshot, report, options, fixtures, seed, progress=None, execution=None, max_hours=None):
    end, actual = report['end_ms'], report['mode'] == 'four_observed_long_forms_unchanged'
    began = perf_counter()
    control = execution if execution is not None else _execution_start(snapshot, report, fixtures, seed)
    rng, cash, allowed = random.Random(), control['cash'], control['allowed']
    rng.setstate(control['rng_state'])
    cursor = control['cursor_ms']
    limit = end if max_hours is None else min(end, cursor+max_hours*HOUR_MS)
    for at in range(cursor, limit, HOUR_MS):
        if progress:
            progress(dict(phase='hour_started', asof_ms=at, elapsed_seconds=perf_counter()-began,
                          completed_hours=report['completed_hours']))
        if not actual:
            candidates, _ = _scan(snapshot, report, at, options, allowed)
            cash = _fill_slots(snapshot, report, candidates, at, cash, rng)
        ending = min(at+HOUR_MS, end)
        liquidated = _track_hour(snapshot, report, at, ending)
        report['model_executed'] = True
        report['completed_hours'] += (ending-at)/HOUR_MS
        if progress:
            progress(dict(phase='hour_completed', asof_ms=ending, elapsed_seconds=perf_counter()-began,
                          completed_hours=report['completed_hours'],
                          running_bots=sum(bot['active'] for bot in report['bots']),
                          cache_stats=dict(getattr(snapshot, 'stats', {}))))
        cursor = ending
        if liquidated:
            break
        if not actual and ending < end:
            cash = _hour_decisions(snapshot, report, ending, options, cash, allowed, rng)
        elif actual or ending == end:
            for bot in report['bots']:
                if bot['active']:
                    report['hourly_tracker'].append(dict(bot['latest'], bot_id=bot['bot_id']))
    complete = cursor == end or bool(report.get('failed_liquidation'))
    if complete:
        report['ending_available_cash'] = cash + sum(net_equity(bot['state'], bot['state'].price)
            for bot in report['bots'] if bot['state'].status in TERMINAL and not bot.get('released'))
    return dict(cursor_ms=cursor, cash=cash, allowed=allowed, rng_state=rng.getstate(), complete=complete)


def _prepare_window(snapshot, start_ms, end_ms, mode, options):
    if options.get('historical_candle_only') and getattr(snapshot, 'historical_candle_only', False) is not True:
        from trader.research.kucoin_snapshot import HistoricalSnapshot
        snapshot = HistoricalSnapshot(snapshot, options)
    if options.get('strategy') == 'income_chart_v3' and getattr(snapshot, 'income_chart_v3', False) is not True:
        from trader.research.chart_snapshot import ChartSnapshot
        snapshot = ChartSnapshot(snapshot, options)
    elif options.get('strategy') == 'income_chart_v3' and hasattr(snapshot, 'for_parameters'):
        snapshot = snapshot.for_parameters(options)
    report = _report(snapshot, start_ms, end_ms, mode, options)
    if options.get('historical_candle_only'):
        report['execution_assumptions'].extend([
            'Historical contract specifications are retrospective and introduce survivorship/contract-change uncertainty.',
            'Discretionary close spread is modeled at '+str(options.get('historical_spread_bps', 10))+' bps; seed/grid fills use model prices.',
            'Intrabar stops/liquidations lack observed spread; missing extremes and unknown funding prevent verification.'])
    return snapshot, report


def run_window(snapshot, start_ms, end_ms, parameters=None, mode='system', fixtures=None, seed=20260911, progress=None):
    """Run a causal [start,end) model with 2400 USDT and no cash injections."""
    if mode not in MODES:
        raise ValueError('unknown replay mode')
    if any(type(t) is not int or t < 0 or t % HOUR_MS for t in (start_ms, end_ms)) or start_ms >= end_ms:
        raise ValueError('window must use increasing UTC hour boundaries')
    options = range_policy_parameters(parameters)
    began = perf_counter()
    snapshot, report = _prepare_window(snapshot, start_ms, end_ms, mode, options)
    if report['coverage']['window_available']:
        try:
            _execute(snapshot, report, options, fixtures, seed, progress)
        except (ValueError, KeyError, TypeError) as error:
            _gap(report, str(error))
    report['performance'] = dict(elapsed_seconds=perf_counter()-began,
                                 cache_stats=dict(getattr(snapshot, 'stats', {})))
    from trader.research.kucoin_portfolio import summarize
    return summarize(report)



def run_chunk(snapshot, start_ms, end_ms, parameters=None, mode='system', fixtures=None,
              seed=20260911, max_hours=24, checkpoint=None, identity=None,
              checkpoint_callback=None, progress=None):
    """Run at most K complete hours; emit one detached JSON checkpoint at chunk end.

    Keep the original window on every call. Callback persistence is external.
    An interrupted hour leaves the previous caller-owned checkpoint untouched.
    Physical cache reads and elapsed time are segment-local performance metadata.
    """
    from trader.research.replay_checkpoint import (checkpoint_binding, make_checkpoint,
                                                   restore_checkpoint, restore_scan_cache)
    from trader.research.kucoin_portfolio import summarize
    if mode not in MODES or type(max_hours) is not int or max_hours < 1:
        raise ValueError('known replay mode and positive integer max_hours required')
    if any(type(t) is not int or t < 0 or t % HOUR_MS for t in (start_ms, end_ms)) or start_ms >= end_ms:
        raise ValueError('window must use increasing UTC hour boundaries')
    options, began = range_policy_parameters(parameters), perf_counter()
    binding = checkpoint_binding(identity, start_ms, end_ms, mode, options, seed, fixtures)
    report, control, cache = restore_checkpoint(checkpoint, binding) if checkpoint is not None else (None, None, None)
    if report is None:
        snapshot, report = _prepare_window(snapshot, start_ms, end_ms, mode, options)
    elif not control['complete']:
        snapshot, current = _prepare_window(snapshot, start_ms, end_ms, mode, options)
        if current['manifest'] != report['manifest']:
            raise ValueError('checkpoint snapshot manifest binding mismatch')
        restore_scan_cache(snapshot, report, cache)
    try:
        if control is None or not control['complete']:
            if report['coverage']['window_available']:
                control = _execute(snapshot, report, options, fixtures, seed, progress, control, max_hours)
            else:
                control = dict(cursor_ms=start_ms, cash=report['initial_capital'], allowed=None,
                               rng_state=random.Random(seed).getstate(), complete=True)
    except (ValueError, KeyError, TypeError) as error:
        _gap(report, str(error))
        report['performance'] = dict(elapsed_seconds=perf_counter()-began, cache_stats=dict(getattr(snapshot, 'stats', {})))
        return dict(complete=True, checkpoint=None, result=summarize(report))
    report['performance'] = dict(elapsed_seconds=perf_counter()-began, cache_stats=dict(getattr(snapshot, 'stats', {})))
    if control['complete'] and cache is not None:
        report.pop('_scan_cache', None)
    packet = make_checkpoint(report, control, binding)
    if checkpoint_callback is not None:
        checkpoint_callback(packet)
    return dict(complete=control['complete'], checkpoint=packet,
                result=summarize(report) if control['complete'] else None)
