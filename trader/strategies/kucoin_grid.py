"""Deterministic KuCoin-inspired paper grid, not exchange execution parity.

Directional intervals each own a position or resting order. Neutral has TWO
independent legs per interval, with 2*N orders: long slots 0..N-1 and short
slots N..2*N-1. Long seeds lie wholly above entry and short seeds wholly below
entry; both legs in a straddling interval start with opening limit orders.
This reproduces the observed RAY 70-grid, 32-long/37-short seed split at
entry 1.574 and tick-truncated step .0128. Seed closes are not arbitrages.

Accounting components are GROSS; equity subtracts total fees exactly once.
Funding is a signed cash flow. Grid net profit allocates both fill fees for
reporting, but is not added again to equity. Supplied funding rates are applied
at crossed UTC 8-hour boundaries using the latest supplied mark; callers must
split paths at every funding boundary. At a boundary, price-path fills precede
funding settlement; only inventory still held then is charged or credited.
There is no take-profit. Both sides hard-stop at 5% beyond the configured range
by default, independent of direction; a nearer custom stop wins. Historical
unchanged baselines can explicitly disable this new rule with None.
Reserve is collateral, never additional order notional.
Default sizing is used margin times leverage divided by grids times entry,
floored to contract lots; this is a paper allocation assumption, not a published
KuCoin sizing equation. Neutral divides that sizing budget across two legs.
All fill events are immutable and ephemeral per advance; consumers persist
and deduplicate by bot identifier plus monotonically increasing event_id.
A stopped gap closes existing inventory at the observed quote with no assumed
intervening grid fills (stop-priority scenario); exchange sequencing is unknown.
"""
from dataclasses import replace
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING
import math

from .grid_types import FillEvent, GridConfig, GridState, Order, Position

FEE = .0006
FUNDING_MS = 8 * 60 * 60 * 1000


def _finite(value, name, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{name} must be a finite number')
    if not math.isfinite(value) or (positive and value <= 0):
        raise ValueError(f'{name} must be a finite positive number')


def _validate(config):
    for name in ('low', 'high', 'investment', 'leverage', 'multiplier', 'lot_size'):
        _finite(getattr(config, name), name, True)
    if config.low >= config.high or not 1 <= config.leverage <= 10:
        raise ValueError('invalid range or leverage')
    if type(config.grids) is not int or not 2 <= config.grids <= 1000:
        raise ValueError('grids must be an integer from 2 to 1000')
    if config.direction not in ('long', 'short', 'neutral') or not config.pair:
        raise ValueError('invalid pair or direction')
    _finite(config.maintenance_rate, 'maintenance_rate')
    if not 0 <= config.maintenance_rate < 1 / config.leverage:
        raise ValueError('invalid maintenance rate')
    if config.trigger is not None:
        _finite(config.trigger, 'trigger', True)
        if not config.low <= config.trigger <= config.high:
            raise ValueError('trigger must be inside range')
    _validate_form(config)
    if config.quantity is not None:
        _finite(config.quantity, 'quantity', True)
        units = config.quantity / (config.multiplier * config.lot_size)
        if not math.isclose(units, round(units), rel_tol=1e-12, abs_tol=1e-9):
            raise ValueError('quantity must be a contract lot multiple')
        centre = _entry_reference(config)
        if config.quantity * config.grids * centre * (2 if config.direction == 'neutral' else 1) > config.investment * config.leverage * (1 + 1e-12):
            raise ValueError('quantity exceeds reference notional allocation')


def _entry_reference(config):
    if config.entry_price is not None:
        return config.entry_price
    return config.trigger if config.trigger is not None else (config.low + config.high) / 2


def _validate_form(config):
    if config.range_exit_stop_pct is not None:
        _finite(config.range_exit_stop_pct, 'range_exit_stop_pct')
        if not 0 < config.range_exit_stop_pct < 1:
            raise ValueError('range_exit_stop_pct must be between zero and one')
    if config.tick_size is not None:
        _finite(config.tick_size, 'tick_size', True)
        if config.tick_size > (config.high - config.low) / config.grids:
            raise ValueError('grid interval is smaller than one price tick')
    _finite(config.reserved_margin, 'reserved_margin')
    if config.reserved_margin < 0:
        raise ValueError('reserved_margin must be nonnegative')
    if config.entry_price is not None:
        _finite(config.entry_price, 'entry_price', True)
        if not config.low <= config.entry_price <= config.high:
            raise ValueError('entry_price must be inside range')
    for name in ('stop_loss', 'stop_loss_high'):
        value = getattr(config, name)
        if value is not None:
            _finite(value, name, True)
    if config.direction == 'neutral':
        if (config.stop_loss is None) != (config.stop_loss_high is None):
            raise ValueError('neutral stop-loss requires lower and upper prices')
        valid = (config.stop_loss is None or
                 config.stop_loss < config.low < config.high < config.stop_loss_high)
    else:
        valid = config.stop_loss_high is None and (config.stop_loss is None or
                 (config.stop_loss < config.low if config.direction == 'long'
                  else config.stop_loss > config.high))
    if not valid:
        raise ValueError('stop-loss must be outside range on losing side')


def _event(state, slot, side, quantity, price, timestamp_ms, kind,
           gross_pnl=0, completed_grid=False):
    event = FillEvent(state.fill_sequence + 1, timestamp_ms, slot, side,
                      quantity, price, quantity * price * FEE, kind,
                      gross_pnl, completed_grid)
    return replace(state, fill_sequence=event.event_id,
                   fill_events=state.fill_events + (event,))


def _quantity(config):
    if config.quantity is not None:
        return config.quantity
    centre = _entry_reference(config)
    raw = config.investment * config.leverage / (config.grids * centre)
    raw /= 2 if config.direction == 'neutral' else 1
    unit = config.multiplier * config.lot_size
    quantity = math.floor(raw / unit + 1e-12) * unit
    if quantity <= 0:
        raise ValueError('investment cannot fund one contract lot per grid')
    return quantity


def _levels(config):
    low, high = Decimal(str(config.low)), Decimal(str(config.high))
    step = (high - low) / config.grids
    if config.tick_size is not None:
        tick = Decimal(str(config.tick_size))
        step = (step / tick).to_integral_value(rounding=ROUND_FLOOR) * tick
    return tuple(float(low + step * i) for i in range(config.grids + 1))


def _start(state):
    config, price = state.config, state.price
    levels, quantity = _levels(config), _quantity(config)
    directions = (1, -1) if config.direction == 'neutral' else (
        (1,) if config.direction == 'long' else (-1,))
    positions, orders = [], []
    for leg, side in enumerate(directions):
        for interval in range(config.grids):
            slot = leg * config.grids + interval
            seeded = _seed_slot(config, levels, interval, side, price)
            if seeded:
                positions.append(Position(slot, side, quantity, price, True))
            boundary = interval + (1 if side == 1 and seeded or side == -1 and not seeded else 0)
            orders.append(Order(slot, -side if seeded else side, levels[boundary], seeded))
    fees = sum(p.quantity * price * FEE for p in positions)
    for position in positions:
        state = _event(state, position.slot, position.side, position.quantity,
                       price, state.timestamp_ms, 'seed')
    return replace(state, positions=tuple(positions), orders=tuple(orders),
                   fees=state.fees + fees, status='running')


def _seed_slot(config, levels, interval, side, price):
    if config.direction == 'neutral':
        return levels[interval] >= price if side == 1 else levels[interval + 1] <= price
    return levels[interval + 1] > price if side == 1 else levels[interval] < price


def create_bot(config, price, timestamp_ms):
    """Create a waiting bot or seed immediately; does not read any data source."""
    _validate(config)
    _validate_tick(price, timestamp_ms)
    if config.entry_price is None:
        config = replace(config, entry_price=config.trigger or
                         (price if config.low <= price <= config.high else _entry_reference(config)))
    state = GridState(config, price, timestamp_ms,
                      last_funding_ms=timestamp_ms // FUNDING_MS * FUNDING_MS)
    if config.trigger is not None and price != config.trigger:
        return state
    if not config.low <= price <= config.high:
        return replace(state, status='out_of_range')
    return _start(state)


def _validate_tick(price, timestamp_ms):
    _finite(price, 'price', True)
    if type(timestamp_ms) is not int or timestamp_ms < 0:
        raise ValueError('timestamp must be a nonnegative integer in milliseconds')


def floating_pnl(state, price):
    _finite(price, 'price', True)
    return sum(p.side * p.quantity * (price - p.entry) for p in state.positions)


def _track_floating_loss(state, price):
    loss = max(state.max_floating_loss, -floating_pnl(state, price), 0)
    return replace(state, max_floating_loss=loss,
                   equity_marks=state.equity_marks + (net_equity(state, price),),
                   floating_pnl_marks=state.floating_pnl_marks + (floating_pnl(state, price),))


def net_equity(state, price):
    return (state.config.total_margin + state.grid_profit + state.seed_pnl
            + state.close_pnl - state.fees + state.funding + floating_pnl(state, price))


def _fund(state, price, timestamp_ms, rate):
    _finite(rate, 'funding_rate')
    latest = timestamp_ms // FUNDING_MS * FUNDING_MS
    settlements = max(0, (latest - state.last_funding_ms) // FUNDING_MS)
    cash = -sum(p.side * p.quantity * price for p in state.positions) * rate * settlements
    return replace(state, funding=state.funding + cash, last_funding_ms=latest)


def _crossed(order, old, new):
    if new > old:
        return order.side == -1 and old <= order.price <= new
    if new < old:
        return order.side == 1 and new <= order.price <= old
    return False


def _fill(state, order, timestamp_ms):
    positions = {p.slot: p for p in state.positions}
    quantity, levels = _quantity(state.config), _levels(state.config)
    interval = order.slot % state.config.grids
    changes = {'fees': state.fees + quantity * order.price * FEE}
    if order.closing:
        position = positions.pop(order.slot)
        gross = position.side * quantity * (order.price - position.entry)
        key = 'seed_pnl' if position.seed else 'grid_profit'
        changes[key] = getattr(state, key) + gross
        if not position.seed:
            net = gross - quantity * (position.entry + order.price) * FEE
            changes.update(completed_grids=state.completed_grids + 1,
                           grid_net_profit=state.grid_net_profit + net,
                           completion_times=state.completion_times + (timestamp_ms,))
        side = position.side
        replacement = Order(order.slot, side, levels[interval + (side == -1)], False)
    else:
        side = order.side
        positions[order.slot] = Position(order.slot, side, quantity, order.price)
        replacement = Order(order.slot, -side, levels[interval + (side == 1)], True)
    state = _event(state, order.slot, order.side, quantity, order.price, timestamp_ms,
                   'seed_close' if order.closing and position.seed else
                   'grid_close' if order.closing else 'grid_open',
                   gross if order.closing else 0,
                   order.closing and not position.seed)
    orders = tuple(replacement if o.slot == order.slot else o for o in state.orders)
    return replace(state, positions=tuple(positions.values()), orders=orders, **changes)


def _liquidation_due(state, price):
    gross = sum(p.quantity * price for p in state.positions)
    return bool(state.positions) and net_equity(state, price) <= gross * state.config.maintenance_rate


def advance(state, price, timestamp_ms, funding_rate=0, bid=None, ask=None, gap=False):
    """Process a monotonic price segment; caller chooses the intrabar path.

    equity_marks contains only this call's chronological critical-price marks,
    including the before/after cash effect of fills, funding and liquidation.
    It is reset for every advance rather than retaining a growing price history.
    """
    _validate_tick(price, timestamp_ms)
    if timestamp_ms < state.timestamp_ms:
        raise ValueError('timestamps must not move backward')
    _finite(funding_rate, 'funding_rate')
    if state.status in ('stopped', 'liquidated'):
        return replace(state, fill_events=(), equity_marks=(), floating_pnl_marks=())
    _validate_funding_path(state, price, timestamp_ms, funding_rate)
    state = replace(state, equity_marks=(), floating_pnl_marks=(), fill_events=())
    state = _track_floating_loss(state, state.price)
    if state.status == 'waiting':
        trigger = state.config.trigger
        if trigger is not None and min(state.price, price) <= trigger <= max(state.price, price):
            activated = _start(replace(state, price=trigger, timestamp_ms=timestamp_ms,
                                     last_funding_ms=timestamp_ms // FUNDING_MS * FUNDING_MS))
            advanced = advance(activated, price, timestamp_ms, funding_rate, bid, ask, gap)
            return replace(advanced, equity_marks=state.equity_marks + advanced.equity_marks,
                           fill_events=activated.fill_events + advanced.fill_events,
                           floating_pnl_marks=state.floating_pnl_marks + advanced.floating_pnl_marks)
        return replace(state, price=price, timestamp_ms=timestamp_ms)
    stop_price = _stop_crossing(state, price)
    if gap and stop_price is not None:
        reason = 'liquidation' if _liquidation_due(state, price) else 'stop_loss'
        return stop(state, price, timestamp_ms, bid, ask, reason, _preserve_events=True)
    endpoint = price if stop_price is None else stop_price
    scaled_bid = None if bid is None else bid * endpoint / price
    scaled_ask = None if ask is None else ask * endpoint / price
    moved = _advance_orders(state, endpoint, timestamp_ms, scaled_bid, scaled_ask)
    if stop_price is not None and not moved.liquidated:
        return stop(moved, endpoint, timestamp_ms, scaled_bid, scaled_ask,
                    'stop_loss', _preserve_events=True)
    if moved.liquidated:
        return moved
    funded = _fund(moved, price, timestamp_ms, funding_rate)
    funded = _track_floating_loss(funded, price)
    if _liquidation_due(funded, price):
        return _liquidate_at(funded, price, price, timestamp_ms, bid, ask)
    return funded


def _validate_funding_path(state, price, timestamp_ms, rate):
    boundary = (state.timestamp_ms // FUNDING_MS + 1) * FUNDING_MS
    if rate and price != state.price and boundary < timestamp_ms:
        raise ValueError('split moving price paths at each UTC 8-hour funding boundary')


def _stop_prices(config):
    low = config.stop_loss if config.direction != 'short' else None
    high = config.stop_loss_high if config.direction == 'neutral' else (
        config.stop_loss if config.direction == 'short' else None)
    range_low, range_high = None, None
    if config.range_exit_stop_pct is not None:
        fraction = Decimal(str(config.range_exit_stop_pct))
        range_low = float(Decimal(str(config.low)) * (1 - fraction))
        range_high = float(Decimal(str(config.high)) * (1 + fraction))
        effective_low, effective_high = range_low, range_high
        if config.tick_size is not None:
            tick = Decimal(str(config.tick_size))
            effective_low = float((Decimal(str(range_low)) / tick).to_integral_value(rounding=ROUND_CEILING) * tick)
            effective_high = float((Decimal(str(range_high)) / tick).to_integral_value(rounding=ROUND_FLOOR) * tick)
        low = effective_low if low is None else max(low, effective_low)
        high = effective_high if high is None else min(high, effective_high)
    return {'range_exit_stop_low': range_low, 'range_exit_stop_high': range_high,
            'effective_stop_loss_low': low, 'effective_stop_loss_high': high}


def _stop_crossing(state, price):
    stops = _stop_prices(state.config)
    low, high = stops['effective_stop_loss_low'], stops['effective_stop_loss_high']
    if low is not None and price <= low:
        return low if state.price > low else state.price
    if high is not None and price >= high:
        return high if state.price < high else state.price
    return None


def _liquidation_between(state, start, end):
    if not state.positions:
        return None
    gross_quantity = sum(p.quantity for p in state.positions)
    slope = sum(p.side * p.quantity for p in state.positions)
    slope -= gross_quantity * state.config.maintenance_rate
    constant = (state.config.total_margin + state.grid_profit + state.seed_pnl
                + state.close_pnl - state.fees + state.funding
                - sum(p.side * p.quantity * p.entry for p in state.positions))
    if constant + slope * start <= 0:
        return start
    if constant + slope * end > 0 or slope == 0:
        return None
    return -constant / slope


def _liquidate_at(state, price, quoted_price, timestamp_ms, bid, ask):
    # Preserve the supplied proportional spread at the interpolated threshold.
    adjusted_bid = None if bid is None else bid * price / quoted_price
    adjusted_ask = None if ask is None else ask * price / quoted_price
    state = _track_floating_loss(state, price)
    return stop(state, price, timestamp_ms, adjusted_bid, adjusted_ask, 'liquidation',
                _preserve_events=True)


def _advance_orders(state, price, timestamp_ms, bid, ask):
    config = state.config
    inside = config.low <= price <= config.high
    old = state.price
    endpoint = max(config.low, min(config.high, price))
    orders = sorted(
        (o for o in state.orders if _crossed(o, old, endpoint)),
        key=lambda o: o.price, reverse=endpoint < old)
    cursor = old
    for order in orders:
        liquidation = _liquidation_between(state, cursor, order.price)
        if liquidation is not None:
            return _liquidate_at(state, liquidation, price, timestamp_ms, bid, ask)
        state = _track_floating_loss(state, order.price)
        state = _fill(state, order, timestamp_ms)
        state = _track_floating_loss(state, order.price)
        cursor = order.price
    liquidation = _liquidation_between(state, cursor, price)
    if liquidation is not None:
        return _liquidate_at(state, liquidation, price, timestamp_ms, bid, ask)
    state = _track_floating_loss(state, price)
    exits = state.range_exits + int(not inside and state.status != 'out_of_range')
    return replace(state, price=price, timestamp_ms=timestamp_ms,
                   orders=state.orders, range_exits=exits,
                   status='running' if inside else 'out_of_range')


def stop(state, price, timestamp_ms, bid=None, ask=None, reason='replacement',
         _preserve_events=False):
    """Market-close all lots; bid/ask includes spread, fees stay explicit."""
    _validate_tick(price, timestamp_ms)
    if timestamp_ms < state.timestamp_ms:
        raise ValueError('timestamps must not move backward')
    if state.status in ('stopped', 'liquidated'):
        return state
    if not _preserve_events:
        state = replace(state, fill_events=(), equity_marks=(), floating_pnl_marks=())
    bid, ask = price if bid is None else bid, price if ask is None else ask
    _finite(bid, 'bid', True)
    _finite(ask, 'ask', True)
    if bid > ask:
        raise ValueError('bid must not exceed ask')
    state = _track_floating_loss(state, price)
    gross, fees = 0, 0
    for position in state.positions:
        fill = bid if position.side == 1 else ask
        gross += position.side * position.quantity * (fill - position.entry)
        fees += position.quantity * fill * FEE
        state = _event(state, position.slot, -position.side, position.quantity,
                       fill, timestamp_ms, reason,
                       position.side * position.quantity * (fill - position.entry))
    closed = replace(state, positions=(), orders=(), price=price, timestamp_ms=timestamp_ms,
                   close_pnl=state.close_pnl + gross, fees=state.fees + fees,
                   status='liquidated' if reason == 'liquidation' else 'stopped',
                   liquidated=reason == 'liquidation', stop_reason=reason)
    return _track_floating_loss(closed, price)


def _full_inventory_liquidation(config, direction):
    centre = _entry_reference(config)
    directional = replace(config, direction=direction, trigger=None, quantity=_quantity(config))
    state = _start(GridState(directional, centre, 0))
    positions = {p.slot: p for p in state.positions}
    for order in state.orders:
        if not order.closing:
            positions[order.slot] = Position(order.slot, order.side, _quantity(config), order.price)
    side = 1 if direction == 'long' else -1
    quantity = sum(p.quantity for p in positions.values())
    cost = sum(p.quantity * p.entry for p in positions.values())
    opening_fees = cost * FEE
    threshold = (side * cost + opening_fees - config.total_margin)
    threshold /= quantity * (side - config.maintenance_rate)
    return threshold if threshold > 0 else None


def preview(config):
    """Full directional-inventory scenarios, with fixed estimated maintenance.

    Each scenario seeds its initial closing-side slots at the configured entry and
    fills all remaining entries at their grid prices. Available equity is the
    investment minus all opening fees. These directional stress scenarios are
    not an exchange risk-tier calculation or a neutral bot liquidation promise.
    """
    _validate(config)
    levels, quantity = _levels(config), _quantity(config)
    profits = [quantity * (b - a - FEE * (a + b)) for a, b in zip(levels, levels[1:])]
    return {**_stop_prices(config), 'profit_per_grid_min': min(profits), 'profit_per_grid': sum(profits) / len(profits), 'profit_per_grid_max': max(profits),
            'order_count': config.grids * (2 if config.direction == 'neutral' else 1), 'quantity': quantity,
            'grid_step': levels[1] - levels[0], 'actual_grid_high': levels[-1],
            'kucoin_profit_pct_min': margin_profit_per_grid(config, config.high)['percent'],
            'kucoin_profit_pct_max': margin_profit_per_grid(config, config.low)['percent'],
            'kucoin_profit_usdt_min': margin_profit_per_grid(config, config.high)['usdt'],
            'kucoin_profit_usdt_max': margin_profit_per_grid(config, config.low)['usdt'],
            'liquidation_price_long': _full_inventory_liquidation(config, 'long'),
            'liquidation_price_short': _full_inventory_liquidation(config, 'short'),
            'liquidation_estimate': True,
            'liquidation_scenario': 'all_directional_grid_entries_filled'}


def margin_profit_per_grid(config, price):
    """User-specified KuCoin leveraged-margin approximation, not fill PnL.

    Percentage=(step/price*leverage-2*fee*leverage)*100; USDT uses used
    margin/grids. Constant-base-quantity fills and neutral split inventory have
    different economics, reported separately as profit_per_grid_min/max.
    """
    _validate(config)
    _finite(price, 'price', True)
    levels = _levels(config)
    fraction = ((levels[1] - levels[0]) / price - 2 * FEE) * config.leverage
    return {'percent': fraction * 100, 'usdt': config.investment / config.grids * fraction}
