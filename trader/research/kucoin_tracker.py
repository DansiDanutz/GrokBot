"""Deterministic minute-bar paper tracking with explicit funding observations.

The intrabar convention visits the adverse extreme first for directional bots;
neutral uses its net inventory direction. OHLC cannot establish the true trade
sequence, so this is a conservative execution convention, not a guaranteed bound.
"""
from dataclasses import asdict, replace
import math
from trader.strategies.kucoin_grid import advance, floating_pnl, net_equity, preview
from trader.strategies.grid_types import Position

HOUR_MS = 3_600_000
MINUTE_MS = 60_000
FUNDING_MS = 8 * HOUR_MS
FEE = .0006
TERMINAL = ('stopped', 'liquidated')


def _positions(state):
    result = {}
    for name, side in [('long', 1), ('short', -1)]:
        lots = [position for position in state.positions if position.side == side]
        quantity = sum(position.quantity for position in lots)
        cost = sum(position.entry * position.quantity for position in lots)
        result[name] = dict(quantity=quantity, average_entry=cost/quantity if quantity else None)
    return result


def _liquidation(state):
    """Current inventory equity=maintenance solve; excludes future resting fills."""
    signed = sum(p.side*p.quantity for p in state.positions)
    gross = sum(p.quantity for p in state.positions)
    costs = sum(p.side*p.quantity*p.entry for p in state.positions)
    cash = net_equity(state, state.price) - floating_pnl(state, state.price)
    denominator = signed - gross * state.config.maintenance_rate
    price = (costs-cash) / denominator if denominator else None
    return price if price is not None and price > 0 else None


def _rates(state, start_ms, asof_ms):
    rates = {}
    for hours in (1, 6, 24):
        beginning = max(start_ms, asof_ms - hours * HOUR_MS)
        elapsed = (asof_ms-beginning) / HOUR_MS
        count = sum(beginning < time <= asof_ms for time in state.completion_times)
        rates['realized_gph_' + str(hours) + 'h'] = count / elapsed if elapsed > 0 else 0
    return rates


def _before_close(state):
    """Recover actual pre-close inventory/cash from immutable close fills."""
    if state.status not in TERMINAL:
        return state
    fills = [event for event in state.fill_events
             if event.kind in ('stop_loss', 'replacement', 'liquidation')]
    if not fills:
        return state
    positions = tuple(Position(event.slot, -event.side, event.quantity,
                      event.price + event.gross_pnl / (event.side * event.quantity))
                      for event in fills)
    return replace(state, positions=positions,
                   fees=state.fees-sum(event.fee for event in fills),
                   close_pnl=state.close_pnl-sum(event.gross_pnl for event in fills))


def _distances(state):
    state = _before_close(state)
    config, price = state.config, state.price
    liquidation = _liquidation(state)
    distance = abs(price-liquidation)/liquidation*100 if liquidation else None
    stops = [value for value in (config.stop_loss, config.stop_loss_high) if value is not None]
    nearest_stop = min(stops, key=lambda stop: abs(price-stop)) if stops else None
    return dict(distance_low_pct=(price-config.low)/config.low*100,
                distance_high_pct=(config.high-price)/config.high*100,
                distance_stop_loss_pct=abs(price-nearest_stop)/nearest_stop*100 if nearest_stop else None,
                distance_liquidation_pct=distance, liquidation_price=liquidation,
                liquidation_estimate=True,
                liquidation_scenario='current inventory; future resting fills excluded',
                distance_liquidation_range_pct=abs(price-liquidation)/(config.high-config.low)*100
                if liquidation else None)


def _hour_risk(summary, observations):
    for source, target in [('distance_liquidation_pct', 'closest_liquidation_pct'),
                           ('distance_liquidation_range_pct', 'closest_liquidation_range_pct')]:
        known = [row[source] for row in observations if row[source] is not None]
        summary[target] = 0 if summary['liquidated'] else (min(known) if known else None)
    closest = summary['closest_liquidation_pct']
    if closest is not None and closest <= 5:
        summary['emergency'] = True
        summary['emergency_action'] = 'stop or add reserve before liquidation'


def track_summary(state, start_ms, asof_ms, expected_start_gph=0):
    """Rates divide by observed bot lifetime when it is shorter than the window."""
    if start_ms > asof_ms or state.timestamp_ms > asof_ms:
        raise ValueError('summary start/state must not be later than asof')
    distances = _distances(state)
    emergency = distances['distance_liquidation_pct'] is not None and distances['distance_liquidation_pct'] <= 5
    closed_fills = [event for event in state.fill_events if event.kind in ('stop_loss', 'replacement', 'liquidation')]
    config = state.config
    return dict(pair=config.pair, direction=config.direction, low=config.low, high=config.high,
                price=state.price, asof_ms=asof_ms, start_ms=start_ms, status=state.status,
                expected_start_gph=expected_start_gph, **_rates(state, start_ms, asof_ms),
                completed_grids=state.completed_grids, positions=_positions(state),
                grid_profit=state.grid_profit, grid_net_profit=state.grid_net_profit,
                seed_pnl=state.seed_pnl, close_pnl=state.close_pnl, funding=state.funding,
                fees=state.fees, floating_pnl=floating_pnl(state, state.price),
                net_equity=net_equity(state, state.price), max_floating_loss=state.max_floating_loss,
                close_fee=sum(p.quantity*state.price*FEE for p in state.positions),
                switch_close_fee_paid=sum(event.fee for event in closed_fills),
                switch_close_gross_pnl=sum(event.gross_pnl for event in closed_fills),
                stop_loss_hit=state.stop_reason == 'stop_loss', stop_reason=state.stop_reason,
                stop_loss=config.stop_loss, stop_loss_high=config.stop_loss_high,
                liquidated=state.liquidated, emergency=emergency,
                emergency_action='stop or add reserve before liquidation' if emergency else None,
                emergency_action_suggested_only=True, **distances,
                full_inventory_preview=preview(config))


def _validated_bars(state, bars, asof_ms):
    cursor, result = state.timestamp_ms, []
    for source in bars:
        timestamp = source.get('timestamp_ms', source.get('time_ms'))
        if type(timestamp) is not int or timestamp % MINUTE_MS or timestamp != cursor:
            raise ValueError('completed minute bars must be contiguous with state; no gaps or duplicates')
        values = [source.get(key) for key in ('open', 'high', 'low', 'close')]
        if any(not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0 for value in values):
            raise ValueError('OHLC values must be positive and finite')
        opening, high, low, close = values
        if low > min(opening, close) or high < max(opening, close) or low > high:
            raise ValueError('invalid OHLC extremes')
        cursor += MINUTE_MS
        if asof_ms is not None and cursor > asof_ms:
            raise ValueError('incomplete minute candle')
        result.append(dict(source, timestamp_ms=timestamp))
    return result


def _funding_map(events):
    result = {}
    for event in events:
        timestamp = event.get('timestamp_ms', event.get('time_ms'))
        rate = event.get('rate')
        if type(timestamp) is not int or timestamp % FUNDING_MS:
            raise ValueError('funding events must identify UTC eight-hour settlements')
        if rate is None or not math.isfinite(rate):
            raise ValueError('actual finite funding rate required')
        if timestamp in result:
            raise ValueError('duplicate funding settlement')
        if event.get('observed_at_ms', timestamp) > timestamp:
            raise ValueError('funding observation must be available by settlement')
        result[timestamp] = rate
    return result


def _path(state, bar):
    timestamp = bar['timestamp_ms']
    long_first = state.config.direction == 'long'
    if state.config.direction == 'neutral':
        long_first = sum(p.side*p.quantity for p in state.positions) >= 0
    extremes = ('low', 'high') if long_first else ('high', 'low')
    return [(bar['open'], timestamp, True), (bar[extremes[0]], timestamp+20_000, False),
            (bar[extremes[1]], timestamp+40_000, False), (bar['close'], timestamp+60_000, False)]


def _advance_tick(state, price, timestamp, gap, funding, ledger, seen):
    boundary = timestamp // FUNDING_MS * FUNDING_MS
    rate = 0
    settled = boundary > state.last_funding_ms
    if settled:
        if boundary not in funding:
            raise ValueError('missing actual funding event at ' + str(boundary))
        rate = funding[boundary]
    prior_funding = state.funding
    state = advance(state, price, timestamp, funding_rate=rate, gap=gap)
    for event in state.fill_events:
        if event.event_id not in seen:
            ledger.append(asdict(event))
            seen.add(event.event_id)
    if settled and state.last_funding_ms == boundary:
        ledger.append(dict(event_id='funding:' + str(boundary), timestamp_ms=boundary,
                           kind='funding', rate=rate, cash=state.funding-prior_funding,
                           fee=0, completed_grid=False))
    return state


def track_bars(state, bars, funding_events=(), start_ms=None, asof_ms=None, expected_start_gph=0):
    """Return a new state and every fill; parent persists ledger keyed by bot/event ID.

    Initial seed events are included. The caller should pass original start_ms
    across hourly calls. Funding is required, including an explicitly observed 0.
    """
    start_ms = state.timestamp_ms if start_ms is None else start_ms
    validated = _validated_bars(state, bars, asof_ms)
    funding, ledger, seen = _funding_map(funding_events), [], set()
    distances = [_distances(state)]
    equity_path = list(state.equity_marks) if state.timestamp_ms == start_ms else []
    equity_timeline = [dict(timestamp_ms=state.timestamp_ms, equity=value, floating_pnl=floating)
                       for value, floating in zip(equity_path, state.floating_pnl_marks)]
    if state.timestamp_ms == start_ms:
        ledger = [asdict(event) for event in state.fill_events]
        seen = {event.event_id for event in state.fill_events}
    end_ms = state.timestamp_ms
    for bar in validated:
        end_ms = bar['timestamp_ms'] + MINUTE_MS
        for price, timestamp, gap in _path(state, bar):
            if state.status in TERMINAL:
                break
            state = _advance_tick(state, price, timestamp, gap, funding, ledger, seen)
            distances.append(_distances(state))
            equity_path.extend(state.equity_marks)
            equity_timeline.extend(dict(timestamp_ms=timestamp, equity=value, floating_pnl=floating)
                                   for value, floating in zip(state.equity_marks, state.floating_pnl_marks))
    summary = track_summary(state, start_ms, end_ms, expected_start_gph)
    _hour_risk(summary, distances)
    return dict(state=state, ledger=ledger, summary=summary, equity_path=equity_path,
                equity_timeline=equity_timeline,
                equity_timeline_time_basis='modeled OHLC vertex time; preserve mark order within timestamp',
                path_convention='adverse extreme first; deterministic OHLC approximation')
