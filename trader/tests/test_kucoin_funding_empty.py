"""Recorded empty funding windows remain distinct from API failures."""
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from trader.data.kucoin_public import PublicClient, ProtocolError
from trader.data.ratelimit import TokenBucket
from trader.data.store import Store
from trader.data.updater import Updater
from trader.tests.test_kucoin_public import Response
from trader.tests.test_updater import START, SyntheticClient, SyntheticClock

FIXTURES = Path(__file__).resolve().parents[2] / 'tests/fixtures/kucoin'
FRONTIER = 1789090716256
TARGET = 1789091042336


def fixture(name):
    return json.loads((FIXTURES / f'{name}.json').read_text())


def client_for(document):
    return PublicClient(opener=lambda *a, **k: Response(document),
                        limiter=TokenBucket())


class EmptyFundingTests(unittest.TestCase):
    def test_recorded_success_null_means_no_returned_settlements(self):
        client = client_for(fixture('funding-empty-incremental'))
        self.assertEqual(client.funding('XBTUSDTM', FRONTIER, TARGET), [])

    def test_null_handling_is_limited_to_funding_success(self):
        malformed = ({'code': '400000', 'data': None}, {'code': '200000'},
                     {'code': '200000', 'data': {}}, {'code': '200000', 'data': False},
                     {'code': '200000', 'data': ''})
        for document in malformed:
            with self.subTest(document=document), self.assertRaises(ProtocolError):
                client_for(document).funding('XBTUSDTM', FRONTIER, TARGET)
        with self.assertRaises(ProtocolError):
            client_for({'code': '200000', 'data': None}).contracts()

    def test_malformed_and_out_of_window_settlements_still_fail(self):
        for row in ({'symbol': 'ETHUSDTM', 'fundingRate': .1, 'timepoint': FRONTIER},
                    {'symbol': 'XBTUSDTM', 'fundingRate': 'bad', 'timepoint': FRONTIER},
                    {'symbol': 'XBTUSDTM', 'fundingRate': .1, 'timepoint': TARGET}):
            with self.subTest(row=row), self.assertRaises(ProtocolError):
                client_for({'code': '200000', 'data': [row]}).funding(
                    'XBTUSDTM', FRONTIER, TARGET)

    def test_real_updater_checkpoint_advances_without_fabricating_funding(self):
        requests = []
        def opener(request, timeout):
            query = parse_qs(urlparse(request.full_url).query)
            requests.append(query)
            name = 'funding-empty-incremental' if int(query['from'][0]) == FRONTIER else 'funding-day-context'
            return Response(fixture(name))
        public = PublicClient(opener=opener, limiter=TokenBucket())
        clock = SyntheticClock()
        clock.elapsed = (FRONTIER - START) / 1000
        synthetic = SyntheticClient(clock)
        synthetic.funding = public.funding
        with tempfile.TemporaryDirectory() as directory:
            with Store(Path(directory).resolve() / 'market.sqlite') as store:
                updater = Updater(store, synthetic, clock)
                first = updater.cycle(clock.monotonic() + 300)
                self.assertEqual(first['status'], 'pass')
                before = store.query('SELECT * FROM funding ORDER BY time_ms')
                self.assertEqual(len(before), 3)
                clock.elapsed = (TARGET - START) / 1000
                second = updater.cycle(clock.monotonic() + 300)
                self.assertEqual(second['status'], 'pass')
                self.assertEqual(store.query('SELECT * FROM funding ORDER BY time_ms'), before)
                point = store.query("SELECT * FROM checkpoints WHERE source='updater-funding'")[0]
                self.assertEqual(point['next_time_ms'], TARGET)
                self.assertTrue(all(row['period_ms'] is None for row in before))
                quality = store.query("SELECT * FROM data_quality WHERE check_name='updater_funding' ORDER BY checked_at_ms")
                self.assertEqual(len(quality), 2)
                self.assertTrue(all(row['status'] == 'pass' for row in quality))
                details = json.loads(quality[-1]['details_json'])
                self.assertEqual(details['rows'], 0)
                self.assertFalse(details['schedule_verified'])
                self.assertEqual(details['completeness'], 'queried_window_only')
        self.assertEqual(requests[-1]['from'], [str(FRONTIER)])
        self.assertEqual(requests[-1]['to'], [str(TARGET - 1)])
