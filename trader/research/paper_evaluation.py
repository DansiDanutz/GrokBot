"""A new paper-evaluation clock starts on an explicit runner event, never a build.

Pure state transitions only. This module does not launch a runner, reset the old
experiment, schedule work, write files, place orders, or assert a run has begun.
"""
from copy import deepcopy
import hashlib
import json
import math
import re

PERIOD_MS = 48 * 3600000
REQUIRED_POLICY = dict(margin_used_per_bot=1000, reserve_per_bot=200,
                       leverage=5, maximum_bots=2)


def _timestamp(value):
    if type(value) is not int or value < 0:
        raise ValueError('timestamp must be nonnegative integer milliseconds')
    return value


def _run_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,96}', value):
        raise ValueError('run ID must be a bounded public identifier')
    return value


def _validate_policy(policy):
    if not isinstance(policy, dict):
        raise ValueError('exact public policy fields are required')
    strategy = policy.get('strategy')
    if strategy not in ('grid-kucoin-policy-v2', 'grid-kucoin-v3-positive', 'grid-kucoin-v3-income-chart'):
        raise ValueError('strategy must identify an explicitly supported paper policy')
    chart = strategy == 'grid-kucoin-v3-income-chart'
    zeros = {'minimum_grid_net_usdt', 'range_exit_stop_pct'} if chart or strategy == 'grid-kucoin-v3-positive' else set()
    chart_fields = {'fee_safety_ratio', 'min_grids', 'max_grids', 'bias_mode',
                    'regime_gate', 'minimum_confidence', 'registration_sha256'} if chart else set()
    if set(policy) != {'strategy', *REQUIRED_POLICY, *zeros, *chart_fields}:
        raise ValueError('exact public policy fields are required')
    if any(type(policy[key]) is not int or policy[key] != value
           for key, value in REQUIRED_POLICY.items()):
        raise ValueError('policy requires two 1000+200 USDT bots at fixed 5x')
    if any(type(policy[key]) not in (int, float) or policy[key] != 0 for key in zeros):
        raise ValueError('positive paper policy requires explicit zero minimum net and range-hit exit')
    if chart:
        _validate_chart_policy(policy)


def _validate_chart_policy(policy):
    for key in ('fee_safety_ratio', 'minimum_confidence'):
        value = policy[key]
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError('chart confidence and fee safety must be finite and nonnegative')
    if policy['minimum_confidence'] > 1:
        raise ValueError('confidence must not exceed one')
    if any(type(policy[key]) is not int for key in ('min_grids', 'max_grids')) or not 2 <= policy['min_grids'] <= policy['max_grids'] <= 200:
        raise ValueError('grid-count search must be bounded within 2..200')
    if policy['bias_mode'] not in ('4h-only', '1d+4h') or type(policy['regime_gate']) is not bool:
        raise ValueError('explicit supported chart and regime variant required')
    digest = policy['registration_sha256']
    if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise ValueError('full preregistration digest required')


def arm_evaluation(policy, previous_run_id=None, source_revision=None):
    """Prepare a separate 2400-USDT experiment without inventing a start time."""
    _validate_policy(policy)
    if previous_run_id is not None:
        _run_id(previous_run_id)
    if not isinstance(source_revision, str) or not re.fullmatch(r'[0-9a-f]{40}', source_revision):
        raise ValueError('a frozen full source revision is required')
    canonical = json.dumps(dict(policy=policy, source_revision=source_revision), sort_keys=True, separators=(',', ':')).encode()
    return dict(schema_version=1, mode='paper', status='awaiting_paper_start',
        policy=deepcopy(policy), source_revision=source_revision, policy_sha256=hashlib.sha256(canonical).hexdigest(),
        bankroll_usdt=2400, audit_period_ms=PERIOD_MS, previous_run_id=previous_run_id,
        run_id=None, started_at_ms=None, first_audit_at_ms=None,
        start_event=None, reporting_timezone='Europe/Bucharest')


def start_evaluation(record, event):
    """Accept the runner's first fresh paper observation, with idempotent replay."""
    expected = {'kind', 'run_id', 'at_ms', 'first_observation_at_ms', 'policy_sha256'}
    if not isinstance(event, dict) or set(event) != expected:
        raise ValueError('a complete paper-run start event is required')
    if event['kind'] != 'paper_run_started' or record.get('mode') != 'paper':
        raise ValueError('only a paper runner start can start the evaluation clock')
    sealed = arm_evaluation(record['policy'], record.get('previous_run_id'), record.get('source_revision'))
    if event['policy_sha256'] != sealed['policy_sha256'] or record['policy_sha256'] != sealed['policy_sha256']:
        raise ValueError('paper run must match the armed policy')
    if record['status'] != 'awaiting_paper_start':
        if record['status'] == 'running' and record.get('start_event') == event:
            expected_record = start_evaluation(sealed, event)
            if record != expected_record:
                raise ValueError('persisted evaluation disagrees with its original start event')
            return deepcopy(record)
        raise ValueError('a running evaluation cannot be reset; arm a separate run')
    run_id, at = _run_id(event['run_id']), _timestamp(event['at_ms'])
    observed = _timestamp(event['first_observation_at_ms'])
    if run_id == record.get('previous_run_id') or not 0 <= at-observed <= 60000:
        raise ValueError('new run ID and a fresh nonfuture observation are required')
    result = deepcopy(record)
    result.update(status='running', run_id=run_id, started_at_ms=at,
                  first_audit_at_ms=at+PERIOD_MS, start_event=deepcopy(event))
    return result


def evaluation_clock(record, at_ms):
    """48 elapsed hours defines an audit period; it never stops trading."""
    at = _timestamp(at_ms)
    if record['status'] == 'awaiting_paper_start':
        return dict(status=record['status'], remaining_seconds=None, elapsed_seconds=None,
                    audit_due=False, stop_trading=False, evaluation_period_ms=None)
    if record['status'] == 'running':
        start_evaluation(record, record.get('start_event'))
    if record['status'] != 'running' or at < record['started_at_ms']:
        raise ValueError('clock requires a running evaluation and nonpast observation')
    due = record['first_audit_at_ms']
    return dict(status='audit_due' if at >= due else 'running',
        remaining_seconds=max(0, math.ceil((due-at)/1000)),
        elapsed_seconds=(at-record['started_at_ms'])/1000, audit_due=at >= due,
        stop_trading=False, evaluation_period_ms=[record['started_at_ms'], due])
