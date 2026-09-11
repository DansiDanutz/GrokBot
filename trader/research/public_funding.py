"""Explicit, credential-free public funding retrieval and causal projection terms.

Official endpoint documentation:
https://www.kucoin.com/docs-new/rest/futures-trading/funding-fees/get-public-funding-history
The public GET has weight 5. A recorded response was capped at 100 latest rows;
pagination therefore moves ``to`` to the earliest returned timestamp minus 1,
including after short pages. Request bounds are inclusive milliseconds.

Imports perform no I/O. The caller explicitly invokes retrieval, supplies a
checkpoint callback, and owns any filesystem persistence. Share one TokenBucket
across symbols to preserve the conservative one-request-per-second policy.
Completion means pagination finished, not that every historical settlement is
proven present. Actual records remain separate from lagged, inferred projections.
"""
from copy import deepcopy
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
import json
import math
import re
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener


ENDPOINT = 'https://api-futures.kucoin.com/api/v1/contract/funding-rates'
REQUEST_WEIGHT = 5
MAX_RETRY_DELAY_SECONDS = 60


@dataclass(frozen=True)
class FundingResponse:
    status: int
    payload: object
    headers: dict = field(default_factory=dict)


def _finite(value, name):
    if isinstance(value, bool):
        raise ValueError(f'{name} must be finite')
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f'{name} must be finite') from exc
    if not math.isfinite(result):
        raise ValueError(f'{name} must be finite')
    return result


def _integer(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f'{name} must be an integer >= {minimum}')
    return value


def _bounds(symbol, start_ms, end_ms):
    if not isinstance(symbol, str) or not re.fullmatch(r'[A-Z0-9][A-Z0-9_-]{0,79}', symbol):
        raise ValueError('Invalid public contract symbol')
    _integer(start_ms, 'start_ms')
    _integer(end_ms, 'end_ms')
    if end_ms < start_ms:
        raise ValueError('end_ms must not precede start_ms')


class TokenBucket:
    """Serial token bucket; injectable monotonic clock and advancing sleep."""

    def __init__(self, capacity=5, refill_per_second=5, *, clock=time.monotonic, sleep=time.sleep):
        self.capacity = _finite(capacity, 'capacity')
        self.refill_per_second = _finite(refill_per_second, 'refill_per_second')
        if self.capacity <= 0 or self.refill_per_second <= 0:
            raise ValueError('Token bucket capacity and refill must be positive')
        self.clock, self.sleep = clock, sleep
        self.tokens = self.capacity
        self.updated = _finite(clock(), 'clock')

    def acquire(self, cost=REQUEST_WEIGHT):
        cost = _finite(cost, 'cost')
        if not 0 < cost <= self.capacity:
            raise ValueError('Token cost must be positive and fit capacity')
        while True:
            now = _finite(self.clock(), 'clock')
            if now < self.updated:
                raise ValueError('Token clock must be monotonic')
            self.tokens = min(self.capacity, self.tokens + (now-self.updated)*self.refill_per_second)
            self.updated = now
            if self.tokens >= cost:
                self.tokens -= cost
                return
            self.sleep((cost-self.tokens)/self.refill_per_second)
            if self.clock() <= now:
                raise RuntimeError('Token bucket sleep did not advance clock')


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, new_url):
        raise HTTPError(request.full_url, code, 'Public funding redirects are not allowed', headers, file)


def public_funding_get(symbol, from_ms, to_ms):
    """Explicit public GET only, fixed endpoint, no keys, auth, or redirects."""
    _bounds(symbol, from_ms, to_ms)
    url = ENDPOINT + '?' + urlencode({'symbol': symbol, 'from': from_ms, 'to': to_ms})
    request = Request(url, headers={'Accept': 'application/json'}, method='GET')
    try:
        with build_opener(_NoRedirect()).open(request, timeout=30) as reply:
            return FundingResponse(reply.status, json.loads(reply.read()), dict(reply.headers))
    except HTTPError as exc:
        return FundingResponse(exc.code, None, dict(exc.headers or {}))


def _record(raw, symbol=None):
    try:
        actual_symbol = raw['symbol']
        if symbol is not None and actual_symbol != symbol:
            raise ValueError('Funding record symbol does not match requested symbol')
        timestamp = raw['timestamp_ms'] if 'timestamp_ms' in raw else raw['timepoint']
        rate = raw['rate'] if 'rate' in raw else raw['fundingRate']
        _bounds(actual_symbol, timestamp, timestamp)
        rate = _finite(rate, 'funding rate')
        if 'timepoint' in raw and raw['timepoint'] != timestamp:
            raise ValueError('Conflicting funding timestamp aliases')
        if 'fundingRate' in raw and _finite(raw['fundingRate'], 'funding rate') != rate:
            raise ValueError('Conflicting funding rate aliases')
        return {'symbol': actual_symbol, 'timestamp_ms': timestamp, 'rate': rate}
    except (KeyError, TypeError) as exc:
        raise ValueError(f'Malformed funding record: {exc}') from exc


def _records(rows, symbol, start_ms, end_ms):
    if not isinstance(rows, list):
        raise ValueError('Funding data must be a list')
    observed = {}
    for raw in rows:
        value = _record(raw, symbol)
        timestamp = value['timestamp_ms']
        if not start_ms <= timestamp <= end_ms:
            raise ValueError('Funding record outside request bounds; pagination made no progress')
        if timestamp in observed and observed[timestamp] != value:
            raise ValueError(f'Conflicting duplicate funding record at {timestamp}')
        observed[timestamp] = value
    return [observed[timestamp] for timestamp in sorted(observed)]


def _retry_delay(headers, attempt, wall_clock):
    value = next((value for key, value in headers.items() if key.lower() == 'retry-after'), None)
    if value is None:
        delay = min(2 ** attempt, MAX_RETRY_DELAY_SECONDS)
    else:
        try:
            delay = float(value)
        except (ValueError, TypeError):
            try:
                delay = parsedate_to_datetime(str(value)).timestamp() - wall_clock()
            except (ValueError, TypeError, OverflowError) as exc:
                raise ValueError('Invalid Retry-After header') from exc
        delay = max(0., _finite(delay, 'Retry-After'))
    if delay > MAX_RETRY_DELAY_SECONDS:
        raise RuntimeError('Retry-After exceeds bounded 60-second wait; resume later')
    return delay


def _request(fetch, symbol, start, end, limiter, sleep, wall_clock, max_retries):
    for attempt in range(max_retries + 1):
        limiter.acquire(REQUEST_WEIGHT)
        reply = fetch(symbol, start, end)
        if not isinstance(reply, FundingResponse):
            raise ValueError('fetch must return FundingResponse')
        if reply.status == 429:
            if attempt == max_retries:
                raise RuntimeError('Public funding HTTP 429 retry budget exhausted')
            sleep(_retry_delay(reply.headers, attempt, wall_clock))
            continue
        if reply.status != 200:
            raise RuntimeError(f'Public funding HTTP {reply.status}')
        if not isinstance(reply.payload, dict) or reply.payload.get('code') != '200000':
            raise ValueError('Public funding response code must be 200000')
        return _records(reply.payload.get('data'), symbol, start, end)


def _initial_state(symbol, start, end, resume):
    fresh = {'symbol': symbol, 'start_ms': start, 'end_ms': end, 'next_to_ms': end,
             'records': [], 'complete': False, 'exhausted': False, 'pages': 0,
             'coverage_verified': False}
    if resume is None:
        return fresh
    try:
        state = deepcopy(resume)
        for key in ('symbol', 'start_ms', 'end_ms'):
            if state[key] != fresh[key]:
                raise ValueError('Resume request bounds/symbol do not match')
        state['records'] = _records(state['records'], symbol, start, end)
        _integer(state['next_to_ms'], 'next_to_ms', -1)
        _integer(state['pages'], 'pages')
        if any(type(state[key]) is not bool for key in ('complete', 'exhausted')):
            raise ValueError('Resume completion flags must be boolean')
        expected = state['records'][0]['timestamp_ms'] - 1 if state['records'] else end
        if state['next_to_ms'] != expected:
            raise ValueError('Resume cursor does not match earliest retained record')
        if state['complete'] != (state['exhausted'] or expected < start):
            raise ValueError('Inconsistent resume completion state')
        state['coverage_verified'] = False
        return state
    except (KeyError, TypeError) as exc:
        raise ValueError(f'Malformed resume checkpoint: {exc}') from exc


def _advance_page(state, page):
    state['pages'] += 1
    if not page:
        state.update(complete=True, exhausted=True)
        return
    next_to = page[0]['timestamp_ms'] - 1
    if next_to >= state['next_to_ms']:
        raise ValueError('Public funding pagination made no progress')
    state['records'] = _records(page + state['records'], state['symbol'],
                                state['start_ms'], state['end_ms'])
    state['next_to_ms'] = next_to
    state['complete'] = next_to < state['start_ms']


def fetch_public_funding_history(symbol, start_ms, end_ms, *, fetch=public_funding_get,
                                 limiter=None, clock=time.monotonic, sleep=time.sleep,
                                 wall_clock=time.time, max_retries=3, max_pages=10000,
                                 resume=None, checkpoint=None):
    """Retrieve inclusive bounds; checkpoint each validated page, then return state.

    The callback receives an independent JSON-safe copy. A failed next request or
    callback can be resumed from the last durably saved copy. ``max_pages`` limits
    this invocation; its exhaustion raises after checkpointing the last page.
    """
    _bounds(symbol, start_ms, end_ms)
    _integer(max_retries, 'max_retries')
    _integer(max_pages, 'max_pages', 1)
    state = _initial_state(symbol, start_ms, end_ms, resume)
    if state['complete']:
        return state
    limiter = limiter if limiter is not None else TokenBucket(clock=clock, sleep=sleep)
    for _ in range(max_pages):
        page = _request(fetch, symbol, start_ms, state['next_to_ms'], limiter,
                        sleep, wall_clock, max_retries)
        _advance_page(state, page)
        if checkpoint is not None:
            checkpoint(deepcopy(state))
        if state['complete']:
            return state
    raise RuntimeError('Public funding page budget exhausted; resume from checkpoint')


def _eligible_history(history, asof_ms, lag_ms):
    if not isinstance(history, list):
        raise ValueError('Funding history must be a list of actual settlement records')
    eligible = []
    for raw in history:
        try:
            timestamp = raw['timestamp_ms'] if 'timestamp_ms' in raw else raw['timepoint']
            _integer(timestamp, 'settlement timestamp')
        except (KeyError, TypeError) as exc:
            raise ValueError(f'Malformed funding timestamp: {exc}') from exc
        if timestamp + lag_ms <= asof_ms:
            eligible.append(_record(raw))
    if not eligible:
        return []
    return _records(eligible, eligible[0]['symbol'], 0, asof_ms)


def latest_funding_terms(history, asof_ms, *, lag_ms=60000):
    """Causal latest settled rate and *inferred* next period, never a cash schedule.

    Default 60-second publication lag is an explicit modeling assumption. A
    regular-looking sequence cannot prove missing records or historical terms;
    irregular intervals or an overdue expected settlement disable projection.
    """
    _integer(asof_ms, 'asof_ms')
    _integer(lag_ms, 'lag_ms')
    records = _eligible_history(history, asof_ms, lag_ms)
    result = {'available': bool(records), 'funding_rate': None, 'funding_interval_ms': None,
              'settlement_timestamp_ms': None, 'available_at_ms': None, 'asof_ms': asof_ms,
              'lag_ms': lag_ms, 'period_source': None, 'historically_verified': False,
              'coverage': 'missing', 'usable_for_projection': False,
              'missing_expected_settlements': None, 'observed_record_count': len(records),
              'warnings': ['Publication lag is assumed; interval coverage is not independently verified.']}
    if not records:
        return result
    latest = records[-1]
    result.update(funding_rate=latest['rate'], settlement_timestamp_ms=latest['timestamp_ms'],
                  available_at_ms=latest['timestamp_ms'] + lag_ms, symbol=latest['symbol'],
                  coverage='insufficient_history')
    if len(records) < 2:
        return result
    intervals = [right['timestamp_ms']-left['timestamp_ms'] for left, right in zip(records, records[1:])]
    interval = intervals[-1]
    missing = (asof_ms-lag_ms-latest['timestamp_ms']) // interval
    coverage = 'inferred_regular'
    if len(set(intervals)) > 1:
        coverage = 'irregular_intervals'
    if missing:
        coverage = 'stale_missing_expected_settlement'
    result.update(funding_interval_ms=interval, period_source='inferred_from_last_two_settlements',
                  coverage=coverage, missing_expected_settlements=missing,
                  usable_for_projection=coverage == 'inferred_regular')
    return result
