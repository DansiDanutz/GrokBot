"""Deterministic KuCoin-inspired paper grid, not exchange execution parity.

Every interval owns one base-quantity position or one resting order. Neutral
seeds the nearest floor(grids/4) upper slots long and lower slots short,
approximately 25% long and 25% short gross exposure. Remaining upper slots
await short entries above the starting price; remaining lower slots await long
entries below it. All initial orders are resting on the correct side of price. Directional bots seed their closing-side slots (about
50% when starting centrally). Seed closures never count as completed grids.

Accounting components are GROSS; equity subtracts total fees exactly once.
Funding is a signed cash flow. Grid net profit allocates both fill fees for
reporting, but is not added again to equity. Supplied funding rates are applied
at crossed UTC 8-hour boundaries using the latest supplied mark; callers must
split paths at every funding boundary. At a boundary, price-path fills precede
funding settlement; only inventory still held then is charged or credited. There is no take-profit.
"""
from dataclasses import replace
import math

from .grid_types import GridConfig, GridState, Order, Position

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
    if config.quantity is not None:
        _finite(config.quantity, 'quantity', True)
        units = config.quantity / (config.multiplier * config.lot_size)
        if not math.isclose(units, round(units), rel_tol=1e-12, abs_tol=1e-9):
            raise ValueError('quantity must be a contract lot multiple')
        centre = (config.low + config.high) / 2
        if config.quantity * config.grids * centre > config.investment * config.leverage * (1 + 1e-12):
            raise ValueError('quantity exceeds reference notional allocation')


def _quantity(config):
    if config.quantity is not None:
        return config.quantity
    centre = (config.low + config.high) / 2
    raw = config.investment * config.leverage / (config.grids * centre)
    unit = config.multiplier * config.lot_size
    quantity = math.floor(raw / unit + 1e-12) * unit
    if quantity <= 0:
        raise ValueError('investment cannot fund one contract lot per grid')
    return quantity


def _levels(config):
    step = (config.high - config.low) / config.grids
    return tuple(config.low + step * i for i in range(config.grids + 1))


def _start(state):
    config, price = state.config, state.price
    levels, quantity = _levels(config), _quantity(config)
    sides = _slot_sides(config, levels, price)
    eligible = [i for i, side in enumerate(sides)
                if (side == 1 and levels[i + 1] > price)
                or (side == -1 and levels[i] < price)]
    if config.direction == 'neutral':
        eligible = _neutral_seeds(eligible, sides, levels, price, config.grids)
        sides = [side if i in eligible else -side for i, side in enumerate(sides)]
    positions, orders = [], []
    for slot, side in enumerate(sides):
        seeded = slot in eligible
        if seeded:
            positions.append(Position(slot, side, quantity, price, True))
        boundary = slot + (1 if side == 1 and seeded or side == -1 and not seeded else 0)
        orders.append(Order(slot, -side if seeded else side, levels[boundary], seeded))
    fees = sum(p.quantity * price * FEE for p in positions)
    return replace(state, positions=tuple(positions), orders=tuple(orders),
                   fees=state.fees + fees, status='running')


def _slot_sides(config, levels, price):
    if config.direction != 'neutral':
        return [1 if config.direction == 'long' else -1] * config.grids
    return [1 if (levels[i] + levels[i + 1]) / 2 >= price else -1
            for i in range(config.grids)]


def _neutral_seeds(eligible, sides, levels, price, count):
    selected = []
    for side in (1, -1):
        slots = sorted((i for i in eligible if sides[i] == side),
                       key=lambda i: abs((levels[i] + levels[i + 1]) / 2 - price))
        selected.extend(slots[:count // 4])
    return selected


def create_bot(config, price, timestamp_ms):
    """Create a waiting bot or seed immediately; does not read any data source."""
    _validate(config)
    _validate_tick(price, timestamp_ms)
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
                   equity_marks=state.equity_marks + (net_equity(state, price),))


def net_equity(state, price):
    return (state.config.investment + state.grid_profit + state.seed_pnl
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
        replacement = Order(order.slot, side, levels[order.slot + (side == -1)], False)
    else:
        side = order.side
        positions[order.slot] = Position(order.slot, side, quantity, order.price)
        replacement = Order(order.slot, -side, levels[order.slot + (side == 1)], True)
    orders = tuple(replacement if o.slot == order.slot else o for o in state.orders)
    return replace(state, positions=tuple(positions.values()), orders=orders, **changes)


def _liquidation_due(state, price):
    gross = sum(p.quantity * price for p in state.positions)
    return bool(state.positions) and net_equity(state, price) <= gross * state.config.maintenance_rate


def advance(state, price, timestamp_ms, funding_rate=0, bid=None, ask=None):
    """Process a monotonic price segment; caller chooses the intrabar path.

    equity_marks contains only this call's chronological critical-price marks,
    including the before/after cash effect of fills, funding and liquidation.
    It is reset for every advance rather than retaining a growing price history.
    """
    _validate_tick(price, timestamp_ms)
    if timestamp_ms < state.timestamp_ms:
        raise ValueError('timestamps must not move backward')
    state = replace(state, equity_marks=())
    if state.status in ('stopped', 'liquidated'):
        return state
    state = _track_floating_loss(state, state.price)
    if state.status == 'waiting':
        trigger = state.config.trigger
        if trigger is not None and min(state.price, price) <= trigger <= max(state.price, price):
            activated = _start(replace(state, price=trigger, timestamp_ms=timestamp_ms,
                                     last_funding_ms=timestamp_ms // FUNDING_MS * FUNDING_MS))
            advanced = advance(activated, price, timestamp_ms, funding_rate, bid, ask)
            return replace(advanced, equity_marks=state.equity_marks + advanced.equity_marks)
        return replace(state, price=price, timestamp_ms=timestamp_ms)
    moved = _advance_orders(state, price, timestamp_ms, bid, ask)
    if moved.liquidated:
        return moved
    funded = _fund(moved, price, timestamp_ms, funding_rate)
    funded = _track_floating_loss(funded, price)
    if _liquidation_due(funded, price):
        return _liquidate_at(funded, price, price, timestamp_ms, bid, ask)
    return funded


def _liquidation_between(state, start, end):
    if not state.positions:
        return None
    gross_quantity = sum(p.quantity for p in state.positions)
    slope = sum(p.side * p.quantity for p in state.positions)
    slope -= gross_quantity * state.config.maintenance_rate
    constant = (state.config.investment + state.grid_profit + state.seed_pnl
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
    return stop(state, price, timestamp_ms, adjusted_bid, adjusted_ask, 'liquidation')


def _advance_orders(state, price, timestamp_ms, bid, ask):
    config = state.config
    inside = config.low <= price <= config.high and state.status != 'out_of_range'
    old = state.price
    endpoint = max(config.low, min(config.high, price))
    orders = [] if state.status == 'out_of_range' else sorted(
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
                   orders=state.orders if inside else (), range_exits=exits,
                   status='running' if inside else 'out_of_range')


def stop(state, price, timestamp_ms, bid=None, ask=None, reason='replacement'):
    """Market-close all lots; bid/ask includes spread, fees stay explicit."""
    _validate_tick(price, timestamp_ms)
    if timestamp_ms < state.timestamp_ms:
        raise ValueError('timestamps must not move backward')
    if state.status in ('stopped', 'liquidated'):
        return state
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
    closed = replace(state, positions=(), orders=(), price=price, timestamp_ms=timestamp_ms,
                   close_pnl=state.close_pnl + gross, fees=state.fees + fees,
                   status='liquidated' if reason == 'liquidation' else 'stopped',
                   liquidated=reason == 'liquidation', stop_reason=reason)
    return _track_floating_loss(closed, price)


def _full_inventory_liquidation(config, direction):
    centre = (config.low + config.high) / 2
    directional = replace(config, direction=direction, trigger=None)
    state = _start(GridState(directional, centre, 0))
    positions = {p.slot: p for p in state.positions}
    for order in state.orders:
        if not order.closing:
            positions[order.slot] = Position(order.slot, order.side, _quantity(config), order.price)
    side = 1 if direction == 'long' else -1
    quantity = sum(p.quantity for p in positions.values())
    cost = sum(p.quantity * p.entry for p in positions.values())
    opening_fees = cost * FEE
    threshold = (side * cost + opening_fees - config.investment)
    threshold /= quantity * (side - config.maintenance_rate)
    return threshold if threshold > 0 else None


def preview(config):
    """Full directional-inventory scenarios, with fixed estimated maintenance.

    Each scenario seeds its initial closing-side slots at the range centre and
    fills all remaining entries at their grid prices. Available equity is the
    investment minus all opening fees. These directional stress scenarios are
    not an exchange risk-tier calculation or a neutral bot liquidation promise.
    """
    _validate(config)
    levels, quantity = _levels(config), _quantity(config)
    profits = [quantity * (b - a - FEE * (a + b)) for a, b in zip(levels, levels[1:])]
    return {'profit_per_grid_min': min(profits), 'profit_per_grid_max': max(profits),
            'order_count': config.grids, 'quantity': quantity,
            'liquidation_price_long': _full_inventory_liquidation(config, 'long'),
            'liquidation_price_short': _full_inventory_liquidation(config, 'short'),
            'liquidation_estimate': True,
            'liquidation_scenario': 'all_directional_grid_entries_filled'}
