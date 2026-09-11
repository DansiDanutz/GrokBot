"""Pure offline hourly orchestration. All fills are OHLC-model estimates.

The snapshot protocol reads detached observations supplied by the caller. No
exchange clients, databases, credentials, schedules or file writes live here.
"""
from dataclasses import asdict, replace
import random
import math

from trader.research.kucoin_radar import radar, normalize_market, crossing_score
from trader.research.kucoin_replacement import decide_replacement
from trader.research.kucoin_tracker import track_bars, track_summary
from trader.strategies.grid_features import features
from trader.strategies.grid_setup import build_setup
from trader.strategies.grid_calibration import fixture_config
from trader.strategies.kucoin_grid import create_bot, stop, net_equity, floating_pnl, FEE
from trader.strategies.grid_types import GridConfig

HOUR_MS = 3600000
DAY_MS = 24 * HOUR_MS
TERMINAL = ('stopped', 'liquidated')
MODES = ('system', 'four_observed_long_forms_unchanged',
         'same_four_symbols_system_setup', 'random_radar_identical_rules')


def window_coverage(snapshot, start, end):
    """Reject unavailable months before expensive minute/hour iteration."""
    lower, upper = snapshot.market_bounds()
    valid = lower is not None and upper is not None and lower <= start and end <= upper
    return dict(complete=valid, market_bounds=[lower, upper], reasons=[] if valid else
                ['historical ticker/book observations do not cover the requested window'])


def _report(snapshot, start, end, mode, parameters):
    return dict(mode=mode, start_ms=start, end_ms=end, parameters=parameters,
                initial_capital=2000., coverage=window_coverage(snapshot, start, end),
                manifest=dict(snapshot.manifest), bots=[], ledger=[], hourly_radar=[],
                hourly_tracker=[], switches=[], capital_events=[], equity_timeline=[],
                metrics={}, per_coin={}, direction_mix={},
                interpretation='counterfactual OHLC model; not observed KuCoin app history',
                limitations=['Adverse-first minute OHLC ordering cannot recover actual trades.',
                             'Critical-price marks share their model path vertex timestamp; the decision uses the conservative drawdown bound.',
                             'Open inventory is marked at the end; no terminal profit stop.',
                             'Outside-range duration uses minute-close sampling, not reconstructed intraminute residence.'])


def _config(form):
    return GridConfig(pair=form['pair'], low=form['low'], high=form['high'],
        grids=form['grids'], leverage=form['leverage'], direction=form['direction'],
        investment=form['used_margin'], reserved_margin=form.get('reserved_margin', 0),
        entry_price=form['entry'], quantity=form.get('quantity'),
        multiplier=form.get('multiplier', 1), lot_size=form.get('lot_size', 1),
        trigger=form.get('trigger'), stop_loss=form.get('stop_loss'),
        stop_loss_high=form.get('stop_loss_high'), tick_size=form.get('tick_size'))


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
    if not observed_form and abs(config.total_margin-1000) > 1e-9:
        raise ValueError('generated bot total used plus reserve must equal 1000 USDT')
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
    report['bots'].append(bot)
    _append_events(report, bot, [asdict(event) for event in state.fill_events])
    _mark(report, bot, at, net_equity(state, state.price))
    return cash-config.total_margin


def _scan(snapshot, report, at, options, allowed=None):
    records = snapshot.records(at, pairs=allowed)
    records = [dict(row, bars=row.get('bars', row.get('bars7days', []))) for row in records]
    result = radar(records, at, _pairs(report['bots']), {'setup': options})
    report['hourly_radar'].append(result)
    if not records:
        _gap(report, 'no historical market membership observations at ' + str(at))
    for row in result.get('rejected', []):
        reason = row['reason']
        if row.get('coverage_issue', False):
            _gap(report, row['pair'] + ': ' + reason)
    return result['radar'], records


def _gap(report, reason):
    report['coverage']['complete'] = False
    if reason not in report['coverage']['reasons']:
        report['coverage']['reasons'].append(reason)


def _price(snapshot, pair, at, fallback):
    market = snapshot.market(pair, at) or {}
    bid, ask = market.get('bid'), market.get('ask')
    return (bid+ask)/2 if bid is not None and ask is not None else fallback


def _fill_slots(snapshot, report, candidates, at, cash, rng, maximum=2):
    rows = list(candidates)
    if report['mode'] == 'random_radar_identical_rules':
        rng.shuffle(rows)
    for row in rows:
        if sum(bot['active'] for bot in report['bots']) >= maximum:
            break
        if row['pair'] in _pairs(report['bots']):
            continue
        cash = _open(report, row, at, cash, _price(snapshot, row['pair'], at, row['setup']['entry']))
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
    _require_quote(snapshot, state.config.pair, end)
    if len(bars) != (end-at)//60000:
        raise ValueError('missing completed minute candles for ' + state.config.pair)
    result = track_bars(state, bars, snapshot.funding(state.config.pair, at, end),
                       bot['start_ms'], end, bot['expected'])
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
    closest = summary.get('closest_liquidation_range_pct')
    if closest is not None:
        bot['closest'] = closest if bot['closest'] is None else min(bot['closest'], closest)
    return summary


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
        close_quote_observed_at_ms=quote.get('book_observed_at_ms', quote.get('observed_at_ms')))


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


def _hour_decisions(snapshot, report, end, options, cash, allowed, rng):
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
    for config in configs:
        form = dict(asdict(config), used_margin=config.investment, entry=config.entry_price)
        row = dict(pair=config.pair, setup=form, score=0)
        price = _price(snapshot, config.pair, start, config.entry_price)
        if not config.low <= price <= config.high:
            raise ValueError('baseline original inventory unavailable when start price is outside its fixed range')
        cash = _open(report, row, start, cash, price, config)
    return cash


def _track_hour(snapshot, report, at, end):
    for bot in report['bots']:
        if bot['active']:
            if bot['state'].status in TERMINAL:
                bot['latest'] = track_summary(bot['state'], bot['start_ms'], end, bot['expected'])
            else:
                bot['latest'] = _track(snapshot, report, bot, at, end)
            if bot['state'].liquidated:
                report['failed_liquidation'] = True
    return report.get('failed_liquidation', False)


def _execute(snapshot, report, options, fixtures, seed):
    start, end, mode = report['start_ms'], report['end_ms'], report['mode']
    rng, cash, allowed = random.Random(seed), report['initial_capital'], None
    actual = mode == 'four_observed_long_forms_unchanged'
    if mode == 'same_four_symbols_system_setup':
        report['limitations'].append('Rules baseline: two 1000-USDT slots restricted to the four observed symbols.')
        allowed = [row['symbol']+'M' if row['symbol'].endswith('USDT') else row['symbol']
                   for row in _fixture_rows(fixtures)]
    if actual:
        cash = _actual_start(snapshot, report, fixtures, start)
    for at in range(start, end, HOUR_MS):
        if not actual:
            candidates, _ = _scan(snapshot, report, at, options, allowed)
            cash = _fill_slots(snapshot, report, candidates, at, cash, rng)
        ending = min(at+HOUR_MS, end)
        if _track_hour(snapshot, report, at, ending):
            break
        if not actual and ending < end:
            cash = _hour_decisions(snapshot, report, ending, options, cash, allowed, rng)
        elif actual or ending == end:
            for bot in report['bots']:
                if bot['active']:
                    report['hourly_tracker'].append(dict(bot['latest'], bot_id=bot['bot_id']))
    report['ending_available_cash'] = cash + sum(net_equity(bot['state'], bot['state'].price)
        for bot in report['bots'] if bot['state'].status in TERMINAL and not bot.get('released'))


def run_window(snapshot, start_ms, end_ms, parameters=None, mode='system', fixtures=None, seed=20260911):
    """Run a causal [start,end) model with 2000 USDT and no cash injections."""
    if mode not in MODES:
        raise ValueError('unknown replay mode')
    if any(type(t) is not int or t < 0 or t % HOUR_MS for t in (start_ms, end_ms)) or start_ms >= end_ms:
        raise ValueError('window must use increasing UTC hour boundaries')
    options = dict(parameters or {})
    report = _report(snapshot, start_ms, end_ms, mode, options)
    if report['coverage']['complete']:
        try:
            _execute(snapshot, report, options, fixtures, seed)
        except (ValueError, KeyError, TypeError) as error:
            _gap(report, str(error))
    from trader.research.kucoin_portfolio import summarize
    return summarize(report)
