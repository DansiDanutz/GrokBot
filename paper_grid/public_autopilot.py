"""Closed-vocabulary, bounded public projections of paper autopilot snapshots."""
import math
import re

from trader.autopilot.storage import validate_event
from trader.autopilot.constants import PAPER_EQUITY_USDT

DIRECTIONS = ('LONG', 'SHORT', 'NEUTRAL')
LABELS = DIRECTIONS + ('TURNING-UP', 'TURNING-DOWN')
REASONS = ('LABEL_FLIP', 'RANGE_BREAK', 'STOP_LOSS', 'DROPPED', 'MAX_AGE', 'MANUAL')
CODES = ('OSCILLATION', 'TREND_CLARITY', 'LIQUIDITY_TURNOVER', 'LIQUIDITY_SPREAD',
         'ROOM', 'FUNDING', 'STABILITY', 'MOVER_RISK', 'YOUNG_LISTING',
         'STALE_DATA', 'MAJOR_LOW_YIELD')
WATCH_EVENTS = ('PROMOTE', 'DEMOTE', 'DROP', 'DIRECTION_CHANGE')
BOT_NUMBERS = ('bot_id price range_low range_high step_pct grids completed_grids '
    'realized_pnl unrealized_pnl grid_profit fees_paid funding_paid opened_ms closed_ms '
    'notional_usdt leverage reserve_usdt equity peak_equity max_drawdown_pct '
    'net grids_per_hour position_contracts avg_entry fills funding_pct').split()
TOTAL_NUMBERS = 'bots grids grid_profit unrealized fees funding net pnl grids_per_hour'.split()
EVENT_NUMBERS = ('ts_ms bot_id event_id price contracts fee profit equity net '
                 'completed_grids realized_pnl unrealized_pnl tick_age_s side line reason_code '
                 'kucoin_down_since_ms code score replaced_score margin').split()


def number(value):
    if value is None:
        return None
    if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 1e15:
        raise ValueError('invalid public number')
    return value


def obj(value):
    if not isinstance(value, dict):
        raise ValueError('invalid public object')
    return value


def rows(value):
    if not isinstance(value, list):
        raise ValueError('invalid public list')
    return value


def symbol(value, empty=False, system=False):
    if empty and value == '':
        return value
    if system and value == 'SYSTEM':
        return value
    if not isinstance(value, str) or not re.fullmatch(r'[A-Z0-9]{1,24}USDTM', value):
        raise ValueError('invalid public symbol')
    return value


def enum(value, choices):
    if not isinstance(value, str) or value not in choices:
        raise ValueError('invalid public enum')
    return value


def numbers(source, keys):
    return {key: number(source.get(key)) for key in keys}


def curve(source, maximum):
    points = rows(source)
    if len(points) > 43201:
        raise ValueError('curve too large')
    validated = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError('invalid curve point')
        at, value = map(number, point)
        if at is None or value is None or at < 0:
            raise ValueError('invalid curve point')
        if validated and at < validated[-1][0]:
            raise ValueError('unordered curve')
        validated.append([at, value])
    if len(validated) <= maximum:
        return validated
    return [validated[i * (len(validated)-1)//(maximum-1)] for i in range(maximum)]


def score_parts(source):
    parts = rows(source)
    if len(parts) > len(CODES):
        raise ValueError('too many score parts')
    result = []
    for part in parts:
        part = obj(part)
        value, points = number(part.get('value')), number(part.get('points'))
        if value is None or points is None or not -20 <= points <= 30:
            raise ValueError('invalid score part')
        result.append(dict(code=enum(part.get('code'), CODES), value=value, points=points))
    if len({p['code'] for p in result}) != len(result):
        raise ValueError('duplicate score code')
    return result


def watch_entry(source):
    source = obj(source)
    result = numbers(source, ('score', 'since_ms', 'rank'))
    if result['score'] is None or not 0 <= result['score'] <= 100:
        raise ValueError('invalid watchlist score')
    result.update(symbol=symbol(source.get('symbol')),
                  direction=enum(source.get('direction'), LABELS),
                  score_parts=score_parts(source.get('score_parts', [])))
    return result


def watch_event(source):
    source = obj(source)
    result = numbers(source, ('ts_ms', 'score', 'replaced_score', 'margin'))
    if any(result[k] is None or not 0 <= result[k] <= 100
           for k in ('score', 'replaced_score')):
        raise ValueError('invalid watchlist event score')
    result.update(type=enum(source.get('type'), WATCH_EVENTS),
                  symbol=symbol(source.get('symbol')),
                  replaced_symbol=symbol(source.get('replaced_symbol'), empty=True))
    return result


def bot(source):
    source = obj(source)
    result = numbers(source, BOT_NUMBERS)
    result.update(symbol=symbol(source.get('symbol')),
                  direction=enum(source.get('direction'), DIRECTIONS),
                  pnl_curve=curve(source.get('pnl_curve', []), 120))
    if source.get('reason') is not None:
        result['reason'] = enum(source['reason'], REASONS)
    return result


def safe(source):
    source = obj(source)
    if source.get('schema_version') != 1:
        raise ValueError('invalid snapshot schema')
    opened, closed = rows(source.get('open_bots', [])), rows(source.get('closed_bots', []))
    if len(opened) > 5 or len(closed) > 20:
        raise ValueError('public bot limit exceeded')
    result = numbers(source, ('generated_at_ms', 'equity', 'change_24h_pct', 'change_7d_pct',
        'peak_equity', 'max_drawdown_pct', 'heartbeat_ms', 'tick_age_s', 'radar_age_min',
        'watchlist_asof_ms'))
    result.update(schema_version=1, kucoin_ok=source.get('kucoin_ok') is True,
        recovery_pending=source.get('recovery_pending') is True,
        open_bots=[bot(row) for row in opened], closed_bots=[bot(row) for row in closed],
        equity_curve=curve(source.get('equity_curve', []), 2000),
        totals=numbers(obj(source.get('totals', {})), TOTAL_NUMBERS), groups={}, watchlist={})
    for direction in DIRECTIONS:
        group = obj(obj(source.get('groups', {})).get(direction, {}))
        result['groups'][direction] = dict(
            open_bots=[b for b in result['open_bots'] if b['direction'] == direction],
            closed_bots=[b for b in result['closed_bots'] if b['direction'] == direction],
            totals=numbers(obj(group.get('totals', {})), TOTAL_NUMBERS))
    for tier in ('core', 'bench'):
        entries = rows(obj(source.get('watchlist', {})).get(tier, []))
        if len(entries) > 5:
            raise ValueError('watchlist limit exceeded')
        result['watchlist'][tier] = [watch_entry(entry) for entry in entries]
    result['watchlist_history'] = [watch_event(row)
        for row in rows(source.get('watchlist_history', []))[-48:]]
    def total(key):
        values = [b[key] for b in result['open_bots']]
        return sum(values) if all(v is not None for v in values) else None
    floating, margin, reserve = total('unrealized_pnl'), total('notional_usdt'), total('reserve_usdt')
    equity = result['equity']
    balance = equity - floating if equity is not None and floating is not None else None
    fees, funding = result['totals']['fees'], result['totals']['funding']
    result['account'] = dict(starting_equity=PAPER_EQUITY_USDT, equity=equity,
        balance=balance, allocated_margin=margin, reserved_margin=reserve,
        free_balance=balance-margin-reserve if all(v is not None for v in (balance, margin, reserve)) else None,
        realized_pnl=balance-PAPER_EQUITY_USDT if balance is not None else None,
        unrealized_pnl=floating, fees=fees, funding=funding)
    return result


def events(source):
    if len(rows(source)) > 500:
        raise ValueError('event limit exceeded')
    result = []
    for item in source:
        item = validate_event(item)
        row = {key: number(item[key]) for key in EVENT_NUMBERS if key in item}
        row.update(type=item['type'], symbol=symbol(item['symbol'], system=True))
        if item['type'] in WATCH_EVENTS:
            row['replaced_symbol'] = symbol(item['replaced_symbol'], empty=True)
        result.append(row)
    return result
