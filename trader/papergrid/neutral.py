"""Independent paper hedge books; RAY seed counts are an observation, not a sizing rule.

Quantity is supplied by the caller. This models arithmetic paired orders, not
an assertion that KuCoin's proprietary sizing or liquidation is replicated.
Seeded closing pairs realize from actual entry, so the first close can be a
partial interval and does not inherit a full-interval profit guarantee.
"""
from copy import deepcopy
from decimal import Decimal
import math
from . import engine


def _sync(bot, price):
    books = bot['hedge_books']
    for book in books:
        engine._mark(book, price)
    for key in ('realized_pnl', 'unrealized_pnl', 'fees_paid', 'funding_paid',
                'fills', 'completed_grids', 'grid_profit', 'position_contracts'):
        if key != 'funding_paid' or not bot.get('funding_managed'):
            bot[key] = sum(book[key] for book in books)
    bot['long_contracts'], bot['short_contracts'] = [b['position_contracts'] for b in books]
    bot['long_avg_entry'], bot['short_avg_entry'] = [b['avg_entry'] for b in books]
    bot['long_unrealized_pnl'], bot['short_unrealized_pnl'] = [b['unrealized_pnl'] for b in books]
    # A net weighted cost preserves the aggregate mark identity, while the two
    # actual entry averages remain separately available to liquidation/UI code.
    quantity = bot['position_contracts']
    cost = sum(b['position_contracts'] * b['avg_entry'] for b in books)
    bot['avg_entry'] = cost / quantity if quantity else 0.0
    bot['orders'] = [dict(order, book=index) for index, book in enumerate(books)
                     for order in book['orders']]
    bot['last_price'] = price
    bot['equity'] = (bot['notional_usdt'] + bot.get('reserve_added_usdt', 0) + bot['realized_pnl'] + bot['unrealized_pnl']
                     - bot['fees_paid'] - bot['funding_paid'])
    bot['peak_equity'] = max(bot['peak_equity'], bot['equity'])
    bot['max_drawdown_pct'] = max(bot['max_drawdown_pct'],
        100 * (bot['peak_equity'] - bot['equity']) / bot['peak_equity'])


def open_neutral(spec, price, now_ms):
    """Seed opposing books with fixed quantity and staggered adjacent gaps."""
    quantity = engine._number(spec['quantity_per_grid'], positive=True)
    if spec['direction'] != 'NEUTRAL' or 'grid_interval' not in spec:
        raise ValueError('neutral hedge requires an arithmetic neutral specification')
    bot = engine._open_single_bot(spec, price, now_ms)
    low_decimal = Decimal(str(spec['range_low']))
    interval_decimal = Decimal(str(spec['grid_interval']))
    bot['lines'] = [float(low_decimal + i * interval_decimal) for i in range(spec['grids'] + 1)]
    for key in ('funding_managed', 'funding_interval_ms', 'contract_lots',
                'contract_multiplier', 'accounting_version'):
        if key in spec:
            bot[key] = spec[key]
    bot['contracts_per_line'] = quantity
    gap = min(spec['grids'], max(0, math.ceil(
        (price - spec['range_low']) / spec['grid_interval'] - 1e-12)))
    books = []
    for direction, empty in (('LONG', gap), ('SHORT', max(0, gap - 1))):
        book = deepcopy(bot)
        book.update(direction=direction, empty_line=empty, orders=[])
        for i in range(len(bot['lines'])):
            if i == empty:
                continue
            side = 'buy' if i < empty else 'sell'
            closing = (direction == 'LONG' and side == 'sell') or (direction == 'SHORT' and side == 'buy')
            book['orders'].append(dict(line=i, side=side, paired_line=None,
                pair_entry=price if closing else None))
        seeded = sum(o['pair_entry'] is not None for o in book['orders'])
        if seeded:
            engine._position_fill(book, quantity * seeded * (1 if direction == 'LONG' else -1), price)
        books.append(book)
    bot['hedge_books'] = books
    bot['accounting_model'] = 'independent_hedge_v1'
    _sync(bot, price)
    return bot


def _fund(book, at):
    if book.get('funding_managed'):
        return
    interval = book.get('funding_interval_ms', engine.FUNDING_INTERVAL_MS)
    start = max(book['last_ts_ms'], book['last_funding_ts_ms'])
    count = at // interval - start // interval
    if count > 0:
        book['funding_paid'] += count * book['position_contracts'] * book['last_price'] * book['funding_pct'] / 100
        book['last_funding_ts_ms'] = at // interval * interval


def step_neutral(bot, price, at):
    """Fill each leg independently; only its matched closing order completes a grid."""
    engine._number(price, positive=True)
    engine._timestamp(at)
    result = deepcopy(bot)
    if bot['closed_ms'] is not None or at <= bot['last_ts_ms']:
        return result, []
    events = []
    for index, book in enumerate(result['hedge_books']):
        _fund(book, at)
        while True:
            empty = book['empty_line']
            order = next((o for o in book['orders'] if
                (o['side'] == 'buy' and o['line'] == empty - 1 and price <= book['lines'][o['line']]) or
                (o['side'] == 'sell' and o['line'] == empty + 1 and price >= book['lines'][o['line']])), None)
            if order is None:
                break
            fill = book['lines'][order['line']]
            quantity = book['contracts_per_line']
            sign = 1 if order['side'] == 'buy' else -1
            closing = order['pair_entry'] is not None
            if closing and abs(book['position_contracts']) + quantity * 1e-10 < quantity:
                raise ValueError('closing hedge order exceeds leg inventory')
            fee = engine._position_fill(book, sign * quantity, fill)
            events.append(engine._event(result, at, 'FILL', price=fill, contracts=quantity,
                side=sign, fee=fee, line=order['line'], book=index))
            if closing:
                profit = quantity * (fill - order['pair_entry']) * (1 if index == 0 else -1)
                book['completed_grids'] += 1
                book['grid_profit'] += profit
                events.append(engine._event(result, at, 'GRID', price=fill, profit=profit,
                    completed_grids=sum(b['completed_grids'] for b in result['hedge_books'])))
            book['orders'].remove(order)
            book['orders'].append(dict(line=empty, side='sell' if sign == 1 else 'buy',
                paired_line=None if closing else order['line'], pair_entry=None if closing else fill))
            book['orders'].sort(key=lambda o: o['line'])
            book['empty_line'] = order['line']
        book['last_ts_ms'] = at
    result['last_ts_ms'] = at
    _sync(result, price)
    return result, events


def close_neutral(bot, price, at, reason='MANUAL'):
    """Flatten both legs, paying both execution fees, without manufacturing grids."""
    engine._number(price, positive=True)
    engine._timestamp(at)
    if reason not in engine.CLOSE_REASONS or at < bot['last_ts_ms']:
        raise ValueError('invalid close reason or timestamp')
    result = deepcopy(bot)
    if bot['closed_ms'] is not None:
        return result, []
    events = []
    for index, book in enumerate(result['hedge_books']):
        _fund(book, at)
        quantity = -book['position_contracts']
        if quantity:
            fee = engine._position_fill(book, quantity, price)
            events.append(engine._event(result, at, 'FILL', price=price,
                contracts=abs(quantity), side=1 if quantity > 0 else -1, fee=fee, book=index))
        book.update(orders=[], closed_ms=at, reason=reason, last_ts_ms=at)
    result.update(closed_ms=at, reason=reason, last_ts_ms=at)
    _sync(result, price)
    events.append(engine._event(result, at, 'CLOSE', price=price,
        equity=result['equity'], reason_code=engine.CLOSE_REASONS.index(reason)))
    return result, events
