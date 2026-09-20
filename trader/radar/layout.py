"""Chart-supported entry layouts shared by the radar and paper admission."""
import math
from copy import deepcopy
from trader.papergrid.engine import BOT_FEE_RATE
from trader.radar.spacing import align_bounds, economics, choose_count, funding_stress
from trader.radar.entry import VERSION, funding_window

# Legacy pure calls keep their baseline; versioned entries and observations use 70.
MIN_GRIDS = 12
OBSERVATION_MIN_GRIDS = 70
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


def layout_valid(low, interval, grids, price, direction, *, split_mode='strict'):
    if split_mode not in ('strict', 'observe'):
        raise ValueError('unknown split mode')
    minimum = OBSERVATION_MIN_GRIDS if split_mode == 'observe' else MIN_GRIDS
    values = (low, interval, price)
    if (direction not in ('LONG','SHORT','NEUTRAL') or type(grids) is not int
            or not minimum <= grids <= MAX_GRIDS
            or any(isinstance(v,bool) or not isinstance(v,(int,float))
                   or not math.isfinite(v) or v <= 0 for v in values)):
        return False
    buys, sells = order_split(low,interval,grids,price,direction)
    if split_mode == 'observe':
        return low < price < low + grids*interval and buys > 0 and sells > 0
    target = {'LONG': .4, 'SHORT': .6, 'NEUTRAL': .5}[direction]
    return abs(buys-target*(buys+sells)) <= ORDER_TOLERANCE + 1e-10


def select_range(supports, resistances, row, direction, *, evidence=None, split_mode='strict',
                 funding_settlements=None, entry_policy=None, asof_ms=None):
    """Nearest feasible confirmed pair under an explicit version or legacy baseline."""
    if split_mode not in ('strict', 'observe'):
        raise ValueError('unknown split mode')
    if entry_policy not in (None, VERSION):
        raise ValueError('unknown entry policy')
    if entry_policy and (split_mode != 'strict' or funding_settlements is not None):
        raise ValueError('active entry policy requires strict split and recorded funding window')
    if funding_settlements is not None and (split_mode != 'observe'
            or type(funding_settlements) is not int or funding_settlements < 0):
        raise ValueError('funding scenario requires observation mode and nonnegative settlement count')
    from trader.autopilot.risk import sizing
    minimum = OBSERVATION_MIN_GRIDS if split_mode == 'observe' or entry_policy else MIN_GRIDS
    window = funding_window(row, asof_ms) if entry_policy else None
    if window and window['status'] != 'READY':
        return None, window['reason']
    if window:
        funding_settlements = window['settlements']
    tick = row.get('tick_size',0)
    if evidence is not None and evidence.get('status') != 'VERIFIED':
        return None, evidence.get('reason') or 'MISSING_STRUCTURE'
    pairs = []
    for support, st in supports:
        for resistance, rt in resistances:
            low, high = align_bounds(support,resistance,tick)
            if low < row['price'] < high:
                pairs.append((high-low,low,high,st,rt,support,resistance))
    if not pairs:
        return None, 'MISSING_STRUCTURE'
    reason = 'INSUFFICIENT_GRID_ROOM'
    for _,low,high,st,rt,support,resistance in sorted(pairs):
        maximum = choose_count(low,high,tick_size=tick,direction=direction)
        higher_rejections = []
        for count in range(maximum,minimum-1,-1):
            spacing = economics(low,high,count,tick_size=tick,direction=direction)
            if not spacing['viable']:
                higher_rejections.append(dict(grids=count,reason='GRID_RETURN_BELOW_1_PERCENT'))
                continue
            if reason == 'INSUFFICIENT_GRID_ROOM':
                reason = 'ENTRY_SPLIT'
            if not layout_valid(low,spacing['interval'],count,row['price'],direction,split_mode=split_mode):
                higher_rejections.append(dict(grids=count,reason='ENTRY_SPLIT'))
                continue
            if funding_settlements is not None:
                scenario = funding_stress(low,high,count,tick_size=tick,direction=direction,
                                          rate_pct=row.get('funding_pct'),settlements=funding_settlements)
                if scenario['status'] == 'UNKNOWN_RATE':
                    return None, 'UNKNOWN_FUNDING_RATE'
                if not scenario['passes_floor']:
                    reason = 'FUNDING_RETURN_BELOW_1_PERCENT'
                    higher_rejections.append(dict(grids=count,reason=reason))
                    continue
            reason = 'LIQUIDATION_OR_LOT_LIMIT'
            candidate = dict(row,range_low=low,range_high=high)
            try:
                risk = sizing(candidate,direction,1000,5,count)
            except ValueError:
                higher_rejections.append(dict(grids=count,reason='LIQUIDATION_OR_LOT_LIMIT'))
                continue
            buys,sells = order_split(low,spacing['interval'],count,row['price'],direction)
            proof = deepcopy({key:value for key,value in (evidence or {}).items()
                              if key not in ('supports','resistances')})
            target = {'LONG': .4, 'SHORT': .6, 'NEUTRAL': .5}[direction]
            proof.update(split_mode=split_mode,
                         split_target_buy_pct=100*target,
                         split_actual_buy_pct=100*buys/(buys+sells),
                         split_deviation_pct=100*abs(buys/(buys+sells)-target),
                         status='VERIFIED' if evidence else 'UNVERIFIED', reason='',
                         original_bounds=[support,resistance],rounded_bounds=[low,high],
                         selection='narrowest_confirmed_then_maximum_feasible',
                         grid_count=count,grid_interval=spacing['interval'],
                         actual_upper_line=spacing['actual_upper_line'],
                         maximum_fee_viable_count=maximum,minimum_required_grids=minimum,
                         higher_count_rejections=higher_rejections,
                         fee_rate_maker=BOT_FEE_RATE,fee_rate_taker=BOT_FEE_RATE)
            if window:
                proof.update(entry_policy_version=entry_policy,funding_window=window)
            if split_mode == 'observe' or window:
                proof['funding_stress'] = funding_stress(
                    low,high,count,tick_size=tick,direction=direction,
                    rate_pct=row.get('funding_pct'),
                    settlements=1 if funding_settlements is None else funding_settlements)
            if evidence:
                proof['selected_support'] = deepcopy(next(p for p in evidence['supports'] if p['price']==support))
                proof['selected_resistance'] = deepcopy(next(p for p in evidence['resistances'] if p['price']==resistance))
            return dict(range_low=low,range_high=high,support=low,resistance=high,
                        support_touches=st,resistance_touches=rt,grids=count,
                        grid_interval=spacing['interval'],profit_pct_min=spacing['profit_pct_min'],
                        profit_pct_max=spacing['profit_pct_max'],entry_buy_orders=buys,
                        entry_sell_orders=sells,range_evidence=proof,**risk), ''
    return None, reason
