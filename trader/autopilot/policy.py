"""Immutable portfolio rules. Wrappers preserve Phase A's accounting unchanged.

Each open/closed wrapper has engine, reserve_usdt, source_section, slot_direction,
the radar label at open and latched signals. A borrowed slot retains its original direction until closed.

Learned-rule gates (trader.review.rules) bind here: trend alignment and
symbol cooldowns gate new opens in decide(), and a minimum hold time gates
non-risk closes in both decide() and advance(). With no valid rules store
all gates are inert, so defaults are unchanged.
"""
from copy import deepcopy
import math
from datetime import datetime

from trader.papergrid import open_bot, close_bot, step
from trader.radar.rates import expected_grids_per_hour
from trader.radar.scoring import score_row
from trader.radar.spacing import economics
from trader.radar.layout import layout_valid
from trader.autopilot import watchlist
from trader.autopilot import setup_evidence
from trader.autopilot import hedge, influence
from trader.autopilot.risk import sizing, ACCOUNTING_VERSION, protection_needed
from trader.review import rules as learned_rules
from trader.autopilot.constants import (
    PAPER_EQUITY_USDT, MAX_BOTS, NOTIONAL_PER_BOT_USDT, SLOTS, DIRECTION_CAP,
    LEVERAGE_TREND, LEVERAGE_NEUTRAL, STEP_NEUTRAL_PCT, NEUTRAL_RESERVE_USDT,
    MAJORS, MAJORS_MAX, MOVERS_MAX, MIN_EXPECTED_GRIDS_PER_HOUR,
    COOLDOWN_HOURS, MAX_AGE_HOURS, OPPORTUNITY_COST_CLOSE,
    OPPORTUNITY_HOLD_MAX_AGE_HOURS, AGENT_INFLUENCE_ENABLED, INFLUENCE_ERROR,
)

HOUR_MS = 3_600_000
RETENTION_MS = 30 * 24 * HOUR_MS

# Closes that protect the account: exempt from the learned min-hold gate.
RISK_CLOSE_REASONS = frozenset({'STOP_LOSS', 'RANGE_BREAK', 'RISK_LIMIT'})
_OPPOSING_LABELS = {'LONG': ('SHORT', 'TURNING-DOWN'),
                    'SHORT': ('LONG', 'TURNING-UP')}
DECISION_RULES = {
    'range_not_verified': 1,
    'insufficient_equity': 2,
    'bot_capacity': 3,
    'missing_liquidity': 4,
    'duplicate_symbol': 5,
    'cooldown': 6,
    'direction_cap': 7,
    'major_cap': 8,
    'movers_cap': 9,
    'low_grid_rate': 10,
    'invalid_profile': 11,
    'invalid_layout': 12,
    'missing_live_price': 13,
    'no_candidates': 14,
    'missing_radar': 15,
    'learned_trend_alignment': 16,
    'learned_symbol_cooldown': 17,
    'learned_min_hold': 18,
    'opportunity_hold': 19,
    'hedge_trigger': 20,
    'influence_boost': 21,
    'influence_veto': 22,
}
# Non-risk closes the opportunity-cost gate may defer. PROFILE_UPDATE rebuilds a
# stale specification rather than abandoning a coin, so it stays unconditional.
OPPORTUNITY_CLOSE_REASONS = frozenset({'LABEL_FLIP', 'DROPPED', 'MAX_AGE'})
DECISION_CLOSE_REASONS = {
    'LABEL_FLIP': 101,
    'RANGE_BREAK': 102,
    'STOP_LOSS': 103,
    'DROPPED': 104,
    'MAX_AGE': 105,
    'MANUAL': 106,
    'PROFILE_UPDATE': 107,
    'RISK_LIMIT': 108,
    'STALL_EXIT': 109,
}


def _range_width_pct(low, high, price):
    return 0.0 if not price else 100.0 * (high - low) / price


def _decision_context(row, direction, state):
    return {
        'direction': direction,
        'radar_direction': row.get('direction', 'NEUTRAL'),
        'radar_score': float(row.get('score', row.get('rank_score', 0.0)) or 0.0),
        'expected_grids_per_hour': float(row.get('expected_grids_per_hour', 0.0) or 0.0),
        'range_width_pct': float(_range_width_pct(
            row.get('range_low', 0.0), row.get('range_high', 0.0), row.get('price', 0.0))),
        'funding_rate': float(row.get('funding_pct', 0.0) or 0.0),
        'kucoin_ok': 1 if state.get('runtime', {}).get('kucoin_ok') is True else 0,
    }


def _decision_event(now_ms, bot_id, symbol, action, context, rule_blocks=()):
    return dict(ts_ms=now_ms, bot_id=bot_id, symbol=symbol, type='DECISION',
                action=action, **context,
                rule_blocks=sorted(set(int(code) for code in rule_blocks)))


def _wrapper_decision(state, wrapper, now_ms, action, rule_blocks):
    bot = wrapper['engine']
    context = wrapper.get('decision_context') or {
        'direction': wrapper.get('slot_direction', bot['direction']),
        'radar_direction': wrapper.get('open_label') or bot['direction'],
        'radar_score': 0.0,
        'expected_grids_per_hour': 0.0,
        'range_width_pct': _range_width_pct(
            bot['range_low'], bot['range_high'], bot.get('opening_price', bot['last_price'])),
        'funding_rate': float(bot.get('funding_pct', 0.0) or 0.0),
        'kucoin_ok': 1 if state.get('runtime', {}).get('kucoin_ok') is True else 0,
    }
    return _decision_event(now_ms, bot['bot_id'], bot['symbol'], action,
                           context, rule_blocks)


def _learned_rules():
    """Load the learned-rules store once per decide/advance cycle.

    Fail-closed: missing/invalid store yields empty rules (no behavior
    change). Tests may patch this to inject a synthetic store.
    """
    return learned_rules.load_rules(learned_rules.DEFAULT_STORE_PATH)


def _rule_block_event(now_ms, bot, rule_code, **fields):
    """Numeric-fields-only event per the events.jsonl schema (no strings)."""
    event = dict(ts_ms=now_ms, bot_id=bot['bot_id'], symbol=bot['symbol'],
                 type='RULE_BLOCK', rule=rule_code)
    event.update(fields)
    return event


def _hold_gate(state, wrapper, reason, rules, now_ms, events):
    """Refuse non-risk closes on bots younger than the learned min hold.

    Returns the reason unchanged when the close is allowed, None when the
    learned rule blocked it. Blocks are logged once per bot per rule via the
    wrapper's rule_blocks latch so events don't spam every cycle.
    """
    if not reason or reason in RISK_CLOSE_REASONS:
        return reason
    min_hold = rules.get('min_hold_hours_before_non_risk_close')
    if min_hold is None:
        return reason
    bot = wrapper['engine']
    hold_hours = (now_ms - bot['opened_ms']) / HOUR_MS
    if hold_hours >= min_hold:
        return reason
    latch = wrapper.setdefault('rule_blocks', {})
    if not latch.get('hold'):
        latch['hold'] = 1
        events.append(_rule_block_event(now_ms, bot, 1,
                                        hold_hours=round(hold_hours, 4),
                                        min_hold_hours=min_hold))
    events.append(_wrapper_decision(state, wrapper, now_ms, 'skip',
                                    [DECISION_RULES['learned_min_hold']]))
    return None


def _stall_exit(wrapper, rules, now_ms, events):
    """Learned early exit for a grid stalled in a trending market.

    Evidence (reports/range-break-discriminators-2026-09-18.md): range-break
    winners ran 8.5 grids/hour median vs 3.9 for losers; geometry did not
    discriminate. Fires only when the store carries a stall_exit object -
    absent or null keeps today's behavior exactly."""
    config = rules.get('stall_exit')
    if not isinstance(config, dict):
        return None
    bot = wrapper['engine']
    hours = (now_ms - bot['opened_ms']) / HOUR_MS
    if hours < config['warmup_hours']:
        return None
    if bot['completed_grids'] / hours >= config['min_grids_per_hour']:
        return None
    span = bot['range_high'] - bot['range_low']
    if span <= 0:
        return None
    position = (bot['last_price'] - bot['range_low']) / span
    travel = config['adverse_travel']
    if bot['direction'] == 'LONG':
        adverse = position <= 1 - travel
    elif bot['direction'] == 'SHORT':
        adverse = position >= travel
    else:
        adverse = position <= 1 - travel or position >= travel
    if not adverse:
        return None
    events.append(_rule_block_event(now_ms, bot, 2,
                                    hold_hours=round(hours, 4),
                                    grids_per_hour=round(bot['completed_grids'] / hours, 4),
                                    range_position=round(position, 4)))
    return 'STALL_EXIT'


def _opportunity_enabled(rules):
    """Learned override wins when present; the constant decides otherwise."""
    override = rules.get('opportunity_cost_close')
    return OPPORTUNITY_COST_CLOSE if override is None else bool(override)


def _opportunity_gate(state, sections, wrapper, reason, labels, rules, now_ms, scan_id, events):
    """Defer a non-risk close while no better coin is free to take the slot.

    Returns the reason unchanged when the close may proceed, None when the bot
    is kept. The bot keeps trading its grids; the normal path resumes as soon as
    the label flips back or a better candidate appears. One RULE_BLOCK per bot
    per radar scan, latched on the wrapper like the min-hold gate.

    A MAX_AGE close is deferred only up to OPPORTUNITY_HOLD_MAX_AGE_HOURS, so a
    thin market cannot hold a stale position open indefinitely.
    """
    from trader.autopilot import opportunity  # local: opportunity imports policy
    if reason not in OPPORTUNITY_CLOSE_REASONS or not _opportunity_enabled(rules):
        return reason
    if (reason == 'MAX_AGE' and now_ms - wrapper['engine']['opened_ms']
            >= OPPORTUNITY_HOLD_MAX_AGE_HOURS * HOUR_MS):
        return reason  # stale beyond the ceiling: it leaves regardless
    better, detail = opportunity.better_candidate_exists(
        state, sections, wrapper, labels, now_ms, rules)
    if better:
        return reason
    bot, latch = wrapper['engine'], wrapper.setdefault('rule_blocks', {})
    if latch.get('opportunity_scan') != str(scan_id):
        latch['opportunity_scan'], latch['opportunity'] = str(scan_id), 0
    if not latch.get('opportunity'):
        latch['opportunity'] = 1
        events.append(_rule_block_event(
            now_ms, bot, DECISION_RULES['opportunity_hold'],
            bot_score=round(detail['bot_score'], 4),
            best_candidate_score=round(detail['candidate_score'], 4)))
    events.append(_wrapper_decision(state, wrapper, now_ms, 'skip',
                                    [DECISION_RULES['opportunity_hold']]))
    return None


def _hedge_gate(state, wrapper, row, generated_ms, prices, rules, now_ms, events):
    """Offset adverse inventory instead of riding it (trader.autopilot.hedge).

    Default OFF: constants.HEDGE_ENABLED is False and the learned override
    `hedge_enabled` is absent from EMPTY_RULES, so with no store this returns
    before touching anything and decide() stays byte-identical.
    """
    if not hedge.enabled(rules):
        return
    bot = wrapper['engine']
    clusters = None if row is None else dict(
        row, liq_clusters_generated_at_ms=generated_ms)
    fired, _ = hedge.should_hedge(bot, clusters, rules, now_ms)
    if not fired:
        return
    wrapper['engine'], emitted = hedge.hedge_leg(
        bot, prices.get(bot['symbol'], bot['last_price']), now_ms)
    _mark_wrapper(wrapper)
    events.extend(emitted)
    events.append(_wrapper_decision(state, wrapper, now_ms, 'hedge',
                                    [DECISION_RULES['hedge_trigger']]))


def _influence_enabled(rules):
    """Learned override wins when present; the constant decides otherwise."""
    override = rules.get('agent_influence_enabled')
    return AGENT_INFLUENCE_ENABLED if override is None else bool(override)


def _influence_records(now_ms, known_symbols):
    """Read the steward's sanitized file once per scan. Tests patch this."""
    return influence.load(influence.DEFAULT_PATH, now_ms, known_symbols)


def _apply_influence(state, sections, labels, rules, now_ms, events):
    """Let the review agents reorder or veto ALREADY ADMITTED candidates.

    Returns (sections, applied). Off by default: with the switch off no file is
    read at all and the sections are returned unchanged, and a scan that admitted
    nothing (a missing radar, say) has nothing to influence and reads no file
    either. Fail-closed: a rejected file leaves the sections unchanged after one
    ERROR event. Every applied influence emits a DECISION action='influence'
    carrying numeric fields plus the agent id. Structural and risk gates are
    untouched -- fill() still runs _eligibility over whatever survives.
    """
    if not _influence_enabled(rules) or not any(sections.values()):
        return sections, []
    records, problems = _influence_records(now_ms, set(labels))
    if problems:
        events.append(dict(ts_ms=now_ms, bot_id=0, symbol='SYSTEM', type='ERROR',
                           code=INFLUENCE_ERROR, problems=len(problems)))
        return sections, []
    adjusted, applied = influence.apply(sections, records, now_ms)
    rows = {row['symbol']: row for name in sections for row in sections[name]}
    for entry in applied:
        code = DECISION_RULES['influence_veto' if entry['verb'] == 'VETO'
                              else 'influence_boost']
        event = _decision_event(
            now_ms, 0, entry['symbol'], 'influence',
            _decision_context(rows[entry['symbol']], entry['direction'], state), [code])
        event.update(agent_id=entry['agent_id'], reason_code=entry['reason_code'],
                     influence_delta=entry['delta'], influence_clamped=entry['clamped'])
        events.append(event)
    return adjusted, applied


def _trend_gate_blocks(direction, row, labels, rules):
    """True when a NEW open disagrees with the radar direction label.

    NEUTRAL opens are exempt; missing/stale radar data never blocks.
    """
    if not rules.get('require_trend_alignment') or direction == 'NEUTRAL':
        return False
    label = labels.get(row['symbol'])
    if not label:
        return False
    return label in _OPPOSING_LABELS.get(direction, ())


def _cooldown_gate_blocks(row, rules, now_ms):
    """True while a learned symbol cooldown still covers today."""
    until = (rules.get('symbol_cooldowns') or {}).get(row['symbol'])
    if not until:
        return False
    return datetime.fromtimestamp(now_ms / 1000).date().isoformat() <= until


def new_state(now_ms):
    return dict(schema_version=1, started_ms=now_ms, open_bots=[], closed_bots=[],
                cooldowns={}, radar_seen={}, equity_curve=[], equity_hourly=[],
                next_bot_id=1,
                archived_net=0.0, peak_equity=PAPER_EQUITY_USDT, max_drawdown_pct=0.0,
                watchlist=watchlist.initial())


def net(bot):
    return bot['realized_pnl'] + bot['unrealized_pnl'] - bot['fees_paid'] - bot['funding_paid']


def profile(row, direction, bot_id):
    """Return the validated paper specification and committed reserve."""
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
    grids, leverage, reserve = row['grids'], LEVERAGE_TREND, NEUTRAL_RESERVE_USDT
    required = ('support', 'resistance')
    for name in required:
        value = row.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError('missing structural boundary')
    if low != row['support'] or high != row['resistance']:
        raise ValueError('range does not match structure')
    if direction == 'NEUTRAL':
        low, high = row['support'], row['resistance']
        step_pct, leverage, reserve = STEP_NEUTRAL_PCT, LEVERAGE_NEUTRAL, NEUTRAL_RESERVE_USDT
        if low <= 0 or high <= low:
            raise ValueError('invalid neutral range')
    spacing = economics(low, high, grids, tick_size=row.get("tick_size", 0), leverage=leverage, direction=direction)
    if not spacing['viable']:
        raise ValueError('minimum grid return must exceed 1 percent after both fees')
    step_pct = spacing['interval'] / price * 100
    size = sizing(row, direction, NOTIONAL_PER_BOT_USDT, leverage, grids)
    return dict(**size, funding_managed=True, bot_id=bot_id, symbol=row['symbol'], direction=direction,
                range_low=low, range_high=high, step_pct=step_pct, grids=grids,
                grid_interval=spacing["interval"], profit_pct_min=spacing["profit_pct_min"],
                profit_pct_max=spacing["profit_pct_max"],
                notional_usdt=NOTIONAL_PER_BOT_USDT, leverage=leverage,
                funding_pct=row['funding_pct']), reserve


def eligible(state, row, direction, source_section, now_ms):
    """Admission shared by slot selection, including explicit trend movers caps."""
    return _eligibility(state, row, direction, source_section, now_ms)[0]


def _eligibility(state, row, direction, source_section, now_ms):
    blocks = []
    if row.get('range_verified') != 1:
        blocks.append(DECISION_RULES['range_not_verified'])
    bots = [item['engine'] for item in state['open_bots']]
    allocated = sum(w['engine']['notional_usdt'] + w['engine'].get('reserve_added_usdt',0) + w['reserve_usdt'] for w in state['open_bots'])
    if _equity(state) - allocated < NOTIONAL_PER_BOT_USDT + NEUTRAL_RESERVE_USDT:
        blocks.append(DECISION_RULES['insufficient_equity'])
    if len(bots) >= MAX_BOTS:
        blocks.append(DECISION_RULES['bot_capacity'])
    if not row.get('passes_liquidity', False):
        blocks.append(DECISION_RULES['missing_liquidity'])
    if any(bot['symbol'] == row['symbol'] for bot in bots):
        blocks.append(DECISION_RULES['duplicate_symbol'])
    if state['cooldowns'].get(row['symbol'], 0) > now_ms:
        blocks.append(DECISION_RULES['cooldown'])
    if sum(bot['direction'] == direction for bot in bots) >= DIRECTION_CAP:
        blocks.append(DECISION_RULES['direction_cap'])
    if row['symbol'] in MAJORS and sum(bot['symbol'] in MAJORS for bot in bots) >= MAJORS_MAX:
        blocks.append(DECISION_RULES['major_cap'])
    if (direction != 'NEUTRAL' and source_section == 'movers'
            and sum(item['source_section'] == 'movers' and item['engine']['direction'] != 'NEUTRAL'
                    for item in state['open_bots']) >= MOVERS_MAX):
        blocks.append(DECISION_RULES['movers_cap'])
    try:
        spec, _ = profile(row, direction, 0)
        if not setup_evidence.range_valid(row, spec, now_ms):
            blocks.append(DECISION_RULES['range_not_verified'])
        rate = expected_grids_per_hour(row['atr_1h_pct'], spec['step_pct'], row['turnover_24h_usdt'])
        if rate < MIN_EXPECTED_GRIDS_PER_HOUR:
            blocks.append(DECISION_RULES['low_grid_rate'])
        if not (spec['range_low'] < row['price'] < spec['range_high']):
            blocks.append(DECISION_RULES['invalid_layout'])
        if not layout_valid(spec['range_low'], spec['grid_interval'], spec['grids'], row['price'], direction):
            blocks.append(DECISION_RULES['invalid_profile'])
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        blocks.append(DECISION_RULES['invalid_profile'])
    return not blocks, sorted(set(blocks))


def _candidate_sections(radar, rows):
    """Use the full radar universe, not the presentation's top-eight truncation."""
    sections = {name: {} for name in ('turning_up', 'long', 'turning_down', 'short', 'neutral', 'movers')}
    mapping = {'LONG': 'long', 'SHORT': 'short', 'TURNING-UP': 'turning_up',
               'TURNING-DOWN': 'turning_down', 'NEUTRAL': 'neutral'}
    for row in rows:
        section = mapping.get(row['direction'])
        if section and (section != 'neutral' or .25 <= row['position_7d'] <= .75):
            sections[section][row['symbol']] = row
        if abs(row['change_24h_pct']) > 15:
            sections['movers'][row['symbol']] = row
    # Explicit section membership preserves the producer's Movers classification.
    by_symbol = {r['symbol']: r for r in rows}
    for row in radar.get('sections', {}).get('movers', []):
        if row['symbol'] in by_symbol:
            sections['movers'][row['symbol']] = by_symbol[row['symbol']]
    return {name: list(items.values()) for name, items in sections.items()}


def _candidates(sections, direction):
    names = {'LONG': ('turning_up', 'long'), 'SHORT': ('turning_down', 'short'),
             'NEUTRAL': ('neutral', 'movers')}[direction]
    for name in names:
        key = 'atr_1h_pct' if name == 'movers' else 'rank_score'
        # influence_delta (T8) only ever reorders rows INSIDE this section; the
        # underlying measurement is never rewritten, so every gate still sees
        # the radar's own numbers. Absent delta == the pre-T8 ordering exactly.
        for row in sorted(sections.get(name, []),
                          key=lambda r: (-(r[key] + r.get('influence_delta', 0.0)),
                                         r['symbol'])):
            yield name, row


def _qualified_rows(radar, rows, now_ms):
    scored = []
    for row in rows:
        if not row.get('passes_liquidity', False):
            continue
        try:
            scored.append(dict(row, **score_row(row)))
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            continue
    sections = _candidate_sections(radar, scored)
    allowed = set()
    for direction in SLOTS:
        for section, row in _candidates(sections, direction):
            if eligible(new_state(now_ms), row, direction, section, now_ms):
                allowed.add(row['symbol'])
    return [r for r in scored if r['symbol'] in allowed]


def _mark_wrapper(wrapper):
    equity = wrapper['engine']['equity'] + wrapper['reserve_usdt']
    initial = wrapper['engine']['notional_usdt'] + wrapper['engine'].get('reserve_added_usdt',0) + wrapper['reserve_usdt']
    peak = max(wrapper.get('peak_equity', initial), equity)
    wrapper['peak_equity'] = peak
    wrapper['max_drawdown_pct'] = max(wrapper.get('max_drawdown_pct', 0.0), 100*(peak-equity)/peak)


def _reason(wrapper, labels, missing, now_ms, flip_hysteresis=None):
    bot = wrapper['engine']
    for signal in ('RANGE_BREAK',):
        if signal in wrapper['signals']:
            return signal
    opposing = {'LONG': ('SHORT', 'TURNING-DOWN'), 'SHORT': ('LONG', 'TURNING-UP'),
                'NEUTRAL': ('LONG', 'SHORT')}
    label = labels.get(bot['symbol'])
    if label in opposing[bot['direction']]:
        # A neutral grid opened on an already-trending coin (a mover ridden with a
        # wide range) is only abandoned when that trend changes, not on the label
        # it was opened with.
        if bot['direction'] != 'NEUTRAL' or label != wrapper.get('open_label'):
            # Learned radar-flip hysteresis (OFF unless the rules store sets
            # radar_flip_hysteresis_cycles >= 2): require that many CONSECUTIVE
            # conflicting radar scans before the flip may close the bot. The
            # counter lives on the wrapper; absent rule -> byte-identical path.
            if isinstance(flip_hysteresis, int) and not isinstance(flip_hysteresis, bool) \
                    and flip_hysteresis >= 2:
                wrapper['flip_conflicts'] = wrapper.get('flip_conflicts', 0) + 1
                if wrapper['flip_conflicts'] < flip_hysteresis:
                    return None
            return 'LABEL_FLIP'
    elif label is not None and isinstance(flip_hysteresis, int) \
            and not isinstance(flip_hysteresis, bool) and flip_hysteresis >= 2:
        wrapper['flip_conflicts'] = 0
    if missing >= 2:
        return 'DROPPED'
    if now_ms - bot['opened_ms'] >= MAX_AGE_HOURS * HOUR_MS:
        return 'MAX_AGE'
    return None


def decide(state, radar, prices, now_ms, scan_id, *, require_live_prices=False):
    result, events = deepcopy(state), []
    rules = _learned_rules()
    if watchlist.is_older(scan_id, result.get('watchlist', {}).get('last_scan_id')):
        radar = None
    radar_available = radar is not None
    radar = radar or {'rows': [], 'sections': {}}
    rows = radar.get('rows', [row for section in radar.get('sections', {}).values() for row in section])
    labels = {row['symbol']: row['direction'] for row in rows}
    qualified = _qualified_rows(radar, rows, now_ms) if radar_available else []
    result.setdefault('watchlist', watchlist.initial())
    if radar_available:
        result['watchlist'], changes = watchlist.update(result['watchlist'], qualified, now_ms, scan_id)
        events.extend(dict(event, bot_id=0) for event in changes)
    annotated = {row['symbol']: row for row in rows}
    generated_ms = radar.get('liq_clusters_generated_at_ms')
    core = {entry['symbol'] for entry in result['watchlist']['core']}
    admitted = _candidate_sections(radar, [r for r in qualified if r['symbol'] in core])
    sections, influenced = _apply_influence(result, admitted, labels, rules, now_ms, events)
    for wrapper in list(result['open_bots']):
        bot = wrapper['engine']
        seen = result['radar_seen'].setdefault(bot['symbol'], [])
        if radar_available and (not seen or seen[-1]['scan_id'] != scan_id):
            seen.append(dict(scan_id=scan_id, present=bot['symbol'] in labels))
            del seen[:-2]
        missing = sum(not entry['present'] for entry in seen) if radar_available else 0
        _hedge_gate(result, wrapper, annotated.get(bot['symbol']), generated_ms,
                    prices, rules, now_ms, events)
        bot = wrapper['engine']
        reason = _reason(wrapper, labels, missing, now_ms,
                         rules.get('radar_flip_hysteresis_cycles'))
        if (not reason and bot['symbol'] in prices and
                (bot['leverage'] != LEVERAGE_TREND or bot.get('accounting_version') != ACCOUNTING_VERSION
                 or not setup_evidence.official_grid_return_valid(bot))):
            reason = 'PROFILE_UPDATE'
        reason = _hold_gate(result, wrapper, reason, rules, now_ms, events)
        reason = _opportunity_gate(result, sections, wrapper, reason, labels,
                                   rules, now_ms, scan_id, events)
        if reason:
            wrapper['engine'], emitted = close_bot(bot, prices.get(bot['symbol'], bot['last_price']), now_ms, reason)
            _mark_wrapper(wrapper)
            events.extend(emitted)
            events.append(_wrapper_decision(
                result, wrapper, now_ms, 'close',
                [DECISION_CLOSE_REASONS.get(reason, 199)]))
            result['open_bots'].remove(wrapper)
            result['closed_bots'].append(wrapper)
            if reason != 'PROFILE_UPDATE':
                result['cooldowns'][bot['symbol']] = now_ms + COOLDOWN_HOURS * HOUR_MS
    # Fill each original slot before considering overflow; no live bot is moved.
    vacancies = [direction for direction, count in SLOTS.items()
                 for _ in range(count - sum(w['slot_direction'] == direction for w in result['open_bots']))]
    deferred = []
    skip_keys = set()

    def skip(row, direction, blocks):
        key = (row['symbol'], direction, tuple(sorted(set(blocks))))
        if key in skip_keys:
            return
        skip_keys.add(key)
        events.append(_decision_event(
            now_ms, 0, row['symbol'], 'skip',
            _decision_context(row, direction, result), blocks))

    def fill(slot, direction):
        for section, row in _candidates(sections, direction):
            if require_live_prices and row['symbol'] not in prices:
                skip(row, direction, [DECISION_RULES['missing_live_price']])
                continue
            if _trend_gate_blocks(direction, row, labels, rules):
                skip(row, direction, [DECISION_RULES['learned_trend_alignment']])
                continue
            if _cooldown_gate_blocks(row, rules, now_ms):
                skip(row, direction, [DECISION_RULES['learned_symbol_cooldown']])
                continue
            marked = dict(row, price=prices.get(row['symbol'], row['price']))
            admitted, blocks = _eligibility(result, marked, direction, section, now_ms)
            if not admitted:
                skip(marked, direction, blocks)
                continue
            spec, reserve = profile(marked, direction, result['next_bot_id'])
            bot = open_bot(spec, marked['price'], now_ms)
            bot['risk_metadata_at_ms'] = now_ms - int(row.get('snapshot_age_min',0)*60000)
            context = _decision_context(marked, direction, result)
            result['open_bots'].append(dict(engine=bot, reserve_usdt=reserve,
                                           source_section=section, slot_direction=slot, signals=[],
                                           open_label=labels.get(bot['symbol']),
                                           range_verified=row.get('range_verified', 0),
                                           setup_evidence=setup_evidence.build(marked, bot),
                                           decision_context=context))
            result['next_bot_id'] += 1
            result['radar_seen'][bot['symbol']] = [dict(scan_id=scan_id, present=True)]
            events.append(dict(ts_ms=now_ms, bot_id=bot['bot_id'], symbol=bot['symbol'], type='OPEN',
                               price=marked['price'], equity=bot['equity'] + reserve))
            events.append(_decision_event(now_ms, bot['bot_id'], bot['symbol'],
                                          'open', context))
            return True
        return False
    # A veto that turned away an entry the desk could have filled is reported as
    # a skip, so the counterfactual replay prices what the agent's refusal cost.
    for section, row, direction in (influence.vetoed_rows(admitted, influenced)
                                    if vacancies else ()):
        if require_live_prices and row['symbol'] not in prices:
            continue
        marked = dict(row, price=prices.get(row['symbol'], row['price']))
        if _eligibility(result, marked, direction, section, now_ms)[0]:
            skip(marked, direction, [DECISION_RULES['influence_veto']])
    for slot in vacancies:
        if not fill(slot, slot):
            deferred.append(slot)
    for slot in deferred:
        fallback = [direction for direction in ('NEUTRAL', 'LONG', 'SHORT') if direction != slot]
        for direction in fallback:
            if fill(slot, direction):
                break
    if not radar_available:
        context = dict(direction='NEUTRAL', radar_direction='NEUTRAL', radar_score=0.0,
                       expected_grids_per_hour=0.0, range_width_pct=0.0,
                       funding_rate=0.0,
                       kucoin_ok=1 if result.get('runtime', {}).get('kucoin_ok') is True else 0)
        events.append(_decision_event(now_ms, 0, 'SYSTEM', 'skip', context,
                                      [DECISION_RULES['missing_radar']]))
    elif vacancies and not sections and not skip_keys:
        context = dict(direction='NEUTRAL', radar_direction='NEUTRAL', radar_score=0.0,
                       expected_grids_per_hour=0.0, range_width_pct=0.0,
                       funding_rate=0.0,
                       kucoin_ok=1 if result.get('runtime', {}).get('kucoin_ok') is True else 0)
        events.append(_decision_event(now_ms, 0, 'SYSTEM', 'skip', context,
                                      [DECISION_RULES['no_candidates']]))
    return sample(result, now_ms), events


def _boundary_price(bot, update):
    from trader.papergrid.engine import _number
    if 'price' in update:
        path = [_number(update['price'], positive=True)]
    else:
        opened, high, low, close = [_number(update[k], positive=True)
                                    for k in ('open', 'high', 'low', 'close')]
        if low > min(opened, close) or high < max(opened, close) or low > high:
            raise ValueError('invalid candle range')
        path = [opened, *( [low, high] if opened-low <= high-opened else [high, low]), close]
    return next((p for p in path if p <= bot['range_low'] or p >= bot['range_high']), None)


def advance(state, updates):
    result, events = deepcopy(state), []
    rules = _learned_rules()
    for wrapper in list(result['open_bots']):
        bot = wrapper['engine']
        update = updates.get(bot['symbol'])
        if update is None or update['ts_ms'] <= bot['last_ts_ms']:
            continue
        boundary = _boundary_price(bot, update)
        if boundary is not None:
            reason, price = 'RANGE_BREAK', boundary
            events.append(dict(ts_ms=update['ts_ms'], bot_id=bot['bot_id'],
                               symbol=bot['symbol'], type=reason, price=price))
        else:
            wrapper['engine'], emitted = step(bot, update, funding_pct=update.get('funding_pct'))
            # STOP_LOSS is a journal marker, not a live exit: an in-range grid
            # holds through drawdown (deliberate since 5693251; locked by
            # test_loss_amount_does_not_override_the_configured_price_boundary).
            # Daily-review stats read the marker; the counterfactual replay
            # ignores it too since 81cff5b. Keep it out of the live stream.
            events.extend(e for e in emitted if e['type'] != 'STOP_LOSS')
            reason = None
            price = wrapper['engine']['last_price']
        if not reason and wrapper['engine'].get('accounting_version') == ACCOUNTING_VERSION:
            current=wrapper['engine']
            if update['ts_ms']-current.get('risk_metadata_at_ms',0)>120*60000:
                reason='RISK_LIMIT'
            elif protection_needed(current):
                reserve=wrapper['reserve_usdt']
                if reserve>0:
                    current['reserve_added_usdt']=current.get('reserve_added_usdt',0)+reserve
                    wrapper['reserve_usdt']=0
                    from trader.papergrid.engine import _mark
                    _mark(current,price)
                    events.append(dict(ts_ms=update['ts_ms'],bot_id=current['bot_id'],symbol=current['symbol'],type='RESERVE',amount=reserve))
                if protection_needed(current):reason='RISK_LIMIT'
        if not reason:
            reason = _stall_exit(wrapper, rules, update['ts_ms'], events)
            if reason:
                price = wrapper['engine']['last_price']
        reason = _hold_gate(result, wrapper, reason, rules, update['ts_ms'], events)
        if reason:
            wrapper['engine'], emitted = close_bot(wrapper['engine'], price, update['ts_ms'], reason)
            events.extend(emitted)
            events.append(_wrapper_decision(
                result, wrapper, update['ts_ms'], 'close',
                [DECISION_CLOSE_REASONS.get(reason, 199)]))
            result['open_bots'].remove(wrapper)
            result['closed_bots'].append(wrapper)
            result['cooldowns'][bot['symbol']] = update['ts_ms'] + COOLDOWN_HOURS * HOUR_MS
        _mark_wrapper(wrapper)
    return result, events


def _equity(state):
    # All margin and reserves are allocations of the initial 10k, not deposits.
    return PAPER_EQUITY_USDT + state['archived_net'] + sum(
        net(wrapper['engine']) for wrapper in state['open_bots'] + state['closed_bots'])


def _hourly_bucket(hourly, ts_ms, equity):
    """Fold one equity sample into chronological UTC clock-hour OHLC buckets."""
    start = ts_ms // HOUR_MS * HOUR_MS
    if hourly and hourly[-1][0] == start:
        bucket = hourly[-1]
        bucket[2] = max(bucket[2], equity)
        bucket[3] = min(bucket[3], equity)
        bucket[4] = equity
    else:
        hourly.append([start, equity, equity, equity, equity])
    return hourly[-168:]


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
        result['equity_hourly'] = _hourly_bucket(result.get('equity_hourly') or [], now_ms, equity)
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
        row['order_ladder'] = [dict(line=o['line'],price=bot['lines'][o['line']],side=1 if o['side']=='buy' else -1,book=o.get('book',0)) for o in bot['orders']]
        row.update(price=bot['last_price'], reserve_usdt=wrapper['reserve_usdt'],
                   setup_evidence=setup_evidence.for_snapshot(wrapper),
                   setup_evidence_id=wrapper.get('setup_evidence', {}).get('evidence_id'),
                   setup_evidence_status=wrapper.get('setup_evidence', {}).get('status', 'MISSING'),
                   range_verified=wrapper.get('range_verified', 0),
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
    close_reasons = {}
    for row in closed:
        key = row.get('reason') or 'UNKNOWN'
        bucket = close_reasons.setdefault(key, dict(
            bots=0, wins=0, grid_profit=0.0, close_pnl=0.0,
            fees=0.0, funding=0.0, net=0.0))
        bucket['bots'] += 1
        bucket['wins'] += 1 if row['net'] > 0 else 0
        bucket['grid_profit'] += row['grid_profit']
        # What the final position hand-back cost, separate from grid income.
        bucket['close_pnl'] += row['realized_pnl'] - row['grid_profit']
        bucket['fees'] += row['fees_paid']
        bucket['funding'] += row['funding_paid']
        bucket['net'] += row['net']
    groups = {}
    for direction in ('LONG', 'SHORT', 'NEUTRAL'):
        op, cl = ([r for r in rows if r['direction'] == direction] for rows in (opened, closed))
        groups[direction] = dict(open_bots=op, closed_bots=[r for r in visible_closed if r['direction'] == direction],
                                 totals=totals(op + cl))
    equity = _equity(state)
    def change(hours):
        prior = [point[1] for point in state['equity_curve'] if point[0] <= now_ms - hours*HOUR_MS]
        if not prior:
            # No sample older than the window: report unknown rather than
            # implying a 0% move from the starting equity.
            return None
        base = prior[-1]
        return 100*(equity/base-1) if base else 0
    review_status = learned_rules.load_review_status()
    result = dict(schema_version=1, generated_at_ms=now_ms, equity=equity,
                peak_equity=state['peak_equity'], max_drawdown_pct=state['max_drawdown_pct'],
                change_24h_pct=change(24), change_7d_pct=change(168),
                open_bots=opened, closed_bots=visible_closed, groups=groups, totals=totals(opened+closed),
                close_reasons=close_reasons,
                equity_curve=_downsample(state['equity_curve'], 2000),
                equity_hourly=_downsample(state.get('equity_hourly', []), 168),
                watchlist={k: deepcopy(state.get('watchlist', watchlist.initial())[k]) for k in ('core', 'bench')},
                watchlist_history=deepcopy(state.get('watchlist', {}).get('history', [])),
                watchlist_scan_id=state.get('watchlist', {}).get('last_scan_id'),
                watchlist_asof_ms=state.get('watchlist', {}).get('asof_ms'),
                watchlist_swap_times=deepcopy(state.get('watchlist', {}).get('swap_times', [])), **health)
    if review_status is not None:
        result['review_status'] = review_status
    return result
