"""Pure replacement policy; costs are a hurdle, never a second ledger debit."""
import math

FEE = 0.0006
HOUR_MS = 3600000


def nonnegative(*values):
    if any(isinstance(v, bool) or not math.isfinite(v) or v < 0 for v in values):
        raise ValueError('expected finite nonnegative values')


def realized_rate(completion_times, now_ms, started_ms, lookback_hours):
    nonnegative(now_ms, started_ms, lookback_hours)
    if not lookback_hours or started_ms > now_ms:
        raise ValueError('invalid observation window')
    if any(t < started_ms or t > now_ms for t in completion_times):
        raise ValueError('completion outside bot lifetime')
    start = max(started_ms, now_ms - lookback_hours * HOUR_MS)
    hours = (now_ms - start) / HOUR_MS
    count = sum(start < t <= now_ms for t in completion_times)
    return count / hours if hours else 0.0


def decide(current_pair, candidate_pair, expected_gph, realized_gph,
           floating_pnl, close_notional, opening_notional, close_slippage=0,
           opening_slippage=0, *, lookback_hours=4, margin_gph=.25,
           payback_hours=4, target_net=1, outside_range=False):
    nonnegative(expected_gph, realized_gph, close_notional, opening_notional,
                close_slippage, opening_slippage, lookback_hours, margin_gph,
                payback_hours, target_net)
    if not math.isfinite(floating_pnl) or min(lookback_hours, payback_hours,
                                            target_net) <= 0:
        raise ValueError('invalid replacement parameters')
    fees = FEE * (close_notional + opening_notional)
    cost = max(0, -floating_pnl) + close_slippage + opening_slippage + fees
    required_gain = margin_gph + cost / (payback_hours * target_net)
    better = (candidate_pair is not None and candidate_pair != current_pair
              and expected_gph - realized_gph > required_gain)
    replace = bool(outside_range or better)
    return dict(replace=replace, reason='range_exit' if outside_range else
                'higher_cost_adjusted_grid_rate' if better else 'hold',
                current_pair=current_pair, next_pair=candidate_pair,
                expected_grids_per_hour=expected_gph,
                realized_grids_per_hour=realized_gph,
                required_increment_gph=required_gain, cost_usdt=cost,
                estimated_close_and_open_fees=fees,
                floating_pnl_realized_estimate=floating_pnl,
                close_slippage=close_slippage, opening_slippage=opening_slippage,
                lookback_hours=lookback_hours, margin_gph=margin_gph,
                payback_hours=payback_hours)
