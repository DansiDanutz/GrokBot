"""Pure KuCoin Futures Grid form selection with an auditable search ledger.

Direction score is a weighted average in [-1,1]. Positive funding is a small
contrarian short component. Range starts at long -15/+40%, short -40/+15%,
neutral +/-20%, then intersects confirmed 7d swing support/resistance, observed
7d extremes and entry +/-20 minute ATRs. Five-bar swings require two closed
right-hand candles. Choose the eligible pivot nearest each nominal edge.
Universal stops are 5% of EDGE PRICE outside both sides, with app prices rounded
inward by less than one tick; the losing stop must still precede liquidation.

Production policy is fixed at 5x, 1000 USDT used plus 200 USDT reserve
(1200 total per bot). Capital and leverage never change during safety search.
Try the unchanged range, then losing-side width factors .75, .5, .25. Both
neutral sides narrow. First feasible wins; otherwise reject the setup.
Every grid level is tick aligned: round step DOWN to ticks, then keep entry's
position in the proposed range and contract the range to N integer steps.

Quantity is BASE units rounded down to multiplier*lot_size. Reserve never buys
exposure. Sizing sets q <= used*L/(legs*N*entry), where neutral has two legs,
and other modes one, additionally reserving taker opening
fees from the used margin. Default admission requires strictly positive planned
cash net after both fill fees at every adjacent pair. An explicit nonnegative
minimum_grid_net_usdt retains optional cash-floor research (1 means >=1 USDT).
Only actual base-quantity cash PnL governs admission; nominal margin/grids and
percent previews are display estimates. Highest feasible N wins. Reducing N
widens the step. A step wider than median minute range emits a warning; actual
crossings determine the radar ranking, without an extra economic rejection.
Liquidation must clear BOTH 10% of range width and 10% of the losing edge price.
"""
import math
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
from types import SimpleNamespace

from trader.strategies.grid_features import features


FIXED_LEVERAGE = 5
FIXED_USED_MARGIN = 1000.
FIXED_RESERVED_MARGIN = 200.


DEFAULT_WEIGHTS = {'ema_slope_4h': 2., 'ema_slope_24h': 2.,
                   'structure_4h': 1., 'structure_24h': 1.,
                   'position_24h': 1.5, 'position_7d': 1.5, 'funding_sign': .5}


def minimum_grid_net_usdt(parameters=None):
    """Zero requests strictly positive cash net; a positive value is a cash floor."""
    value = (parameters or {}).get('minimum_grid_net_usdt',0.)
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value < 0:
        raise ValueError('minimum_grid_net_usdt must be a finite nonnegative number')
    return float(value)


def grid_cash_profit(quantity, buy_price, sell_price):
    """Exact decimal planned USDT cash net from base quantity and both fill fees.

    Decimal input conversion keeps a mathematical break-even at zero instead of
    admitting floating-point crumbs. This calculation imposes no USDT epsilon.
    """
    try:
        values = tuple(Decimal(str(value)) for value in (quantity,buy_price,sell_price))
    except InvalidOperation as exc:
        raise ValueError('Cash profit inputs must be finite positive numbers') from exc
    if (any(isinstance(value,bool) for value in (quantity,buy_price,sell_price)) or
            any(not value.is_finite() or value <= 0 for value in values)):
        raise ValueError('Cash profit inputs must be finite positive numbers')
    quantity,buy,sell = values
    return quantity*(sell-buy-Decimal('0.0006')*(buy+sell))


def grid_profit_preview(low, high, grids, leverage, used_margin, price=None, tick_size=None):
    """Dan's margin-per-grid estimate, distinct from fixed-quantity fill PnL.

    percent = 100 * leverage * (interval / price - 2 * .0006).
    USDT = used_margin / grids * percent / 100. Reserve is excluded.
    Bounds evaluate high/low prices conservatively. For RAY 1.1..2 at 5x and
    1000 used, N=50 pays ~1.019 at 1.58 but only .78 at 2.0; the whole-range
    formula floor permits at most 44 grids before lot/fee/neutral constraints.
    """
    if (type(grids) is not int or grids < 2 or
            not all(math.isfinite(value) and value > 0
                    for value in (low, high, leverage, used_margin)) or low >= high):
        raise ValueError('Profit preview requires positive finite prices, margin, leverage and at least two integer grids')
    if price is not None and (not math.isfinite(price) or price <= 0):
        raise ValueError('Profit preview price must be finite and positive')
    from trader.strategies.kucoin_grid import GridConfig, margin_profit_per_grid
    config = GridConfig(pair='PREVIEW',low=low,high=high,grids=grids,leverage=leverage,
                        investment=used_margin,tick_size=tick_size)
    step = float((Decimal(str(high))-Decimal(str(low)))/grids)
    if tick_size is not None:
        if not math.isfinite(tick_size) or tick_size <= 0:
            raise ValueError('Tick size must be finite and positive')
        step = _round(step,tick_size)
    result = {'step':step, 'used_margin':used_margin}
    for suffix, at_price in (('min',high),('max',low),('at_price',price)):
        if at_price is None:
            continue
        estimate = margin_profit_per_grid(config,at_price)
        result[f'kucoin_profit_pct_{suffix}'] = estimate['percent']
        result[f'kucoin_profit_usdt_{suffix}'] = estimate['usdt']
    return result


def _round(value, increment, up=False):
    unit = Decimal(str(increment))
    return float((Decimal(str(value))/unit).to_integral_value(
        rounding=ROUND_CEILING if up else ROUND_FLOOR)*unit)


def _direction(data, params):
    weights = dict(DEFAULT_WEIGHTS, **params.get('direction_weights', {}))
    if set(weights) != set(DEFAULT_WEIGHTS):
        raise ValueError('Unknown direction weight')
    if any(not math.isfinite(float(weight)) or weight < 0 for weight in weights.values()) or sum(weights.values()) <= 0:
        raise ValueError('Direction weights must be finite, nonnegative and have positive sum')
    components = {key: float(data[key]) for key in DEFAULT_WEIGHTS}
    if not all(math.isfinite(value) for value in components.values()):
        raise ValueError('Direction features must be finite')
    components['position_24h'] = 1-2*components['position_24h']
    components['position_7d'] = 1-2*components['position_7d']
    components['funding_sign'] *= -1
    score = sum(weights[key]*components[key] for key in DEFAULT_WEIGHTS)/sum(weights.values())
    threshold = float(params.get('direction_threshold', .18))
    if not 0 <= threshold <= 1:
        raise ValueError('direction_threshold must lie in [0,1]')
    direction = 'neutral' if abs(score) <= threshold else 'long' if score > 0 else 'short'
    return direction, score, components, weights


def _preview(config, callback):
    # Injection permits independent offline tests while the replica is integrated.
    if callback is not None:
        return callback(SimpleNamespace(**config))
    from trader.strategies.kucoin_grid import GridConfig, preview
    return preview(GridConfig(**config))


def _levels(low, high, entry, n, tick):
    step = _round((high-low)/n, tick)
    if step <= 0:
        return None
    fraction = (entry-low)/(high-low)
    start = _round(entry-fraction*n*step, tick, up=True)
    end = _round(start+n*step, tick)
    if end > high + tick*1e-6:
        start = _round(start-tick, tick)
    prices = [float(Decimal(str(start))+i*Decimal(str(step))) for i in range(n+1)]
    if prices[0] < low-tick*1e-6 or not prices[0] < entry < prices[-1] or prices[-1] > high+tick*1e-6:
        return None
    return prices


def _range_structure(data, entry, nominal_low, nominal_high):
    evidence = data.get('support_resistance',{})
    result = {'method':'Confirmed five-bar support/resistance nearest nominal range edges; 7d extreme fallback',
              'confirmation_bars':evidence.get('confirmation_bars',2),
              'lookback_minutes':evidence.get('lookback_minutes',data.get('history_minutes',10080)),
              'support_pivot_count':evidence.get('support_pivot_count',0),
              'resistance_pivot_count':evidence.get('resistance_pivot_count',0)}
    for side, nominal, extreme in (('support',nominal_low,data['low_7d']),
                                    ('resistance',nominal_high,data['high_7d'])):
        candidates = []
        for level in evidence.get(f'{side}s',[]):
            price = level['price']
            if not math.isfinite(price) or price <= 0 or level['count'] <= 0:
                raise ValueError('Invalid confirmed support/resistance evidence')
            if (data['low_7d'] <= price < entry if side == 'support'
                    else entry < price <= data['high_7d']):
                candidates.append(level)
        result[f'{side}_candidate_count'] = len(candidates)
        if candidates:
            result[side] = dict(min(candidates,key=lambda level:(abs(level['price']-nominal),
                                        -level['count'],-level.get('last_confirmed_index',0))))
            result[f'{side}_source'] = 'confirmed swing pivot nearest nominal edge'
        else:
            result[side] = {'price':extreme,'count':0}
            result[f'{side}_source'] = f'7d {"low" if side == "support" else "high"} fallback; no confirmed eligible pivot'
    return result


def build_setup(pair, data, market, parameters=None):
    """Build exact form from cached ``features`` and normalized market metadata."""
    params = dict(parameters or {})
    leverage, used, reserve = FIXED_LEVERAGE, FIXED_USED_MARGIN, FIXED_RESERVED_MARGIN
    result = {'pair':pair,'eligible':False,'reason':'','leverage':leverage,
              'used_margin':used,'reserved_margin':reserve,'total_margin':used+reserve,
              'attempts':[],'warnings':[]}
    if not data.get('valid', False):
        result['reason'] = data.get('reason', 'Invalid feature history')
        return result
    try:
        minimum = minimum_grid_net_usdt(params)
        cash_floor = Decimal(str(minimum))
        requirement = ('strictly positive actual per-order cash net' if minimum == 0 else
                       f'at least {minimum:g} USDT actual per-order cash net')
        result.update(minimum_grid_net_usdt=minimum,target_profit_per_grid=minimum,
                      target_semantics=requirement+' after both 0.06% fill fees; nominal previews are display only')
        direction, score, components, weights = _direction(data, params)
        result.update(direction=direction, direction_score=score, direction_components=components,
                      direction_weights=weights,
                      direction_reason=f'{direction}: weighted trend/structure/position/funding score {score:+.3f}')
        quote_price = ((float(market['bid'])+float(market['ask']))/2
                       if market.get('bid') is not None and market.get('ask') is not None else data['price'])
        price = float(market.get('price', quote_price))
        tick = float(market.get('tick_size', market.get('tickSize', .00000001)))
        lot = float(market.get('lot_size', market.get('lotSize', 1.)))
        multiplier = float(market.get('multiplier', 1.))
        atr = float(data['atr_1m'])
        movement = float(data['typical_movement_1m'])
        essentials = [price,tick,lot,multiplier,atr,data['low_7d'],data['high_7d'],data['low_24h'],data['high_24h']]
        if not all(math.isfinite(value) and value > 0 for value in essentials):
            raise ValueError('Prices, ATR, tick size, multiplier and lot size must be finite and positive')
        if not math.isfinite(movement) or movement < 0:
            raise ValueError('Typical movement must be finite and nonnegative')
        if data['high_7d'] <= data['low_7d'] or data['high_24h'] <= data['low_24h']:
            raise ValueError('Structure range must have positive width')
        fixed_parameters = {'leverage_cap':leverage,'leverage':leverage,
                            'used_margin':used,'reserved_margin':reserve,'total_margin':used+reserve}
        for key,expected in fixed_parameters.items():
            if key in params and (isinstance(params[key],bool) or params[key] != expected):
                raise ValueError(f'{key} conflicts with fixed policy: 5x, 1000 used + 200 reserve = 1200 USDT')
        atr_multiple = float(params.get('atr_multiple', 20.))
        if not math.isfinite(atr_multiple) or atr_multiple <= 0:
            raise ValueError('ATR multiple must be finite and positive')
        midpoint = (data['low_24h']+data['high_24h'])/2
        trigger = None
        entry = price
        if direction == 'long' and (price > midpoint or data.get('fresh_high')):
            entry = min(midpoint, price-tick)
            trigger = entry
        elif direction == 'short' and (price < midpoint or data.get('fresh_low')):
            entry = max(midpoint, price+tick)
            trigger = entry
        elif direction == 'neutral' and abs(price-midpoint) > .1*(data['high_24h']-data['low_24h']):
            entry = midpoint
            trigger = entry
        entry = _round(entry,tick,up=direction == 'short')
        if trigger is not None:
            trigger = entry
        loss_pct, gain_pct = (.15,.40) if direction == 'long' else (.40,.15) if direction == 'short' else (.20,.20)
        nominal_low, nominal_high = entry*(1-loss_pct), entry*(1+gain_pct)
        range_evidence = _range_structure(data,entry,nominal_low,nominal_high)
        range_evidence.update(atr_1m=atr,atr_multiple=atr_multiple,
                              nominal_low=nominal_low,nominal_high=nominal_high)
        low = _round(max(nominal_low, data['low_7d'],range_evidence['support']['price'], entry-atr_multiple*atr),tick,up=True)
        high = _round(min(nominal_high, data['high_7d'],range_evidence['resistance']['price'], entry+atr_multiple*atr),tick)
        if direction == 'neutral':
            radius = _round(min(entry-low,high-entry),tick)
            low, high = _round(entry-radius,tick,up=True), _round(entry+radius,tick)
        if not 0 < low < entry < high:
            raise ValueError('Clipped structure/ATR range does not enclose a finite positive entry')
        result.update(entry=entry, trigger=trigger, tick_size=tick, lot_size=lot,multiplier=multiplier,
                      initial_range={'low':low,'high':high},range_evidence=range_evidence,
                      range_rule='Nominal band clipped to confirmed 7d support/resistance, 7d extremes and ATR bounds',
                      entry_reason='Wait for trigger' if trigger is not None else 'Current price qualifies')
        min_n, max_n = int(params.get('min_grids', 2)), int(params.get('max_grids', 300))
        if not 2 <= min_n <= max_n <= 300:
            raise ValueError('Grid count limits must be within 2..300')
        fee = .0006
        quantity_unit = float(Decimal(str(multiplier))*Decimal(str(lot)))
        legs = 2 if direction == 'neutral' else 1
        for stage, factor in enumerate((1.,.75,.5,.25)):
            lower = _round(entry-(entry-low)*(factor if direction in ('long','neutral') else 1),tick,up=True)
            upper = _round(entry+(high-entry)*(factor if direction in ('short','neutral') else 1),tick)
            level_cache = {n:_levels(lower,upper,entry,n,tick) for n in range(min_n,max_n+1)}
            attempt = {'range_stage':stage,'losing_width_factor':factor,'leverage':leverage,
                       'used_margin':used,'reserved_margin':reserve,'low':lower,'high':upper,
                       'eligible':False,'reason':'No valid lot/tick grid produces '+requirement}
            result['attempts'].append(attempt)
            for n in range(max_n,min_n-1,-1):
                prices = level_cache[n]
                if prices is None:
                    continue
                centre = (prices[0]+prices[-1])/2
                if (direction == 'long' and entry > centre+tick*1e-6 or
                        direction == 'short' and entry < centre-tick*1e-6):
                    attempt['reason'] = 'Clipped range entry is outside the required half'
                    continue
                # Opening taker fees remain within used margin rather than silently exceeding $1000.
                quantity = _round(used*leverage/(legs*n*entry*(1+leverage*fee)),quantity_unit)
                if quantity <= 0:
                    continue
                min_profit = grid_cash_profit(quantity,prices[-2],prices[-1])
                if min_profit <= 0 or min_profit < cash_floor:
                    continue
                step = prices[1]-prices[0]
                config = dict(pair=pair,low=prices[0],high=prices[-1],grids=n,leverage=leverage,
                              investment=used,reserved_margin=reserve,entry_price=entry,direction=direction,
                              quantity=quantity,multiplier=multiplier,lot_size=lot,tick_size=tick,trigger=trigger,
                              adaptive_range_stops=True,adaptive_tight_stop_pct=.01,
                              adaptive_liquidation_clearance_pct=.01)
                estimate = dict(_preview(config,params.get('preview_fn')))
                step = estimate.get('grid_step',step)
                modeled_net = estimate.get('profit_per_grid_min',min_profit)
                if isinstance(modeled_net,bool) or not isinstance(modeled_net,(int,float,Decimal)):
                    attempt['reason'] = 'Actual per-order net PnL is unknown'
                    continue
                modeled_cash = Decimal(str(modeled_net))
                if not modeled_cash.is_finite():
                    attempt['reason'] = 'Actual per-order net PnL is unknown'
                    continue
                min_profit = min(min_profit,modeled_cash)
                if min_profit <= 0 or min_profit < cash_floor:
                    attempt['reason'] = 'Actual per-order cash net fails '+requirement
                    continue
                liq_long = estimate.get('liquidation_price_long',estimate.get('estimated_liquidation_price'))
                liq_short = estimate.get('liquidation_price_short',estimate.get('estimated_liquidation_price_high',estimate.get('estimated_liquidation_price')))
                width = prices[-1]-prices[0]
                buffers = {}
                safe = True
                for side, edge, liq in (('long',prices[0],liq_long),('short',prices[-1],liq_short)):
                    if direction not in (side,'neutral'):
                        continue
                    if liq is None or not math.isfinite(float(liq)) or liq <= 0:
                        safe = False
                        buffers[side] = {'range_fraction':None,'edge_fraction':None}
                    else:
                        distance = edge-liq if side == 'long' else liq-edge
                        buffers[side] = {'range_fraction':distance/width,'edge_fraction':distance/edge}
                        safe = safe and distance >= .1*width and distance >= .1*edge
                attempt.update(grids=n,quantity=quantity,low=prices[0],high=prices[-1],buffers=buffers,
                               reason='Safe liquidation buffer' if safe else 'Liquidation fails 10% edge and range buffers')
                if not safe:
                    # Lower N can alter full inventory loading, so continue testing it.
                    continue
                analytic_low = float(Decimal(str(prices[0]))*Decimal('.95'))
                analytic_high = float(Decimal(str(prices[-1]))*Decimal('1.05'))
                stops = {'long':_round(analytic_low,tick,up=True),
                         'short':_round(analytic_high,tick)}
                if not 0 < stops['long'] < prices[0] < prices[-1] < stops['short']:
                    attempt['reason'] = 'No valid tick-aligned stops within the 5% edge limits'
                    continue
                if ((direction in ('long','neutral') and not liq_long < stops['long']) or
                        (direction in ('short','neutral') and not stops['short'] < liq_short)):
                    attempt['reason'] = '5% range exit stop must precede losing-side liquidation'
                    continue
                margin_preview = grid_profit_preview(prices[0],prices[-1],n,leverage,used,entry,tick)
                estimate.update({key:value for key,value in margin_preview.items() if key.startswith('kucoin_profit_')})
                estimate.update(profit_per_grid_min=float(min_profit),
                                profit_per_grid_max=float(grid_cash_profit(quantity,prices[0],prices[1])),
                                actual_profit_basis='base quantity times price difference minus both fill fees',
                                nominal_profit_basis='margin/grids estimate and percent preview; display only, not admission',
                                quantity=quantity, order_count=estimate.get('order_count',legs*n),
                                range_exit_stop_low=analytic_low,range_exit_stop_high=analytic_high,
                                app_stop_low=stops['long'],app_stop_high=stops['short'],
                                effective_stop_loss_low=stops['long'],
                                effective_stop_loss_high=stops['short'],
                                liquidation_price_long=liq_long,liquidation_price_short=liq_short,
                                buffer_range_percent={side:values['range_fraction']*100 for side,values in buffers.items()},
                                buffer_edge_percent={side:values['edge_fraction']*100 for side,values in buffers.items()})
                attempt['eligible'] = True
                if step > movement + tick*1e-6:
                    result['warnings'].append('Step exceeds typical 1m movement; reducing grid count widens the step. Highest feasible count minimizes step; use actual crossings to rank.')
                range_evidence.update(final_low=prices[0],final_high=prices[-1])
                result.update(eligible=True,reason=f'{result["direction_reason"]}; safe {leverage}x, {n} grids, planned cash net {min_profit:.8g} USDT',
                              low=prices[0],high=prices[-1],levels=prices,step=step,interval=step,grids=n,leverage=leverage,
                              used_margin=used,reserved_margin=reserve,quantity=quantity,contracts_per_grid=quantity/multiplier,
                              per_grid_notional=quantity*entry,stop_loss=stops.get(direction,stops.get('long')),
                              stop_loss_high=stops.get('short') if direction == 'neutral' else None,preview=estimate,
                              range_exit_stop_pct=.05,range_exit_stop_low=analytic_low,range_exit_stop_high=analytic_high,
                              adaptive_range_stops=True,adaptive_tight_stop_pct=.01,
                              adaptive_liquidation_clearance_pct=.01,
                              hard_stop_low=stops['long'],hard_stop_high=stops['short'],
                              stop_tick_adjustment='App stops round inward by less than one tick, closing no later than 5% outside each edge',
                              opening_fee_budget=legs*quantity*n*entry*fee,typical_movement_1m=movement)
                return result
        result['reason'] = 'No safe positive-lot setup produces '+requirement+' after fees at fixed 5x with 1000 used + 200 reserve (1200 total)'
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        result['reason'] = str(exc)
    return result


def setup(pair, candles, market, parameters=None):
    """Convenience wrapper; scanner can cache ``features`` and use build_setup."""
    return build_setup(pair,features(candles,market.get('funding_rate',0.)),market,parameters)
