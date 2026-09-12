"""Closed-vocabulary, bounded public projections of paper autopilot snapshots."""
import math
import re

from trader.autopilot.storage import validate_event
from trader.autopilot.constants import PAPER_EQUITY_USDT

DIRECTIONS = ('LONG', 'SHORT', 'NEUTRAL')
LABELS = DIRECTIONS + ('TURNING-UP', 'TURNING-DOWN')
REASONS = ('LABEL_FLIP', 'RANGE_BREAK', 'STOP_LOSS', 'DROPPED', 'MAX_AGE', 'MANUAL', 'PROFILE_UPDATE', 'RISK_LIMIT')
CODES = ('OSCILLATION', 'TREND_CLARITY', 'LIQUIDITY_TURNOVER', 'LIQUIDITY_SPREAD',
         'ROOM', 'FUNDING', 'STABILITY', 'MOVER_RISK', 'YOUNG_LISTING',
         'STALE_DATA', 'MAJOR_LOW_YIELD')
WATCH_EVENTS = ('PROMOTE', 'DEMOTE', 'DROP', 'DIRECTION_CHANGE')
BOT_NUMBERS = ('opening_price accounting_version contract_lots contract_multiplier quantity_is_observed reserve_added_usdt funding_interval_ms next_funding_ms long_contracts short_contracts long_avg_entry short_avg_entry grid_interval profit_pct_min profit_pct_max bot_id price range_low range_high step_pct grids completed_grids '
    'realized_pnl unrealized_pnl grid_profit fees_paid funding_paid opened_ms closed_ms '
    'notional_usdt leverage reserve_usdt equity peak_equity max_drawdown_pct '
    'net grids_per_hour position_contracts avg_entry fills funding_pct contracts_per_line empty_line range_verified').split()
TOTAL_NUMBERS = 'bots grids grid_profit unrealized fees funding net pnl grids_per_hour'.split()
EVENT_NUMBERS = ('amount ts_ms bot_id event_id price contracts fee profit equity net '
                 'completed_grids realized_pnl unrealized_pnl tick_age_s side line reason_code '
                 'book kucoin_down_since_ms code score replaced_score margin radar_score '
                 'expected_grids_per_hour range_width_pct funding_rate kucoin_ok').split()


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


def hourly(source):
    buckets = rows(source)
    validated = []
    for bucket in buckets:
        if not isinstance(bucket, (list, tuple)) or len(bucket) != 5:
            raise ValueError('invalid hourly bucket')
        start, opened, high, low, close = map(number, bucket)
        if start is None or start < 0 or None in (opened, high, low, close):
            raise ValueError('invalid hourly bucket')
        if high < low or high < max(opened, close) or low > min(opened, close):
            raise ValueError('invalid hourly bucket range')
        if validated and start <= validated[-1][0]:
            raise ValueError('unordered hourly buckets')
        validated.append([start, opened, high, low, close])
    if len(validated) <= 168:
        return validated
    return [validated[i * (len(validated)-1)//(168-1)] for i in range(168)]


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
    if 'order_ladder' in source:
        result['order_ladder'] = [numbers(obj(x), ('line','price','side','book')) for x in rows(source['order_ladder'])[:400]]
    if 'funding_schedule_status' in source:
        result['funding_schedule_status'] = enum(source['funding_schedule_status'], ('ESTIMATED','RECORDED','UNAVAILABLE'))
    if 'liquidation' in source:
        risk = obj(source['liquidation'])
        result['liquidation'] = numbers(risk, ('price', 'with_reserve_price', 'mmr', 'fee_rate', 'metadata_at_ms','lower_price','upper_price','lower_with_reserve','upper_with_reserve'))
        result['liquidation']['status'] = enum(risk.get('status'), ('HEDGE_ESTIMATED', 'ESTIMATED', 'FLAT', 'NO_POSITIVE_PRICE', 'STALE_METADATA', 'INVALID_METADATA', 'INVALID_POSITION', 'TIER_UNAVAILABLE', 'METADATA_UNAVAILABLE'))
    if source.get('reason') is not None:
        result['reason'] = enum(source['reason'], REASONS)
    return result


def _newest(rows, limit, stamp_key):
    """Deterministically keep the newest rows by stamp, preserving source order."""
    if len(rows) <= limit:
        return rows, 0
    keep = sorted(sorted(range(len(rows)), key=lambda i: rows[i].get(stamp_key) or 0,
                         reverse=True)[:limit])
    return [rows[i] for i in keep], len(rows) - limit


def safe(source):
    source = obj(source)
    if source.get('schema_version') != 1:
        raise ValueError('invalid snapshot schema')
    opened, closed = rows(source.get('open_bots', [])), rows(source.get('closed_bots', []))
    if len(opened) > 5:
        raise ValueError('public bot limit exceeded')
    closed, dropped_closed = _newest(closed, 20, 'closed_ms')
    result = numbers(source, ('generated_at_ms', 'equity', 'change_24h_pct', 'change_7d_pct',
        'peak_equity', 'max_drawdown_pct', 'heartbeat_ms', 'tick_age_s', 'radar_age_min',
        'watchlist_asof_ms'))
    result.update(schema_version=1, kucoin_ok=source.get('kucoin_ok') is True,
        recovery_pending=source.get('recovery_pending') is True,
        open_bots=[bot(row) for row in opened], closed_bots=[bot(row) for row in closed],
        equity_curve=curve(source.get('equity_curve', []), 2000),
        equity_hourly=hourly(source.get('equity_hourly', [])),
        totals=numbers(obj(source.get('totals', {})), TOTAL_NUMBERS), groups={}, watchlist={})
    for direction in DIRECTIONS:
        group = obj(obj(source.get('groups', {})).get(direction, {}))
        result['groups'][direction] = dict(
            open_bots=[b for b in result['open_bots'] if b['direction'] == direction],
            closed_bots=[b for b in result['closed_bots'] if b['direction'] == direction],
            totals=numbers(obj(group.get('totals', {})), TOTAL_NUMBERS))
    dropped_watchlist = 0
    for tier in ('core', 'bench'):
        entries = rows(obj(source.get('watchlist', {})).get(tier, []))
        if len(entries) > 5:
            dropped_watchlist += len(entries) - 5
            entries = entries[:5]
        result['watchlist'][tier] = [watch_entry(entry) for entry in entries]
    result['truncated'] = dict(closed_bots=dropped_closed, watchlist=dropped_watchlist)
    result['watchlist_history'] = [watch_event(row)
        for row in rows(source.get('watchlist_history', []))[-48:]]
    def total(key):
        values = [b[key] for b in result['open_bots']]
        return sum(values) if all(v is not None for v in values) else None
    floating, margin, reserve = total('unrealized_pnl'), total('notional_usdt'), total('reserve_usdt')
    if margin is not None:
        margin += sum(b.get('reserve_added_usdt') or 0 for b in result['open_bots'])
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
        if item['type'] == 'DECISION':
            row.update(action=enum(item['action'], ('open', 'close', 'skip')),
                       direction=enum(item['direction'], DIRECTIONS),
                       radar_direction=enum(item['radar_direction'], LABELS),
                       rule_blocks=[number(code) for code in item['rule_blocks']])
        result.append(row)
    return result
