"""Arithmetic grid returns on allocated margin, after both modeled fill fees.

A completed grid pays the Futures Trading Bot fixed fee on both legs.
The return floor applies to each pair's allocated margin, excluding funding.
"""
import math
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING
from trader.papergrid.engine import BOT_FEE_RATE as FEE_RATE

FEE_SAFETY_MARGIN = .20
MIN_GRID_PROFIT_PCT = 1.0
GRID_LEVERAGE = 5


def economics(low, high, grids, safety_margin=FEE_SAFETY_MARGIN, *,
              tick_size=0, leverage=GRID_LEVERAGE, direction='LONG'):
    values = (low, high, safety_margin, tick_size, leverage)
    if (not all(isinstance(x, (float,int)) and not isinstance(x,bool) and math.isfinite(x) for x in values)
            or low <= 0 or high <= low or safety_margin < 0 or tick_size < 0 or leverage <= 0
            or type(grids) is not int or not 1 <= grids <= 200):
        raise ValueError('invalid grid spacing')
    interval = (Decimal(str(high))-Decimal(str(low)))/grids
    if tick_size:
        tick=Decimal(str(tick_size))
        interval=(interval/tick).to_integral_value(rounding=ROUND_FLOOR)*tick
    actual_high = float(Decimal(str(low)) + grids*interval)
    interval=float(interval)
    # The engine emits these arithmetic lines; an unused tick remainder is not a pair.
    def pair(buy, sell):
        fees=(buy+sell)*FEE_RATE
        net=sell-buy-fees
        margin_price=sell if direction == 'SHORT' else buy
        return net, fees, net/margin_price*leverage*100
    net, fees, maximum=pair(low,low+interval)
    _, _, minimum=pair(actual_high-interval,actual_high)
    if direction == 'NEUTRAL':
        minimum *= (actual_high-interval)/actual_high  # Short closing pairs use sell-side margin.
    return dict(interval=interval, actual_upper_line=actual_high, step_pct=interval/low*100,
                net_per_unit_low=net, fees_per_unit_low=fees,
                profit_pct_min=minimum, profit_pct_max=maximum,
                viable=interval > 0 and minimum > MIN_GRID_PROFIT_PCT + 1e-10
                       and net >= safety_margin*fees)


def choose_count(low, high, *, tick_size=0, leverage=GRID_LEVERAGE,
                 direction='LONG'):
    """Maximum count passing the return floor; never move the chart boundaries."""
    if low <= 0 or high <= low:
        return 0
    for count in range(200,0,-1):
        if economics(low,high,count,tick_size=tick_size,leverage=leverage,direction=direction)['viable']:
            return count
    return 0


def align_bounds(low, high, tick_size):
    """Round inward to tradable prices; never widen the chart-derived range."""
    if not tick_size:
        return low, high
    tick = Decimal(str(tick_size))
    return (float((Decimal(str(low))/tick).to_integral_value(rounding=ROUND_CEILING)*tick),
            float((Decimal(str(high))/tick).to_integral_value(rounding=ROUND_FLOOR)*tick))


def funding_stress(low, high, grids, *, rate_pct, settlements,
                   tick_size=0, leverage=GRID_LEVERAGE, direction='LONG'):
    """Adverse-cost scenario per base unit; not a predicted hold or realized PnL."""
    if direction not in ('LONG','SHORT','NEUTRAL') or type(settlements) is not int or settlements < 0:
        raise ValueError('invalid funding scenario')
    spacing = economics(low,high,grids,tick_size=tick_size,leverage=leverage,direction=direction)
    if (isinstance(rate_pct,bool) or not isinstance(rate_pct,(int,float))
            or not math.isfinite(rate_pct)):
        return dict(status='UNKNOWN_RATE')
    # Upper chart bound conservatively values one unit held at each settlement.
    # Ignore receipts; Neutral must survive the paying book without assuming a hedge.
    paid_rate = abs(rate_pct) if direction == 'NEUTRAL' else max(0,rate_pct*(1 if direction == 'LONG' else -1))
    funding = high*paid_rate/100*settlements
    interval = spacing['interval']
    returns, nets = [], []
    for index in range(grids):
        buy = low + index*interval
        sell = buy + interval
        net = interval-(buy+sell)*FEE_RATE-funding
        sides = ('LONG','SHORT') if direction == 'NEUTRAL' else (direction,)
        for side in sides:
            returns.append(net/(sell if side == 'SHORT' else buy)*leverage*100)
        nets.append(net)
    minimum = min(returns)
    return dict(status='SCENARIO',settlements=settlements,rate_pct=rate_pct,
                funding_per_unit=funding,net_per_unit_min=min(nets),
                profit_pct_min=minimum,
                passes_floor=spacing['viable'] and minimum > MIN_GRID_PROFIT_PCT+1e-10)
