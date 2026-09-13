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
                 'expected_grids_per_hour range_width_pct funding_rate kucoin_ok '
                 'influence_delta influence_clamped').split()


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


_STAMP_RE = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}')


def review_status(source):
    """Closed-vocabulary projection of the self-learning review status."""
    source = obj(source)
    result = numbers(source, ('last_run_at_ms',))
    stamp = source.get('doctrine_version')
    if stamp is not None and (not isinstance(stamp, str) or not _STAMP_RE.match(stamp) or len(stamp) > 40):
        raise ValueError('invalid doctrine version')
    counts = numbers(obj(source.get('proposals')), ('planned', 'applied', 'deferred', 'rejected'))
    if any(value is None or value < 0 or value != int(value) for value in counts.values()):
        raise ValueError('invalid proposal counts')
    active = obj(source.get('active_rules'))
    hold = active.get('min_hold_hours_before_non_risk_close')
    if hold is not None and (type(hold) not in (int, float) or not math.isfinite(hold) or hold <= 0):
        raise ValueError('invalid min hold rule')
    if not isinstance(active.get('require_trend_alignment'), bool):
        raise ValueError('invalid trend rule flag')
    cooldowns = rows(active.get('symbol_cooldowns', []))
    if len(cooldowns) > 24:
        raise ValueError('too many cooldowns')
    headline = source.get('evidence_headline')
    if not isinstance(headline, str) or not 0 < len(headline) <= 240:
        raise ValueError('invalid evidence headline')
    result.update(doctrine_version=stamp,
                  proposals={key: int(value) for key, value in counts.items()},
                  active_rules=dict(min_hold_hours_before_non_risk_close=hold,
                                    require_trend_alignment=active['require_trend_alignment'],
                                    symbol_cooldowns=[symbol(item) for item in cooldowns]),
                  evidence_headline=headline)
    return result


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


def _optional_non_negative(source, key):
    """Published as-is when present; absent on entries from an older scan."""
    value = number(source.get(key))
    if value is not None and value < 0:
        raise ValueError('invalid watchlist measurement')
    return value


def watch_entry(source):
    source = obj(source)
    result = numbers(source, ('score', 'since_ms', 'rank'))
    if result['score'] is None or not 0 <= result['score'] <= 100:
        raise ValueError('invalid watchlist score')
    result.update(symbol=symbol(source.get('symbol')),
                  direction=enum(source.get('direction'), LABELS),
                  score_parts=score_parts(source.get('score_parts', [])),
                  rank_score=_optional_non_negative(source, 'rank_score'),
                  expected_grids_per_hour=_optional_non_negative(
                      source, 'expected_grids_per_hour'))
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


def _boolean(value):
    if type(value) is not bool:
        raise ValueError('invalid public boolean')
    return value


def _digest(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{64}', value):
        raise ValueError('invalid evidence digest')
    return value


def entry_evidence(source):
    """Publish bounded facts; full entry records remain in the local paper ledger."""
    if source is None:
        return dict(status='MISSING', evidence_id=None)
    source = obj(source)
    status = enum(source.get('status'), ('RECORDED', 'MISSING'))
    if status == 'MISSING':
        return dict(status=status, evidence_id=None)
    result = dict(status=status, evidence_id=_digest(source.get('evidence_id')),
                  opened_ms=number(source.get('opened_ms')),
                  range_status=enum(source.get('range_status'), ('VERIFIED', 'MISSING')))
    layout = obj(source.get('layout'))
    result['layout'] = numbers(layout, ('range_low', 'range_high', 'grids',
        'maximum_pair_economic_count', 'interval', 'tick_size', 'lot_size',
        'contract_lots', 'contract_multiplier', 'quantity_per_grid', 'leverage',
        'allocated_margin_usdt'))
    structure = source.get('range_evidence')
    if structure is not None and result['range_status'] == 'VERIFIED':
        structure = obj(structure)
        result['range_provenance'] = dict(
            candles_sha256=_digest(structure.get('candles_sha256')),
            **numbers(structure, ('analysis_asof_ms', 'candle_asof_ms', 'window_start_ms',
                                  'window_end_ms', 'observed_candles', 'coverage_ratio')))
        for name in ('selected_support', 'selected_resistance'):
            level = obj(structure.get(name))
            confirmations = rows(level.get('confirmed_at_ms', []))
            if len(confirmations) > 168:
                raise ValueError('too many pivot confirmations')
            result['range_provenance'][name] = dict(
                **numbers(level, ('price', 'touches')),
                last_confirmed_at_ms=max((number(t) for t in confirmations), default=None))
    fees = obj(source.get('fees'))
    result['fees'] = numbers(fees, ('grid_fill_rate', 'seed_and_flatten_rate', 'reference_bot_rate'))
    pairs = obj(source.get('grid_pairs'))
    result['grid_pairs'] = dict(
        pair_count=number(pairs.get('pair_count')),
        min_long_margin_return_pct=number(obj(pairs.get('minimum_long_return_pair')).get('long_margin_return_pct')),
        min_short_margin_return_pct=number(obj(pairs.get('minimum_short_return_pair')).get('short_margin_return_pct')))
    seeded = rows(pairs.get('seeded_closes', []))
    if len(seeded) > 2:
        raise ValueError('too many seeded books')
    result['grid_pairs']['seeded_closes'] = [dict(
        direction=enum(obj(leg).get('direction'), ('LONG', 'SHORT')),
        **numbers(obj(leg['minimum_return_pair']), ('entry_price', 'exit_price',
                   'seed_and_close_fees_usdt', 'net_usdt', 'margin_return_pct'))) for leg in seeded]
    costs = obj(source.get('costs'))
    result['costs'] = numbers(costs, ('seed_fee_paid_usdt',
        'flatten_seed_inventory_same_price_fee_usdt', 'seed_gross_notional_usdt',
        'seed_signed_notional_usdt', 'observed_spread_pct',
        'seed_and_flatten_spread_cost_usdt', 'double_spread_cost_usdt'))
    funding = obj(costs.get('funding_4h'))
    result['costs']['funding_4h'] = dict(
        status=enum(funding.get('status'), ('SCENARIO_ONLY', 'UNKNOWN')),
        **numbers(funding, ('holding_window_ms', 'interval_ms', 'next_settlement_ms',
                           'observed_at_ms', 'observed_rate_pct', 'scheduled_settlements',
                           'constant_rate_seed_position_cost_usdt',
                           'adverse_absolute_rate_gross_cost_usdt')))
    result['costs']['future_actual_cost_usdt'] = None
    break_even = costs.get('break_even')
    if break_even is not None:
        break_even = obj(break_even)
        result['costs']['break_even'] = dict(
            status=enum(break_even.get('status'), ('CONDITIONAL', 'NONPOSITIVE_PAIR_NET')),
            **numbers(break_even, ('minimum_adjacent_pair_net_usdt',
                'seed_and_flatten_fee_cost_usdt', 'including_spread_cost_usdt',
                'including_spread_adverse_funding_4h_cost_usdt', 'fee_only_completed_pairs',
                'including_spread_completed_pairs', 'including_spread_adverse_funding_4h_completed_pairs')),
            actual_future_break_even_status='UNKNOWN', actual_future_completed_pairs=None,
            assumption='COMPLETE_PAIRS_NO_INVENTORY_LOSS_SAME_PRICE_FLATTEN')
    clusters = source.get('coinglass_liquidation_clusters')
    if clusters is not None:
        clusters = obj(clusters)
        result['coinglass_liquidation_clusters'] = dict(
            status=enum(clusters.get('status'), ('UNAVAILABLE_NOT_WIRED',)),
            heatmap_kind=enum(clusters.get('heatmap_kind'), ('MODELED_POTENTIAL_LIQUIDATION_LEVELS',)),
            historical_kind=enum(clusters.get('historical_kind'), ('REPORTED_PAST_LIQUIDATION_TOTALS',)),
            historical_totals_are_cluster_levels=False, cluster_levels=None, observed_at_ms=None)
    result['future_net_pnl_usdt'] = None
    result['interpretation'] = 'ENTRY_COST_SENSITIVITY_NOT_PROFIT_FORECAST'
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
        result['funding_schedule_status'] = enum(source['funding_schedule_status'], ('ESTIMATED','RECORDED','UNAVAILABLE','PENDING_RECONCILIATION'))
    if 'accounting_status' in source:
        result['accounting_status'] = enum(source['accounting_status'], ('PENDING_FUNDING_RECONCILIATION',))
    if 'recovery_incomplete_at_close' in source:
        result['recovery_incomplete_at_close'] = _boolean(source['recovery_incomplete_at_close'])
    result['setup_evidence'] = entry_evidence(source.get('setup_evidence'))
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
    for kind in ('funding', 'recovery'):
        pending_key, status_key = kind+'_reconciliation_pending', kind+'_reconciliation_status'
        if pending_key in source or status_key in source:
            count = source.get(pending_key)
            if type(count) is not int or count < 0 or count > 1000000:
                raise ValueError('invalid reconciliation count')
            result[pending_key] = count
            result[status_key] = enum(source.get(status_key), ('NO_RECORDED_FAILURE', 'PENDING'))
            if (count > 0) != (result[status_key] == 'PENDING'):
                raise ValueError('inconsistent reconciliation status')
    if 'setup_evidence_archive_status' in source:
        result['setup_evidence_archive_status'] = enum(source['setup_evidence_archive_status'],
            ('PENDING', 'COMPLETE', 'BLOCKED', 'CAPACITY_BLOCKED'))
    if source.get('review_status') is not None:
        result['review_status'] = review_status(source['review_status'])
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
            row.update(action=enum(item['action'], ('open', 'close', 'skip', 'influence')),
                       direction=enum(item['direction'], DIRECTIONS),
                       radar_direction=enum(item['radar_direction'], LABELS),
                       rule_blocks=[number(code) for code in rows(item['rule_blocks'])])
        result.append(row)
    return result
