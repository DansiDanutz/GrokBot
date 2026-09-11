"""Preregistered walk-forward orchestration; never trains on holdout results."""
import datetime as dt
import hashlib
import itertools
import json
import math
from pathlib import Path
import subprocess

DAY = 86400000
ROOT = Path(__file__).resolve().parents[2]
REGISTRATION = 'research/preregistration/grid-5x.json'


def timestamp(value):
    return int(dt.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()*1000)


def calendar_month(start, end):
    a = dt.datetime.fromtimestamp(start/1000, dt.timezone.utc)
    b = dt.datetime.fromtimestamp(end/1000, dt.timezone.utc)
    return (a.day == b.day == 1 and a.hour == b.hour == a.minute == b.minute == 0
            and a.second == b.second == a.microsecond == b.microsecond == 0
            and b.year*12+b.month == a.year*12+a.month+1)


def deployment_decision(months, parity_verified=False):
    reasons = []
    if len(months) != 2:
        reasons.append('two complete disjoint months required')
    ordered = sorted(months, key=lambda x: x['start_ms'])
    if len(ordered) == 2 and ordered[0]['end_ms'] > ordered[1]['start_ms']:
        reasons.append('holdouts overlap')
    for row in months:
        if not all(math.isfinite(row[k]) for k in ('net', 'max_drawdown')):
            reasons.append('nonfinite result metrics')
        if not calendar_month(row['start_ms'], row['end_ms']):
            reasons.append('holdout is not a full calendar month')
        if not row.get('coverage_complete'):
            reasons.append('historical evidence is incomplete')
        if not row.get('net_after_end_close_reserve', row['net']) > 0:
            reasons.append('net is not positive after all modeled costs')
        if not row['max_drawdown'] < .1:
            reasons.append('drawdown is not below ten percent')
    if not parity_verified:
        reasons.append('neutral allocation and liquidation parity unverified')
    return {'decision': 'shelve' if reasons else 'eligible_for_separate_paper_deployment_review',
            'reasons': sorted(set(reasons))}


def choose_parameters(trials):
    if not trials:
        raise ValueError('no training trials')
    return min(trials, key=lambda t: (-t['completed_grids_per_hour'], -t['net'],
                                     t['lookback_hours'], t['margin_gph']))


def registered_document():
    path = ROOT / REGISTRATION
    content = path.read_bytes()
    recorded = subprocess.check_output(['git', 'show', 'HEAD:'+REGISTRATION], cwd=ROOT)
    if content != recorded:
        raise ValueError('preregistration must be committed before a sweep')
    doc = json.loads(content)
    if doc['id'] != 'grid-5x' or doc['primary_metric'] != 'completed_grids_per_hour':
        raise ValueError('unexpected research registration')
    commit = subprocess.check_output(['git', 'log', '-1', '--format=%H', '--', REGISTRATION],
                                     cwd=ROOT, text=True).strip()
    return doc, {'commit': commit, 'sha256': hashlib.sha256(content).hexdigest()}


def _parameters(doc, hours, margin):
    return dict(lookback_hours=hours, margin_gph=margin,
                payback_hours=doc['switch_payback_hours'],
                investment=doc['investment_usdt'], grids=doc['grids'], tick_ms=doc['tick_ms'])


def sweep(snapshot, runner=None):
    from .grid_runner import run_window
    runner = runner or run_window
    doc, provenance = registered_document()
    holdouts, nulls, selections = [], [], []
    for index, window in enumerate(doc['holdouts']):
        start, end = timestamp(window['start']), timestamp(window['end'])
        train_start = start-doc['training_days']*DAY
        trials = []
        for hours, margin in itertools.product(doc['lookback_hours'], doc['margin_gph']):
            parameters = _parameters(doc, hours, margin)
            result = runner(snapshot, train_start, start, parameters)
            trials.append(dict(parameters, completed_grids_per_hour=result['completed_grids_per_hour'],
                               net=result['net'], coverage_complete=result['coverage_complete']))
        selected = choose_parameters(trials)
        parameters = _parameters(doc, selected['lookback_hours'], selected['margin_gph'])
        result = runner(snapshot, start, end, parameters)
        if not all(t['coverage_complete'] for t in trials):
            result['coverage_complete'] = False
        holdouts.append(result)
        nulls.append(runner(snapshot, start, end, parameters, seed=doc['null_seed']+index))
        selections.append(dict(train_start_ms=train_start, train_end_ms=start,
                               selected=parameters, trials=trials))
    return dict(registration=provenance, primary_metric=doc['primary_metric'],
                selections=selections, holdouts=holdouts, random_coin_null=nulls,
                **deployment_decision(holdouts),
                assumptions=doc['execution_assumptions'],
                snapshot_manifest=snapshot.manifest)
