"""Bounded, close-path grid-count estimates, not exchange fills or calibration.

A gap or observed boundary touch censors open pairs. A later inside observation
starts a new hypothetical episode; this never restarts an actual stopped bot.
Only paired adjacent opens/closes count. Initial seeded inventory is excluded.
Funding assumes the supplied signed rate persists, prorated over the empirical
median completed-pair hold. It is an estimate, not a settlement prediction.
"""
from bisect import bisect_left, bisect_right
from collections.abc import Mapping
from decimal import Decimal, ROUND_FLOOR
import math
from statistics import median

MINUTE_MS = 60_000
HOUR_MS = 60 * MINUTE_MS
WINDOW_MS = 168 * HOUR_MS
FEE = Decimal('0.0006')


def _decimal(value, name, *, positive=False, nonnegative=False):
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError(name+' must be a finite number')
    result = Decimal(str(value))
    if not result.is_finite() or (positive and result <= 0) or (nonnegative and result < 0):
        raise ValueError(name+' has an invalid value')
    return result


def _funding(value, asof_ms=None):
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError('funding must be a mapping or None')
    rate = _decimal(value.get('rate'), 'funding rate')
    interval = _decimal(value.get('interval_ms'), 'funding interval', positive=True)
    observed = value.get('observed_at_ms')
    if observed is not None:
        _decimal(observed, 'funding observation time', nonnegative=True)
        if asof_ms is not None and observed > asof_ms:
            raise ValueError('future funding observation is not allowed')
    return rate, interval


def cycle_economics(quantity, buy_price, sell_price, side, *, median_hold_ms=None,
                    funding=None, fee_safety_ratio=.2):
    """Actual base quantity cash arithmetic, with conservative credit admission.

    Positive funding is a cost for Long and a credit for Short. Use the upper
    adjacent price as a disclosed notional assumption; no Neutral cancellation.
    Unknown funding/hold keeps adjusted cash null and the offer provisional.
    """
    q = _decimal(quantity, 'quantity', positive=True)
    buy = _decimal(buy_price, 'buy price', positive=True)
    sell = _decimal(sell_price, 'sell price', positive=True)
    buffer = _decimal(fee_safety_ratio, 'fee safety ratio', nonnegative=True)
    if side not in ('long', 'short') or sell <= buy:
        raise ValueError('long/short side and ascending adjacent prices required')
    terms = _funding(funding)
    hold = None if median_hold_ms is None else _decimal(median_hold_ms, 'median hold', positive=True)
    gross, fees = q*(sell-buy), q*(buy+sell)*FEE
    fee_net, requirement = gross-fees, fees*buffer
    known = terms is not None and hold is not None
    cost = q*sell*terms[0]*hold/terms[1]*(1 if side == 'long' else -1) if known else None
    admission = fee_net-max(Decimal(0), cost) if known else None
    tested = fee_net if admission is None else admission
    eligible = tested > 0 and tested >= requirement
    return dict(side=side, quantity=float(q), gross_profit_usdt=float(gross),
        two_fill_fees_usdt=float(fees), fee_net_usdt=float(fee_net),
        fee_safety_ratio=float(buffer), required_fee_buffer_usdt=float(requirement),
        expected_funding_cost_usdt=None if cost is None else float(cost),
        expected_funding_credit_usdt=None if cost is None else float(max(Decimal(0), -cost)),
        funding_adjusted_net_usdt=None if cost is None else float(fee_net-cost),
        admission_net_usdt=None if admission is None else float(admission),
        eligible=eligible, funded_economics_eligible=eligible and known,
        provisional=not known, funding_status='known_rate_estimate' if terms else 'unknown',
        hold_status='observed_completed_pairs' if hold is not None else 'unknown',
        median_hold_ms=None if hold is None else float(hold),
        funding_basis='upper adjacent price notional; signed rate prorated over median hold; credits do not rescue admission')


def _history(bars, asof_ms):
    if type(asof_ms) is not int or asof_ms < 0 or asof_ms % MINUTE_MS:
        raise ValueError('asof_ms must be an aligned nonnegative integer minute')
    start, by_time = asof_ms-WINDOW_MS, {}
    for row in bars:
        if not isinstance(row, Mapping):
            raise ValueError('candles must be mappings')
        timestamp = row.get('timestamp_ms', row.get('time_ms'))
        if type(timestamp) is not int or timestamp % MINUTE_MS:
            raise ValueError('aligned integer candle timestamp required')
        if timestamp < start or timestamp >= asof_ms:
            continue
        if row.get('synthetic') or row.get('indicator_only'):
            continue
        prices = tuple(float(_decimal(row.get(key), key, positive=True)) for key in ('low','high','close'))
        low, high, close = prices
        if not low <= close <= high:
            raise ValueError('candle close must lie within low/high')
        canonical = (timestamp, low, high, close, row.get('crossing_eligible') is not False)
        if timestamp in by_time and by_time[timestamp] != canonical:
            raise ValueError('conflicting duplicate candle')
        by_time[timestamp] = canonical
    rows = sorted(by_time.values())
    actual = len(rows)
    return rows, dict(actual=actual, expected=10080, fraction=actual/10080,
                      eligible=actual*100 >= 95*10080)


def _window_accumulator():
    return {side:dict(holds=[], gross=0., fees=0., upper_notional=0.) for side in ('long','short')}


def _paired_cycles(rows, levels, direction, quantity, asof_ms):
    """One pass per count; keep hold samples and sums, never a fill ledger.

    Boundary extrema cancel the whole minute before inferring close-path fills.
    At a gap the current real observation becomes a fresh anchor, so no bridge.
    """
    sides = ('long','short') if direction == 'neutral' else (direction,)
    opened = {side:{} for side in sides}
    windows = {'24h':_window_accumulator(), '7d':_window_accumulator()}
    previous = None
    gap_censored = boundary_censored = 0
    for timestamp, low, high, price, crossing_eligible in rows:
        when = timestamp+MINUTE_MS
        boundary = low <= levels[0] or high >= levels[-1]
        gap = previous is not None and timestamp != previous[0]+MINUTE_MS
        if boundary or gap or not crossing_eligible:
            count = sum(len(value) for value in opened.values())
            if boundary:
                boundary_censored += count
            else:
                gap_censored += count
            for value in opened.values():
                value.clear()
            previous = None
        if boundary:
            continue
        if previous is not None and price != previous[1]:
            old = previous[1]
            upward = price > old
            indices = (range(bisect_right(levels,old),bisect_right(levels,price)) if upward else
                       range(bisect_left(levels,old)-1,bisect_left(levels,price)-1,-1))
            for index in indices:
                close_side = 'long' if upward else 'short'
                close_interval = index-1 if upward else index
                if close_side in opened and close_interval in opened[close_side]:
                    began = opened[close_side].pop(close_interval)
                    hold = when-began
                    if hold > 0:
                        buy, sell = levels[close_interval:close_interval+2]
                        for label, hours in (('24h',24),('7d',168)):
                            if when > asof_ms-hours*HOUR_MS:
                                sums = windows[label][close_side]
                                sums['holds'].append(hold)
                                sums['gross'] += quantity*(sell-buy)
                                sums['fees'] += quantity*(buy+sell)*float(FEE)
                                sums['upper_notional'] += quantity*sell
                open_side = 'short' if upward else 'long'
                open_interval = index-1 if upward else index
                if open_side in opened and 0 <= open_interval < len(levels)-1:
                    opened[open_side].setdefault(open_interval,when)
        previous = timestamp,price
    return windows, gap_censored, boundary_censored, sum(len(value) for value in opened.values())


def _window_result(sums, sides, hours, funding):
    holds = {side:median(sums[side]['holds']) if sums[side]['holds'] else None for side in sides}
    counts = {side:len(sums[side]['holds']) for side in sides}
    gross = math.fsum(sums[side]['gross'] for side in sides)
    fees = math.fsum(sums[side]['fees'] for side in sides)
    terms = _funding(funding)
    known = terms is not None and all(holds[side] is not None for side in sides)
    cost = (math.fsum(sums[side]['upper_notional']*float(terms[0])*holds[side]/float(terms[1])
                     *(1 if side == 'long' else -1) for side in sides) if known else None)
    return dict(completed_cycles=sum(counts.values()), completed_by_side=counts,
        completed_grids_per_hour=sum(counts.values())/hours, median_hold_ms=holds,
        gross_income_usdt=gross, two_fill_fees_usdt=fees, fee_net_income_usdt=gross-fees,
        fee_net_income_per_hour=(gross-fees)/hours, expected_funding_cost_usdt=cost,
        funding_adjusted_income_per_hour=None if cost is None else (gross-fees-cost)/hours,
        denominator_hours=hours)


def _count(value, name):
    if type(value) is not int or value < 2:
        raise ValueError(name+' must be an integer >= 2')
    return value


def search_grid_counts(bars, low, high, *, asof_ms, direction, quantity_for_count,
                       tick_size, funding=None, parameters=None):
    """Return top three modeled income/hour counts; caller owns range/risk policy.

    Q(N) supplies actual base quantity per order, already split for Neutral. It
    may instead return {quantity, eligible, reason} from the sizing/risk model.
    No allocation or liquidation calibration gate is added here. Economic
    approval is distinct from the caller's full funded-entry/risk approval.
    """
    options = parameters or {}
    lower = _decimal(low,'low',positive=True)
    upper = _decimal(high,'high',positive=True)
    tick = _decimal(tick_size,'tick size',positive=True)
    ratio = _decimal(options.get('fee_safety_ratio',.2),'fee safety ratio',nonnegative=True)
    if upper <= lower or direction not in ('long','short','neutral'):
        raise ValueError('ascending range and long/short/neutral direction required')
    if lower % tick or upper % tick:
        raise ValueError('range endpoints must be tick aligned')
    minimum = _count(options.get('min_grids',2),'min_grids')
    maximum = min(200,_count(options.get('max_grids',200),'max_grids'))
    if options.get('exchange_max_grids') is not None:
        maximum = min(maximum,_count(options['exchange_max_grids'],'exchange_max_grids'))
    if minimum > maximum:
        raise ValueError('minimum count exceeds effective maximum')
    _funding(funding,asof_ms)
    rows, coverage = _history(bars,asof_ms)
    result = dict(candidates=[], rejected_counts=[], evaluated_count=0, coverage=coverage,
        count_limits={'min':minimum,'max':maximum}, quantity_calibrated=False, liquidation_estimated=True,
        assumptions=['Observed close-path paired adjacent cycles, not exchange fills; initial seeded inventory excluded.',
                     'Gaps and observed boundary touches censor open cycles; later inside bars start hypothetical estimation episodes only.',
                     '24h completions may have opened earlier within the 7d window; unclosed holds are censored.',
                     'Funding is a constant-rate, median-hold estimate; unknown inputs remain null.',
                     'Fixed 24h/168h denominators include gaps and stopped intervals; no missing-time extrapolation.',
                     'Quantity and liquidation are estimated; calibration does not block provisional offers.'])
    if not coverage['eligible']:
        return result
    sides = ('long','short') if direction == 'neutral' else (direction,)
    accepted = []
    for count in range(minimum,maximum+1):
        result['evaluated_count'] += 1
        step = ((upper-lower)/count/tick).to_integral_value(rounding=ROUND_FLOOR)*tick
        end = lower+count*step
        # Necessary fee-only bound is independent of Q(N). Funding credits are
        # forbidden to rescue admission, so pruning here cannot discard a pass.
        gross_unit, fees_unit = step, FEE*(2*end-step)
        if step <= 0 or gross_unit-fees_unit <= 0 or gross_unit-fees_unit < ratio*fees_unit:
            result['rejected_counts'].append(dict(grids=count,reason='interval fails positive cash and fee buffer before funding'))
            continue
        supplied = quantity_for_count(count)
        if isinstance(supplied,Mapping):
            if supplied.get('eligible',True) is not True:
                result['rejected_counts'].append(dict(grids=count,reason=supplied.get('reason','sizing/risk rejected')))
                continue
            supplied = supplied.get('quantity')
        try:
            quantity = float(_decimal(supplied,'quantity',positive=True))
        except ValueError as error:
            result['rejected_counts'].append(dict(grids=count,reason=str(error)))
            continue
        levels = [float(lower+i*step) for i in range(count+1)]
        sums, gap_censored, boundary_censored, open_censored = _paired_cycles(rows,levels,direction,quantity,asof_ms)
        windows, economics = {}, {}
        for label,hours in (('24h',24),('7d',168)):
            windows[label] = _window_result(sums[label],sides,hours,funding)
            economics[label] = {side:cycle_economics(quantity,levels[-2],levels[-1],side,
                median_hold_ms=windows[label]['median_hold_ms'][side],funding=funding,fee_safety_ratio=ratio)
                for side in sides}
        values = [row for by_side in economics.values() for row in by_side.values()]
        if not all(row['eligible'] for row in values):
            result['rejected_counts'].append(dict(grids=count,reason='expected funding fails positive cash or fee buffer',economics=economics))
            continue
        known = all(row['funded_economics_eligible'] for row in values)
        adjusted = min(row['funding_adjusted_income_per_hour'] for row in windows.values()) if known else None
        fee_only = min(row['fee_net_income_per_hour'] for row in windows.values())
        accepted.append(dict(grids=count,low=levels[0],high=levels[-1],requested_high=float(upper),
            step=float(step),interval=float(step),quantity=quantity,windows=windows,economics=economics,
            funding_adjusted_income_per_hour=adjusted,fee_net_income_per_hour=fee_only,
            selection_income_per_hour=adjusted if known else fee_only,
            ranking_basis='funding_adjusted_estimate' if known else 'fee_only_provisional_estimate',
            eligible=True,funded_economics_eligible=known,provisional=not known,
            quantity_calibrated=False,liquidation_estimated=True,
            gap_censored_cycles=gap_censored,boundary_censored_cycles=boundary_censored,
            open_censored_cycles=open_censored))
    accepted.sort(key=lambda row:(-row['selection_income_per_hour'],-row['grids']))
    result['candidates'] = accepted[:3]
    return result
