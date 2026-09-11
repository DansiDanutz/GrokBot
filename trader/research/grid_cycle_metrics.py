"""Fee-net completion diagnostics; totals remain in their original model ledger."""
from decimal import Decimal

HOUR_MS = 3600000
FEE = Decimal('0.0006')
CYCLE_METRICS = (
    'completed_positive_net', 'completed_nonpositive_net',
    'completed_positive_net_per_hour', 'completed_positive_net_per_day',
    'completed_nonpositive_net_per_hour', 'completed_nonpositive_net_per_day',
    'completed_at_target', 'completed_below_target',
    'completed_grids_per_hour_at_net_1_usdt',
    'legacy_completed_at_net_1_usdt', 'legacy_completed_below_net_1_usdt',
    'legacy_completed_grids_per_hour_at_net_1_usdt')


def net_completion(event):
    """Reconstruct both fill fees from serialized model amounts using decimals.

    Closing side is opposite inventory side. Thus entry notional equals close
    notional + gross PnL / closing side. Decimal arithmetic prevents an exactly
    break-even serialized cycle becoming positive through binary fee arithmetic.
    This is model accounting, not proof of exchange fills or observed entry legs.
    """
    gross = Decimal(str(event['gross_pnl']))
    closing_notional = Decimal(str(event['quantity'])) * Decimal(str(event['price']))
    entry_notional = closing_notional + gross / Decimal(str(event['side']))
    return gross - (entry_notional + closing_notional) * FEE


def completed_cycle_metrics(events, start_ms, end_ms):
    """Classify this supplied ledger; elapsed time includes missing observations.

    Zero is excluded from success. No minimum positive economic profit is added.
    The old one-USDT counters retain their historical numerical tolerance and
    names, plus explicit legacy aliases; they do not define current success.
    """
    if end_ms < start_ms:
        raise ValueError('cycle window end must not precede start')
    nets = [net_completion(event) for event in events if event.get('completed_grid')]
    positive = sum(net > 0 for net in nets)
    nonpositive = len(nets)-positive
    legacy = sum(net >= Decimal('0.999999999') for net in nets)
    hours = (end_ms-start_ms)/HOUR_MS
    rate = lambda count: count/hours if hours else None
    return dict(completed_positive_net=positive, completed_nonpositive_net=nonpositive,
        completed_positive_net_per_hour=rate(positive),
        completed_positive_net_per_day=rate(24*positive),
        completed_nonpositive_net_per_hour=rate(nonpositive),
        completed_nonpositive_net_per_day=rate(24*nonpositive),
        completed_at_target=legacy, completed_below_target=len(nets)-legacy,
        completed_grids_per_hour_at_net_1_usdt=rate(legacy),
        legacy_completed_at_net_1_usdt=legacy, legacy_completed_below_net_1_usdt=len(nets)-legacy,
        legacy_completed_grids_per_hour_at_net_1_usdt=rate(legacy))
