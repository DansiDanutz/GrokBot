"""Point-in-time candidate filters and a crossing proxy, never claimed as fills."""
import math
from trader.strategies.grid_features import features
from trader.strategies.grid_setup import build_setup, minimum_grid_net_usdt
from trader.strategies.recommender_setup import build_recommender_setup
from trader.strategies.candle_coverage import prepare, is_prepared_history

HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS
MINUTE_MS = 60_000


def _number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def normalize_market(raw, asof_ms):
    """KuCoin sizes are contracts; turnoverOf24h is quote value, volumeOf24h is not."""
    result = dict(raw)
    aliases = {'quote_currency': 'quoteCurrency', 'quote_turnover_24h': 'turnoverOf24h',
               'bid': 'bestBidPrice', 'ask': 'bestAskPrice', 'listed_at_ms': 'firstOpenDate',
               'tick_size': 'tickSize', 'lot_size': 'lotSize', 'funding_rate': 'fundingFeeRate'}
    for key, alias in aliases.items():
        result[key] = raw.get(key, raw.get(alias))
    if 'active' not in result:
        result['active'] = raw.get('status') == 'Open' if 'status' in raw else None
    if 'perpetual' not in result:
        result['perpetual'] = raw.get('isPerpetual')
    asset = raw.get('asset_class', raw.get('assetClass'))
    result['asset_class'] = asset.lower() if isinstance(asset, str) else None
    interval = _number(raw.get('funding_interval_hours'))
    if interval is None and raw.get('fundingRateGranularity') is not None:
        granularity = _number(raw['fundingRateGranularity'])
        interval = granularity / HOUR_MS if granularity is not None else None
    rate = _number(result['funding_rate'])
    result['funding_rate_8h'] = rate * 8 / interval if rate is not None and interval and interval > 0 else None
    for side in ('bid', 'ask'):
        depth = _number(raw.get(side + '_depth_usdt'))
        size = _number(raw.get('best' + side.title() + 'Size'))
        price, multiplier = _number(result[side]), _number(raw.get('multiplier'))
        if depth is None and size is not None and price is not None and multiplier is not None:
            depth = size * price * multiplier
        result[side + '_depth_usdt'] = depth
    result['asof_ms'] = asof_ms
    return result


def completed_bars(bars, asof_ms, hours=168):
    """Canonical timestamps identify minute opens; exclude still-open/future bars."""
    start = asof_ms - hours * HOUR_MS
    result = []
    for row in bars:
        timestamp = row.get('timestamp_ms', row.get('time_ms'))
        if timestamp is None:
            raise ValueError('candle timestamp_ms or time_ms required')
        if start <= timestamp and timestamp + MINUTE_MS <= asof_ms:
            result.append(dict(row, timestamp_ms=timestamp))
    result.sort(key=lambda row: row['timestamp_ms'])
    if len({row['timestamp_ms'] for row in result}) != len(result):
        raise ValueError('duplicate minute candles')
    return result


def _turnover_from_bars(market, bars, asof_ms):
    observed = [row for row in completed_bars(bars, asof_ms, 24) if not row.get('synthetic')]
    declared = market.get('candle_volume_unit')
    if market.get('candle_turnover_unit') == 'quote_usdt':
        values = [_number(row.get('turnover')) for row in observed]
        basis = 'observed candle turnover in quote USDT; missing minutes not extrapolated'
    else:
        scale = 1 if declared in ('base', 'quote_usdt') else _number(market.get('multiplier'))
        if declared not in ('base', 'quote_usdt', 'contracts') or scale is None or scale <= 0:
            return None, 'candle volume units or contract multiplier unknown'
        values = []
        for row in observed:
            volume, close = _number(row.get('volume')), _number(row.get('close'))
            values.append(None if volume is None or close is None else
                          volume*scale*(1 if declared == 'quote_usdt' else close))
        basis = ('observed '+declared+' candle volume converted to USDT'
                 + (' using close-price estimate' if declared != 'quote_usdt' else '')
                 + '; missing minutes not extrapolated')
    if not values or any(value is None or value < 0 for value in values):
        return None, 'candle quote turnover observations incomplete or invalid'
    return sum(values), basis


def _market_context(record, asof_ms, prepared):
    market = normalize_market(record['market'], asof_ms)
    if market.get('filter_mode') != 'candle-only filters':
        market['filter_mode'] = 'quote/book filters'
        return market
    for field in ('bid', 'ask', 'bid_depth_usdt', 'ask_depth_usdt',
                  'funding_rate', 'funding_rate_8h', 'observed_at_ms', 'price'):
        market[field] = None
    for field in ('bestBidPrice', 'bestAskPrice', 'bestBidSize', 'bestAskSize',
                  'fundingFeeRate', 'markPrice', 'indexPrice', 'lastTradePrice'):
        market.pop(field, None)
    turnover = _number(market.get('quote_turnover_24h'))
    if turnover is None or not market.get('turnover_basis'):
        turnover, basis = _turnover_from_bars(market, _observed_prepared(prepared), asof_ms)
        market.update(quote_turnover_24h=turnover, turnover_basis=basis)
    if market.get('membership_basis') == 'observed_candles':
        observed = [row['timestamp_ms'] for row in _observed_prepared(prepared)]
        seed = record.get('prior_seed')
        if seed and not seed.get('synthetic'):
            observed.append(seed.get('timestamp_ms', seed.get('time_ms')))
        inferred = min((time for time in observed if time is not None), default=None)
        existing = _number(market.get('listed_at_ms'))
        market['listed_at_ms'] = min(inferred, existing) if inferred is not None and existing is not None else inferred
        market['active'] = True if observed else None
        market['membership_disclosure'] = 'active membership and minimum listing age inferred from historical candles; not verified exchange membership history'
    return market


def _identity_reason(market):
    for field in ('active', 'perpetual'):
        if market.get(field) is not True:
            return field+' must be known and true'
    if market.get('quote_currency') != 'USDT':
        return 'requires USDT quote currency'
    if market.get('asset_class') != 'crypto':
        return 'requires known crypto asset class; stocks and forex are excluded'
    return None


def _market_reason(market, asof_ms, options, *, funding_check=True):
    candle_only = market.get('filter_mode') == 'candle-only filters'
    observed, age = market.get('observed_at_ms'), options.get('market_max_age_ms', HOUR_MS)
    if not candle_only and (observed is None or not 0 <= asof_ms-observed <= age):
        return 'unknown, stale or future market observation'
    identity = _identity_reason(market)
    if identity:
        return identity
    turnover = _number(market.get('quote_turnover_24h'))
    if turnover is None or turnover < 500_000:
        return 'quote turnover unknown or below 500000 USDT; base volume is not turnover'
    if not candle_only:
        bid, ask = _number(market.get('bid')), _number(market.get('ask'))
        if bid is None or ask is None or bid <= 0 or ask < bid or (ask-bid)/((bid+ask)/2) > .001:
            return 'spread unknown, invalid or above 0.1 percent'
    listed = _number(market.get('listed_at_ms'))
    if listed is None or asof_ms-listed < 7*DAY_MS:
        return 'listing age unknown or below seven days'
    if not candle_only and funding_check:
        funding = market.get('funding_rate_8h')
        if funding is None or abs(funding) > .001:
            return 'normalized eight-hour funding unknown or outside 0.1 percent'
    return None


def _crossings(bars, step, low, high):
    anchor, previous_time, crossings = None, None, 0
    for row in bars:
        timestamp = row['timestamp_ms']
        if row.get('synthetic') or row.get('indicator_only'):
            anchor, previous_time = None, None
            continue
        price = min(high, max(low, float(row['close'])))
        if anchor is None or timestamp-previous_time != MINUTE_MS or row.get('crossing_eligible') is False:
            anchor, previous_time = price, timestamp
            continue
        count = int((abs(price-anchor)+step*1e-10)/step)
        if count:
            anchor += math.copysign(count*step, price-anchor)
            crossings += count
        previous_time = timestamp
    return crossings


def crossing_score(bars, setup, asof_ms):
    """Close-only hysteresis ignores sub-step jitter; monotonic moves are not cycles."""
    step = setup.get('interval', (setup['high'] - setup['low']) / setup['grids'])
    if not math.isfinite(step) or step <= 0:
        raise ValueError('positive finite grid step required')
    rates = {hours: _crossings(completed_bars(bars, asof_ms, hours), step,
                              setup['low'], setup['high']) / hours
             for hours in (4, 24)}
    return dict(score=min(rates.values()), rate_4h=rates[4], rate_24h=rates[24],
                step=step, kind='hysteretic_step_crossing_proxy',
                limitation='close-only step crossings are not executed fills or completed grids')


def _actual_cash_net(form):
    value = form.get('preview',{}).get('profit_per_grid_min')
    return None if isinstance(value,bool) else _number(value)


def _candidate_context(market, prepared, form, score, minimum=0.):
    candle_only = market['filter_mode'] == 'candle-only filters'
    floor = _actual_cash_net(form)
    if floor is None or floor <= 0 or floor < minimum:
        return None
    return dict(filter_mode=market['filter_mode'],
                skipped_filters=['spread', 'depth', 'funding_window'] if candle_only else [],
                membership_basis=market.get('membership_basis', 'historical_contract_metadata'),
                membership_disclosure=market.get('membership_disclosure'),
                metadata_basis=market.get('metadata_basis', 'supplied market metadata'),
                metadata_retrospective=candle_only,
                metadata_disclosure=('retrospective static contract metadata; survivorship uncertainty'
                                     if candle_only else 'historical quote/book observations'),
                quote_turnover_24h=_number(market['quote_turnover_24h']),
                turnover_basis=market.get('turnover_basis', 'observed quote turnover'),
                candle_coverage=prepared['coverage'],
                actual_net_usdt_per_grid=floor,minimum_grid_net_usdt=minimum,expected_gph=score['score'],
                grid_income_per_hour=score['score']*floor, quantity_calibrated=False,
                income_basis='expected crossing GPH times minimum modeled net per grid; existing quantity remains uncalibrated')


def _evaluate(record, asof_ms, options, prepared, market):
    try:
        minimum = minimum_grid_net_usdt(options.get('setup'))
    except ValueError as exc:
        return None,str(exc)
    reason = _market_reason(market, asof_ms, options)
    if reason:
        return None, reason
    if not prepared['valid']:
        return None, prepared['reason']
    bars = prepared['bars']
    market['price'] = bars[-1]['close']
    candle_only = market['filter_mode'] == 'candle-only filters'
    funding = None if candle_only else market['funding_rate_8h']
    form = build_setup(record['pair'], features(prepared, funding), market, options.get('setup'))
    if not form.get('eligible'):
        return None, form.get('reason', 'setup rejected')
    notional = form.get('per_grid_notional', form['quantity']*form['entry'])
    depths = [market.get(side+'_depth_usdt') for side in ('bid', 'ask')]
    if not candle_only and any(depth is None or depth < notional for depth in depths):
        return None, 'top-of-book depth unknown or below one grid notional on either side'
    score = crossing_score(bars, form, asof_ms)
    context = _candidate_context(market, prepared, form, score, minimum)
    if context is None:
        unknown = _actual_cash_net(form) is None
        failure = ('unknown' if unknown else 'not strictly positive after both fill fees' if minimum == 0
                   else f'below configured {minimum:g} USDT cash floor or nonpositive')
        return None,'actual modeled net profit per grid '+failure
    reason = form['reason'] + ('; candle-only filters' if candle_only else '')
    return dict(pair=record['pair'], asof_ms=asof_ms, **score, **context, setup=form,
                direction=form['direction'], reason=reason,
                coverage='complete' if prepared['coverage']['fraction'] == 1 else 'partial'), None


def _chart_funding(record, asof_ms, candle_only):
    terms = record.get('funding_terms')
    if terms is None:
        return None, None
    if not isinstance(terms, dict):
        return None, 'chart funding terms must be a rate/interval mapping or unknown'
    rate, interval = terms.get('rate'), terms.get('interval_ms')
    observed = terms.get('observed_at_ms')
    if (any(type(value) not in (int,float) or not math.isfinite(value) for value in (rate,interval))
            or interval <= 0 or observed is not None and
            (type(observed) not in (int,float) or not math.isfinite(observed) or not 0 <= observed <= asof_ms)):
        return None, 'chart funding terms have invalid rate, interval or causal observation time'
    normalized = abs(rate)*8*HOUR_MS/interval
    if not candle_only and normalized > .0005:
        return terms, 'normalized eight-hour funding outside 0.05 percent'
    return terms, None


def _chart_view(form):
    chart = form.get('chart',{})
    cells = chart.get('five_cells',{})
    return dict(setup=form,five_cells=cells,top_grid_counts=form.get('candidates',[]),
        chart_reasons=dict(bias=chart.get('bias_reason'),entry=form.get('entry_signal',{}).get('reason'),
                           gate=form.get('gate_reason'),setup=form.get('reason')),
        timeframe_coverage={tf:{key:cell.get(key) for key in
            ('available','fresh','last_closed_ms','indicator_observed_fraction','estimated_buckets')}
            for tf,cell in cells.items()},
        offered=form.get('offered') is True,entry_eligible=form.get('entry_eligible') is True,
        can_arm=form.get('can_arm') is True,funded_entry_eligible=form.get('funded_entry_eligible') is True,
        funded_economics_eligible=form.get('funded_economics_eligible') is True,
        provisional=form.get('provisional',True),quantity_calibrated=False,liquidation_estimated=True,
        **{key:form.get(key) for key in ('net_usdt_per_grid','funding_adjusted_net_usdt_per_grid',
            'estimated_fee_only_net_usdt_per_grid','net_usdt_per_grid_basis','kucoin_profit_pct_min',
            'kucoin_profit_pct_max','kucoin_percent_basis')})


def _evaluate_chart(record, asof_ms, options, prepared, market):
    reason = _market_reason(market,asof_ms,options,funding_check=False)
    if reason:
        return None,reason
    if not prepared['valid']:
        return None,prepared['reason']
    candle_only = market['filter_mode']=='candle-only filters'
    funding,reason = _chart_funding(record,asof_ms,candle_only)
    if reason:
        return None,reason
    bars = prepared['bars']
    market = dict(market)
    if candle_only:
        market['price'] = _observed_prepared(prepared)[-1]['close']
    elif market.get('price') is None:
        market['price'] = (_number(market['bid'])+_number(market['ask']))/2
    form = build_recommender_setup(record['pair'],bars,market,record.get('frames',{}),asof_ms,
        options.get('setup'),regime=record.get('regime'),funding=funding)
    view = _chart_view(form)
    if not form.get('offered'):
        return view,form.get('reason','chart setup unavailable')
    notional = form['quantity']*form['entry']
    if not candle_only and any(_number(market.get(side+'_depth_usdt')) is None or
            _number(market[side+'_depth_usdt']) < notional for side in ('bid','ask')):
        return view,'top-of-book depth unknown or below one grid notional on either side'
    income,rate = _number(form.get('grid_income_per_hour')),_number(form.get('expected_gph'))
    floor = _actual_cash_net(form)
    if income is None or rate is None or income < 0 or rate < 0 or floor is None or floor <= 0:
        return view,'actual modeled chart income, completed rate or positive cash net is unknown or invalid'
    skipped = ['spread','depth','funding_window'] if candle_only else ['funding_window'] if funding is None else []
    return dict(view,pair=record['pair'],asof_ms=asof_ms,strategy='income_chart_v3',
        direction=form['direction'],reason=form['reason'],kind='paired_completed_grid_income_estimate',
        score=rate,expected_gph=rate,grid_income_per_hour=income,actual_net_usdt_per_grid=floor,
        funding_adjusted_income_per_hour=form.get('funding_adjusted_income_per_hour'),
        fee_net_income_per_hour=form.get('fee_net_income_per_hour'),ranking_basis=form.get('ranking_basis'),
        income_basis='minimum of 24h and 7d paired completed-cycle income estimates; funding/quantity assumptions remain explicit',
        funding_terms=funding,funding_diagnostics=record.get('funding_diagnostics'),regime=record.get('regime'),
        funding_filter_status='skipped_candle_only' if candle_only else 'unknown_skipped_provisional' if funding is None else 'passed_known_0.05_percent_8h_limit',
        filter_mode=market['filter_mode'],skipped_filters=skipped,candle_coverage=prepared['coverage'],
        quote_turnover_24h=_number(market.get('quote_turnover_24h')),turnover_basis=market.get('turnover_basis','observed quote turnover'),
        membership_basis=market.get('membership_basis','supplied market metadata'),
        membership_disclosure=market.get('membership_disclosure'),
        coverage='complete' if prepared['coverage']['fraction']==1 else 'partial'),None


def _rejection_coverage(record, asof_ms, reason, market):
    """Known filter failures do not mean data is missing for the strategy."""
    if reason.startswith('quote turnover'):
        return _number(market.get('quote_turnover_24h')) is None
    if reason.startswith('spread'):
        bid, ask = _number(market.get('bid')), _number(market.get('ask'))
        return bid is None or ask is None or bid <= 0 or ask < bid
    if reason.startswith('listing age'):
        return _number(market.get('listed_at_ms')) is None
    if reason.startswith('normalized eight-hour'):
        return market.get('funding_rate_8h') is None
    if reason.startswith('top-of-book depth'):
        return any(market.get(side+'_depth_usdt') is None for side in ('bid', 'ask'))
    if reason.startswith(('active', 'perpetual')):
        return market.get(reason.split()[0]) is None
    if reason.startswith('requires known crypto'):
        return market.get('asset_class') is None
    if reason.startswith('requires USDT'):
        return market.get('quote_currency') is None
    if reason.startswith('actual modeled net profit'):
        return reason.endswith('unknown')
    return reason.startswith(('fresh one-hour', 'one-hour indicator minute coverage',
                              'chart funding terms', 'actual modeled chart income', 'unknown, stale', 'requires seven days',
                              'seven-day candle', 'missing one-minute'))


def _observed_prepared(prepared):
    if is_prepared_history(prepared):
        return prepared.observed_bars
    return [row for row in prepared['bars'] if not row.get('synthetic')]


def _prepared_record(record, asof_ms):
    candidate = record.get('prepared')
    end = asof_ms//MINUTE_MS*MINUTE_MS
    if is_prepared_history(candidate):
        coverage = candidate['coverage']
        if (coverage.get('expected') == 10080 and coverage.get('window_end_ms') == end
                and coverage.get('window_start_ms') == end-7*DAY_MS):
            return candidate
    return prepare(record['bars'], asof_ms, prior_seed=record.get('prior_seed'))


def _volatility_context(record, asof_ms):
    prepared = record.get('prepared')
    if is_prepared_history(prepared):
        bars = [row for row in prepared.observed_bars if row['timestamp_ms'] >= asof_ms-DAY_MS]
    else:
        bars = [row for row in completed_bars(record['bars'], asof_ms, 24) if not row.get('synthetic')]
    value = None
    if len(bars) == 1440:
        high, low = max(row['high'] for row in bars), min(row['low'] for row in bars)
        value = volatility_proxy(high, low)
    return dict(pair=record['pair'], volatility_proxy_pct=value,
                volatility_definition_verified=False,
                volatility_proxy_formula='100*(24h_high-24h_low)/24h_low')


def radar(records, asof_ms, running_pairs=(), parameters=None):
    """Return 5..10 non-running research candidates ranked by modeled grid income."""
    options, selected, rejected = parameters or {}, [], []
    count, universe = options.get('radar_size', 10), []
    if type(count) is not int or not 5 <= count <= 10:
        raise ValueError('radar_size must be an integer from 5 through 10')
    running, seen = set(running_pairs), set()
    for record in records:
        pair = record['pair']
        if pair in seen:
            raise ValueError('duplicate market pair')
        seen.add(pair)
        prepared = _prepared_record(record, asof_ms)
        market = _market_context(record, asof_ms, prepared)
        context = dict(_volatility_context(dict(record, bars=_observed_prepared(prepared), prepared=prepared), asof_ms),
                       candle_coverage=prepared['coverage'])
        universe.append(context)
        if pair in running:
            rejected.append(dict(pair=pair, reason='already running', coverage_issue=False,
                                 candle_coverage=prepared['coverage'], filter_mode=market['filter_mode']))
            continue
        evaluate = _evaluate_chart if options.get('strategy')=='income_chart_v3' else _evaluate
        candidate, reason = evaluate(record, asof_ms, options, prepared, market)
        if reason:
            rejected.append(dict(pair=pair, reason=reason,
                                 coverage_issue=reason == prepared.get('reason') or _rejection_coverage(record, asof_ms, reason, market),
                                 candle_coverage=prepared['coverage'], filter_mode=market['filter_mode'],
                                 **(candidate or {})))
        else:
            selected.append(dict(candidate, **{key: value for key, value in context.items() if key != 'pair'}))
    selected.sort(key=lambda row: (-row['grid_income_per_hour'], -row['score'], row['pair']))
    universe.sort(key=lambda row: (row['volatility_proxy_pct'] is None,
                                  -(row['volatility_proxy_pct'] or 0), row['pair']))
    return dict(asof_ms=asof_ms, radar=selected[:count], rejected=rejected, volatility_universe=universe,
                volatility_basis='independent 24h amplitude context; KuCoin UI definition remains unverified',
                coverage=dict(observed=len(seen), eligible=len(selected), rejected=len(rejected)),
                **({'strategy':'income_chart_v3'} if options.get('strategy')=='income_chart_v3' else {}))


def volatility_proxy(high_24h, low_24h):
    """Candidate definition only: 100*(high24-low24)/low24; KuCoin unverified."""
    high, low = _number(high_24h), _number(low_24h)
    if high is None or low is None or low <= 0 or high < low:
        raise ValueError('valid positive time-aligned daily high and low required')
    return (high-low) / low * 100


def compare_volatility(fixture_rows, aligned_daily_ranges, tolerance_pp=1):
    """Ranges must be independently observed at the fixture capture time."""
    rows = []
    for expected in fixture_rows:
        data = aligned_daily_ranges.get(expected['symbol'])
        actual = None
        if data and data.get('capture_aligned') is True:
            actual = volatility_proxy(data['high_24h'], data['low_24h'])
        difference = None if actual is None else abs(actual-expected['volatility_pct'])
        rows.append(dict(pair=expected['symbol'], expected=expected['volatility_pct'],
                         measured=actual, difference_pp=difference,
                         status='unknown' if actual is None else
                         ('within_tolerance' if difference <= tolerance_pp else 'mismatch')))
    verified = sum(row['status'] == 'within_tolerance' for row in rows)
    return dict(formula='100 * (high24 - low24) / low24 (unverified candidate)',
                rows=rows, verified=verified,
                unknown=sum(row['status'] == 'unknown' for row in rows),
                validated=bool(rows) and verified == len(rows), tolerance_pp=tolerance_pp)
