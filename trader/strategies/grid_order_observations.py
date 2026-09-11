"""Observed lots test accounting; unfitted quantity hypotheses remain uncalibrated.

No observed quantity, profit or liquidation target enters a forward prediction.
Current margin may differ from original funding, so neither a RAY match nor a
profit-band match identifies KuCoin's allocation algorithm.
"""
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
import re

ROOT_KEYS = {'schema_version', 'source', 'fee_rate_per_fill', 'held_out_pair',
             'limitations', 'observations'}
SOURCE_KEYS = {'surface', 'views', 'captured_at_local', 'capture_time_precision',
               'contract_metadata_source', 'redacted'}
ROW_KEYS = {'pair', 'direction', 'leverage', 'grids', 'order_quantity_lots',
            'used_margin_current_usdt', 'reserved_margin_current_usdt',
            'total_margin_current_usdt', 'original_used_margin_usdt',
            'range_low', 'range_high', 'entry_price', 'contract_multiplier',
            'lot_size', 'price_tick', 'cycles', 'observation_scope'}
OPTIONAL_KEYS = {'observed_interval', 'open_orders', 'buy_orders', 'sell_orders',
                 'long_position_lots', 'short_position_lots'}
CYCLE_KEYS = {'buy_price', 'sell_price', 'quantity_lots', 'observed_net_usdt'}
RULES = ('entry_notional', 'high_notional', 'high_notional_with_fees')


def _decimal(value, name, positive=False, nonnegative=False):
    if type(value) not in (int, float, Decimal):
        raise ValueError(name+' must be numeric')
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError(name+' must be finite') from error
    if not result.is_finite() or positive and result <= 0 or nonnegative and result < 0:
        raise ValueError(name+' is outside its valid numeric range')
    return result


def _integer(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(name+' must be an integer at least '+str(minimum))
    return value


def _keys(value, required, optional=()):
    if not isinstance(value, dict) or not required <= value.keys() or value.keys()-required-set(optional):
        raise ValueError('missing or unknown observation fields; account and credential fields are forbidden')


def _aligned(value, tick, name):
    if value % tick:
        raise ValueError(name+' is not aligned to its observed tick or lot increment')


def _validate_cycles(row):
    if not isinstance(row['cycles'], list):
        raise ValueError('cycles must be a list')
    tick, lot = Decimal(str(row['price_tick'])), Decimal(str(row['lot_size']))
    for cycle in row['cycles']:
        _keys(cycle, CYCLE_KEYS)
        for key in ('buy_price', 'sell_price'):
            _aligned(_decimal(cycle[key], key, positive=True), tick, key)
        _aligned(_decimal(cycle['quantity_lots'], 'quantity_lots', positive=True), lot, 'quantity_lots')
        _decimal(cycle['observed_net_usdt'], 'observed_net_usdt')


def _validate_optional(row):
    if 'observed_interval' in row:
        step = _decimal(row['observed_interval'], 'observed_interval', positive=True)
        _aligned(step, Decimal(str(row['price_tick'])), 'observed_interval')
    counts = {'open_orders', 'buy_orders', 'sell_orders'}
    if counts.intersection(row):
        if not counts <= row.keys():
            raise ValueError('order counts must include total, buys and sells')
        for key in counts:
            _integer(row[key], key)
        if row['open_orders'] != row['buy_orders']+row['sell_orders']:
            raise ValueError('observed buy and sell order counts do not reconcile')
        expected = row['grids']*(2 if row['direction'] == 'neutral' else 1)
        if row['open_orders'] != expected:
            raise ValueError('observed open orders do not match configured interval count')
    for key in ('long_position_lots', 'short_position_lots'):
        if key in row:
            _aligned(_decimal(row[key], key, nonnegative=True),
                     Decimal(str(row['lot_size'])), key)


def _validate_row(row):
    _keys(row, ROW_KEYS, OPTIONAL_KEYS)
    if not isinstance(row['pair'], str) or not re.fullmatch(r'[A-Z0-9]{1,24}USDTM?', row['pair']):
        raise ValueError('invalid public contract symbol')
    if row['direction'] not in ('long', 'short', 'neutral'):
        raise ValueError('invalid observed direction')
    if row['observation_scope'] not in ('running_parameters', 'archived_parameters'):
        raise ValueError('observation scope must identify running or archived parameters')
    _integer(row['grids'], 'grids', 2)
    positive = ('leverage', 'order_quantity_lots', 'used_margin_current_usdt',
                'total_margin_current_usdt', 'range_low', 'range_high', 'entry_price',
                'contract_multiplier', 'lot_size', 'price_tick')
    values = {key: _decimal(row[key], key, positive=True) for key in positive}
    reserve = _decimal(row['reserved_margin_current_usdt'], 'reserve', nonnegative=True)
    if values['used_margin_current_usdt']+reserve != values['total_margin_current_usdt']:
        raise ValueError('current used margin plus reserve must equal current total')
    if row['original_used_margin_usdt'] is not None:
        _decimal(row['original_used_margin_usdt'], 'original margin', positive=True)
    if not values['range_low'] <= values['entry_price'] <= values['range_high'] or values['range_low'] == values['range_high']:
        raise ValueError('observed entry must be inside a nonempty range')
    for key in ('range_low', 'range_high', 'entry_price'):
        _aligned(values[key], values['price_tick'], key)
    _aligned(values['order_quantity_lots'], values['lot_size'], 'order_quantity_lots')
    _validate_optional(row)
    _validate_cycles(row)


def validate_observations(document):
    """Validate explicit units and exact schema without altering observations."""
    _keys(document, ROOT_KEYS)
    if type(document['schema_version']) is not int or document['schema_version'] != 1:
        raise ValueError('unsupported order-observation schema')
    _keys(document['source'], SOURCE_KEYS)
    if document['source']['redacted'] is not True:
        raise ValueError('only redacted observation fixtures are accepted')
    for key in SOURCE_KEYS-{'views', 'redacted'}:
        if not isinstance(document['source'][key], str) or not document['source'][key]:
            raise ValueError('source descriptions must be nonempty text')
    if not isinstance(document['source']['views'], list) or not all(isinstance(item, str) for item in document['source']['views']):
        raise ValueError('source views must be a list of descriptions')
    if not isinstance(document['limitations'], list) or not all(isinstance(item, str) for item in document['limitations']):
        raise ValueError('limitations must be a list of descriptions')
    rate = _decimal(document['fee_rate_per_fill'], 'fee rate', nonnegative=True)
    if rate >= 1:
        raise ValueError('fee rate must be below one')
    if not isinstance(document['observations'], list) or not document['observations']:
        raise ValueError('at least one observed parameter set is required')
    seen = set()
    for row in document['observations']:
        _validate_row(row)
        identity = tuple(row[key] for key in ('pair', 'direction', 'leverage', 'grids', 'range_low', 'range_high', 'entry_price'))
        if identity in seen:
            raise ValueError('duplicate observed parameter set')
        seen.add(identity)
    if sum(row['pair'] == document['held_out_pair'] for row in document['observations']) != 1:
        raise ValueError('exactly one retrospective held-out parameter set is required')
    return document


def net_cycle(quantity_lots, multiplier, buy_price, sell_price, fee_rate=.0006):
    """Signed round-trip PnL in USDT; both fills pay their actual-price fee."""
    lots = _decimal(quantity_lots, 'quantity_lots', positive=True)
    base = lots*_decimal(multiplier, 'multiplier', positive=True)
    buy = _decimal(buy_price, 'buy_price', positive=True)
    sell = _decimal(sell_price, 'sell_price', positive=True)
    rate = _decimal(fee_rate, 'fee_rate', nonnegative=True)
    if rate >= 1:
        raise ValueError('fee rate must be below one')
    gross, buy_fee, sell_fee = base*(sell-buy), base*buy*rate, base*sell*rate
    return dict(quantity_lots=float(lots), quantity_base=float(base),
                buy_price=float(buy), sell_price=float(sell), gross=float(gross),
                buy_fee=float(buy_fee), sell_fee=float(sell_fee),
                fees=float(buy_fee+sell_fee), net=float(gross-buy_fee-sell_fee))


def quantity_prediction(row, rule, fee_rate=.0006):
    """Fixed algebraic hypotheses access form inputs only, never observed targets."""
    if rule not in RULES or row['direction'] not in ('long', 'short', 'neutral'):
        raise ValueError('unknown quantity hypothesis or direction')
    slots = _integer(row['grids'], 'grids', 2)*(2 if row['direction'] == 'neutral' else 1)
    original = row.get('original_used_margin_usdt')
    margin = _decimal(row['used_margin_current_usdt'] if original is None else original,
                      'used margin', positive=True)
    leverage = _decimal(row['leverage'], 'leverage', positive=True)
    entry, high = [_decimal(row[key], key, positive=True) for key in ('entry_price', 'range_high')]
    multiplier = _decimal(row['contract_multiplier'], 'contract_multiplier', positive=True)
    unit = multiplier*_decimal(row['lot_size'], 'lot_size', positive=True)
    fee = _decimal(fee_rate, 'fee_rate', nonnegative=True)
    if fee >= 1:
        raise ValueError('fee rate must be below one')
    denominators = {'entry_notional': Decimal(slots)*entry/leverage,
                    'high_notional': Decimal(slots)*high/leverage,
                    'high_notional_with_fees': Decimal(slots)*high*(1/leverage+2*fee)}
    denominator = denominators[rule]
    raw = margin/denominator
    quantity = (raw/unit).to_integral_value(rounding=ROUND_FLOOR)*unit
    return dict(rule=rule, predicted_lots=float(quantity/multiplier),
                predicted_quantity_base=float(quantity), raw_quantity_base=float(raw),
                allocation_slots=slots, denominator_usdt_per_base=float(denominator),
                margin_basis='current_used_margin_as_original_assumption' if original is None else 'observed_original_used_margin',
                rounding='floor to supplied contract lot increment', uses_profit_targets=False)


def _rule_comparison(row, rule, fee_rate):
    predicted = quantity_prediction(row, rule, fee_rate)
    actual = row['order_quantity_lots']
    error = predicted['predicted_lots']-actual
    return dict(pair=row['pair'], observation_scope=row['observation_scope'],
                leverage=row['leverage'], grids=row['grids'], **predicted,
                observed_lots=actual, error_lots=error,
                relative_error=abs(error)/actual, quantity_match=error == 0)


def _blocking_reasons(original_known, rules):
    reasons = []
    if not original_known:
        reasons.append('original margin and margin-amendment history are not independently established')
    if not any(rule['all_long_quantities_match'] for rule in rules):
        reasons.append('No fixed hypothesis explains every observed Long order quantity.')
    reasons.extend(['A matching RAY quantity does not identify the exchange allocation algorithm.',
                    'The held-out comparison is retrospective; no independently validated allocation rule is adopted.'])
    return reasons


def quantity_rule_diagnostics(document):
    validate_observations(document)
    rules = []
    for rule in RULES:
        rows = [_rule_comparison(row, rule, document['fee_rate_per_fill']) for row in document['observations']]
        held = [row for row in rows if row['pair'] == document['held_out_pair']]
        longs = [value for row, value in zip(document['observations'], rows) if row['direction'] == 'long']
        rules.append(dict(name=rule, observations=rows,
            held_out_quantity_match=bool(held) and all(row['quantity_match'] for row in held),
            all_long_quantities_match=bool(longs) and all(row['quantity_match'] for row in longs)))
    original_known = all(row['original_used_margin_usdt'] is not None for row in document['observations'])
    return dict(calibrated=False, status='blocked', rules=rules, quantity_tolerance_lots=0,
        held_out_status='retrospective comparison, not a preregistered blind prediction',
        original_margin_known=original_known, blocked_reasons=_blocking_reasons(original_known, rules),
        allocation_rule_adopted=None)


def _band(row, fee_rate):
    low, high, tick = [Decimal(str(row[key])) for key in ('range_low', 'range_high', 'price_tick')]
    step = Decimal(str(row['observed_interval'])) if 'observed_interval' in row else (
        ((high-low)/row['grids']/tick).to_integral_value(rounding=ROUND_FLOOR)*tick)
    if step <= 0:
        raise ValueError('interval is narrower than one observed price tick')
    cycles = [net_cycle(row['order_quantity_lots'], row['contract_multiplier'],
                        low+index*step, low+(index+1)*step, fee_rate) for index in range(row['grids'])]
    return dict(pair=row['pair'], observation_scope=row['observation_scope'], grids=row['grids'],
                step=float(step), step_basis='observed order-history interval' if 'observed_interval' in row
                else 'inferred arithmetic interval rounded down to the observed tick',
                quantity_lots=row['order_quantity_lots'],
                minimum_net_usdt=min(cycle['net'] for cycle in cycles),
                maximum_net_usdt=max(cycle['net'] for cycle in cycles),
                accounting_only=True, allocation_calibrated=False)


def _reference_mean(row, prior_bots):
    for prior in prior_bots:
        matches = (prior['symbol'] == row['pair'] and prior['mode'] == row['direction']
            and prior['leverage'] == row['leverage'] and prior['entry_price'] == row['entry_price']
            and prior['range_low'] == row['range_low'] and prior['range_high'] == row['range_high']
            and prior['grids_buy']+prior['grids_sell'] == row['grids'])
        if matches:
            count = _integer(prior['arbitrage_total'], 'prior arbitrage_total', 1)
            return float(_decimal(prior['grid_profit'], 'prior grid_profit')/count)
    return None


def accounting_band_checks(document, prior_running_bots=()):
    """Compare targets only after an observed-quantity accounting band is fixed."""
    validate_observations(document)
    references, results = list(prior_running_bots), []
    for row in document['observations']:
        band = _band(row, document['fee_rate_per_fill'])
        mean = _reference_mean(row, references)
        results.append(dict(band, reference_mean_net_usdt=mean,
            reference_mean_inside_band=None if mean is None else
            band['minimum_net_usdt'] <= mean <= band['maximum_net_usdt']))
    return results


def observation_report(document, prior_running_bots=()):
    validate_observations(document)
    checks, tolerance = [], .00005
    for row in document['observations']:
        for cycle in row['cycles']:
            computed = net_cycle(cycle['quantity_lots'], row['contract_multiplier'],
                                  cycle['buy_price'], cycle['sell_price'], document['fee_rate_per_fill'])
            error = abs(computed['net']-cycle['observed_net_usdt'])
            checks.append(dict(pair=row['pair'], computed=computed,
                               observed_net_usdt=cycle['observed_net_usdt'], absolute_error_usdt=error,
                               within_display_tolerance=error <= tolerance))
    return dict(calibrated=False, status='blocked', cycles=checks,
                cycle_display_tolerance_usdt=tolerance,
                cycle_accounting_matches=bool(checks) and all(row['within_display_tolerance'] for row in checks),
                quantity_rule_diagnostics=quantity_rule_diagnostics(document),
                accounting_bands=accounting_band_checks(document, prior_running_bots),
                limitations=list(document['limitations']))
