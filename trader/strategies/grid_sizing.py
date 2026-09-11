"""Size arithmetic grids for a minimum paired profit after both fill fees.

Quantity uses investment * leverage / (grids * centre), rounded down to the
contract lot. This is a reference notional allocation, not KuCoin's unpublished
fee-reserve sizing algorithm. Entry fees reduce paper equity immediately; there
is no separate fabricated exchange reserve. The worst upper interval pays more
fees, so use its exact fee equation rather than the central approximation.
"""
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import math

from .kucoin_grid import FEE


@dataclass(frozen=True)
class GridSizing:
    investment: float
    leverage: float
    centre: float
    grids: int
    step: float
    low: float
    high: float
    quantity: float
    profit_floor: float
    target_profit: float

    def form_values(self, pair):
        return {'pair': pair, 'direction': 'neutral', 'low': self.low,
                'high': self.high, 'grids': self.grids, 'leverage': self.leverage,
                'investment': self.investment, 'spacing': 'arithmetic'}


def _decimal(value, name, allow_zero=False):
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError(f'{name} must be finite')
    if not math.isfinite(value) or value < 0 or (value == 0 and not allow_zero):
        raise ValueError(f'{name} must be positive')
    return Decimal(str(value))


def size_grid(*, centre, investment=1000, leverage=5, target_profit=1,
              grids=20, tick_size=0, multiplier=1, lot_size=1):
    """Return form values and lot-rounded quantity; raise if infeasible."""
    values = {'centre': centre, 'investment': investment, 'leverage': leverage,
              'target_profit': target_profit, 'multiplier': multiplier, 'lot_size': lot_size}
    numbers = {key: _decimal(value, key) for key, value in values.items()}
    tick = _decimal(tick_size, 'tick_size', True)
    if type(grids) is not int or not 2 <= grids <= 1000 or not 1 <= leverage <= 10:
        raise ValueError('invalid grid count or leverage')
    c, margin, lev = numbers['centre'], numbers['investment'], numbers['leverage']
    target, fee = numbers['target_profit'], Decimal(str(FEE))
    unit = numbers['multiplier'] * numbers['lot_size']
    q = (margin * lev / (grids * c) / unit).to_integral_value(rounding=ROUND_FLOOR) * unit
    if q <= 0:
        raise ValueError('investment cannot fund one contract lot per grid')
    step = (target / q + 2 * fee * c) / (1 - fee * (grids - 1))
    low, high, step = _round_range(c, step, grids, tick, q, target, fee)
    if low <= 0:
        raise ValueError('target requires a nonpositive lower price')
    floor = q * (step - fee * (2 * high - step))
    return GridSizing(investment, leverage, centre, grids, float(step), float(low),
                      float(high), float(q), float(floor), target_profit)


def _round_range(centre, step, grids, tick, quantity, target, fee):
    if tick:
        step = (step / tick).to_integral_value(rounding=ROUND_CEILING) * tick
    low = centre - grids * step / 2
    if tick:
        low = (low / tick).to_integral_value(rounding=ROUND_FLOOR) * tick
    high = low + grids * step
    # Flooring low shifts the range downward, which reduces the worst fee;
    # rounding step upward increases net profit. No heuristic iteration needed.
    if quantity * (step - fee * (2 * high - step)) < target - Decimal('1e-24'):
        raise ValueError('rounded range does not meet target')
    return low, high, step
