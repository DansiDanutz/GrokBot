"""Pure operator reports reconstructed from explicitly supplied offline snapshots.

Every trade is modeled. This report deliberately cannot authorize live use:
fixture calibration, volatility validation and preregistered holdouts are gates.
"""
import math
import re
from dataclasses import asdict
from trader.research.kucoin_radar import radar, normalize_market, crossing_score
from trader.research.kucoin_tracker import track_bars, track_summary
from trader.research.kucoin_replacement import decide_replacement
from trader.research.kucoin_replay import close_cost_summary
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


def _config(bot):
    return GridConfig(pair=bot['pair'], direction=bot['direction'],
        leverage=bot['leverage'], investment=bot['used_margin'],
        reserved_margin=bot['reserved_margin'], entry_price=bot['entry'],
        low=bot['low'], high=bot['high'], grids=bot['grids'],
        stop_loss=bot['stop_loss'], stop_loss_high=bot.get('stop_loss_high'),
        trigger=bot.get('trigger'), quantity=bot.get('quantity'),
        multiplier=bot.get('multiplier', 1), lot_size=bot.get('lot_size', 1),
        tick_size=bot.get('tick_size'))


def _validate_bot(bot, asof_ms):
    if not isinstance(bot, dict) or not REQUIRED <= bot.keys() or bot.keys()-REQUIRED-OPTIONAL:
        raise ValueError('running bot has missing or unknown form fields; credentials are not accepted')
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
    if not 3 <= bot['leverage'] <= 6:
        raise ValueError('running leverage must be between 3x and 6x')
    if abs(bot['used_margin']+bot['reserved_margin']-1000) > 1e-9:
        raise ValueError('used margin plus reserve must equal 1000 USDT')
    create_bot(_config(bot), bot['entry'], started)


def validate_running(document, asof_ms):
    """Strict public form schema; unknown fields are rejected before data access."""
    if type(asof_ms) is not int or asof_ms < 0 or asof_ms % HOUR_MS:
        raise ValueError('asof must be a UTC hour boundary in milliseconds')
    if not isinstance(document, dict) or set(document) != {'schema_version', 'bots'}:
        raise ValueError('running document requires only schema_version and bots')
    if document['schema_version'] != 1 or not isinstance(document['bots'], list):
        raise ValueError('running document must use schema_version 1 and a bots list')
    if len(document['bots']) > 2:
        raise ValueError('at most two running bot forms are supported')
    for bot in document['bots']:
        _validate_bot(bot, asof_ms)
    for field in ('bot_id', 'pair'):
        if len({bot[field] for bot in document['bots']}) != len(document['bots']):
            raise ValueError('duplicate running ' + field)
    return document['bots']


def _direction(snapshot, bot, at, parameters):
    records = snapshot.records(at, pairs=[bot['pair']])
    if not records:
        return dict(known=False, flipped=False, candidate=None, reason='direction observations unavailable')
    record = records[0]
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
        result = track_bars(state, candles, snapshot.funding(bot['pair'], at, ending),
                            bot['start_ms'], ending, bot['expected_start_gph'])
    direction = _direction(snapshot, bot, ending, parameters)
    summary = dict(result['summary'], direction_flip=direction['flipped'],
                   direction_flip_known=direction['known'], direction_reason=direction['reason'])
    return result['state'], result['ledger'], summary, direction['candidate']


def _verdict(state, summary, candidates, history, parameters, flip, quote):
    decision = close_cost_summary(state, summary, quote) if quote else summary
    result = decide_replacement(decision, candidates, history,
                                 dict(parameters, direction_flip_candidate=flip))
    if quote:
        fields = ('mark_floating_pnl', 'close_spread_cost', 'close_cost_basis',
                  'close_quote_bid', 'close_quote_ask', 'close_quote_observed_at_ms')
        result['switch_cost'].update({key: decision[key] for key in fields})
        result['switch_cost']['quote_observation_age_ms'] = quote['quote_observation_age_ms']
        result['switch_cost']['quote_recording_latency_ms'] = quote['quote_recording_latency_ms']
    return result


def _track_bot(snapshot, bot, asof, candidates, pairs, parameters):
    state, at = create_bot(_config(bot), bot['entry'], bot['start_ms']), bot['start_ms']
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
        history.append(summary)
        at = ending
    summary = dict(history[-1] if history else
                   track_summary(state, bot['start_ms'], asof, bot['expected_start_gph']),
                   running_pairs=pairs)
    quote = _quote(snapshot, bot['pair'], asof) if state.status not in TERMINAL else None
    verdict = _verdict(state, summary, candidates, history[:-1], parameters, flip, quote)
    modeled_close = state.stop_reason in ('stop_loss', 'liquidation')
    return dict(bot_id=bot['bot_id'], form=bot, preview=preview(_config(bot)),
                tracker=summary, verdict=verdict, ledger=ledger, hourly_history=history,
                execution_cost_coverage=dict(complete=not modeled_close,
                    reason='intrabar bid/ask unavailable; modeled mark-price emergency close' if modeled_close
                    else 'hourly close estimates use historical bid/ask; no live fills asserted'))


def operator_report(snapshot, asof_ms, running_document, parameters=None):
    """Rebuild each running bot from its declared start, preserving hourly history."""
    bots = validate_running(running_document, asof_ms)
    parameters = dict(parameters or {})
    pairs = [bot['pair'] for bot in bots]
    reader = getattr(snapshot, 'iter_records', snapshot.records)
    scan = radar(reader(asof_ms), asof_ms, pairs, {'setup': parameters})
    tracked = [_track_bot(snapshot, bot, asof_ms, scan['radar'], pairs, parameters) for bot in bots]
    return dict(schema_version=1, asof_ms=asof_ms,
        interpretation='offline counterfactual OHLC research; fills are modeled, not KuCoin account trades',
        live_use=dict(actionable=False, status='blocked_calibration_and_holdout_validation',
                      reason='All four bot calibration, volatility fixture validation and two disjoint monthly holdouts must pass.'),
        radar=scan, recommended_forms=[row['setup'] for row in scan['radar'][:2]],
        running=tracked,
        coverage=dict(universe_observed=scan.get('coverage', {}).get('observed', 0),
                      minute_candles='complete reconstruction for supplied running forms',
                      execution_costs_complete=all(bot['execution_cost_coverage']['complete'] for bot in tracked)),
        refresh_radar_after_switch=True,
        instructions='Research proposals only. This command never starts, stops or replaces an exchange bot.')
