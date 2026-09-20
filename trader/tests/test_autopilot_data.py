"""Offline daemon market protocol and restart-ingestion tests."""
import json
import tempfile
import unittest
from pathlib import Path
from trader.tests.test_kucoin_public import Response
from trader.data.kucoin_public import PublicClient, ProtocolError


class AutopilotDataTests(unittest.TestCase):
    def test_restart_ingestion_is_bounded_and_does_not_replace_stored_rows(self):
        from trader.autopilot.market import ingest_open_minutes, candles_after, rates_at
        from trader.data.store import Store
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / 'market.sqlite3'
            with Store(path) as store:
                store.upsert('funding', [dict(symbol='XBTUSDTM', time_ms=60000, rate=.001, period_ms=28800000)])
            class Client:
                calls = []
                def klines(self, symbol, interval, start, end):
                    self.calls.append((symbol, interval, start, end))
                    return [dict(symbol=symbol, interval=interval, time_ms=at, open=100.,
                                 high=101., low=99., close=100., volume=1., turnover=100.)
                            for at in range(start, end, 60000)]
            client = Client()
            ingest_open_minutes(path, ['XBTUSDTM'], client, 360 * 60000)
            self.assertEqual(len(client.calls), 2)
            ingest_open_minutes(path, ['XBTUSDTM'], client, 360 * 60000)
            self.assertEqual(len(client.calls), 2)
            candles = candles_after(path, 'XBTUSDTM', 120000, 300000)
            self.assertEqual([r['ts_ms'] for r in candles], [180000, 240000, 300000])
            self.assertEqual(rates_at(path, 'XBTUSDTM', [60000, 120000]), {60000: .1, 120000: .1})

    def test_one_anonymous_all_tickers_call_and_strict_shape(self):
        fixture = json.loads((Path(__file__).parents[2] / 'tests/fixtures/autopilot/all_tickers.json').read_text())
        requests = []
        def opener(request, timeout):
            requests.append(request)
            return Response(fixture)
        client = PublicClient(opener=opener)
        rows = client.all_tickers()
        self.assertEqual(rows[0]['symbol'], 'XBTUSDTM')
        self.assertEqual(rows[0]['ts_ms'], 1729163466659)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].full_url, 'https://api-futures.kucoin.com/api/v1/allTickers')
        self.assertNotIn('Authorization', requests[0].headers)
        fixture['data'][0]['price'] = 'NaN'
        with self.assertRaises(ProtocolError):
            client.all_tickers()

    def test_invalid_numeric_ticker_cannot_poison_other_contracts(self):
        fixture={'code':'200000','data':[
            dict(symbol='ETHBTCUSDTM',ts=1729163466659000000,price='0'),
            dict(symbol='XBTUSDTM',ts=1729163466659000000,price='67153')]}
        client=PublicClient(opener=lambda *a,**k:Response(fixture))
        self.assertEqual([r['symbol'] for r in client.all_tickers()],['XBTUSDTM'])
        fixture['data'][1]['price']='NaN'
        with self.assertRaises(ProtocolError):client.all_tickers()

    def test_duplicate_identity_still_fails_even_when_first_price_invalid(self):
        fixture={'code':'200000','data':[
            dict(symbol='XBTUSDTM',ts=1729163466659000000,price='0'),
            dict(symbol='XBTUSDTM',ts=1729163466659000000,price='67153')]}
        with self.assertRaises(ProtocolError):
            PublicClient(opener=lambda *a,**k:Response(fixture)).all_tickers()

    def test_failed_tick_request_does_not_retry_inside_one_pass(self):
        requests = []
        def opener(*args, **kwargs):
            requests.append(1)
            raise OSError('not logged')
        with self.assertRaises(ProtocolError):
            PublicClient(opener=opener).all_tickers()
        self.assertEqual(len(requests), 1)


if __name__ == '__main__':
    unittest.main()
