"""Pure compact research storage; never feed these views to execution or search.

Chosen form fields and scalar values are copied, never recalculated. Full trade
ledgers and execution scan caches are outside this module. Regime raw reads are
losslessly interned within each stored scan; all other gate evidence remains.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import json


SEARCH_LISTS = {'candidates', 'rejected_counts', 'trials', 'full_trials'}
TRADEOFF_VALUES = {
    'grids', 'low', 'high', 'requested_high', 'step', 'interval', 'quantity',
    'contracts_per_grid', 'direction', 'entry', 'trigger', 'expected_gph',
    'grid_income_per_hour', 'selection_income_per_hour', 'fee_net_income_per_hour',
    'funding_adjusted_income_per_hour', 'net_usdt_per_grid',
    'funding_adjusted_net_usdt_per_grid', 'estimated_fee_only_net_usdt_per_grid',
    'net_usdt_per_grid_basis', 'kucoin_profit_pct_min', 'kucoin_profit_pct_max',
    'kucoin_profit_usdt_min', 'kucoin_profit_usdt_max', 'kucoin_percent_basis',
    'eligible', 'offered', 'entry_eligible', 'can_arm', 'funded_entry_eligible',
    'funded_economics_eligible', 'provisional', 'quantity_calibrated',
    'liquidation_estimated', 'market_entry_ready', 'waiting_for_trigger',
    'ranking_basis', 'opening_fee_budget', 'gap_censored_cycles',
    'boundary_censored_cycles', 'open_censored_cycles', 'windows', 'economics'}


def _tradeoff(candidate):
    if not isinstance(candidate, dict):
        return deepcopy(candidate)
    result = {key:deepcopy(value) for key,value in candidate.items() if key in TRADEOFF_VALUES or key.startswith('kucoin_profit_')}
    preview = candidate.get('preview')
    if isinstance(preview, dict):
        for key,value in preview.items():
            if key.startswith('kucoin_profit_') and key not in result:
                result[key] = deepcopy(value)
    return result


def _top_three(candidates):
    return [_tradeoff(row) for row in candidates[:3]] if isinstance(candidates, list) else deepcopy(candidates)


def _search(search):
    if not isinstance(search, dict):
        return deepcopy(search)
    result = {key:deepcopy(value) for key,value in search.items() if key not in SEARCH_LISTS}
    if 'rejected_counts' not in search:
        return result
    rejected = search['rejected_counts']
    if not isinstance(rejected, list):
        return dict(result, rejected_count=None, rejected_reason_counts=None, rejected_examples=[])
    reasons = [row.get('reason') if isinstance(row, dict) else None for row in rejected]
    result.update(rejected_count=len(rejected),
        rejected_reason_counts=dict(Counter(reason if isinstance(reason, str) else '<unknown>' for reason in reasons)),
        rejected_examples=[{key:deepcopy(value) for key,value in row.items() if key in ('grids','reason','economics')}
                           if isinstance(row, dict) else deepcopy(row) for row in rejected[:3]])
    return result


def compact_form(form):
    """Detach a chosen income-chart form and retain bounded count alternatives.

    Keep the chosen config, preview, entry/range evidence, rates, economics and
    full chart. Replace repeated candidate/search trees with top-three tradeoff
    summaries and complete search rejection counts plus first-three examples.
    """
    if not isinstance(form, dict):
        return deepcopy(form)
    result = {key:deepcopy(value) for key,value in form.items() if key not in ('candidates','search','top_grid_counts')}
    if 'search' in form:
        result['search'] = _search(form['search'])
    if 'candidates' in form or 'top_grid_counts' in form:
        result['top_grid_counts'] = _top_three(form.get('candidates', form.get('top_grid_counts')))
    return result


def _read_ref(raw, pool):
    if set(raw) == {'read_ref'} and isinstance(raw['read_ref'], str) and raw['read_ref'] in pool:
        return deepcopy(raw)
    canonical = json.dumps(raw, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    digest = hashlib.sha256(canonical).hexdigest()
    if digest not in pool:
        pool[digest] = deepcopy(raw)
    elif pool[digest] != raw:
        raise ValueError('regime read pool digest collision or inconsistent existing evidence')
    return {'read_ref': digest}


def _intern_regime(value, pool):
    if isinstance(value, dict):
        return {key:(_read_ref(item, pool) if key == 'raw_read' and isinstance(item, dict)
                     else _intern_regime(item, pool)) for key,item in value.items()}
    if isinstance(value, list):
        return [_intern_regime(item, pool) for item in value]
    return deepcopy(value)


def _compact_row(row, pool):
    if not isinstance(row, dict):
        return deepcopy(row)
    result = {key:deepcopy(value) for key,value in row.items() if key not in ('setup','top_grid_counts','regime')}
    if 'setup' in row:
        result['setup'] = compact_form(row['setup'])
    setup = result.get('setup')
    if 'top_grid_counts' in row:
        result['top_grid_counts'] = _top_three(row['top_grid_counts'])
    if isinstance(setup, dict):
        if 'top_grid_counts' in setup:
            result.setdefault('top_grid_counts', setup['top_grid_counts'])
            if result['top_grid_counts'] == setup['top_grid_counts']:
                del setup['top_grid_counts']
        chart = setup.get('chart')
        if isinstance(chart, dict) and 'five_cells' in chart:
            result.setdefault('five_cells', chart['five_cells'])
            if result['five_cells'] == chart['five_cells']:
                del chart['five_cells']
        if 'regime' in setup:
            setup['regime'] = _intern_regime(setup['regime'], pool)
    if 'regime' in row:
        result['regime'] = _intern_regime(row['regime'], pool)
    return result


def _scalars(value):
    if not isinstance(value, dict):
        return deepcopy(value)
    return {key:deepcopy(item) for key,item in value.items() if item is None or type(item) in (bool,int,float,str)}


def _brief_cells(cells):
    if not isinstance(cells, dict):
        return deepcopy(cells)
    fields = {'timeframe','asof_ms','last_closed_ms','direction','regime','confidence','confidence_basis',
              'available','fresh','reason','indicator_observed_fraction','estimated_buckets',
              'missing_indicator_minutes','contiguous_bars','unavailable_buckets','strength','coverage_basis'}
    return {name:({key:deepcopy(value) for key,value in cell.items() if key in fields}
                   if isinstance(cell, dict) else deepcopy(cell)) for name,cell in cells.items()}


def _brief_form(form, pool):
    if not isinstance(form, dict):
        return deepcopy(form)
    result = _scalars(form)
    for name in ('config','entry_signal','windows','economics','warnings','chart_summary'):
        if name in form:
            result[name] = deepcopy(form[name])
    if 'preview' in form:
        result['preview'] = _scalars(form['preview'])
    if 'search' in form:
        result['search'] = _search(form['search'])
    if 'chart' in form:
        result['chart_summary'] = _scalars(form['chart'])
    if 'regime' in form:
        result['regime'] = _intern_regime(form['regime'], pool)
    return result


def _rejected_row(row, pool):
    if not isinstance(row, dict):
        return deepcopy(row)
    omitted = {'setup','five_cells','top_grid_counts','regime','setup_summary','timeframe_reads'}
    result = {key:deepcopy(value) for key,value in row.items() if key not in omitted}
    form = row.get('setup', row.get('setup_summary'))
    if 'setup' in row or 'setup_summary' in row:
        result['setup_summary'] = _brief_form(form, pool)
    chart = form.get('chart') if isinstance(form, dict) else None
    cells = row.get('five_cells', row.get('timeframe_reads', chart.get('five_cells') if isinstance(chart, dict) else None))
    if 'five_cells' in row or 'timeframe_reads' in row or isinstance(chart, dict) and 'five_cells' in chart:
        result['timeframe_reads'] = _brief_cells(cells)
    if 'regime' in row:
        result['regime'] = _intern_regime(row['regime'], pool)
    fields = set(row.get('omitted_reproducible_fields', []))
    fields.update(name for name in ('setup','five_cells','top_grid_counts') if name in row)
    if fields:
        result['omitted_reproducible_fields'] = sorted(fields)
        result['rejected_evidence_basis'] = 'brief pre-entry rejection evidence; regenerate full diagnostics with the operator and bound inputs'
    return result


def compact_scan(scan):
    """Compact only income_chart_v3 storage; legacy/unknown scans are unchanged.

    Expand each regime ``raw_read: {read_ref: SHA}`` using the scan's
    ``regime_read_pool[SHA]`` to recover the exact original gate evidence.
    Candidate order, all scan stats, every rejection and coverage field remain.
    Radar candidates retain rich cells/top-three offers. Rejected alternatives
    retain concise evidence and explicitly list omitted reproducible fields.
    """
    if not isinstance(scan, dict) or scan.get('strategy') != 'income_chart_v3':
        return deepcopy(scan)
    result = {key:deepcopy(value) for key,value in scan.items() if key not in ('radar','rejected','regime_read_pool')}
    pool = deepcopy(scan.get('regime_read_pool', {}))
    if not isinstance(pool, dict):
        raise ValueError('regime read pool must be a mapping')
    for name in ('radar', 'rejected'):
        if name in scan:
            rows = scan[name]
            compact = _compact_row if name == 'radar' else _rejected_row
            result[name] = [compact(row, pool) for row in rows] if isinstance(rows, list) else deepcopy(rows)
    if 'regime' in result:
        result['regime'] = _intern_regime(result['regime'], pool)
    result['regime_read_pool'] = pool
    return result
