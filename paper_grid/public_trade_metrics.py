"""Explicit allowlist for publicly published lifecycle metrics."""
from paper_grid.metric_constants import (MAX_REPORTED_TRADES, MAX_REPORTED_SYMBOLS,
    EXCURSION_FIELDS, COHORT_NOTE, EXCURSION_NOTE)

SUMMARY_NUMBERS = ('closed_trades','profit_factor','average_hold_seconds',
                   'known_hold_trades','unknown_hold_trades','trades_total','per_symbol_total')
TRADE_NUMBERS = ('opened_at','closed_at','hold_seconds','net_pnl','entry_notional',
                 'entry_fees','exit_fee','funding_cost','adds') + EXCURSION_FIELDS


def _choice(value,choices,default='unavailable'):
    return value if isinstance(value,str) and value in choices else default


def safe(source,number,symbol):
    """Readers supply their existing bounded numeric and symbol validators."""
    source=source if isinstance(source,dict) else {}
    result={key:number(source.get(key)) for key in SUMMARY_NUMBERS}
    result['profit_factor_state']=_choice(source.get('profit_factor_state'),
        ('finite','no_losses','no_closes'))
    exposure=source.get('exposure') or {}
    result['exposure']={key:number(exposure.get(key)) for key in
                       ('observed_seconds','fraction','unknown_lifecycles')}
    result['exposure']['coverage']=_choice(exposure.get('coverage'),
        ('partial','complete_recorded_lifecycles'))
    result['per_symbol_pnl']=[]
    for row in (source.get('per_symbol_pnl') or [])[:MAX_REPORTED_SYMBOLS]:
        result['per_symbol_pnl'].append(dict(symbol=symbol(row.get('symbol')),
            closes=number(row.get('closes')),net_pnl=number(row.get('net_pnl'))))
    result['trades']=[]
    for row in (source.get('trades') or [])[:MAX_REPORTED_TRADES]:
        trade={key:number(row.get(key)) for key in TRADE_NUMBERS}
        trade.update(symbol=symbol(row.get('symbol')),excursion_coverage=_choice(
            row.get('excursion_coverage'),('partial','complete_observations')))
        result['trades'].append(trade)
    cohort=source.get('baseline_filter_blocked')
    result['baseline_filter_blocked']=None
    if isinstance(cohort,dict):
        result['baseline_filter_blocked']={key:number(cohort.get(key)) for key in
            ('blocked_trades','allowed_trades','unknown_trades','net_pnl')}
        result['baseline_filter_blocked'].update(interpretation=COHORT_NOTE,
            coverage=_choice(cohort.get('coverage'),('partial','complete_recorded_entries')))
    result['excursion_definition']=EXCURSION_NOTE
    return result
