"""Fee feasibility on the geometric lines actually used by paper execution."""
import math
from trader.papergrid.engine import FEE_RATE

FEE_SAFETY_MARGIN = .20


def economics(low, high, grids, safety_margin=FEE_SAFETY_MARGIN):
    if (not all(math.isfinite(x) for x in (low, high, safety_margin))
            or low <= 0 or high <= low or safety_margin < 0
            or type(grids) is not int or not 1 <= grids <= 200):
        raise ValueError('invalid grid spacing')
    ratio = math.exp(math.log(high / low) / grids)
    gross = low * (ratio - 1)
    fees = low * (1 + ratio) * FEE_RATE
    net = gross - fees
    return dict(step_pct=(ratio-1)*100, net_per_unit_low=net,
                fees_per_unit_low=fees, viable=net > 0 and net >= safety_margin*fees)


def choose_count(low, high, target_step_pct):
    """Fit the count to structure; zero means even one grid cannot pay its fees."""
    if low <= 0 or high <= low or target_step_pct <= 0:
        return 0
    count = min(200, max(1, math.floor(math.log(high/low)/math.log1p(target_step_pct/100))))
    while count and not economics(low, high, count)['viable']:
        count -= 1
    return count
