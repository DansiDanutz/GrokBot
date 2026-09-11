import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from trader.research.public_funding import (
    ENDPOINT, FundingResponse, TokenBucket, fetch_public_funding_history,
    latest_funding_terms, public_funding_get,
)


SYMBOL = 'XBTUSDTM'
HOUR = 3600000


def raw(timestamp, rate=.0001, symbol=SYMBOL):
    return {'symbol': symbol, 'timepoint': timestamp, 'fundingRate': rate}


def response(*rows):
    return FundingResponse(200, {'code': '200000', 'data': list(rows)}, {})


class FakeTime:
    def __init__(self):
        self.now = 0.
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class PublicFundingTests(unittest.TestCase):
    def run_fetch(self, pages, start=0, end=100, **kwargs):
        timer = FakeTime()
        calls = []
        iterator = iter(pages)

        def fetch(symbol, from_ms, to_ms):
            calls.append((symbol, from_ms, to_ms, timer.now))
            return next(iterator)

        result = fetch_public_funding_history(
            SYMBOL, start, end, fetch=fetch, clock=timer.clock, sleep=timer.sleep,
            wall_clock=timer.clock, **kwargs)
        return result, calls, timer

    def test_recorded_public_rows_and_short_page_still_paginate(self):
        fixture = json.loads((Path(__file__).parents[2] /
                              'tests/fixtures/kucoin-public-funding-history.json').read_text())
        payload = fixture['response']
        start, end = fixture['original_request']['from'], fixture['original_request']['to']
        result, calls, _ = self.run_fetch([FundingResponse(200, payload, {}), response()], start, end)
        self.assertEqual(len(result['records']), 3)
        self.assertEqual(result['records'][-1]['rate'], .00005)
        self.assertEqual(calls[1][2], min(r['timepoint'] for r in payload['data']) - 1)
        self.assertTrue(result['complete'])
        self.assertTrue(result['exhausted'])
        self.assertFalse(result['coverage_verified'])

    def test_reverse_pages_deduplicate_and_consume_five_tokens_every_request(self):
        checkpoints = []
        result, calls, timer = self.run_fetch(
            [response(raw(100), raw(80), raw(80)), response(raw(20), raw(0))],
            checkpoint=checkpoints.append)
        self.assertEqual([r['timestamp_ms'] for r in result['records']], [0, 20, 80, 100])
        self.assertEqual([call[2] for call in calls], [100, 79])
        self.assertEqual([call[3] for call in calls], [0, 1])
        self.assertEqual(timer.sleeps, [1])
        self.assertEqual(len(checkpoints), 2)
        self.assertFalse(checkpoints[0]['complete'])
        self.assertTrue(checkpoints[-1]['complete'])
        self.assertFalse(result['exhausted'])

    def test_interrupted_checkpoint_resumes_without_repeating_completed_page(self):
        saved = []

        def interrupt(state):
            saved.append(state)
            raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            self.run_fetch([response(raw(100), raw(80))], checkpoint=interrupt)
        result, calls, _ = self.run_fetch([response(raw(0))], resume=saved[0])
        self.assertEqual(calls[0][2], 79)
        self.assertEqual(len(result['records']), 3)
        self.assertEqual(result['pages'], 2)
        finished, calls, _ = self.run_fetch([], resume=result)
        self.assertEqual(finished, result)
        self.assertEqual(calls, [])

    def test_callback_cannot_mutate_internal_progress(self):
        result, _, _ = self.run_fetch([response(raw(80)), response()],
                                      checkpoint=lambda state: state['records'].clear())
        self.assertEqual(len(result['records']), 1)

    def test_conflicting_duplicate_wrong_symbol_and_bad_values_reject(self):
        bad_pages = [response(raw(80), raw(80, .2)), response(raw(80, symbol='ETHUSDTM')),
                     response(raw(80, float('nan'))), response(raw(True)),
                     response(raw(-1)), response(raw(101)), response(raw(80.5)),
                     FundingResponse(200, {'code': '400100', 'data': []}, {}),
                     FundingResponse(200, {'code': '200000', 'data': {}}, {})]
        for page in bad_pages:
            with self.subTest(page=page), self.assertRaises(ValueError):
                self.run_fetch([page])

    def test_repeated_server_page_and_bounded_page_budget_fail_closed(self):
        with self.assertRaisesRegex(ValueError, 'bounds|progress'):
            self.run_fetch([response(raw(80)), response(raw(80))])
        saved = []
        with self.assertRaisesRegex(RuntimeError, 'page budget'):
            self.run_fetch([response(raw(80))], max_pages=1, checkpoint=saved.append)
        self.assertEqual(saved[0]['next_to_ms'], 79)

    def test_bad_resume_bounds_cursor_and_records_are_rejected(self):
        saved = []
        self.run_fetch([response(raw(80)), response()], checkpoint=saved.append)
        for changes in ({'symbol': 'ETHUSDTM'}, {'start_ms': 1}, {'next_to_ms': 99},
                        {'records': [{'symbol': SYMBOL, 'timestamp_ms': 80, 'rate': float('inf')}]}):
            resume = dict(copy.deepcopy(saved[0]), **changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.run_fetch([], resume=resume)

    def test_429_retry_after_seconds_and_http_date(self):
        for value, delay in [('3', 3), ('Thu, 01 Jan 1970 00:00:04 GMT', 4)]:
            with self.subTest(value=value):
                result, calls, _ = self.run_fetch(
                    [FundingResponse(429, None, {'Retry-After': value}), response(raw(0))])
                self.assertTrue(result['complete'])
                self.assertEqual(calls[1][3], delay)
        with self.assertRaisesRegex(RuntimeError, '429'):
            self.run_fetch([FundingResponse(429, None, {})] * 3, max_retries=2)

    def test_http_error_is_not_an_empty_success(self):
        with self.assertRaisesRegex(RuntimeError, '503'):
            self.run_fetch([FundingResponse(503, {}, {})])

    def test_retry_after_cannot_create_unbounded_waits(self):
        with self.assertRaisesRegex(RuntimeError, 'bounded'):
            self.run_fetch([FundingResponse(429, None, {'retry-after': '1000'})])
        for value in ('nan', 'not a date'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.run_fetch([FundingResponse(429, None, {'Retry-After': value})])

    def test_shared_bucket_limits_requests_across_symbols(self):
        timer = FakeTime()
        limiter = TokenBucket(clock=timer.clock, sleep=timer.sleep)
        times = []

        def fetch(symbol, start, end):
            times.append(timer.now)
            return response()

        for symbol in (SYMBOL, 'ETHUSDTM', 'SOLUSDTM'):
            fetch_public_funding_history(symbol, 0, 100, fetch=fetch, limiter=limiter)
        self.assertEqual(times, [0, 1, 2])

    def test_invalid_request_options_do_not_reach_transport(self):
        with patch('trader.research.public_funding.public_funding_get') as fetch:
            for symbol, start, end in [('https://example.com', 0, 100), (SYMBOL, -1, 100),
                                       (SYMBOL, 100, 0), (SYMBOL, 0, True)]:
                with self.subTest(symbol=symbol, start=start, end=end), self.assertRaises(ValueError):
                    fetch_public_funding_history(symbol, start, end, fetch=fetch)
        fetch.assert_not_called()

    def test_token_bucket_caps_idle_tokens_and_rejects_invalid_cost(self):
        timer = FakeTime()
        bucket = TokenBucket(clock=timer.clock, sleep=timer.sleep)
        bucket.acquire(5)
        timer.now = 100
        bucket.acquire(5)
        bucket.acquire(5)
        self.assertEqual(timer.now, 101)
        with self.assertRaises(ValueError):
            bucket.acquire(6)

    def test_default_http_transport_uses_fixed_public_get_without_auth(self):
        class Reply:
            status = 200
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self):
                return b'{"code":"200000","data":[]}'

        with patch('trader.research.public_funding.build_opener') as builder:
            builder.return_value.open.return_value = Reply()
            result = public_funding_get(SYMBOL, 0, 100)
        request = builder.return_value.open.call_args.args[0]
        self.assertTrue(request.full_url.startswith(ENDPOINT + '?'))
        self.assertEqual(request.get_method(), 'GET')
        self.assertFalse(any('key' in key.lower() or 'auth' in key.lower() for key in request.headers))
        self.assertEqual(result.status, 200)


class CausalFundingTermsTests(unittest.TestCase):
    def test_lag_boundary_and_period_are_causal_without_fixed_eight_hours(self):
        history = [raw(0, .001), raw(4 * HOUR, .002), raw(8 * HOUR, -.003)]
        before = latest_funding_terms(history, 8 * HOUR + 59999)
        self.assertEqual(before['funding_rate'], .002)
        self.assertEqual(before['funding_interval_ms'], 4 * HOUR)
        at = latest_funding_terms(history, 8 * HOUR + 60000)
        self.assertEqual(at['funding_rate'], -.003)
        self.assertEqual(at['available_at_ms'], 8 * HOUR + 60000)
        self.assertFalse(at['historically_verified'])
        self.assertEqual(at['period_source'], 'inferred_from_last_two_settlements')
        self.assertTrue(at['usable_for_projection'])

    def test_missing_insufficient_irregular_and_stale_are_explicit(self):
        missing = latest_funding_terms([], 0)
        self.assertIsNone(missing['funding_interval_ms'])
        self.assertEqual(missing['coverage'], 'missing')
        one = latest_funding_terms([raw(0)], 60000)
        self.assertEqual(one['coverage'], 'insufficient_history')
        self.assertIsNone(one['funding_interval_ms'])
        irregular = latest_funding_terms([raw(0), raw(4 * HOUR), raw(12 * HOUR)],
                                         12 * HOUR + 60000)
        self.assertEqual(irregular['funding_interval_ms'], 8 * HOUR)
        self.assertEqual(irregular['coverage'], 'irregular_intervals')
        self.assertFalse(irregular['usable_for_projection'])
        stale = latest_funding_terms([raw(0), raw(4 * HOUR)], 8 * HOUR + 60000)
        self.assertEqual(stale['coverage'], 'stale_missing_expected_settlement')
        self.assertEqual(stale['missing_expected_settlements'], 1)

    def test_future_rows_and_missing_next_record_never_change_past_terms(self):
        history = [raw(0), raw(4 * HOUR)]
        result = latest_funding_terms(history, 4 * HOUR + 60000)
        future = raw(8 * HOUR, float('nan'))
        self.assertEqual(result, latest_funding_terms(history + [future], 4 * HOUR + 60000))
        self.assertEqual(history[0], raw(0))
        with self.assertRaises(ValueError):
            latest_funding_terms(history + [raw(0, .3)], 4 * HOUR + 60000)


if __name__ == '__main__':
    unittest.main()
