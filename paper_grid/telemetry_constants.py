"""Closed telemetry vocabulary; historical names remain readable, never rewritten."""
POSITION_CAP_EPSILON = 1e-9
BUY_REJECTION_REASONS = frozenset({
    'invalid_unit_cost', 'lots_lt_1', 'thin_ask_depth', 'insufficient_cash',
    'position_notional_cap', 'stop_inside_range', 'expected_net_lt_target',
    'score_below_min', 'symbol_cooldown', 'position_capacity',
})
HISTORICAL_BUY_REJECTION_REASONS = frozenset({
    'below_minimum_lot', 'insufficient_ask_depth', 'cash_or_position_cap',
    'immediate_risk_limit', 'insufficient_expected_net', 'below_min_score',
})
READABLE_BUY_REJECTION_REASONS = BUY_REJECTION_REASONS | HISTORICAL_BUY_REJECTION_REASONS
