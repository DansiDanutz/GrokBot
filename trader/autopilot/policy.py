"""Immutable portfolio rules. Wrappers preserve Phase A's accounting unchanged.

Each open/closed wrapper has engine, reserve_usdt, source_section, slot_direction
and latched signals. A borrowed slot retains its original direction until closed.
"""
from copy import deepcopy
import math

from trader.papergrid import open_bot, close_bot, step
from trader.radar.rates import expected_grids_per_hour
from trader.autopilot.constants import (
    PAPER_EQUITY_USDT, MAX_BOTS, NOTIONAL_PER_BOT_USDT, SLOTS, DIRECTION_CAP,
    LEVERAGE_TREND, LEVERAGE_NEUTRAL, STEP_NEUTRAL_PCT, NEUTRAL_RESERVE_USDT,
    MAJORS, MAJORS_MAX, MOVERS_MAX, MIN_EXPECTED_GRIDS_PER_HOUR,
    COOLDOWN_HOURS, MAX_AGE_HOURS,
)

HOUR_MS = 3_600_000
RETENTION_MS = 30 * 24 * HOUR_MS


def new_state(now_ms):
    return dict(schema_version=1, started_ms=now_ms, open_bots=[], closed_bots=[],
                cooldowns={}, radar_seen={}, equity_curve=[], next_bot_id=1,
                archived_net=0.0, peak_equity=PAPER_EQUITY_USDT, max_drawdown_pct=0.0)


def net(bot):
    return bot['realized_pnl'] + bot['unrealized_pnl'] - bot['fees_paid'] - bot['funding_paid']


def profile(row, direction, bot_id):
    """Return Phase A spec and undeployed reserve; never rewrite engine economics."""
    for key in ('price', 'range_low', 'range_high', 'step_pct', 'funding_pct', 'atr_1h_pct',
                'turnover_24h_usdt', 'atr_4h_pct', 'low_7d', 'high_7d'):
        value = row[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError('invalid profile number')
    if direction not in SLOTS or type(row['grids']) is not int or not 1 <= row['grids'] <= 200:
        raise ValueError('invalid profile direction or grids')
    if row['price'] <= 0 or row['step_pct'] <= 0 or row['range_low'] <= 0 or row['range_high'] <= row['range_low']:
        raise ValueError('invalid profile range')
    price = row['price']
    low, high, step_pct = row['range_low'], row['range_high'], row['step_pct']
    grids, leverage, reserve = row['grids'], LEVERAGE_TREND, 0
    if direction == 'NEUTRAL':
        atr = price * row['atr_4h_pct'] / 100
        low, high = min(row['low_7d'], price - atr), max(row['high_7d'], price + atr)
        step_pct, leverage, reserve = STEP_NEUTRAL_PCT, LEVERAGE_NEUTRAL, NEUTRAL_RESERVE_USDT
        if low <= 0 or high <= low:
            raise ValueError('invalid neutral range')
        grids = min(200, max(1, round(math.log(high / low) / math.log1p(step_pct / 100))))
    return dict(bot_id=bot_id, symbol=row['symbol'], direction=direction,
                range_low=low, range_high=high, step_pct=step_pct, grids=grids,
                notional_usdt=NOTIONAL_PER_BOT_USDT, leverage=leverage,
                funding_pct=row['funding_pct']), reserve


def eligible(state, row, direction, source_section, now_ms):
    """Admission shared by slot selection, including explicit trend movers caps."""
    bots = [item['engine'] for item in state['open_bots']]
    if len(bots) >= MAX_BOTS or not row.get('passes_liquidity', False):
        return False
    if any(bot['symbol'] == row['symbol'] for bot in bots):
        return False
    if state['cooldowns'].get(row['symbol'], 0) > now_ms:
        return False
    if sum(bot['direction'] == direction for bot in bots) >= DIRECTION_CAP:
        return False
    if row['symbol'] in MAJORS and sum(bot['symbol'] in MAJORS for bot in bots) >= MAJORS_MAX:
        return False
    if (direction != 'NEUTRAL' and source_section == 'movers'
            and sum(item['source_section'] == 'movers' and item['engine']['direction'] != 'NEUTRAL'
                    for item in state['open_bots']) >= MOVERS_MAX):
        return False
    try:
        spec, _ = profile(row, direction, 0)
        rate = expected_grids_per_hour(row['atr_1h_pct'], spec['step_pct'], row['turnover_24h_usdt'])
        return rate >= MIN_EXPECTED_GRIDS_PER_HOUR and spec['range_low'] <= row['price'] <= spec['range_high']
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return False


def _candidates(radar, direction):
    sections = {'LONG': ('turning_up', 'long'), 'SHORT': ('turning_down', 'short'),
                'NEUTRAL': ('neutral', 'movers')}[direction]
    for section in sections:
        key = 'atr_1h_pct' if section == 'movers' else 'rank_score'
        for row in sorted(radar.get('sections', {}).get(section, []), key=lambda r: (-r[key], r['symbol'])):
            yield section, row


def _mark_wrapper(wrapper):
    equity = wrapper['engine']['equity'] + wrapper['reserve_usdt']
    initial = wrapper['engine']['notional_usdt'] + wrapper['reserve_usdt']
    peak = max(wrapper.get('peak_equity', initial), equity)
    wrapper['peak_equity'] = peak
    wrapper['max_drawdown_pct'] = max(wrapper.get('max_drawdown_pct', 0.0), 100*(peak-equity)/peak)


def _reason(wrapper, labels, missing, now_ms):
    bot = wrapper['engine']
    for signal in ('STOP_LOSS', 'RANGE_BREAK'):
        if signal in wrapper['signals']:
            return signal
    opposing = {'LONG': ('SHORT', 'TURNING-DOWN'), 'SHORT': ('LONG', 'TURNING-UP'),
                'NEUTRAL': ('LONG', 'SHORT')}
    if labels.get(bot['symbol']) in opposing[bot['direction']]:
        return 'LABEL_FLIP'
    if missing >= 2:
        return 'DROPPED'
    if now_ms - bot['opened_ms'] >= MAX_AGE_HOURS * HOUR_MS:
        return 'MAX_AGE'
    return None


def decide(state, radar, prices, now_ms, scan_id):
    result, events = deepcopy(state), []
    radar_available = radar is not None
    radar = radar or {'rows': [], 'sections': {}}
    rows = radar.get('rows', [row for section in radar.get('sections', {}).values() for row in section])
    labels = {row['symbol']: row['direction'] for row in rows}
    for wrapper in list(result['open_bots']):
        bot = wrapper['engine']
        seen = result['radar_seen'].setdefault(bot['symbol'], [])
        if radar_available and (not seen or seen[-1]['scan_id'] != scan_id):
            seen.append(dict(scan_id=scan_id, present=bot['symbol'] in labels))
            del seen[:-2]
        missing = sum(not entry['present'] for entry in seen) if radar_available else 0
        reason = _reason(wrapper, labels, missing, now_ms)
        if reason:
            wrapper['engine'], emitted = close_bot(bot, prices.get(bot['symbol'], bot['last_price']), now_ms, reason)
            _mark_wrapper(wrapper)
            events.extend(emitted)
            result['open_bots'].remove(wrapper)
            result['closed_bots'].append(wrapper)
            result['cooldowns'][bot['symbol']] = now_ms + COOLDOWN_HOURS * HOUR_MS
    # Fill each original slot before considering overflow; no live bot is moved.
    vacancies = [direction for direction, count in SLOTS.items()
                 for _ in range(count - sum(w['slot_direction'] == direction for w in result['open_bots']))]
    deferred = []
    def fill(slot, direction):
        for section, row in _candidates(radar, direction):
            marked = dict(row, price=prices.get(row['symbol'], row['price']))
            if not eligible(result, marked, direction, section, now_ms):
                continue
            spec, reserve = profile(marked, direction, result['next_bot_id'])
            bot = open_bot(spec, marked['price'], now_ms)
            result['open_bots'].append(dict(engine=bot, reserve_usdt=reserve,
                                           source_section=section, slot_direction=slot, signals=[]))
            result['next_bot_id'] += 1
            result['radar_seen'][bot['symbol']] = [dict(scan_id=scan_id, present=True)]
            events.append(dict(ts_ms=now_ms, bot_id=bot['bot_id'], symbol=bot['symbol'], type='OPEN',
                               price=marked['price'], equity=bot['equity'] + reserve))
            return True
        return False
    for slot in vacancies:
        if not fill(slot, slot):
            deferred.append(slot)
    for slot in deferred:
        fallback = [direction for direction in ('NEUTRAL', 'LONG', 'SHORT') if direction != slot]
        for direction in fallback:
            if fill(slot, direction):
                break
    return sample(result, now_ms), events


def advance(state, updates):
    result, events = deepcopy(state), []
    for wrapper in result['open_bots']:
        update = updates.get(wrapper['engine']['symbol'])
        if update is None:
            continue
        wrapper['engine'], emitted = step(wrapper['engine'], update, funding_pct=update.get('funding_pct'))
        _mark_wrapper(wrapper)
        events.extend(emitted)
        wrapper['signals'] = sorted(set(wrapper['signals']) | {
            event['type'] for event in emitted if event['type'] in ('STOP_LOSS', 'RANGE_BREAK')})
    return result, events


def _equity(state):
    # All margin and reserves are allocations of the initial 10k, not deposits.
    return PAPER_EQUITY_USDT + state['archived_net'] + sum(
        net(wrapper['engine']) for wrapper in state['open_bots'] + state['closed_bots'])


def sample(state, now_ms):
    result = deepcopy(state)
    expired = [w for w in result['closed_bots'] if w['engine']['closed_ms'] < now_ms - RETENTION_MS]
    result['archived_net'] += sum(net(w['engine']) for w in expired)
    result['closed_bots'] = [w for w in result['closed_bots'] if w not in expired]
    equity = _equity(result)
    result['peak_equity'] = max(result['peak_equity'], equity)
    result['max_drawdown_pct'] = max(result['max_drawdown_pct'], 100*(result['peak_equity']-equity)/result['peak_equity'])
    curve = [point for point in result['equity_curve'] if point[0] >= now_ms - RETENTION_MS]
    if not curve or (now_ms > curve[-1][0] and curve[-1][0] // 60_000 != now_ms // 60_000):
        curve.append([now_ms, equity])
    result['equity_curve'] = curve
    result['cooldowns'] = {symbol: expiry for symbol, expiry in result['cooldowns'].items() if expiry > now_ms}
    active = {w['engine']['symbol'] for w in result['open_bots']}
    result['radar_seen'] = {symbol: seen for symbol, seen in result['radar_seen'].items() if symbol in active}
    for wrapper in result['open_bots']:
        _mark_wrapper(wrapper)
        points = [point for point in wrapper.get('pnl_curve', []) if point[0] >= now_ms - 72*HOUR_MS]
        if not points or (now_ms > points[-1][0] and points[-1][0] // 60_000 != now_ms // 60_000):
            points.append([now_ms, net(wrapper['engine'])])
        wrapper['pnl_curve'] = points[-4321:]
    return result


def _downsample(points, limit):
    if len(points) <= limit:
        return deepcopy(points)
    return [deepcopy(points[i*(len(points)-1)//(limit-1)]) for i in range(limit)]


def snapshot(state, now_ms, health):
    def flatten(wrapper):
        bot = wrapper['engine']
        row = {key: value for key, value in bot.items() if not isinstance(value, (list, dict))}
        duration = max(0, (bot['closed_ms'] if bot['closed_ms'] is not None else now_ms) - bot['opened_ms'])
        row.update(price=bot['last_price'], reserve_usdt=wrapper['reserve_usdt'],
                   source_section=wrapper['source_section'], equity=bot['equity'] + wrapper['reserve_usdt'],
                   peak_equity=wrapper.get('peak_equity', bot['peak_equity'] + wrapper['reserve_usdt']),
                   max_drawdown_pct=wrapper.get('max_drawdown_pct', 0.0),
                   pnl_curve=_downsample(wrapper.get('pnl_curve', []), 120),
                   net=net(bot), grids_per_hour=bot['completed_grids'] * HOUR_MS / duration if duration else 0)
        return row
    opened, closed = ([flatten(w) for w in state[key]] for key in ('open_bots', 'closed_bots'))
    def totals(rows):
        return dict(bots=len(rows), grids=sum(r['completed_grids'] for r in rows),
                    grid_profit=sum(r['grid_profit'] for r in rows), unrealized=sum(r['unrealized_pnl'] for r in rows),
                    fees=sum(r['fees_paid'] for r in rows), funding=sum(r['funding_paid'] for r in rows),
                    net=sum(r['net'] for r in rows), pnl=sum(r['net'] for r in rows),
                    grids_per_hour=sum(r['grids_per_hour'] for r in rows))
    visible_closed = closed[-20:]
    groups = {}
    for direction in ('LONG', 'SHORT', 'NEUTRAL'):
        op, cl = ([r for r in rows if r['direction'] == direction] for rows in (opened, closed))
        groups[direction] = dict(open_bots=op, closed_bots=[r for r in visible_closed if r['direction'] == direction],
                                 totals=totals(op + cl))
    equity = _equity(state)
    def change(hours):
        prior = [point[1] for point in state['equity_curve'] if point[0] <= now_ms - hours*HOUR_MS]
        base = prior[-1] if prior else PAPER_EQUITY_USDT
        return 100*(equity/base-1) if base else 0
    return dict(schema_version=1, generated_at_ms=now_ms, equity=equity,
                peak_equity=state['peak_equity'], max_drawdown_pct=state['max_drawdown_pct'],
                change_24h_pct=change(24), change_7d_pct=change(168),
                open_bots=opened, closed_bots=visible_closed, groups=groups, totals=totals(opened+closed),
                equity_curve=_downsample(state['equity_curve'], 2000), **health)
