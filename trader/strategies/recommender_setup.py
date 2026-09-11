"""Paper recommendation adapter: chart entry, income sweep and estimated risk.

This module does not place orders. An offered estimate, an eligible chart entry,
known funding economics, and the ability to arm a pending bot are separate facts.
Only one-hour support/resistance defines the band; no minute-range substitute is
invented when the one-hour evidence is unavailable. Quantity remains the existing
uncalibrated 5x allocation model, with 1000 used margin and 200 reserve.
"""
from copy import deepcopy
from decimal import Decimal, ROUND_FLOOR
import math

from trader.features.chart_read import read_chart, interpret_entry
from trader.strategies.grid_count_search import search_grid_counts, cycle_economics
from trader.strategies.grid_setup import (_round, _preview, grid_profit_preview,
    FIXED_LEVERAGE, FIXED_USED_MARGIN, FIXED_RESERVED_MARGIN)


def _positive(value, name):
    if type(value) not in (int,float) or not math.isfinite(value) or value <= 0:
        raise ValueError(name+' must be finite and positive')
    return float(value)


def _policy(parameters):
    fixed = dict(leverage=5,leverage_cap=5,used_margin=1000,reserved_margin=200,total_margin=1200,
                 range_exit_stop_pct=0)
    for name, value in fixed.items():
        if name in parameters and (type(parameters[name]) not in (int,float) or parameters[name] != value):
            raise ValueError(name+' conflicts with fixed 5x, 1000+200, boundary-exit policy')
    if parameters.get('adaptive_range_stops',False) is not False:
        raise ValueError('boundary exits require adaptive_range_stops=False')


def _range(chart, tick):
    cell = chart['five_cells']['1h']
    levels = cell.get('support_resistance',{})
    if cell.get('fresh') is not True:
        raise ValueError('fresh one-hour support/resistance is unavailable; wait')
    coverage = cell.get('indicator_observed_fraction')
    if type(coverage) not in (int,float) or not math.isfinite(coverage) or not .95 <= coverage <= 1:
        raise ValueError('one-hour indicator minute coverage must be known and at least 95%; wait')
    low = _round(_positive(levels.get('support'),'one-hour support'),tick,up=True)
    high = _round(_positive(levels.get('resistance'),'one-hour resistance'),tick)
    if low >= high:
        raise ValueError('one-hour support/resistance has no positive tick-aligned width')
    fallback = any('fallback' in levels.get(name,'') for name in ('support_basis','resistance_basis'))
    estimated = levels.get('estimated') is True
    basis = ('one_hour_estimated_extrema_fallback' if estimated else 'one_hour_observed_extrema_fallback') if fallback else 'one_hour_support_resistance'
    evidence = dict(levels,basis=basis,estimated=estimated,indicator_observed_fraction=coverage,
        minimum_indicator_observed_fraction=.95,confidence_coverage_penalty=cell.get('confidence_coverage_penalty'),
        fallback=fallback,timeframe='1h',low=low,high=high,
        source_asof_ms=chart['asof_ms'],last_closed_ms=cell.get('last_closed_ms'))
    return low,high,evidence


def _allowed(direction, pair, asof_ms, regime):
    if regime is None:
        return direction,'macro/SOL gate was not supplied'
    if regime.get('asof_ms') != asof_ms or regime.get('pair') != pair:
        raise ValueError('regime gate must match this pair and asof time')
    allowed = regime.get('allowed_directions')
    if (not isinstance(allowed,(list,tuple)) or
            any(value not in ('long','short','neutral') for value in allowed)):
        raise ValueError('regime gate must supply valid allowed_directions')
    if direction in allowed:
        return direction,'chart bias permitted by supplied macro/SOL gate'
    if 'neutral' in allowed:
        return 'neutral','chart bias forbidden by macro/SOL gate; Neutral entry checked independently'
    raise ValueError('chart bias forbidden by macro/SOL gate; no permitted Neutral fallback')


def _entry(chart, direction, price, low, high, tick, neutral_band):
    # The original chart remains untouched. interpret_entry only needs the
    # one-hour cell for its SR range, not for EMA50; explicitly promote only
    # that separate range predicate when finite 1h bars have sufficient minute
    # coverage. Preserve estimated SR provenance and the coverage penalty.
    cells = deepcopy(chart['five_cells'])
    levels = dict(cells['1h']['support_resistance'],support=low,resistance=high)
    cells['1h'].update(available=True,support_resistance=levels)
    signal = interpret_entry(cells,direction,neutral_band)
    eligible = signal['eligible'] is True
    trigger = None
    if not low < price < high:
        eligible = False
        signal = dict(signal,eligible=False,reason='current market price must be strictly inside one-hour range')
    if eligible and direction == 'neutral':
        fraction = (price-low)/(high-low)
        if not neutral_band[0] <= fraction <= neutral_band[1]:
            eligible = False
            signal = dict(signal,eligible=False,reason='current market price is outside Neutral central band')
    elif eligible:
        trigger = _round(signal['trigger'],tick,up=direction == 'long')
        if not low < trigger < high:
            eligible = False
            trigger = None
            signal = dict(signal,eligible=False,reason='rounded trigger must be strictly inside one-hour range')
    reference = trigger if trigger is not None else _round(price,tick)
    if not low < reference < high:
        reference = _round((low+high)/2,tick)
    if not low < reference < high:
        raise ValueError('one-hour range cannot contain a valid tick-aligned entry reference')
    waiting = bool(eligible and trigger is not None and
                   (price < trigger if direction == 'long' else price > trigger))
    return dict(entry=reference,trigger=trigger,entry_eligible=eligible,waiting_for_trigger=waiting,
        market_entry_ready=eligible and not waiting,entry_signal=signal,
        entry_reference_basis='valid chart trigger' if trigger is not None else
            'current inside-range market price' if low < price < high else
            'hypothetical range midpoint for an offer only; no valid entry signal')


def _safe_estimate(config, callback, fee_ratio):
    estimate = dict(_preview(config,callback))
    low,high = config['low'],config['high']
    width = high-low
    liquidity = {'long':estimate.get('liquidation_price_long',estimate.get('estimated_liquidation_price')),
                 'short':estimate.get('liquidation_price_short',estimate.get('estimated_liquidation_price_high'))}
    sides = ('long','short') if config['direction']=='neutral' else (config['direction'],)
    buffers = {}
    for side in sides:
        liq = _positive(liquidity[side],side+' estimated liquidation')
        edge = low if side=='long' else high
        distance = edge-liq if side=='long' else liq-edge
        buffers[side] = dict(range_fraction=distance/width,edge_fraction=distance/edge)
        if distance < .1*width or distance < .1*edge:
            raise ValueError('estimated liquidation fails 10% losing-edge and range-width buffers')
    step = float((Decimal(str(high))-Decimal(str(low)))/config['grids'])
    base = cycle_economics(config['quantity'],float(Decimal(str(high))-Decimal(str(step))),high,
                          sides[0],fee_safety_ratio=fee_ratio)
    actual_min = _positive(estimate.get('profit_per_grid_min'),'actual preview cash minimum')
    if not base['eligible'] or Decimal(str(actual_min)) < Decimal(str(base['required_fee_buffer_usdt'])):
        raise ValueError('actual preview cash fails the positive net and fee safety buffer')
    estimate.update(grid_step=step,quantity_calibrated=False,liquidation_estimated=True,
        actual_profit_basis='actual base quantity times interval minus both fill fees; funding estimates reported separately',
        nominal_profit_basis='KuCoin percentage and margin/grids display estimate; not the cash admission rule',
        buffer_range_percent={s:v['range_fraction']*100 for s,v in buffers.items()},
        buffer_edge_percent={s:v['edge_fraction']*100 for s,v in buffers.items()})
    return estimate,buffers


def build_recommender_setup(pair,bars,market,frames,asof_ms,parameters=None,regime=None,funding=None):
    """Build top-three paper offers; explicit funding uses rate/interval_ms/asof.

    The supplied frames are lists of UTC closed-timeframe bars, read causally by
    chart_read. A pending valid trigger may be armed and reserves the full 1200;
    it is not a market entry until reached. Missing holds/funding never grants
    funded economic approval. Calibration status never suppresses an offer.
    """
    options = dict(parameters or {})
    result = dict(pair=pair,asof_ms=asof_ms,offered=False,eligible=False,entry_eligible=False,
        funded_economics_eligible=False,funded_entry_eligible=False,can_arm=False,market_entry_ready=False,
        waiting_for_trigger=False,provisional=True,candidates=[],warnings=[],status='waiting',reason=None,
        leverage=FIXED_LEVERAGE,used_margin=FIXED_USED_MARGIN,reserved_margin=FIXED_RESERVED_MARGIN,
        total_margin=FIXED_USED_MARGIN+FIXED_RESERVED_MARGIN,range_exit_stop_pct=0,
        adaptive_range_stops=False,quantity_calibrated=False,liquidation_estimated=True,
        selection_policy='min_24h_7d_modeled_completed_grid_income_per_hour')
    try:
        _policy(options)
        if not isinstance(pair,str) or not pair:
            raise ValueError('nonempty pair required')
        observed = market.get('observed_at_ms')
        if observed is not None and (type(observed) is not int or not 0 <= observed <= asof_ms):
            raise ValueError('market observation must be a nonnegative timestamp at or before asof')
        tick = _positive(market.get('tick_size',market.get('tickSize')),'tick size')
        lot = _positive(market.get('lot_size',market.get('lotSize')),'lot size')
        multiplier = _positive(market.get('multiplier'),'contract multiplier')
        price = market.get('price')
        if price is None and market.get('bid') is not None and market.get('ask') is not None:
            bid,ask = _positive(market['bid'],'bid'),_positive(market['ask'],'ask')
            if bid > ask:
                raise ValueError('market bid exceeds ask')
            price = (bid+ask)/2
        price = _positive(price,'market price')
        neutral_band = options.get('neutral_band',(.2,.8))
        chart = read_chart(frames,asof_ms,bias_mode=options.get('bias_mode','1d+4h'),
                           minimum_confidence=options.get('minimum_confidence',.75),neutral_band=neutral_band)
        result.update(chart=chart,original_direction=chart['direction'],direction=chart['direction'],regime=regime)
        direction,gate_reason = _allowed(chart['direction'],pair,asof_ms,regime)
        result.update(direction=direction,gate_reason=gate_reason)
        low,high,evidence = _range(chart,tick)
        result['range_evidence'] = evidence
        entry = _entry(chart,direction,price,low,high,tick,neutral_band)
        result.update(entry,tick_size=tick,lot_size=lot,multiplier=multiplier)
        search_options = {name:options[name] for name in ('min_grids','max_grids','fee_safety_ratio') if name in options}
        exchange_max = market.get('max_grids',market.get('max_grid_count'))
        if exchange_max is not None:
            search_options['exchange_max_grids'] = exchange_max
        if 'exchange_max_grids' in options:
            search_options['exchange_max_grids'] = (min(exchange_max,options['exchange_max_grids'])
                                                   if exchange_max is not None else options['exchange_max_grids'])
        configs = {}
        quantity_unit = float(Decimal(str(multiplier))*Decimal(str(lot)))
        legs = 2 if direction=='neutral' else 1
        lower,upper,tick_decimal = map(lambda value:Decimal(str(value)),(low,high,tick))
        def quantity_for_count(count):
            step = ((upper-lower)/count/tick_decimal).to_integral_value(rounding=ROUND_FLOOR)*tick_decimal
            actual_high = float(lower+count*step)
            if step <= 0 or not low < entry['entry'] < actual_high:
                return dict(eligible=False,reason='tick-trimmed range does not strictly enclose entry/trigger')
            if direction=='neutral' and entry['entry_eligible']:
                position = (price-low)/(actual_high-low)
                if not neutral_band[0] <= position <= neutral_band[1]:
                    return dict(eligible=False,reason='Neutral entry leaves central band after tick trim')
            quantity = _round(1000*5/(legs*count*entry['entry']*(1+5*.0006)),quantity_unit)
            config = dict(pair=pair,low=low,high=actual_high,grids=count,leverage=5,investment=1000.,
                reserved_margin=200.,direction=direction,entry_price=entry['entry'],trigger=entry['trigger'],
                quantity=quantity,multiplier=multiplier,lot_size=lot,tick_size=tick,
                range_exit_stop_pct=0,adaptive_range_stops=False,
                stop_loss=actual_high if direction=='short' else low,
                stop_loss_high=actual_high if direction=='neutral' else None)
            try:
                estimate,buffers = _safe_estimate(config,options.get('preview_fn'),options.get('fee_safety_ratio',.2))
            except (TypeError,ValueError,OverflowError) as error:
                return dict(eligible=False,reason=str(error))
            configs[count] = dict(config=config,preview=estimate,buffers=buffers)
            return dict(eligible=True,quantity=quantity)
        search = search_grid_counts(bars,low,high,asof_ms=asof_ms,direction=direction,
            quantity_for_count=quantity_for_count,tick_size=tick,funding=funding,parameters=search_options)
        result['search'] = search
        offers = []
        for candidate in search['candidates']:
            cached = configs[candidate['grids']]
            estimate,config = cached['preview'],cached['config']
            nominal = grid_profit_preview(candidate['low'],candidate['high'],candidate['grids'],5,1000,
                                          entry['entry'],tick)
            estimate.update({k:v for k,v in nominal.items() if k.startswith('kucoin_profit_')})
            economics = [row for by_side in candidate['economics'].values() for row in by_side.values()]
            fee_only = min(row['fee_net_usdt'] for row in economics)
            funded_net = (min(row['admission_net_usdt'] for row in economics)
                          if all(row['admission_net_usdt'] is not None for row in economics) else None)
            cash_display = dict(net_usdt_per_grid=fee_only if funded_net is None else funded_net,
                funding_adjusted_net_usdt_per_grid=funded_net,estimated_fee_only_net_usdt_per_grid=fee_only,
                net_usdt_per_grid_basis='worst adjacent price, minimum across legs and 24h/7d hold estimates; funding credits excluded; fee-only estimate when funding/holds unknown',
                kucoin_profit_pct_min=estimate['kucoin_profit_pct_min'],
                kucoin_profit_pct_max=estimate['kucoin_profit_pct_max'],
                kucoin_percent_basis='nominal KuCoin margin/grids display preview; separate from actual-quantity cash')
            funded = entry['entry_eligible'] and candidate['funded_economics_eligible']
            offers.append(dict(candidate,pair=pair,direction=direction,**entry,**cash_display,config=config,preview=estimate,
                leverage=5,used_margin=1000.,reserved_margin=200.,total_margin=1200.,
                tick_size=tick,lot_size=lot,multiplier=multiplier,contracts_per_grid=candidate['quantity']/multiplier,
                opening_fee_budget=legs*candidate['quantity']*candidate['grids']*entry['entry']*.0006,
                range_exit_stop_pct=0,adaptive_range_stops=False,
                stop_loss=config['stop_loss'],stop_loss_high=config['stop_loss_high'],
                hard_stop_low=candidate['low'],hard_stop_high=candidate['high'],
                range_exit_stop_low=candidate['low'],range_exit_stop_high=candidate['high'],
                buffers=cached['buffers'],range_evidence=dict(evidence,final_low=candidate['low'],final_high=candidate['high']),
                range_tick_adjustment='Interval floors to full ticks; final high equals low plus N intervals',offered=True,
                can_arm=funded,funded_entry_eligible=funded,
                expected_gph=min(row['completed_grids_per_hour'] for row in candidate['windows'].values()),
                grid_income_per_hour=candidate['selection_income_per_hour']))
        if not offers:
            result['reason'] = 'No safe eligible grid count; inspect coverage and per-count rejection reasons'
            return result
        best = offers[0]
        result.update(best,candidates=offers,eligible=True,
            status='provisional_unknown_economics' if best['provisional'] else
                   'waiting_for_entry_signal' if not entry['entry_eligible'] else
                   'ready_to_arm_waiting_for_trigger' if entry['waiting_for_trigger'] else 'ready_to_arm',
            reason='Top counts ranked by the smaller 24h/7d completed-cycle income estimate')
        result['warnings'].extend(['Quantity is an unvalidated allocation estimate; liquidation is estimated.',
            'Observed candle cycles and prorated median-hold funding are modeled estimates, not exchange fills or guaranteed income.'])
        if best['provisional']:
            result['warnings'].append('Funding or completed-pair hold observations are unknown; funded paper entry is not eligible.')
        return result
    except (KeyError,TypeError,ValueError,OverflowError) as error:
        result['reason'] = str(error)
        return result
