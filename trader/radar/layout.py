"""Chart-supported entry layouts shared by the radar and paper admission."""
import math
from trader.radar.spacing import align_bounds, economics, choose_count

MIN_GRIDS = 70
MAX_GRIDS = 200
ORDER_TOLERANCE = 1


def order_split(low, interval, grids, price, direction):
    """Buy/sell order counts using the engine's omitted line and hedge books."""
    offset = (price-low)/interval
    gap = min(grids, max(0, math.ceil(offset-1e-12)))
    if direction == 'NEUTRAL':
        buys = gap + max(0, gap-1)
        return buys, 2*grids-buys
    if direction == 'SHORT':
        gap = min(grids, max(0, math.floor(offset+1e-12)))
    return gap, grids-gap


def layout_valid(low, interval, grids, price, direction):
    values = (low, interval, price)
    if (direction not in ('LONG','SHORT','NEUTRAL') or type(grids) is not int
            or not MIN_GRIDS <= grids <= MAX_GRIDS
            or any(isinstance(v,bool) or not isinstance(v,(int,float))
                   or not math.isfinite(v) or v <= 0 for v in values)):
        return False
    buys, sells = order_split(low,interval,grids,price,direction)
    target = {'LONG': .4, 'SHORT': .6, 'NEUTRAL': .5}[direction]
    return abs(buys-target*(buys+sells)) <= ORDER_TOLERANCE + 1e-10


def select_range(supports, resistances, row, direction):
    """Narrowest confirmed feasible pair, then greatest feasible grid count."""
    from trader.autopilot.risk import sizing
    tick = row.get('tick_size',0)
    pairs = []
    for support, st in supports:
        for resistance, rt in resistances:
            low, high = align_bounds(support,resistance,tick)
            if low < row['price'] < high:
                pairs.append((high-low,low,high,st,rt))
    if not pairs:
        return None, 'MISSING_STRUCTURE'
    reason = 'INSUFFICIENT_GRID_ROOM'
    for _,low,high,st,rt in sorted(pairs):
        maximum = choose_count(low,high,tick_size=tick,direction=direction)
        for count in range(maximum,MIN_GRIDS-1,-1):
            spacing = economics(low,high,count,tick_size=tick,direction=direction)
            if not spacing['viable']:
                continue
            if reason == 'INSUFFICIENT_GRID_ROOM':
                reason = 'ENTRY_SPLIT'
            if not layout_valid(low,spacing['interval'],count,row['price'],direction):
                continue
            reason = 'LIQUIDATION_OR_LOT_LIMIT'
            candidate = dict(row,range_low=low,range_high=high)
            try:
                risk = sizing(candidate,direction,1000,5,count)
            except ValueError:
                continue
            buys,sells = order_split(low,spacing['interval'],count,row['price'],direction)
            return dict(range_low=low,range_high=high,support=low,resistance=high,
                        support_touches=st,resistance_touches=rt,grids=count,
                        grid_interval=spacing['interval'],profit_pct_min=spacing['profit_pct_min'],
                        profit_pct_max=spacing['profit_pct_max'],entry_buy_orders=buys,
                        entry_sell_orders=sells,**risk), ''
    return None, reason
