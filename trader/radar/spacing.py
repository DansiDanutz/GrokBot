"""Arithmetic grid returns on allocated margin, after both modeled fill fees."""
import math
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING
from trader.papergrid.engine import FEE_RATE

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
    interval=float(interval)
    # Upper configured boundary is conservative even if tick truncation leaves a remainder.
    def pair(buy, sell):
        fees=(buy+sell)*FEE_RATE
        net=sell-buy-fees
        margin_price=sell if direction == 'SHORT' else buy
        return net, fees, net/margin_price*leverage*100
    net, fees, maximum=pair(low,low+interval)
    _, _, minimum=pair(high-interval,high)
    if direction == 'NEUTRAL':
        minimum *= (high-interval)/high  # Short closing pairs use sell-side margin.
    return dict(interval=interval, step_pct=interval/low*100,
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
