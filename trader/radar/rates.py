"""Shared fixed calibration from Dan's live bots, 2026-09-11; no fitting."""
import math

K_STANDARD = 1.9
K_MAJOR = .45
MAJOR_TURNOVER_USDT = 50_000_000


def expected_grids_per_hour(atr_pct, step_pct, turnover):
    """Estimate crossings using the approved square-root step adjustment."""
    if any(not math.isfinite(x) for x in (atr_pct, step_pct, turnover)) or step_pct <= 0:
        raise ValueError('invalid grid rate inputs')
    major = turnover >= MAJOR_TURNOVER_USDT
    coefficient, base = (K_MAJOR, .52) if major else (K_STANDARD, .8)
    return coefficient * math.sqrt(step_pct / base) * atr_pct / step_pct
