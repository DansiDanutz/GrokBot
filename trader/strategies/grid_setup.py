"""Pure KuCoin Futures Grid form selection with an auditable search ledger.

Direction score is a weighted average in [-1,1]. Positive funding is a small
contrarian short component. Range starts at long -15/+40%, short -40/+15%,
neutral +/-20%, then intersects 7d structure and entry +/-20 minute ATRs.

Search priority: at the unchanged range try 6x reserves 0..900 in steps of 100,
then 5x, 4x, 3x with the same reserve schedule; only then repeat at losing-side
width factors .75, .5, .25. Both neutral sides narrow. First feasible wins.
Every grid level is tick aligned: round step DOWN to ticks, then keep entry's
position in the proposed range and contract the range to N integer steps.

Quantity is BASE units rounded down to multiplier*lot_size. Reserve never buys
exposure. Sizing sets q <= used*L/(legs*N*entry), where neutral has two legs,
and other modes one, additionally reserving taker opening
fees from the used margin. +1 is a net FLOOR across all adjacent pairs; tick/lot
constraints prevent an exact +1 guarantee. Both the user margin-per-grid formula
and actual fixed-quantity fill PnL must clear the floor. Highest feasible N wins. Reducing N
widens the step. A step wider than median minute range emits a warning; actual
crossings determine the radar ranking, without an extra economic rejection.
Liquidation must clear BOTH 10% of range width and 10% of the losing edge price.
"""
import math
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from types import SimpleNamespace

from trader.strategies.grid_features import features


DEFAULT_WEIGHTS = {'ema_slope_4h': 2., 'ema_slope_24h': 2.,
                   'structure_4h': 1., 'structure_24h': 1.,
                   'position_24h': 1.5, 'position_7d': 1.5, 'funding_sign': .5}


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


def build_setup(pair, data, market, parameters=None):
    """Build exact form from cached ``features`` and normalized market metadata."""
    params = dict(parameters or {})
    result = {'pair': pair, 'eligible': False, 'reason': '', 'total_margin': 1000., 'attempts': [], 'warnings': []}
    if not data.get('valid', False):
        result['reason'] = data.get('reason', 'Invalid feature history')
        return result
    try:
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
        cap = params.get('leverage_cap', 6)
        if isinstance(cap, bool) or int(cap) != cap or not 3 <= cap <= 6:
            raise ValueError('Leverage cap must be an integer from 3 to 6')
        cap = int(cap)
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
        low = _round(max(entry*(1-loss_pct), data['low_7d'], entry-atr_multiple*atr),tick,up=True)
        high = _round(min(entry*(1+gain_pct), data['high_7d'], entry+atr_multiple*atr),tick)
        if direction == 'neutral':
            radius = _round(min(entry-low,high-entry),tick)
            low, high = _round(entry-radius,tick,up=True), _round(entry+radius,tick)
        if not 0 < low < entry < high:
            raise ValueError('Clipped structure/ATR range does not enclose a finite positive entry')
        result.update(entry=entry, trigger=trigger, tick_size=tick, lot_size=lot,multiplier=multiplier,
                      initial_range={'low':low,'high':high}, range_rule='Nominal band intersected with 7d structure and entry +/-20 ATR (configurable)',
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
            profit_cache = {}
            for leverage in range(cap,2,-1):
                for reserve in range(0,1000,100):
                    used = 1000-reserve
                    attempt = {'range_stage':stage,'losing_width_factor':factor,'leverage':leverage,
                               'used_margin':used,'reserved_margin':reserve,'low':lower,'high':upper,
                               'eligible':False,'reason':'No valid lot/tick grid reaches +1 USDT net'}
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
                        if n not in profit_cache:
                            profit_cache[n] = grid_profit_preview(prices[0], prices[-1], n, cap, 1000., entry, tick)
                        # Formula is linear in leverage and used margin; cache its price lattice.
                        margin_preview = {key: value*(leverage/cap)*(used/1000 if 'usdt' in key else 1)
                                          for key,value in profit_cache[n].items() if key.startswith('kucoin_profit_')}
                        if margin_preview['kucoin_profit_usdt_min'] < 1.:
                            continue
                        # Opening taker fees remain within used margin rather than silently exceeding $1000.
                        quantity = _round(used*leverage/(legs*n*entry*(1+leverage*fee)),quantity_unit)
                        if quantity <= 0:
                            continue
                        min_profit = quantity*((prices[-1]-prices[-2])-fee*(prices[-1]+prices[-2]))
                        if min_profit < 1.:
                            continue
                        step = prices[1]-prices[0]
                        config = dict(pair=pair,low=prices[0],high=prices[-1],grids=n,leverage=leverage,
                                      investment=used,reserved_margin=reserve,entry_price=entry,direction=direction,
                                      quantity=quantity,multiplier=multiplier,lot_size=lot,tick_size=tick,trigger=trigger)
                        estimate = dict(_preview(config,params.get('preview_fn')))
                        step = estimate.get('grid_step',step)
                        min_profit = min(min_profit, estimate.get('profit_per_grid_min', min_profit))
                        if min_profit < 1.:
                            attempt['reason'] = 'Actual per-order net PnL is below +1 USDT'
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
                        stops = {}
                        for side,edge,liq in (('long',prices[0],liq_long),('short',prices[-1],liq_short)):
                            if direction in (side,'neutral'):
                                stop = _round((edge+liq)/2,tick,up=side == 'short')
                                if not (liq < stop < edge if side == 'long' else edge < stop < liq):
                                    safe = False
                                    break
                                stops[side] = stop
                        if not safe:
                            attempt['reason'] = 'No tick-aligned stop strictly between range and liquidation'
                            continue
                        estimate.update({key:value for key,value in margin_preview.items() if key.startswith('kucoin_profit_')})
                        estimate.update(profit_per_grid_min=min_profit,
                                        profit_per_grid_max=quantity*(step-fee*(prices[0]+prices[1])),
                                        quantity=quantity, order_count=estimate.get('order_count',legs*n),
                                        liquidation_price_long=liq_long,liquidation_price_short=liq_short,
                                        buffer_range_percent={side:values['range_fraction']*100 for side,values in buffers.items()},
                                        buffer_edge_percent={side:values['edge_fraction']*100 for side,values in buffers.items()})
                        attempt['eligible'] = True
                        if step > movement + tick*1e-6:
                            result['warnings'].append('Step exceeds typical 1m movement; reducing grid count widens the step. Highest feasible count minimizes step; use actual crossings to rank.')
                        result.update(eligible=True,reason=f'{result["direction_reason"]}; safe {leverage}x, {n} grids, net floor {min_profit:.4f} USDT',
                                      low=prices[0],high=prices[-1],levels=prices,step=step,interval=step,grids=n,leverage=leverage,
                                      used_margin=used,reserved_margin=reserve,quantity=quantity,contracts_per_grid=quantity/multiplier,
                                      per_grid_notional=quantity*entry,stop_loss=stops.get(direction,stops.get('long')),
                                      stop_loss_high=stops.get('short') if direction == 'neutral' else None,preview=estimate,
                                      target_profit_per_grid=1.,target_semantics='both margin-per-grid estimate and actual per-order PnL >=1 after two 0.06% fees; exact equality limited by tick/lot sizes',
                                      opening_fee_budget=legs*quantity*n*entry*fee,typical_movement_1m=movement)
                        return result
        result['reason'] = 'No safe positive-lot setup reaches +1 USDT after fees within $1000 total margin'
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        result['reason'] = str(exc)
    return result


def setup(pair, candles, market, parameters=None):
    """Convenience wrapper; scanner can cache ``features`` and use build_setup."""
    return build_setup(pair,features(candles,market.get('funding_rate',0.)),market,parameters)
