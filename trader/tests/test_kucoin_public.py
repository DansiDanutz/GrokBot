"""Offline protocol, bounded transport, and limiter tests."""
import io
import json
import unittest
from pathlib import Path
from urllib.error import HTTPError

from trader.data.kucoin_public import PublicClient, ProtocolError, select_contracts
from trader.data.ratelimit import TokenBucket

FIXTURES = Path(__file__).resolve().parents[2] / 'tests/fixtures/kucoin'


class Response(io.BytesIO):
    def __init__(self, document, status=200, headers=None):
        super().__init__(json.dumps(document).encode())
        self.status = status
        self.headers = headers or {}

    def read1(self, size=-1):
        return self.read(size)


class PublicProtocolTests(unittest.TestCase):
    def client(self, document):
        self.requests = []
        def opener(request, timeout):
            self.requests.append((request, timeout))
            return Response(document)
        return PublicClient(opener=opener, limiter=TokenBucket())

    def fixture(self, name):
        return json.loads((FIXTURES / f'{name}.json').read_text())

    def test_recorded_candle_order_units_and_exclusive_end(self):
        fixture = self.fixture('candles1m')
        start = fixture['data'][0][0]
        client = self.client(fixture)
        rows = client.klines('XBTUSDTM', '1m', start, start + 180000)
        self.assertEqual(rows[0]['high'], fixture['data'][0][2])
        self.assertEqual(rows[0]['close'], fixture['data'][0][4])
        self.assertEqual(rows[0]['volume'], fixture['data'][0][5])
        self.assertIn(f'to={start + 179999}', self.requests[0][0].full_url)
        self.assertEqual(self.requests[0][0].get_method(), 'GET')
        self.assertNotIn('Authorization', self.requests[0][0].headers)

    def test_conflicting_duplicate_and_invalid_candles_rejected(self):
        fixture = self.fixture('candles1m')
        start = fixture['data'][0][0]
        for row in ([start, 1, 0, 1, 1, 1, 1],
                    [start, 1, 2, 1, float('nan'), 1, 1],
                    [start + 1, 1, 2, 1, 1, 1, 1]):
            with self.subTest(row=row), self.assertRaises(ProtocolError):
                self.client({'code': '200000', 'data': [row]}).klines(
                    'XBTUSDTM', '1m', start, start + 60000)
        conflict = list(fixture['data'][0]); conflict[5] += 1
        with self.assertRaises(ProtocolError):
            self.client({'code': '200000', 'data': [fixture['data'][0], conflict]}).klines(
                'XBTUSDTM', '1m', start, start + 60000)

    def test_out_of_window_rows_and_page_oversize_rejected(self):
        row = self.fixture('candles1m')['data'][0]
        with self.assertRaises(ProtocolError):
            self.client({'code': '200000', 'data': [row]}).klines(
                'XBTUSDTM', '1m', row[0] + 60000, row[0] + 120000)
        with self.assertRaises(ValueError):
            self.client({}).klines('XBTUSDTM', '1m', 0, 501 * 60000)

    def test_symbol_injection_and_non_crypto_contracts_rejected(self):
        contract = self.fixture('contracts')['data'][0]
        for field, value in [('status', 'Closed'), ('expireDate', 1),
                             ('isInverse', True), ('assetClass', 'STOCK'),
                             ('quoteCurrency', 'USDC')]:
            with self.subTest(field=field):
                self.assertEqual(select_contracts([{**contract, field: value}]), [])
        with self.assertRaises(ValueError):
            self.client({}).ticker('../orders')
        self.assertEqual(len(select_contracts(self.fixture('contracts')['data'])), 2)

    def test_recorded_funding_book_and_ticker_shapes(self):
        funding = self.fixture('funding')
        oldest = min(x['timepoint'] for x in funding['data'])
        latest = max(x['timepoint'] for x in funding['data'])
        rows = self.client(funding).funding('XBTUSDTM', oldest, latest + 1)
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(x['symbol'] == 'XBTUSDTM' for x in rows))
        self.assertIn('bestBidPrice', self.client(self.fixture('ticker')).ticker('XBTUSDTM'))
        self.assertEqual(len(self.client(self.fixture('depth20')).book('XBTUSDTM')['bids']), 20)

    def test_retry_429_and_transient_errors_are_bounded(self):
        waits = []
        class Limit:
            def acquire(self, weight):
                pass
            def feedback(self, headers, limited=False):
                waits.append((headers, limited))
        calls = []
        def opener(request, timeout):
            calls.append(request)
            raise HTTPError(request.full_url, 429, 'limited', {'Retry-After': '2'}, None)
        client = PublicClient(opener=opener, limiter=Limit(), sleep=lambda _: None)
        with self.assertRaises(ProtocolError):
            client.ticker('XBTUSDTM')
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(flag for _, flag in waits))

    def test_oversized_json_and_non_success_code_rejected(self):
        for value in ({'code': '400000', 'data': []}, {'code': '200000', 'data': 'x' * 2000001}):
            with self.subTest(code=value['code']), self.assertRaises(ProtocolError):
                self.client(value).contracts()


class LimiterTests(unittest.TestCase):
    def test_weighted_wait_and_provider_reset(self):
        time = [0.0]; waits = []
        def sleep(delay):
            waits.append(delay); time[0] += delay
        limiter = TokenBucket(rate=3, capacity=3, clock=lambda: time[0], sleep=sleep)
        limiter.acquire(3); limiter.acquire(3)
        self.assertEqual(waits, [1.0])
        limiter.feedback({'gw-ratelimit-remaining': '0', 'gw-ratelimit-reset': '2000'})
        limiter.acquire(1)
        self.assertGreaterEqual(sum(waits), 3)

class DeadlineTests(unittest.TestCase):
    def test_expired_deadline_does_not_call_transport(self):
        calls = []
        client = PublicClient(deadline=4, clock=lambda: 4,
                              opener=lambda *a, **k: calls.append(a))
        with self.assertRaisesRegex(ProtocolError, 'deadline'):
            client.ticker('XBTUSDTM')
        self.assertEqual(calls, [])

    def test_timeout_clamped_and_no_retry_after_deadline(self):
        clock = [1.0]; calls = []
        class Limit:
            def acquire(self, weight, deadline=None):
                pass
            def feedback(self, headers, limited=False):
                pass
        def opener(request, timeout):
            calls.append(timeout)
            clock[0] += timeout
            raise TimeoutError('injected')
        client = PublicClient(deadline=4, clock=lambda: clock[0], opener=opener,
                              limiter=Limit(), sleep=lambda _: None)
        with self.assertRaisesRegex(ProtocolError, 'deadline'):
            client.ticker('XBTUSDTM')
        self.assertEqual(calls, [3])

    def test_redirect_never_follows_external_target(self):
        from trader.data.kucoin_public import _NoRedirect
        from urllib.request import Request
        with self.assertRaisesRegex(ProtocolError, 'redirect'):
            _NoRedirect().redirect_request(Request('https://api-futures.kucoin.com'),
                None, 302, 'Found', {}, 'https://attacker.invalid')

    def test_http_403_is_not_retried(self):
        calls = []
        def opener(request, timeout):
            calls.append(1)
            raise HTTPError(request.full_url, 403, 'forbidden', {}, None)
        with self.assertRaises(ProtocolError):
            PublicClient(opener=opener).ticker('XBTUSDTM')
        self.assertEqual(len(calls), 1)

class UnicodeContractTests(unittest.TestCase):
    def test_non_latin_exchange_symbol_is_safely_encoded(self):
        from trader.data.kucoin_public import symbol_name
        self.assertEqual(symbol_name('牛来USDTM'), '牛来USDTM')

class ReadDeadlineTests(unittest.TestCase):
    def test_read_finishing_after_deadline_is_discarded(self):
        clock = [1.0]
        class LateResponse(Response):
            def read(self, size=-1):
                raw = super().read(size)
                clock[0] = 101.0
                return raw
        limiter = TokenBucket(clock=lambda: clock[0])
        client = PublicClient(deadline=4, clock=lambda: clock[0], limiter=limiter,
                              opener=lambda *a, **k: LateResponse({'code': '200000', 'data': []}))
        with self.assertRaisesRegex(ProtocolError, 'deadline'):
            client.contracts()

    def test_multiple_bounded_reads_obey_size_limit(self):
        sizes = []
        class ChunkResponse(Response):
            def read(self, size=-1):
                sizes.append(size)
                return super().read(min(size, 1024))
        payload = {'code': '200000', 'data': [{'field': 'x' * 70000}]}
        client = PublicClient(opener=lambda *a, **k: ChunkResponse(payload))
        self.assertEqual(client.contracts(), payload['data'])
        self.assertTrue(all(0 < size <= 65536 for size in sizes))

class SingleReadTests(unittest.TestCase):
    def test_transport_uses_single_raw_read_when_available(self):
        class BufferedResponse(Response):
            def read(self, size=-1):
                raise AssertionError('buffer-filling read can wait across many socket reads')
            def read1(self, size=-1):
                return io.BytesIO.read(self, size)
        client = PublicClient(opener=lambda *a, **k: BufferedResponse({'code': '200000', 'data': []}))
        self.assertEqual(client.contracts(), [])
