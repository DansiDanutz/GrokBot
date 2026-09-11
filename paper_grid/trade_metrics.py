"""Pure lifecycle reporting from saved paper evidence; no provider or write path."""
import math
from paper_grid import coinglass, engine
from paper_grid.metric_constants import (ARMS, CLOSE_REASONS, SYMBOL,
    SECONDS_PER_HOUR, MAX_REPORTED_TRADES, MAX_REPORTED_SYMBOLS, DEFAULT_TICK_SECONDS,
    COHORT_NOTE, EXCURSION_NOTE)


def _number(value, *, nonnegative=False):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or (nonnegative and value < 0)):
        raise ValueError('invalid analytics number')
    return value


def _symbol(value, strict):
    if not isinstance(value, str) or not SYMBOL.fullmatch(value):
        if strict:
            raise ValueError('invalid analytics symbol')
        return 'UNKNOWN'
    return value


def _closed(event, position, symbol, strict):
    opened = position.get('opened_at')
    pnl = _number(event.get('net_pnl'))
    def cost(key):
        value = event.get(key)
        return _number(value, nonnegative=True) if strict or value is not None else None
    return dict(opened_at=opened, closed_at=event['time'], symbol=symbol,
        adds=position.get('adds'), entry_notional=position.get('entry_notional'),
        net_pnl=pnl, entry_fees=cost('entry_fees'), exit_fee=cost('exit_fee'),
        funding_cost=cost('funding_model_cost'),
        hold_seconds=event['time']-opened if opened is not None else None,
        reason=event.get('reason') if event.get('reason') in CLOSE_REASONS else 'unknown',
        result='win' if pnl > 0 else 'loss' if pnl < 0 else 'breakeven',
        _fills=position.get('_fills', []))


def lifecycles(events, end, strict=False):
    active, closed = {}, {arm: [] for arm in ARMS}
    for event in sorted(events, key=lambda e:e['time']):
        arm, kind = event.get('account'), event.get('type')
        if event['time'] > end or arm not in ARMS or kind not in ('open','add','close'):
            continue
        symbol = _symbol(event.get('symbol'), strict); key = (arm,symbol)
        if kind == 'open':
            if key in active:
                raise ValueError('conflicting open analytics lifecycle')
            notional = event.get('notional')
            if strict or notional is not None:
                _number(notional, nonnegative=True)
            active[key] = dict(opened_at=event['time'],adds=0,
                               entry_notional=notional,_fills=[event])
        elif kind == 'add':
            if strict:
                _number(event.get('notional'),nonnegative=True)
            if key not in active:
                active[key]=dict(opened_at=None,adds=None,entry_notional=None,_fills=[])
            if key in active:
                old=active[key]; total=old.get('entry_notional')
                extra=event.get('notional')
                active[key]=dict(old,adds=old['adds']+1 if old['adds'] is not None else None,
                    entry_notional=total+extra if total is not None and extra is not None else None,
                    _fills=old['_fills']+[event])
        else:
            closed[arm].append(_closed(event,active.pop(key,{}),symbol,strict))
    return closed,active


def closed_trades(events,end):
    """Legacy analytics lifecycle contract, with identical strict validation."""
    closed,_ = lifecycles(events,end,strict=True)
    return {arm:[{k:v for k,v in t.items() if not k.startswith('_')} for t in rows]
            for arm,rows in closed.items()}


def _union(intervals):
    total=0; previous=None
    for begin,end in sorted(intervals):
        if previous is None:
            previous=(begin,end)
        elif begin <= previous[1]:
            previous=(previous[0],max(previous[1],end))
        else:
            total+=previous[1]-previous[0];previous=(begin,end)
    return total+(previous[1]-previous[0] if previous else 0)


def _exposure(doc,arm,start,end,closed,active):
    intervals=[]; unknown=0
    for trade in closed:
        opened=trade['opened_at']; stop=trade['closed_at']
        if opened is None:
            unknown+=stop>start
        elif stop>start:
            intervals.append((max(start,opened),min(end,stop)))
    for (account,_),position in active.items():
        if account==arm:
            if position['opened_at'] is None:
                unknown+=1
            else:
                intervals.append((max(start,position['opened_at']),end))
    known_symbols={symbol for account,symbol in active if account==arm}
    current=doc.get('accounts',{}).get(arm,{}).get('state',{}).get('positions',{})
    for symbol in set(current)-known_symbols:
        position=current[symbol]
        opened=position.get('opened_at') if isinstance(position,dict) else None
        if not engine._number(opened) or opened<end:
            unknown+=1
    seconds=_union(intervals); duration=end-start
    return dict(observed_seconds=seconds,fraction=seconds/duration if duration and not unknown else None,
                coverage='partial' if unknown else 'complete_recorded_lifecycles',
                unknown_lifecycles=unknown)


def _filter_state(trade,observations):
    obs=observations.get(trade['opened_at'])
    if (obs is None or trade['opened_at'] is None
            or not isinstance(obs.get('coinglass'),dict)):
        return 'unknown'
    symbol=trade['symbol']; quote=obs.get('market',{}).get(symbol)
    if not isinstance(quote,dict) or quote.get('eligible') is not True:
        return 'unknown'
    probe={symbol:dict(quote,reasons=[])}
    result=coinglass.apply_filter(probe,obs.get('coinglass',{}),obs['time'])
    return 'allowed' if result[symbol].get('eligible') is True else 'blocked'


def _cohort(trades,observations):
    classified=[(_filter_state(t,observations),t) for t in trades]
    blocked=[t for state,t in classified if state=='blocked']
    unknown=sum(state=='unknown' for state,_ in classified)
    return dict(blocked_trades=len(blocked),allowed_trades=len(trades)-len(blocked)-unknown,
        unknown_trades=unknown,net_pnl=sum(t['net_pnl'] for t in blocked),
        coverage='partial' if unknown else 'complete_recorded_entries',interpretation=COHORT_NOTE)


def _advance(position,record,at,c,symbol):
    elapsed=at-position['funding_time']
    cost=position['quantity']*position['last_mark']*position['funding_rate']*elapsed
    updated=dict(position,funding_time=at,funding_accrued=position['funding_accrued']+
                 cost/(position['funding_interval_hours']*SECONDS_PER_HOUR))
    if engine._quote(record,at,c):
        updated.update(last_mark=record['mark'],last_bid=record['bid'])
    if engine._entry_record(symbol,record,at,c):
        updated.update(funding_rate=max(record['funding_rate'],0),
                       funding_interval_hours=record['funding_interval_hours'])
    return updated


def _fill(position,event,record,at,c,symbol):
    if not engine._entry_record(symbol,record,at,c):
        return None
    if not all(engine._number(event.get(k),True) for k in ('notional','fill_price','contracts')):
        return None
    if not engine._number(event.get('fee')):
        return None
    old=position or dict(quantity=0,cost_basis=0,entry_fees=0,contracts=0,
                         funding_accrued=0,funding_time=at)
    return dict(old,quantity=old['quantity']+event['notional']/event['fill_price'],
        cost_basis=old['cost_basis']+event['notional'],entry_fees=old['entry_fees']+event['fee'],
        contracts=old['contracts']+event['contracts'],last_mark=record['mark'],last_bid=record['bid'],
        funding_rate=max(record['funding_rate'],0),funding_interval_hours=record['funding_interval_hours'])


def _excursion_result(values,missing,available):
    return dict(observed_mae_net_usdt=min(values) if available and values else None,
        observed_mfe_net_usdt=max(values) if available and values else None,
        excursion_samples=len(values) if available else 0,excursion_missing_samples=missing,
        excursion_coverage=('partial' if missing else 'complete_observations') if available else 'unavailable')


def _excursions(trade,observations,config,tick_seconds):
    if trade['opened_at'] is None or not isinstance(config,dict):
        return _excursion_result([],0,False)
    if not set(engine.default_config()).issubset(config):
        return _excursion_result([],0,False)
    c=engine._config(config); start,end=trade['opened_at'],trade['closed_at']
    rows={at:o for at,o in observations.items() if start<=at<=end}
    fills={}
    for event in trade['_fills']:
        fills.setdefault(event['time'],[]).append(event)
    position=None; values=[]; missing=0; previous=start; valid=True; funding_known=True
    for at in sorted(set(rows)|set(fills)):
        obs=rows.get(at); record=obs.get('market',{}).get(trade['symbol']) if obs else None
        gap=max(0,math.floor((at-previous)/tick_seconds)-1);missing+=gap;previous=at
        funding_known=funding_known and gap==0
        if position is not None:
            position=_advance(position,record,at,c,trade['symbol'])
            if at in fills and funding_known and engine._quote(record,at,c):
                values.append(engine._net(position,record,c))
        for fill in fills.get(at,[]):
            position=_fill(position,fill,record,at,c,trade['symbol']) if valid else None
            valid=position is not None
        if position is None or not engine._quote(record,at,c):
            missing+=1
        elif funding_known:
            values.append(engine._net(position,record,c))
            missing+=not engine._full_depth(record,'bid',position['contracts'])
    missing+=max(0,math.floor((end-previous)/tick_seconds)-1)
    if start not in rows or end not in rows:
        missing+=1
    if not valid or position is None:
        return _excursion_result([],missing,False)
    values.append(trade['net_pnl'])
    return _excursion_result(values,missing,True)


def _summary(trades):
    profit=sum(t['net_pnl'] for t in trades if t['net_pnl']>0)
    loss=-sum(t['net_pnl'] for t in trades if t['net_pnl']<0)
    holds=[t['hold_seconds'] for t in trades if t['hold_seconds'] is not None]
    symbols={}
    for trade in trades:
        symbol=trade['symbol'];old=symbols.get(symbol,dict(symbol=symbol,closes=0,net_pnl=0))
        symbols[symbol]=dict(old,closes=old['closes']+1,net_pnl=old['net_pnl']+trade['net_pnl'])
    return dict(closed_trades=len(trades),profit_factor=profit/loss if loss else None,
        profit_factor_state='finite' if loss else 'no_losses' if trades else 'no_closes',
        average_hold_seconds=sum(holds)/len(holds) if holds else None,
        known_hold_trades=len(holds),unknown_hold_trades=len(trades)-len(holds),
        per_symbol_pnl=[symbols[s] for s in sorted(symbols)][:MAX_REPORTED_SYMBOLS],
        per_symbol_total=len(symbols))


def report(doc,arm,start,end,include_start=False):
    """Window closes retain full known lifecycle history; exposure clips to window."""
    closed,active=lifecycles(doc.get('events',[]),end)
    selected=[t for t in closed[arm] if (start<=t['closed_at'] if include_start else start<t['closed_at'])]
    observations={o['time']:o for o in doc.get('observations',[]) if not o.get('skipped')}
    decorated=[dict({k:v for k,v in t.items() if not k.startswith('_')},
                    **_excursions(t,observations,doc.get('config'),doc.get('tick_seconds',DEFAULT_TICK_SECONDS)))
               for t in selected[-MAX_REPORTED_TRADES:]]
    result=_summary(selected)
    result.update(exposure=_exposure(doc,arm,start,end,closed[arm],active),
        trades=decorated,trades_total=len(selected),excursion_definition=EXCURSION_NOTE,
        baseline_filter_blocked=_cohort(selected,observations) if arm=='baseline' else None)
    return result
