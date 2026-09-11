"""Honest offline comparison; observed targets never determine order sizing."""
import math
import re
from dataclasses import replace

from .grid_types import GridConfig
from .kucoin_grid import FEE, create_bot, floating_pnl, margin_profit_per_grid, preview


def fixture_config(bot):
    """Interpret running Margin as total collateral including Reserved Margin.

    Screenshots omit contract metadata: fractional base units remove lot-rounding
    as a material source of error. They also omit original order sizes and margin
    amendments. Current order count is only a proxy for configured grid count.
    """
    return GridConfig(pair=bot['symbol'], direction=bot['mode'],
                      low=bot['range_low'], high=bot['range_high'],
                      grids=bot['grids_buy'] + bot['grids_sell'],
                      leverage=bot['leverage'], investment=bot['margin_usdt'] - bot['reserved_margin'],
                      reserved_margin=bot['reserved_margin'],
                      entry_price=bot['entry_price'], multiplier=1e-9)


def _comparison(bot):
    estimate = preview(fixture_config(bot))
    actual_profit = bot['grid_profit'] / bot['arbitrage_total']
    actual_liquidation = bot['est_liq_price']
    profit = estimate['profit_per_grid']
    margin_formula = margin_profit_per_grid(fixture_config(bot), bot['entry_price'])['usdt']
    margin_error = abs(margin_formula - actual_profit) / actual_profit
    liquidation = estimate['liquidation_price_' + bot['mode']]
    profit_error = abs(profit - actual_profit) / actual_profit
    liquidation_error = (abs(liquidation - actual_liquidation) / actual_liquidation
                         if liquidation is not None else None)
    return {'symbol': bot['symbol'], 'quantity': estimate['quantity'],
            'margin_formula_profit_per_grid': margin_formula,
            'margin_formula_relative_error': margin_error, 'margin_formula_pass': margin_error <= .15,
            'computed_profit_per_grid': profit, 'observed_profit_per_grid': actual_profit,
            'profit_relative_error': profit_error, 'profit_pass': profit_error <= .15,
            'computed_liquidation_price': liquidation,
            'observed_liquidation_price': actual_liquidation,
            'liquidation_relative_error': liquidation_error,
            'liquidation_pass': liquidation_error is not None and liquidation_error <= .10,
            'profit_per_grid_min': estimate['profit_per_grid_min'],
            'profit_per_grid_max': estimate['profit_per_grid_max']}


def calibration_report(bots, include_reconstruction=False):
    """Return explicit PASS/FAIL comparisons; a failure blocks parity claims."""
    bots = list(bots)
    rows = [_comparison(bot) for bot in bots]
    calibrated = bool(rows) and all(row['profit_pass'] and row['liquidation_pass'] for row in rows)
    report = {'calibrated': calibrated, 'status': 'passed' if calibrated else 'blocked',
            'profit_tolerance': .15, 'liquidation_tolerance': .10, 'bots': rows,
            'sizing_assumption': 'used_margin * leverage / (grid_count * entry_price)',
            'limitations': [
                'Original per-order quantity, contract risk tiers and margin amendment history absent.',
                'Current buy plus sell order count is used as configured grid count proxy.',
                'Mean arithmetic-grid net profit is compared with realized path-weighted average.',
                'Full inventory stress liquidation differs from current inventory liquidation.',
                'Running Margin is total collateral; used margin subtracts its reserved component.',
                'No symbol-specific fitted factors or inversion of observed targets is used.']}
    if include_reconstruction:
        report['state_reconstruction_diagnostics'] = state_reconstruction_diagnostics(bots)
    return report


def _assumed_inventory(bot):
    count = bot['grids_buy'] + bot['grids_sell']
    step = (bot['range_high'] - bot['range_low']) / count
    entry = bot['entry_price']
    if bot['mode'] == 'long':
        entries = [min(entry, bot['range_low'] + i * step) for i in range(count)]
        held = entries[count - bot['grids_sell']:]
        return entries, held, 1
    if bot['mode'] == 'short':
        entries = [max(entry, bot['range_low'] + (i + 1) * step) for i in range(count)]
        return entries, entries[:bot['grids_buy']], -1
    return [], [], 0


def _margin_hypothesis(bot, entries, side, quantity, includes_reserve):
    reserve = bot['reserved_margin']
    used = bot['margin_usdt'] - (reserve if includes_reserve else 0)
    total = used + reserve
    cost = quantity * sum(entries)
    threshold = (side * cost + cost * FEE - total) / (quantity * len(entries) * (side - .005))
    step = (bot['range_high'] - bot['range_low']) / len(entries)
    profit = quantity * (step - FEE * (bot['range_low'] + bot['range_high']))
    return {'name': ('margin_total_includes_reserve' if includes_reserve
                     else 'margin_used_reserve_additional'),
            'used_margin': used, 'reserved_margin': reserve, 'total_margin': total,
            'quantity': quantity, 'profit_per_grid': profit,
            'liquidation_price': threshold if threshold > 0 else None,
            'valid_margin_split': used > 0}


def _reconstruct_snapshot(bot):
    entries, held, side = _assumed_inventory(bot)
    coefficient = side * (len(held) * bot['price_at_capture'] - sum(held))
    quantity = bot['unrealized_pnl'] / coefficient if coefficient else None
    identified = (quantity is not None and math.isfinite(quantity) and quantity > 0
                  and not math.isclose(coefficient, 0, abs_tol=sum(held) * 1e-12))
    return {'symbol': bot['symbol'], 'status': 'reconstructed' if identified else 'underidentified',
            'reconstructed_quantity': quantity if identified else None,
            'assumed_held_slots': len(held), 'pnl_per_base_quantity': coefficient,
            'margin_hypotheses': [
                _margin_hypothesis(bot, entries, side, quantity, includes_reserve)
                for includes_reserve in (False, True)] if identified else []}


def state_reconstruction_diagnostics(bots):
    """Infer snapshot quantity from unrealized PnL, never calibration targets.

    This is a conditional state reconstruction, not a prediction of KuCoin's
    initial order allocation. Both margin interpretations are always reported;
    neither is selected using its apparent profit/liquidation agreement.
    """
    return {'predictive_calibration': False, 'bots': [_reconstruct_snapshot(bot) for bot in bots],
            'assumptions': [
                'Arithmetic equal-base-quantity intervals; current order counts proxy grid count.',
                'Long holds the top sell-count slots with entry_i=min(initial_entry,lower_level_i).',
                'Short mirrors long: bottom buy-count slots with entry_i=max(initial_entry,upper_level_i).',
                'Reported unrealized PnL excludes fees and funding and belongs to precisely those slots.',
                'Actual trade history can invalidate assumed slot inventory and its average entries.',
                'Quantity equals unrealized / sum(side*(current_price-assumed_entry_i)).',
                'Near-zero PnL coefficient or nonpositive inferred quantity is underidentified.',
                'Neutral inventory cannot be inferred from net PnL and order counts alone.',
                'Both margin interpretations use the same inferred quantity and full inventory stress.',
                'Maintenance is estimated at 0.5%; original exchange sizing and risk tiers remain unknown.',
                'These retrospective diagnostics do not change predictive calibration status.']}


def _neutral_example_config(example, metadata):
    return GridConfig(pair=example['symbol'], low=example['range_low'],
                      high=example['range_high'], grids=example['grids'],
                      leverage=example['leverage'], direction='neutral',
                      investment=example['investment_margin_usdt'],
                      reserved_margin=example['reserved_margin_usdt'],
                      entry_price=example['price_at_create'],
                      multiplier=metadata['multiplier'], lot_size=metadata['lotSize'],
                      tick_size=metadata['tickSize'], maintenance_rate=metadata['maintainMargin'])


def _observed_neutral_snapshot(config, example, quantity):
    state = create_bot(replace(config, quantity=quantity), config.entry_price, 0)
    observed = example['after_create']
    long = sum(p.quantity for p in state.positions if p.side == 1)
    short = sum(p.quantity for p in state.positions if p.side == -1)
    buys, sells = sum(o.side == 1 for o in state.orders), sum(o.side == -1 for o in state.orders)
    return {'long_quantity': long, 'short_quantity': short, 'buy_orders': buys,
            'sell_orders': sells, 'order_count': len(state.orders),
            'order_split_pass': buys == observed['grids_buy'] and sells == observed['grids_sell'],
            'position_split_pass': long == observed['position_long_lots'] * config.multiplier
                and short == abs(observed['position_short_lots']) * config.multiplier,
            'computed_unrealized_pnl': floating_pnl(state, config.entry_price),
            'observed_unrealized_pnl': observed['unrealized_pnl'],
            'actual_profit_per_grid_min': preview(state.config)['profit_per_grid_min'],
            'actual_profit_per_grid_max': preview(state.config)['profit_per_grid_max']}


def _neutral_liquidation_errors(estimate, example):
    results = {}
    for side in ('long', 'short'):
        predicted = estimate['liquidation_price_' + side]
        observed = example['est_' + side + '_liq_price']
        error = abs(predicted - observed) / observed if predicted is not None else None
        results[side] = {'computed': predicted, 'observed': observed,
                         'relative_error': error, 'pass': error is not None and error <= .10}
    return results


def neutral_creation_calibration(example, contract_metadata):
    """Compare forward allocation and independently observed order lots separately.

    Contract metadata must be supplied with its source/date; historical metadata
    is not asserted to describe the later screenshot's exact risk tier.
    """
    config = _neutral_example_config(example, contract_metadata)
    estimate = preview(config)
    observed_pct = [float(value) for value in re.findall(r'[0-9.]+', example['profit_per_grid_pct_shown'])]
    observed_lots = float(re.search(r'([0-9.]+) lots each',
        example['after_create']['open_order_ladder_first_15']).group(1))
    observed_quantity = observed_lots * config.multiplier
    percentages = [estimate['kucoin_profit_pct_min'], estimate['kucoin_profit_pct_max']]
    return {'calibrated': False, 'status': 'blocked', 'metadata_source': contract_metadata.get('source'),
            'percentage_tolerance_points': .3, 'liquidation_tolerance': .10,
            'computed_percentages': percentages, 'observed_percentages': observed_pct,
            'percentage_pass': all(abs(a - b) <= .3 for a, b in zip(percentages, observed_pct)),
            'predicted_quantity': estimate['quantity'], 'observed_quantity': observed_quantity,
            'quantity_pass': math.isclose(estimate['quantity'], observed_quantity),
            'forward_liquidation': _neutral_liquidation_errors(estimate, example),
            'observed_quantity_liquidation': _neutral_liquidation_errors(
                preview(replace(config, quantity=observed_quantity)), example),
            'observed_quantity_snapshot': _observed_neutral_snapshot(config, example, observed_quantity),
            'unrealized_reconstructed': False,
            'limitations': [
                'Observed order lots calibrate the inventory structure, not predictive sizing.',
                'Exact opening fill prices and contemporaneous bid/ask are absent; -2.39 is not reconstructed.',
                'Neutral per-leg exchange liquidation and collateral allocation remain unknown.',
                'Leveraged-margin percentage approximation is separate from actual base-quantity fill PnL.',
                'Historical contract metadata may differ from metadata at screenshot time.']}
