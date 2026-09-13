"""Entry-time evidence, separate from mutable paper accounting and numeric events."""
from copy import deepcopy
import hashlib
import json
import math

from trader.radar.spacing import choose_count, align_bounds

FEE_SOURCE = 'https://www.kucoin.com/support/21960469554201'
HOLDING_WINDOW_MS = 4 * 60 * 60 * 1000
HOUR_MS = 3600000


def _number(value, *, positive=False):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or (positive and value <= 0)):
        return None
    return value


def evidence_id(dossier):
    payload = {k: v for k, v in dossier.items() if k != 'evidence_id'}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def range_valid(row, spec, now_ms):
    """Admission checks provenance shape and consistency, not source authenticity."""
    proof = row.get('range_evidence')
    if not isinstance(proof, dict):
        return False
    try:
        if (type(proof['version']) is not int or proof['version'] != 1 or proof['method'] != 'repeated_hourly_pivots_v1'
                or proof['status'] != 'VERIFIED' or proof['venue'] != 'KuCoin'
                or proof['timeframe'] != '1h' or proof['contiguous'] is not True
                or proof['expected_candles'] != 168 or proof['observed_candles'] != 168
                or any(type(proof[k]) is not int for k in ('expected_candles', 'observed_candles', 'pivot_confirmation_candles'))
                or _number(proof['coverage_ratio']) != 1 or proof['pivot_confirmation_candles'] != 2):
            return False
        start, end, asof, candle = [proof[k] for k in (
            'window_start_ms', 'window_end_ms', 'analysis_asof_ms', 'candle_asof_ms')]
        if (any(type(v) is not int for v in (start, end, asof, candle))
                or end-start != 168*HOUR_MS or end % HOUR_MS
                or candle != end or not end <= asof <= now_ms or asof-end >= HOUR_MS):
            return False
        digest = proof['candles_sha256']
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            return False
        original = proof['original_bounds']
        for name, bound in zip(('selected_support', 'selected_resistance'), original):
            level = proof[name]
            times, confirmed = level['pivot_times_ms'], level['confirmed_at_ms']
            if (level['price'] != bound or type(level['touches']) is not int
                    or not 2 <= level['touches'] <= 168
                    or len(times) != level['touches'] or len(confirmed) != len(times)
                    or any(type(t) is not int for t in times+confirmed)
                    or times != sorted(set(times)) or times[-1]-times[0] < 3*HOUR_MS
                    or any(not start <= t < end for t in times)
                    or confirmed != [t+3*HOUR_MS for t in times]
                    or any(t > end for t in confirmed)):
                return False
        if (not isinstance(original, list) or len(original) != 2
                or any(_number(v, positive=True) is None for v in original)):
            return False
        tick = _number(row.get('tick_size'), positive=True)
        bounds = [spec['range_low'], spec['range_high']]
        return (tick is not None and list(align_bounds(*original, tick)) == bounds
                and proof['rounded_bounds'] == bounds
                and type(proof['grid_count']) is int and proof['grid_count'] == spec['grids']
                and _number(proof['grid_interval'], positive=True) is not None
                and math.isclose(proof['grid_interval'], spec['grid_interval'], rel_tol=1e-10)
                and _number(proof['actual_upper_line'], positive=True) is not None
                and math.isclose(proof['actual_upper_line'], bounds[0]+spec['grids']*spec['grid_interval'], rel_tol=1e-10)
                and proof['fee_rate_maker'] == .0006 and proof['fee_rate_taker'] == .0006)
    except (KeyError, TypeError, ValueError, IndexError):
        return False


def for_snapshot(wrapper):
    """Never reconstruct old entry evidence from today's market or engine defaults."""
    result = deepcopy(wrapper.get('setup_evidence') or {
        'status': 'MISSING', 'reason': 'entry_evidence_not_recorded',
        'evidence_id': None,
    })
    if result['status'] == 'RECORDED':
        # Snapshot input is capped at 2 MiB; retain full lines/orders only in
        # the durable wrapper. The digest always addresses that full dossier.
        result['layout'].pop('opening_orders', None)
        result['layout'].pop('grid_lines', None)
        result['projection'] = 'SUMMARY_FULL_DOSSIER_IN_LOCAL_STATE'
    return result


def _funding(row, bot, gross_notional, signed_notional):
    interval = _number(row.get('funding_interval_ms'), positive=True)
    next_ms = _number(row.get('next_funding_ms'), positive=True)
    asof = _number(row.get('funding_asof_ms'))
    rate = _number(row.get('funding_pct'))
    opened = bot['opened_ms']
    # A previous settlement is not evidence of the next settlement's timing.
    count = None
    if interval is not None and next_ms is not None and next_ms > opened:
        count = (0 if next_ms > opened + HOLDING_WINDOW_MS else
                 1 + math.floor((opened + HOLDING_WINDOW_MS - next_ms) / interval))
    known = count is not None and rate is not None and asof is not None and asof <= opened
    return dict(status='SCENARIO_ONLY' if known else 'UNKNOWN',
                holding_window_ms=HOLDING_WINDOW_MS, interval_ms=interval,
                next_settlement_ms=next_ms, observed_at_ms=asof,
                observed_rate_pct=rate, scheduled_settlements=count,
                constant_rate_seed_position_cost_usdt=(signed_notional * rate / 100 * count
                                                       if known else None),
                adverse_absolute_rate_gross_cost_usdt=(gross_notional * abs(rate) / 100 * count
                                                       if known else None),
                future_actual_cost_usdt=None,
                assumption='Frozen entry positions and price; constant observed rate. '
                           'Adverse scenario applies its absolute magnitude to gross exposure. '
                           'Future rates, positions and schedule changes are unknown.')


def _break_even(pair_net, seed_fee, flatten_fee, spread_cost, funding):
    """Cost recovery in repeatable full pairs, not a prediction of bot break-even."""
    fee_cost = seed_fee + flatten_fee
    spread_total = fee_cost + spread_cost if spread_cost is not None else None
    funding_cost = funding['adverse_absolute_rate_gross_cost_usdt']
    combined = (spread_total + funding_cost
                if spread_total is not None and funding_cost is not None else None)
    def count(cost):
        return math.ceil(cost/pair_net) if cost is not None and pair_net > 0 else None
    return dict(status='CONDITIONAL' if pair_net > 0 else 'NONPOSITIVE_PAIR_NET',
                minimum_adjacent_pair_net_usdt=pair_net,
                seed_and_flatten_fee_cost_usdt=fee_cost,
                including_spread_cost_usdt=spread_total,
                including_spread_adverse_funding_4h_cost_usdt=combined,
                fee_only_completed_pairs=count(fee_cost),
                including_spread_completed_pairs=count(spread_total),
                including_spread_adverse_funding_4h_completed_pairs=count(combined),
                actual_future_break_even_status='UNKNOWN', actual_future_completed_pairs=None,
                assumption='Ceiling of scenario costs divided by the minimum actual adjacent '
                           'pair net after both fill fees. Assumes repeatable complete pairs, '
                           'no inventory loss, unchanged seed inventory for same-price flatten, '
                           'and no extra slippage or impact. Partial seeded closes are excluded. '
                           'Funding uses only the existing conditional four-hour scenario; '
                           'this is not an admission threshold or a profitability forecast.')


def build(row, bot):
    """Freeze the actual opened layout and its source evidence, without new entry rules."""
    price, quantity = bot['opening_price'], bot['contracts_per_line']
    maker, taker = bot['fee_rate_maker'], bot['fee_rate_taker']
    legs = bot.get('hedge_books', [bot])
    gross_notional = sum(abs(leg['position_contracts']) * price for leg in legs)
    signed_notional = sum(leg['position_contracts'] * price for leg in legs)
    pairs = []
    for buy, sell in zip(bot['lines'], bot['lines'][1:]):
        gross = (sell - buy) * quantity
        fee = (buy + sell) * quantity * maker
        pairs.append(dict(buy=buy, sell=sell, gross_usdt=gross,
                          both_fill_fees_usdt=fee, net_usdt=gross-fee,
                          long_margin_return_pct=(gross-fee)/(buy*quantity/bot['leverage'])*100,
                          short_margin_return_pct=(gross-fee)/(sell*quantity/bot['leverage'])*100))
    seeded = []
    for leg in legs:
        closes = []
        for order in leg['orders']:
            entry = order.get('pair_entry')
            if entry is None:
                continue
            exit_price = bot['lines'][order['line']]
            gross = (exit_price-entry)*quantity*(1 if leg['direction'] == 'LONG' else -1)
            fees = quantity*(entry*taker+exit_price*maker)
            closes.append(dict(line=order['line'], entry_price=entry, exit_price=exit_price,
                               gross_usdt=gross, seed_and_close_fees_usdt=fees,
                               net_usdt=gross-fees,
                               margin_return_pct=(gross-fees)/(entry*quantity/bot['leverage'])*100))
        if closes:
            worst = min(closes, key=lambda p: p['margin_return_pct'])
            seeded.append(dict(direction=leg['direction'], count=len(closes),
                               minimum_return_pair=worst,
                               all_above_one_pct=worst['margin_return_pct'] > 1 + 1e-10))
    structure = deepcopy(row.get('range_evidence'))
    spread = _number(row.get('spread_pct'))
    if spread is not None and spread < 0:
        spread = None
    funding = _funding(row, bot, gross_notional, signed_notional)
    break_even = _break_even(min(p['net_usdt'] for p in pairs), bot['fees_paid'],
                            gross_notional*taker,
                            gross_notional*spread/100 if spread is not None else None,
                            funding)
    unknowns = ['future_price_path', 'future_funding_rates', 'future_inventory',
                'queue_priority_partial_fills_and_market_impact']
    if not structure or structure.get('status') != 'VERIFIED':
        unknowns.append('verified_entry_range_provenance')
    if funding['status'] == 'UNKNOWN':
        unknowns.append('funding_observation_or_settlement_schedule')
    if spread is None:
        unknowns.append('entry_spread')
    tick = _number(row.get('tick_size'), positive=True)
    if tick is None:
        unknowns.append('exchange_tick_size')
    maximum = (choose_count(bot['range_low'], bot['range_high'], tick_size=tick,
                           leverage=bot['leverage'], direction=bot['direction'])
               if tick is not None else None)
    dossier = dict(
        version=1, status='RECORDED', bot_id=bot['bot_id'], symbol=bot['symbol'],
        opened_ms=bot['opened_ms'], direction=bot['direction'],
        range_status='VERIFIED' if structure and structure.get('status') == 'VERIFIED' else 'MISSING',
        range_evidence=structure,
        coinglass_liquidation_clusters=dict(
            status='UNAVAILABLE_NOT_WIRED',
            heatmap_kind='MODELED_POTENTIAL_LIQUIDATION_LEVELS',
            historical_kind='REPORTED_PAST_LIQUIDATION_TOTALS',
            historical_totals_are_cluster_levels=False,
            cluster_levels=None, observed_at_ms=None,
            reference_url='https://docs.coinglass.com/reference/liquidation-heatmap',
            caution='Modeled potential liquidation heatmaps are distinct from historical '
                    'liquidation totals. Neither is supplied as entry cluster evidence here; '
                    'do not infer price levels or future liquidations from totals.'),
        layout=dict(range_low=bot['range_low'], range_high=bot['range_high'],
                    grids=bot['grids'], grid_lines=deepcopy(bot['lines']),
                    maximum_pair_economic_count=maximum,
                    matches_pair_economic_maximum=(bot['grids'] == maximum if maximum is not None else None),
                    count_scope='Pair fee/return ceiling only; excludes entry split, lots and risk gates.',
                    interval=bot.get('grid_interval'), tick_size=tick,
                    lot_size=_number(row.get('lot_size'), positive=True),
                    contract_lots=bot.get('contract_lots'),
                    contract_multiplier=bot.get('contract_multiplier'),
                    quantity_per_grid=quantity, leverage=bot['leverage'],
                    allocated_margin_usdt=bot['notional_usdt'],
                    opening_orders=deepcopy(bot['orders'])),
        fees=dict(grid_fill_rate=maker, seed_and_flatten_rate=taker,
                  reference_bot_rate=.0006, reference_url=FEE_SOURCE,
                  matches_reference=maker == .0006 and taker == .0006,
                  source='engine_rates_frozen_at_entry'),
        grid_pairs=dict(basis='Adjacent complete limit-fill pair; return on pair margin, '
                              'not total bot investment. First seeded close may span only '
                              'part of an interval.',
                        pair_count=len(pairs), lowest_price_pair=pairs[0],
                        minimum_long_return_pair=min(pairs, key=lambda p: p['long_margin_return_pct']),
                        minimum_short_return_pair=min(pairs, key=lambda p: p['short_margin_return_pct']),
                        seeded_closes=seeded),
        costs=dict(seed_fee_paid_usdt=bot['fees_paid'],
                   flatten_seed_inventory_same_price_fee_usdt=gross_notional*taker,
                   seed_gross_notional_usdt=gross_notional,
                   seed_signed_notional_usdt=signed_notional,
                   observed_spread_pct=spread,
                   seed_and_flatten_spread_cost_usdt=(gross_notional*spread/100
                                                    if spread is not None else None),
                   double_spread_cost_usdt=(gross_notional*spread/100*2
                                           if spread is not None else None),
                   spread_assumption='Half observed spread on each seed and flatten leg '
                                     'at unchanged gross inventory; sensitivity only, '
                                     'not booked by the paper engine.',
                   funding_4h=funding, break_even=break_even),
        whole_bot=dict(net_formula='realized_pnl + unrealized_pnl - fees_paid - funding_paid',
                       grid_profit_is_separate=True, future_net_pnl_usdt=None,
                       caution='Positive grid-pair return does not ensure positive bot PNL. '
                               'Seed fees, partial first closes, flatten fees, funding and '
                               'inventory losses can outweigh completed grid gains.'),
        unknowns=unknowns,
    )
    dossier['evidence_id'] = evidence_id(dossier)
    return dossier
