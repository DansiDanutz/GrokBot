"""Review-only KuCoin form values from a supplied offline market snapshot."""
from .grid_scanner import scan, MAX_AGE_MS
from .grid_runner import new_bot, quote, switch_proposal
from trader.strategies.kucoin_grid import preview


def operator_output(snapshot, at_ms, state=None, started_ms=None,
                    lookback_hours=4, margin_gph=.25, payback_hours=4,
                    investment=1000):
    result = scan(snapshot, at_ms, investment=investment)
    best = result['candidates'][0] if result['candidates'] else None
    output = dict(asof_ms=at_ms, execution_enabled=False,
                  snapshot_only=True, form=best['form'] if best else None,
                  candidates=result['candidates'], excluded=result['excluded'],
                  coverage=result['coverage'],
                  signal='review initial form' if best else 'no eligible coin',
                  replica_verified=False)
    if best:
        output['preview'] = preview(new_bot(best, at_ms, investment).config)
    if state:
        if at_ms-state.timestamp_ms > MAX_AGE_MS:
            raise ValueError('stale bot state cannot produce an operator signal')
        if started_ms is None or started_ms > at_ms or state.timestamp_ms > at_ms:
            raise ValueError('valid bot start and as-of state are required')
        bid, ask = quote(snapshot, state.config.pair, at_ms)
        proposal = switch_proposal(state, best, at_ms, started_ms, bid, ask,
                                   lookback_hours, margin_gph, payback_hours)
        output['replacement'] = proposal
        output['signal'] = ('replace now with '+best['pair'] if best else
                            'range exit; close with no eligible replacement') \
            if proposal['replace'] else 'keep running'
    return output
