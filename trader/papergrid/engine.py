"""Signed position accounting and a separate adjacent-line grid ledger."""

from copy import deepcopy
import math
from decimal import Decimal

FEE_RATE = 0.0006
FUNDING_INTERVAL_MS = 8 * 60 * 60 * 1000
CLOSE_REASONS = ('LABEL_FLIP', 'RANGE_BREAK', 'STOP_LOSS', 'DROPPED', 'MAX_AGE', 'MANUAL', 'PROFILE_UPDATE', 'RISK_LIMIT')


def _number(value, *, positive=False):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or (positive and value <= 0)):
        raise ValueError('invalid paper grid number')
    return value


def _timestamp(value):
    if type(value) is not int or value < 0:
        raise ValueError('invalid paper grid timestamp')
    return value


def _event(bot, at, kind, **numbers):
    return dict(ts_ms=at, bot_id=bot['bot_id'], symbol=bot['symbol'], type=kind, **numbers)


def _position_fill(bot, quantity, price):
    old, average = bot['position_contracts'], bot['avg_entry']
    new = old + quantity
    if old == 0 or old * quantity > 0:
        bot['avg_entry'] = (abs(old) * average + abs(quantity) * price) / abs(new)
    else:
        closed = min(abs(old), abs(quantity))
        bot['realized_pnl'] += closed * (price - average) * (1 if old > 0 else -1)
        if abs(new) < bot['contracts_per_line'] * 1e-10:
            new = 0.0
        bot['avg_entry'] = 0.0 if new == 0 else price if old * new < 0 else average
    bot['position_contracts'] = new
    fee = abs(quantity) * price * FEE_RATE
    bot['fees_paid'] += fee
    bot['fills'] += 1
    return fee


def _mark(bot, price):
    if 'hedge_books' in bot:
        from .neutral import _sync
        return _sync(bot, price)
    bot['last_price'] = price
    bot['unrealized_pnl'] = bot['position_contracts'] * (price - bot['avg_entry'])
    bot['equity'] = (bot['notional_usdt'] + bot.get('reserve_added_usdt', 0) + bot['realized_pnl'] + bot['unrealized_pnl']
                     - bot['fees_paid'] - bot['funding_paid'])
    bot['peak_equity'] = max(bot['peak_equity'], bot['equity'])
    drawdown = 100 * (bot['peak_equity'] - bot['equity']) / bot['peak_equity']
    bot['max_drawdown_pct'] = max(bot['max_drawdown_pct'], drawdown)


def open_bot(spec, price, now_ms):
    if spec.get('accounting_version') == 2 and spec.get('direction') == 'NEUTRAL':
        from .neutral import open_neutral
        return open_neutral(spec, price, now_ms)
    return _open_single_bot(spec, price, now_ms)


def _open_single_bot(spec, price, now_ms):
    """Create a paper bot, seeding trend inventory at the supplied opening price."""
    _number(price, positive=True)
    _timestamp(now_ms)
    for key in ('range_low', 'range_high', 'step_pct', 'notional_usdt', 'leverage'):
        _number(spec[key], positive=True)
    _number(spec['funding_pct'])
    grids = spec['grids']
    if type(grids) is not int or not 1 <= grids <= 200:
        raise ValueError('grid count must be between 1 and 200')
    low, high = spec['range_low'], spec['range_high']
    if not low < high or not low <= price <= high:
        raise ValueError('opening price must lie inside a valid range')
    if spec['direction'] not in ('LONG', 'SHORT', 'NEUTRAL'):
        raise ValueError('invalid paper grid direction')
    if not isinstance(spec['symbol'], str) or not spec['symbol']:
        raise ValueError('invalid paper grid symbol')
    if not isinstance(spec['bot_id'], (str, int)) or isinstance(spec['bot_id'], bool):
        raise ValueError('invalid paper bot identifier')
    lines = [low * math.exp(math.log(high / low) * i / grids) for i in range(grids + 1)]
    lines[0], lines[-1] = low, high
    if 'grid_interval' in spec:
        interval = _number(spec['grid_interval'], positive=True)
        if low + grids * interval > high + high*1e-12:
            raise ValueError('arithmetic grid exceeds configured range')
        lines = [float(Decimal(str(low)) + i * Decimal(str(interval))) for i in range(grids + 1)]
    empty = min(range(len(lines)), key=lambda i: abs(lines[i] - price))
    if spec.get('accounting_version') == 2 and spec['direction'] in ('LONG','SHORT'):
        offset=(price-low)/spec['grid_interval']
        empty=min(grids,max(0,math.ceil(offset-1e-12) if spec['direction']=='LONG' else math.floor(offset+1e-12)))
    quantity = spec.get('quantity_per_grid', spec['notional_usdt'] * spec['leverage'] / grids / price)
    _number(quantity, positive=True)
    bot = {key: deepcopy(spec[key]) for key in (
        'bot_id', 'symbol', 'direction', 'range_low', 'range_high', 'step_pct', 'grids',
        'notional_usdt', 'leverage', 'funding_pct')}
    for key in ('grid_interval', 'profit_pct_min', 'profit_pct_max', 'accounting_version',
                'contract_lots', 'contract_multiplier', 'maintain_margin', 'risk_limit'):
        if key in spec:
            bot[key] = _number(spec[key], positive=True)
    bot['funding_managed'] = spec.get('funding_managed', False)
    bot['reserve_added_usdt'] = 0.0
    bot['opening_price'] = price
    bot['risk_metadata_at_ms'] = now_ms
    bot['quantity_is_observed'] = spec.get('quantity_is_observed', 0)
    bot.update(lines=lines, orders=[], empty_line=empty, contracts_per_line=quantity,
               fills=0, completed_grids=0, grid_profit=0.0, realized_pnl=0.0,
               unrealized_pnl=0.0, position_contracts=0.0, avg_entry=0.0,
               fees_paid=0.0, funding_paid=0.0, equity=spec['notional_usdt'],
               peak_equity=spec['notional_usdt'], max_drawdown_pct=0.0,
               last_price=price, last_ts_ms=now_ms, opened_ms=now_ms, closed_ms=None,
               reason=None, range_break_count=0, stop_loss_emitted=False,
               last_funding_ts_ms=now_ms // FUNDING_INTERVAL_MS * FUNDING_INTERVAL_MS)
    for i in range(len(lines)):
        if i == empty:
            continue
        side = 'buy' if i < empty else 'sell'
        seeded = ((spec['direction'] == 'LONG' and side == 'sell')
                  or (spec['direction'] == 'SHORT' and side == 'buy'))
        bot['orders'].append(dict(line=i, side=side,
                                 paired_line=(i - 1 if side == 'sell' else i + 1) if seeded else None))
    if spec.get('accounting_version') == 2:
        for order in bot['orders']:
            order['pair_entry'] = price if order['paired_line'] is not None else None
    seed_count = sum(order['paired_line'] is not None for order in bot['orders'])
    if seed_count:
        _position_fill(bot, quantity * seed_count * (1 if spec['direction'] == 'LONG' else -1), price)
    _mark(bot, price)
    return bot


def _fund(bot, at, rate):
    if bot.get('funding_managed'):
        return
    # Every crossed boundary uses the pre-update position and last known price.
    start = max(bot['last_ts_ms'], bot['last_funding_ts_ms'])
    count = at // FUNDING_INTERVAL_MS - start // FUNDING_INTERVAL_MS
    if count > 0:
        bot['funding_paid'] += count * bot['position_contracts'] * bot['last_price'] * rate / 100
        bot['last_funding_ts_ms'] = at // FUNDING_INTERVAL_MS * FUNDING_INTERVAL_MS


def _price_update(bot, price, at, events):
    # Only the adjacent order can fill next. Its replacement occupies the old gap.
    while True:
        empty = bot['empty_line']
        candidates = [order for order in bot['orders']
                      if (order['side'] == 'buy' and order['line'] == empty - 1
                          and price <= bot['lines'][order['line']])
                      or (order['side'] == 'sell' and order['line'] == empty + 1
                          and price >= bot['lines'][order['line']])]
        if not candidates:
            break
        order = candidates[0]
        i, quantity = order['line'], bot['contracts_per_line']
        fill_price = bot['lines'][i]
        sign = 1 if order['side'] == 'buy' else -1
        position_before = abs(bot['position_contracts'])
        fee = _position_fill(bot, quantity * sign, fill_price)
        events.append(_event(bot, at, 'FILL', price=fill_price, contracts=quantity,
                             side=sign, fee=fee, line=i))
        if order['paired_line'] is not None and abs(bot['position_contracts']) < position_before:
            pair_entry = order.get('pair_entry') if bot.get('accounting_version') == 2 else bot['lines'][order['paired_line']]
            profit = quantity * abs(fill_price - pair_entry)
            bot['completed_grids'] += 1
            bot['grid_profit'] += profit
            events.append(_event(bot, at, 'GRID', price=fill_price, profit=profit,
                                 completed_grids=bot['completed_grids']))
        bot['orders'].remove(order)
        bot['orders'].append(dict(line=empty, side='sell' if sign == 1 else 'buy', paired_line=i, pair_entry=fill_price))
        bot['orders'].sort(key=lambda row: row['line'])
        bot['empty_line'] = i
        _mark(bot, fill_price)
    _mark(bot, price)


def step(bot, tick_or_candle, *, funding_pct=None):
    """Apply one tick or ordered OHLC path; duplicate/older timestamps are no-ops."""
    at = _timestamp(tick_or_candle['ts_ms'])
    if 'price' in tick_or_candle:
        path = [_number(tick_or_candle['price'], positive=True)]
    else:
        opened, high, low, close = [_number(tick_or_candle[k], positive=True)
                                    for k in ('open', 'high', 'low', 'close')]
        if low > min(opened, close) or high < max(opened, close) or low > high:
            raise ValueError('invalid candle range')
        extremes = [low, high] if opened - low <= high - opened else [high, low]
        path = [opened, *extremes, close]
    rate = bot['funding_pct'] if funding_pct is None else _number(funding_pct)
    result = deepcopy(bot)
    if bot['closed_ms'] is not None or at <= bot['last_ts_ms']:
        return result, []
    if 'hedge_books' in bot:
        from .neutral import step_neutral
        events = []
        for price in path:
            result['last_ts_ms'] = bot['last_ts_ms']
            result, emitted = step_neutral(result, price, at)
            events.extend(emitted)
        return result, events
    _fund(result, at, rate)
    events = []
    for price in path:
        _price_update(result, price, at, events)
    ratio = 1 + result['step_pct'] / 100
    outside = price < result['range_low'] / ratio or price > result['range_high'] * ratio
    result['range_break_count'] = result['range_break_count'] + 1 if outside else 0
    if result['range_break_count'] == 3:
        events.append(_event(result, at, 'RANGE_BREAK', price=price))
    net = result['realized_pnl'] + result['unrealized_pnl'] - result['fees_paid'] - result['funding_paid']
    if net < -0.12 * result['notional_usdt'] and not result['stop_loss_emitted']:
        events.append(_event(result, at, 'STOP_LOSS', net=net))
        result['stop_loss_emitted'] = True
    result['last_ts_ms'] = at
    return result, events


def close_bot(bot, price, now_ms, reason):
    """Cancel orders and flatten once, without adding any grid-ledger profit."""
    if 'hedge_books' in bot:
        from .neutral import close_neutral
        return close_neutral(bot, price, now_ms, reason)
    _number(price, positive=True)
    _timestamp(now_ms)
    if reason not in CLOSE_REASONS or now_ms < bot['last_ts_ms']:
        raise ValueError('invalid close reason or timestamp')
    result = deepcopy(bot)
    if result['closed_ms'] is not None:
        return result, []
    _fund(result, now_ms, result['funding_pct'])
    events = []
    quantity = -result['position_contracts']
    if quantity:
        fee = _position_fill(result, quantity, price)
        events.append(_event(result, now_ms, 'FILL', price=price,
                             contracts=abs(quantity), side=1 if quantity > 0 else -1, fee=fee))
    result.update(orders=[], closed_ms=now_ms, reason=reason, last_ts_ms=now_ms)
    _mark(result, price)
    events.append(_event(result, now_ms, 'CLOSE', price=price, equity=result['equity'],
                         reason_code=CLOSE_REASONS.index(reason)))
    return result, events
