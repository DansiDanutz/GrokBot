"""Deterministic paper-only long, linear-USDT simulator; no exchange writes.

Dollar tranches are collateral/notional at 1x. Adds require a 1%, then 2%
decline from the preceding fill AND the caller's rebound gate. A $1 target
means the entire position's net PnL. Funding is an explicitly conservative
pro-rata MODEL using the last valid positive rate, never negative credits.
Only full orders fitting the displayed top-of-book contract size may fill.
Thin/missing depth defers exits, including stops; partial fills are not modeled.
"""
from copy import deepcopy
from datetime import datetime
import math
from zoneinfo import ZoneInfo


def default_config():
    return dict(initial_balance=1000.0, max_positions=2, leverage=1, timezone='Europe/Bucharest',
                position_notional_cap=200.0, tranches=[50.0, 65.0, 85.0],
                add_drop_pct=[1.0, 2.0], target_net_profit=1.0,
                fee_rate=0.0006, slippage_rate=0.0002, max_quote_age=90.0,
                max_spread_pct=0.2, cooldown_seconds=300.0,
                max_position_loss=10.0, max_price_drop_pct=5.0,
                daily_loss_limit=30.0, min_score=60.0, rotation_score_delta=15.0,
                rotation_min_hold=1800.0, rotation_cooldown=1800.0,
                rotation_max_loss=2.0)


def _number(value, positive=False):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and (value > 0 if positive else value >= 0))


def _config(config):
    c = default_config()
    c.update(config)
    if c['leverage'] != 1 or c['max_positions'] not in (1, 2):
        raise ValueError('paper mode supports only 1x and at most two positions')
    for key, value in c.items():
        if key == 'timezone':
            ZoneInfo(value)
        elif key in ('tranches', 'add_drop_pct'):
            if not isinstance(value, list) or not all(_number(v, True) for v in value):
                raise ValueError('invalid ' + key)
        elif not _number(value, True):
            raise ValueError('invalid ' + key)
    if len(c['tranches']) != 3 or len(c['add_drop_pct']) != 2:
        raise ValueError('exactly one entry and two bounded adds are supported')
    if c['fee_rate'] >= 1 or c['slippage_rate'] >= 1:
        raise ValueError('invalid execution rates')
    return c


def _day(now, c):
    return datetime.fromtimestamp(now, ZoneInfo(c['timezone'])).date().isoformat()


def initial_state(config, now):
    c = _config(config)
    if not _number(now):
        raise ValueError('invalid timestamp')
    return dict(mode='paper', cash=c['initial_balance'], positions={},
                realized_pnl=0.0, unpaid_liabilities=0.0, day=_day(now, c),
                day_start_equity=c['initial_balance'], halted_day=None,
                last_run=None, last_rotation=None, symbol_closed_at={})


def _quote(record, now, c):
    if not isinstance(record, dict):
        return False
    if not all(_number(record.get(k), True) for k in ('bid', 'ask', 'mark')):
        return False
    timestamp = record.get('quote_time')
    return (_number(timestamp) and 0 <= now - timestamp <= c['max_quote_age']
            and record['bid'] <= record['ask'])


def _entry_record(symbol, record, now, c):
    if not _quote(record, now, c) or record.get('symbol') != symbol:
        return False
    if not all(_number(record.get(k), True) for k in
               ('lot_size', 'multiplier', 'tick_size', 'funding_interval_hours',
                'atr_pct', 'entry_edge_pct', 'turnover24h')):
        return False
    if (not isinstance(record['lot_size'], int)
            or not _number(record.get('score')) or record['score'] > 100):
        return False
    rate = record.get('funding_rate')
    if (not isinstance(rate, (int, float)) or isinstance(rate, bool)
            or not math.isfinite(rate) or abs(rate) > 1):
        return False
    spread = (record['ask'] / record['bid'] - 1) * 100
    return (spread <= c['max_spread_pct']
            and isinstance(record.get('eligible'), bool)
            and isinstance(record.get('add_eligible'), bool))


def _exit_price(record, c):
    return record['bid'] * (1 - c['slippage_rate'])


def _full_depth(record, side, contracts):
    size = record.get(side + '_size') if isinstance(record, dict) else None
    return _number(size, True) and contracts <= size


def _net(position, record, c):
    proceeds = position['quantity'] * _exit_price(record, c)
    return (proceeds - position['cost_basis'] - position['entry_fees']
            - proceeds * c['fee_rate'] - position['funding_accrued'])


def _equity(state, market, now, c):
    equity = state['cash'] - state.get('unpaid_liabilities', 0.0)
    unpriced = []
    for symbol, p in state['positions'].items():
        r = market.get(symbol)
        if not _quote(r, now, c):
            unpriced.append(symbol)
            r = dict(bid=p['last_bid'])
        # Cash already paid entry fees, so do not subtract them a second time.
        equity += p['cost_basis'] + _net(p, r, c) + p['entry_fees']
    return equity, unpriced


def status(state, market, now, config):
    c = _config(config)
    equity, unpriced = _equity(state, market or {}, now, c)
    floating = equity - c['initial_balance'] - state['realized_pnl']
    illiquid = [symbol for symbol, p in state['positions'].items()
                if not _full_depth((market or {}).get(symbol), 'bid', p['contracts'])]
    return dict(mode='paper', cash=state['cash'], equity=equity,
                equity_is_estimate=bool(unpriced or illiquid), unpriced_positions=unpriced,
                insufficient_exit_depth=illiquid,
                realized_pnl=state['realized_pnl'], unrealized_net_pnl=floating,
                daily_pnl=equity - state['day_start_equity'],
                positions=deepcopy(state['positions']), halted_day=state['halted_day'],
                funding_model='positive rate, pro-rata elapsed, no negative credits')


def _close(state, symbol, record, now, c, reason, events):
    if not _full_depth(record, 'bid', state['positions'][symbol]['contracts']):
        events.append(dict(type='deferred_exit', symbol=symbol, reason=reason, time=now,
                           detail='full position exceeds valid displayed bid_size',
                           required_contracts=state['positions'][symbol]['contracts']))
        return False
    p = state['positions'].pop(symbol)
    net = _net(p, record, c)
    # Entry fees were paid at entry; return collateral plus exit PnL/costs.
    proceeds = state['cash'] + p['cost_basis'] + net + p['entry_fees']
    # A catastrophic modeled funding liability is reported, never hidden by
    # a negative spendable-cash balance or silently forgiven.
    if proceeds < 0:
        state['unpaid_liabilities'] = state.get('unpaid_liabilities', 0) - proceeds
    state['cash'] = max(proceeds, 0.0)
    state['realized_pnl'] += net
    state['symbol_closed_at'][symbol] = now
    events.append(dict(type='close', symbol=symbol, reason=reason, time=now,
                       fill_price=_exit_price(record, c), contracts=p['contracts'],
                       net_pnl=net, entry_fees=p['entry_fees'],
                       exit_fee=p['quantity'] * _exit_price(record, c) * c['fee_rate'],
                       funding_model_cost=p['funding_accrued']))
    return True


def _buy(state, symbol, record, now, c, events, add=False):
    p = state['positions'].get(symbol)
    tranche_index = p['adds'] + 1 if add else 0
    fill = record['ask'] * (1 + c['slippage_rate'])
    existing = p['cost_basis'] if p else 0
    budget = min(c['tranches'][tranche_index],
                 c['position_notional_cap'] - existing,
                 state['cash'] / (1 + c['fee_rate']))
    unit_cost = fill * record['multiplier'] * record['lot_size']
    if not _number(unit_cost, True) or not math.isfinite(budget / unit_cost):
        return False
    lots = math.floor(budget / unit_cost)
    if lots < 1:
        return False
    contracts = lots * record['lot_size']
    if not _full_depth(record, 'ask', contracts):
        return False
    quantity = contracts * record['multiplier']
    cost = quantity * fill
    fee = cost * c['fee_rate']
    if cost + fee > state['cash'] or existing + cost > c['position_notional_cap'] + 1e-9:
        return False
    total_quantity = quantity + (p['quantity'] if p else 0)
    total_cost = existing + cost
    total_fees = fee + (p['entry_fees'] if p else 0)
    immediate_exit = total_quantity * _exit_price(record, c)
    immediate_net = (immediate_exit * (1 - c['fee_rate']) - total_cost - total_fees
                     - (p['funding_accrued'] if p else 0))
    weighted_drop = (1 - record['bid'] / (total_cost / total_quantity)) * 100
    if immediate_net <= -c['max_position_loss'] or weighted_drop >= c['max_price_drop_pct']:
        return False
    if not add:
        hypothetical_bid = record['bid'] * (1 + record['entry_edge_pct'] / 100)
        exit_proceeds = quantity * hypothetical_bid * (1 - c['slippage_rate'])
        expected_net = exit_proceeds * (1 - c['fee_rate']) - cost - fee
        # Declared price edge is a scanner heuristic, never a profit guarantee.
        if expected_net < c['target_net_profit']:
            return False
    state['cash'] -= cost + fee
    if p is None:
        p = dict(contracts=0, quantity=0.0, cost_basis=0.0, entry_fees=0.0,
                 funding_accrued=0.0, funding_time=now, opened_at=now, adds=0,
                 multiplier=record['multiplier'], lot_size=record['lot_size'])
        state['positions'][symbol] = p
    p['contracts'] += contracts
    p['quantity'] += quantity
    p['cost_basis'] += cost
    p['entry_fees'] += fee
    p['average_price'] = p['cost_basis'] / p['quantity']
    p.update(last_fill=fill, last_fill_at=now, last_bid=record['bid'],
             last_mark=record['mark'], score=record['score'],
             funding_rate=max(record['funding_rate'], 0),
             funding_interval_hours=record['funding_interval_hours'])
    if add:
        p['adds'] += 1
    events.append(dict(type='add' if add else 'open', symbol=symbol, time=now,
                       contracts=contracts, fill_price=fill, notional=cost,
                       fee=fee, tranche_index=tranche_index))
    return True


def _validate_state(s, c):
    if s.get('mode') != 'paper' or not _number(s.get('cash')):
        raise ValueError('invalid paper state or cash')
    positions = s.get('positions')
    if not isinstance(positions, dict) or len(positions) > c['max_positions']:
        raise ValueError('invalid position count')
    for k in ('realized_pnl', 'day_start_equity'):
        v = s.get(k)
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v):
            raise ValueError('invalid state ' + k)
    if not _number(s.get('unpaid_liabilities', 0)):
        raise ValueError('invalid unpaid liabilities')
    if s.get('last_run') is not None and not _number(s['last_run']):
        raise ValueError('invalid last_run')
    if not isinstance(s.get('symbol_closed_at'), dict) or not isinstance(s.get('day'), str):
        raise ValueError('invalid state metadata')
    if (any(not isinstance(k, str) or not _number(v) for k, v in s['symbol_closed_at'].items())
            or (s.get('last_rotation') is not None and not _number(s['last_rotation']))):
        raise ValueError('invalid cooldown timestamps')
    for p in positions.values():
        if not isinstance(p, dict):
            raise ValueError('invalid position')
        for k in ('contracts', 'quantity', 'cost_basis', 'average_price', 'multiplier',
                  'lot_size', 'last_fill', 'last_bid', 'last_mark', 'funding_interval_hours'):
            if not _number(p.get(k), True):
                raise ValueError('invalid position ' + k)
        for k in ('entry_fees', 'funding_accrued', 'funding_time', 'opened_at',
                  'last_fill_at', 'funding_rate', 'score'):
            if not _number(p.get(k)):
                raise ValueError('invalid position ' + k)
        if (not isinstance(p['contracts'], int) or not isinstance(p['lot_size'], int)
                or p['contracts'] % p['lot_size'] or type(p.get('adds')) is not int
                or p['adds'] not in (0, 1, 2)
                or p['cost_basis'] > c['position_notional_cap'] + 1e-8
                or not math.isclose(p['quantity'], p['contracts'] * p['multiplier'])
                or not math.isclose(p['average_price'] * p['quantity'], p['cost_basis'])):
            raise ValueError('inconsistent position sizing')


def step(state, market, now, config):
    """Apply one tick. Replayed/older timestamps are no-ops, including funding.

    Invalid entry metadata fails closed; valid bid/ask/mark can still close an
    existing position only with enough displayed bid contracts for a full fill.
    Missing quotes freeze fills, retain last-known valuation,
    and block all new risk. A daily halt persists for the configured local day and retries
    closing unpriced positions as fresh quotes return.
    Stops/fills are evaluated only at received ticks, never between samples.
    """
    c = _config(config)
    s = deepcopy(state)
    events = []
    _validate_state(s, c)
    if s.get('mode') != 'paper' or not _number(now):
        raise ValueError('paper state and valid time required')
    if s['last_run'] is not None and now <= s['last_run']:
        return s, events
    market = market if isinstance(market, dict) else {}
    new_day = _day(now, c)
    # Prior-rate accrual avoids applying newly learned funding retroactively.
    for symbol, p in s['positions'].items():
        elapsed = max(0, now - p['funding_time'])
        p['funding_accrued'] += (p['quantity'] * p['last_mark'] * p['funding_rate']
                                * elapsed / (p['funding_interval_hours'] * 3600))
        p['funding_time'] = now
        r = market.get(symbol)
        if _quote(r, now, c):
            p.update(last_bid=r['bid'], last_mark=r['mark'])
        if _entry_record(symbol, r, now, c):
            p.update(score=r['score'], funding_rate=max(r['funding_rate'], 0),
                     funding_interval_hours=r['funding_interval_hours'])
    equity, unpriced = _equity(s, market, now, c)
    if new_day != s['day']:
        # Preserve prior marked baseline to count losses first observed today.
        s['day_start_equity'] = s.get('last_equity', s['day_start_equity'])
        s['day'] = new_day
        s['halted_day'] = None
    if equity - s['day_start_equity'] <= -c['daily_loss_limit']:
        if s['halted_day'] != new_day:
            events.append(dict(type='daily_halt', time=now, equity=equity,
                               unpriced_positions=unpriced))
        s['halted_day'] = new_day
    closed_this_tick = set()
    deferred_this_tick = set()
    for symbol, p in list(s['positions'].items()):
        r = market.get(symbol)
        if not _quote(r, now, c):
            continue
        net = _net(p, r, c)
        drop = (1 - r['bid'] / p['average_price']) * 100
        reason = ('daily_loss_limit' if s['halted_day'] == new_day else
                  'position_loss_limit' if net <= -c['max_position_loss'] else
                  'price_stop' if drop >= c['max_price_drop_pct'] else
                  'net_profit_target' if net >= c['target_net_profit'] else None)
        if reason:
            if _close(s, symbol, r, now, c, reason, events):
                closed_this_tick.add(symbol)
            else:
                deferred_this_tick.add(symbol)
    _, unpriced = _equity(s, market, now, c)
    if (s['halted_day'] != new_day and not unpriced and not deferred_this_tick
            and not s.get('unpaid_liabilities', 0)):
        valid = {symbol: r for symbol, r in market.items()
                 if _entry_record(symbol, r, now, c)}
        for symbol, p in list(s['positions'].items()):
            r = valid.get(symbol)
            if (r and p['adds'] < 2 and r['add_eligible']
                    and r['multiplier'] == p['multiplier'] and r['lot_size'] == p['lot_size']
                    and now - p['last_fill_at'] >= c['cooldown_seconds']
                    and r['ask'] * (1 + c['slippage_rate']) <=
                    p['last_fill'] * (1 - c['add_drop_pct'][p['adds']] / 100)):
                _buy(s, symbol, r, now, c, events, add=True)
        candidates = sorted((symbol for symbol, r in valid.items()
                             if r['eligible'] and r['score'] >= c['min_score']
                             and symbol not in s['positions'] and symbol not in closed_this_tick
                             and now - s['symbol_closed_at'].get(symbol, -math.inf)
                             >= c['cooldown_seconds']),
                            key=lambda symbol: (-valid[symbol]['score'], symbol))
        if (len(s['positions']) == c['max_positions'] == 2 and candidates
                and (s['last_rotation'] is None or
                     now - s['last_rotation'] >= c['rotation_cooldown'])):
            weak = min(s['positions'], key=lambda symbol: (s['positions'][symbol]['score'], symbol))
            p = s['positions'][weak]
            best = candidates[0]
            if (weak in valid and now - p['opened_at'] >= c['rotation_min_hold']
                    and valid[best]['score'] >= p['score'] + c['rotation_score_delta']
                    and _net(p, valid[weak], c) >= -c['rotation_max_loss']):
                # Check affordability before realizing a rotation exit.
                trial = deepcopy(s)
                trial_events = []
                if not _close(trial, weak, valid[weak], now, c, 'rotation', trial_events):
                    events.extend(trial_events)
                elif _buy(trial, best, valid[best], now, c, []):
                    if _close(s, weak, valid[weak], now, c, 'rotation', events):
                        _buy(s, best, valid[best], now, c, events)
                        s['last_rotation'] = now
                        candidates.remove(best)
        for symbol in candidates:
            if len(s['positions']) >= c['max_positions']:
                break
            _buy(s, symbol, valid[symbol], now, c, events)
    s['last_run'] = now
    s['last_equity'], _ = _equity(s, market, now, c)
    return s, events
