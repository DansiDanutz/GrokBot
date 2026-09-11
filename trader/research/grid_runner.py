"""Causal minute replay with explicit path and market-evidence limitations."""
from dataclasses import replace
import math
import random

from trader.strategies.grid_types import GridConfig
from trader.strategies.grid_sizing import size_grid
from dataclasses import asdict
from trader.strategies.kucoin_grid import (
    create_bot, advance, stop, floating_pnl, net_equity, FUNDING_MS, FEE,
)
from .grid_scanner import scan, MAX_AGE_MS
from .grid_replacement import decide, realized_rate

MINUTE = 60000
HOUR = 3600000


class CoverageError(ValueError):
    pass


def quote(snapshot, pair, at_ms):
    market = snapshot.market(pair, at_ms)
    if not market:
        raise CoverageError('missing historical market quote')
    for key in ('book_time_ms', 'book_observed_at_ms', 'ticker_observed_at_ms'):
        value = market.get(key)
        if value is None or not 0 <= at_ms - value <= MAX_AGE_MS:
            raise CoverageError('stale or future historical quote')
    bid, ask = market.get('bid'), market.get('ask')
    if not all(isinstance(x, (int, float)) and math.isfinite(x) and x > 0
               for x in (bid, ask)) or bid > ask:
        raise CoverageError('invalid historical quote')
    return bid, ask



def resize(candidate, capital):
    if capital <= 0:
        raise CoverageError('capital exhausted after close costs')
    sizing = size_grid(centre=(candidate['bid']+candidate['ask'])/2,
                       investment=capital, leverage=5,
                       grids=candidate['form']['grids'],
                       multiplier=candidate['multiplier'],
                       lot_size=candidate['lot_size'],
                       tick_size=candidate.get('tick_size', 0))
    return dict(candidate, form=sizing.form_values(candidate['pair']),
                sizing=asdict(sizing))


def new_bot(candidate, at_ms, capital):
    form, sizing = candidate['form'], candidate['sizing']
    config = GridConfig(pair=form['pair'], low=form['low'], high=form['high'],
                        grids=form['grids'], leverage=form['leverage'],
                        investment=capital, direction=form['direction'],
                        quantity=sizing['quantity'],
                        multiplier=candidate['multiplier'],
                        lot_size=candidate['lot_size'])
    midpoint = (candidate['bid'] + candidate['ask']) / 2
    state = create_bot(config, midpoint, at_ms)
    # Market entry fills cross the observed spread. Seed quantities and orders
    # retain the declared grid convention; opening PnL starts below zero.
    positions = tuple(replace(p, entry=candidate['ask'] if p.side == 1
                              else candidate['bid']) for p in state.positions)
    fees = sum(p.quantity * p.entry * FEE for p in positions)
    return replace(state, positions=positions, fees=fees)


def _bar(snapshot, pair, end_ms):
    rows = snapshot.candles(pair, end_ms - MINUTE, end_ms)
    if len(rows) != 1 or rows[0]['time_ms'] != end_ms - MINUTE:
        raise CoverageError('missing or duplicate replay candle')
    row = rows[0]
    prices = [row.get(k) for k in ('open', 'high', 'low', 'close')]
    if not all(isinstance(p, (int, float)) and math.isfinite(p) and p > 0
               for p in prices):
        raise CoverageError('invalid candle price')
    if row['low'] > min(prices) or row['high'] < max(prices):
        raise CoverageError('invalid candle OHLC bounds')
    return row


def minute_paths(snapshot, state, end_ms):
    row = _bar(snapshot, state.config.pair, end_ms)
    bid, ask = quote(snapshot, state.config.pair, end_ms - MINUTE)
    spread_fraction = (ask - bid) / (ask + bid)
    rate, mark = 0, None
    if end_ms % FUNDING_MS == 0:
        rows = snapshot.funding(state.config.pair, end_ms - MINUTE, end_ms)
        if len(rows) != 1 or rows[0]['time_ms'] != end_ms:
            raise CoverageError('missing actual eight-hour funding settlement')
        rate = rows[0]['rate']
        quote(snapshot, state.config.pair, end_ms)
        mark = snapshot.market(state.config.pair, end_ms).get('mark')
        if not isinstance(mark, (int, float)) or not math.isfinite(mark) or mark <= 0:
            raise CoverageError('missing settlement mark price')
    paths = []
    for middle in (('low', 'high'), ('high', 'low')):
        current, equity, floating = state, [], []
        for offset, field in zip((0, 20000, 40000, 60000),
                                 ('open', *middle, 'close')):
            price, timestamp = row[field], end_ms - MINUTE + offset
            current = advance(current, price, timestamp,
                              funding_rate=rate * mark / price if mark else 0,
                              bid=price*(1-spread_fraction),
                              ask=price*(1+spread_fraction))
            equity.append(net_equity(current, current.price))
            floating.append(floating_pnl(current, current.price))
            if current.liquidated or current.status == 'out_of_range':
                break
        if current.status == 'out_of_range' and current.timestamp_ms < end_ms:
            current = advance(current, current.price, end_ms,
                              funding_rate=rate*mark/current.price if mark else 0)
            equity.append(net_equity(current, current.price))
            floating.append(floating_pnl(current, current.price))
        paths.append((current, equity, floating))
    return min(paths, key=lambda p: (p[1][-1], p[0].completed_grids))


def switch_proposal(state, candidate, at_ms, started_ms, bid, ask,
                    lookback_hours, margin_gph, payback_hours):
    price = (bid + ask) / 2
    floating = floating_pnl(state, price)
    close_notional = sum(p.quantity * (bid if p.side == 1 else ask)
                         for p in state.positions)
    slippage = sum(p.quantity * abs(price - (bid if p.side == 1 else ask))
                   for p in state.positions)
    opening, opening_slippage = 0, 0
    if candidate:
        trial = new_bot(candidate, at_ms, candidate['form']['investment'])
        opening = sum(p.quantity*p.entry for p in trial.positions)
        opening_slippage = max(0, -floating_pnl(trial, trial.price))
    return decide(state.config.pair, candidate['pair'] if candidate else None,
                  candidate['expected_grids_per_hour'] if candidate else 0,
                  realized_rate(state.completion_times, at_ms, started_ms,
                                lookback_hours), floating, close_notional,
                  opening, slippage, opening_slippage,
                  lookback_hours=lookback_hours, margin_gph=margin_gph,
                  payback_hours=payback_hours,
                  outside_range=state.status == 'out_of_range')


def _metrics(state, held_ms):
    return dict(pair=state.config.pair, held_ms=held_ms,
                initial_investment=state.config.investment,
                completed_grids=state.completed_grids,
                grid_profit=state.grid_profit, grid_net_profit=state.grid_net_profit,
                seed_pnl=state.seed_pnl, floating_pnl_realized=state.close_pnl,
                funding=state.funding, fees=state.fees,
                floating_pnl=floating_pnl(state, state.price),
                net=net_equity(state, state.price)-state.config.investment,
                liquidations=int(state.liquidated), range_exits=state.range_exits)


class Run:
    def __init__(self, snapshot, start, end, parameters, seed=None):
        self.snapshot, self.start, self.end = snapshot, start, end
        self.parameters, self.rng = parameters, random.Random(seed) if seed is not None else None
        self.state, self.started = None, start
        self.cash = parameters.get('investment', 1000)
        self.initial, self.peak = self.cash, self.cash
        self.max_drawdown, self.max_floating_loss = 0, 0
        self.segments, self.switches, self.ticks, self.errors = [], [], [], []
        self.coverage_complete = True
        self.last_ms = start
        self.coin_risk = {}

    def mark(self, equity, floating):
        self.peak = max(self.peak, equity)
        self.max_drawdown = max(self.max_drawdown, (self.peak-equity)/self.peak)
        self.max_floating_loss = max(self.max_floating_loss, -floating,
                                     self.state.max_floating_loss if self.state else 0)
        if self.state:
            pair = self.state.config.pair
            realized = sum(s['net'] for s in self.segments if s['pair'] == pair)
            coin_equity = self.initial+realized+equity-self.cash-self.state.config.investment
            risk = self.coin_risk.setdefault(pair, dict(peak=self.initial,
                      max_drawdown=0, max_floating_loss=0))
            risk['peak'] = max(risk['peak'], coin_equity)
            risk['max_drawdown'] = max(risk['max_drawdown'],
                                      (risk['peak']-coin_equity)/risk['peak'])
            risk['max_floating_loss'] = max(risk['max_floating_loss'],
                                            self.state.max_floating_loss, -floating)

    def minute(self, at_ms):
        self.state, equities, floating = minute_paths(self.snapshot, self.state, at_ms)
        for eq, pnl in zip(equities, floating):
            self.mark(self.cash+eq, pnl)
        self.last_ms = at_ms

    def tick(self, at_ms):
        allocation = min(self.initial, self.cash + (net_equity(self.state, self.state.price)
                                                   if self.state else 0))
        if allocation <= 0:
            raise CoverageError('capital exhausted; no external top-up modeled')
        result = scan(self.snapshot, at_ms, investment=allocation,
                      grids=self.parameters.get('grids', 20))
        coverage = result['coverage']
        if coverage['unknown'] or not coverage['asof_symbols']:
            self.coverage_complete = False
        choices = result['eligible_candidates'] if self.rng else result['candidates']
        candidate = (self.rng.choice(choices) if self.rng and choices else
                     choices[0] if choices else None)
        tick = dict(at_ms=at_ms, rates=[{'pair': x['pair'],
                    'expected_grids_per_hour': x['expected_grids_per_hour'],
                    'rate_4h': x['rate_4h'], 'rate_24h': x['rate_24h']} for x in choices],
                    coverage=coverage)
        self.ticks.append(tick)
        if self.state:
            self.maybe_replace(candidate, at_ms, tick)
        if self.state is None and candidate:
            allocation = min(self.initial, self.cash)
            candidate = resize(candidate, allocation)
            self.state = new_bot(candidate, at_ms, allocation)
            if self.switches and self.switches[-1]['at_ms'] == at_ms:
                self.switches[-1]['opening_fee'] = self.state.fees
                self.switches[-1]['opening_spread_loss'] = -floating_pnl(self.state, self.state.price)
            self.cash -= allocation
            self.started = at_ms
            self.mark(self.cash + net_equity(self.state, self.state.price),
                      floating_pnl(self.state, self.state.price))

    def maybe_replace(self, candidate, at_ms, tick):
        bid, ask = quote(self.snapshot, self.state.config.pair, at_ms)
        proposal = switch_proposal(self.state, candidate, at_ms, self.started,
                                   bid, ask, self.parameters['lookback_hours'],
                                   self.parameters['margin_gph'],
                                   self.parameters.get('payback_hours', 4))
        tick['replacement'] = proposal
        if not proposal['replace']:
            return
        old = self.state
        closed = stop(old, (bid+ask)/2, at_ms, bid, ask, proposal['reason'])
        self.state = closed
        self.mark(self.cash + net_equity(closed, closed.price), 0)
        self.cash += net_equity(closed, closed.price)
        self.segments.append(_metrics(closed, at_ms-self.started))
        self.switches.append(dict(proposal, at_ms=at_ms,
            floating_pnl_realized=closed.close_pnl-old.close_pnl,
            close_fee=closed.fees-old.fees))
        self.state = None
        self.mark(self.cash, 0)

    def result(self):
        segments = self.segments + ([_metrics(self.state, self.last_ms-self.started)]
                                    if self.state else [])
        totals = _totals(segments, self.end-self.start)
        equity = self.cash + (net_equity(self.state, self.state.price) if self.state else 0)
        totals.update(start_ms=self.start, end_ms=self.end, net=equity-self.initial,
                      cash_unallocated=self.cash,
                      max_drawdown=self.max_drawdown,
                      max_floating_loss=self.max_floating_loss,
                      switches_per_day=len(self.switches)*86400000/(self.end-self.start),
                      coverage_complete=self.coverage_complete and not self.errors,
                      errors=self.errors, parameters=self.parameters,
                      switches=self.switches, ticks=self.ticks, per_coin=_per_coin(segments))
        return self.reserve_and_coin_risk(totals, segments)

    def reserve_and_coin_risk(self, totals, segments):
        reserve = sum(p.quantity*self.state.price*FEE for p in self.state.positions) if self.state else 0
        spread_reserve = 0
        totals['hypothetical_end_liquidation_net'] = None
        if self.state and self.state.positions and not self.errors:
            try:
                bid, ask = quote(self.snapshot, self.state.config.pair, self.last_ms)
                midpoint = (bid+ask)/2
                hypothetical = stop(self.state, midpoint, self.last_ms, bid, ask,
                                    'report_valuation_only')
                reserve = hypothetical.fees-self.state.fees
                close_equity = net_equity(hypothetical, midpoint)
                spread_reserve = max(0, net_equity(self.state, self.state.price)
                                     -close_equity-reserve)
                totals['hypothetical_end_liquidation_net'] = self.cash+close_equity-self.initial
            except CoverageError as error:
                totals['coverage_complete'] = False
                totals['errors'].append({'at_ms': self.last_ms, 'reason': str(error)})
        totals['end_close_fee_reserve'] = reserve
        totals['end_close_spread_reserve'] = spread_reserve
        totals['net_after_end_close_reserve'] = totals['net']-reserve-spread_reserve
        for pair, metrics in totals['per_coin'].items():
            metrics.update({k: v for k, v in self.coin_risk.get(pair, {}).items() if k != 'peak'})
            held = sum(s['held_ms'] for s in segments if s['pair'] == pair)
            switches = sum(s['current_pair'] == pair for s in self.switches)
            metrics['switches_per_day'] = switches*86400000/held if held else 0
        return totals


def _totals(segments, duration):
    keys = ('completed_grids', 'grid_profit', 'grid_net_profit', 'seed_pnl',
            'floating_pnl_realized', 'funding', 'fees', 'floating_pnl', 'net',
            'liquidations', 'range_exits', 'initial_investment')
    result = {key: sum(s[key] for s in segments) for key in keys}
    result['completed_grids_per_hour'] = result['completed_grids']*HOUR/duration if duration else 0
    result['completed_grids_per_day'] = result['completed_grids_per_hour']*24
    return result


def _per_coin(segments):
    return {pair: _totals([s for s in segments if s['pair'] == pair],
                          sum(s['held_ms'] for s in segments if s['pair'] == pair))
            for pair in sorted({s['pair'] for s in segments})}


def run_window(snapshot, start_ms, end_ms, parameters, seed=None):
    if end_ms <= start_ms or start_ms % MINUTE or end_ms % MINUTE:
        raise ValueError('window must contain aligned complete minutes')
    run = Run(snapshot, start_ms, end_ms, parameters, seed)
    for at_ms in range(start_ms, end_ms+1, MINUTE):
        try:
            if run.state and at_ms > start_ms:
                run.minute(at_ms)
                if run.state.liquidated:
                    run.errors.append({'at_ms': at_ms, 'reason': 'forced_liquidation'})
                    break
            if at_ms < end_ms and (not (at_ms-start_ms) % parameters.get('tick_ms', 300000)
                                  or run.state and run.state.status == 'out_of_range'):
                run.tick(at_ms)
            run.last_ms = at_ms
        except CoverageError as error:
            run.errors.append({'at_ms': at_ms, 'reason': str(error)})
            break
    return run.result()
