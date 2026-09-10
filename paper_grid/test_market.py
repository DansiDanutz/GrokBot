import copy
import unittest
from unittest.mock import patch

from paper_grid import market as m

NOW = 1789084800 + 30


def contract(symbol='XBTUSDTM', high=115, low=95, turnover=2e6):
    return dict(symbol=symbol, quoteCurrency='USDT', settleCurrency='USDT', status='Open',
                isInverse=False, expireDate=None, lotSize=1, tickSize=0.01, multiplier=1,
                turnoverOf24h=turnover, highPrice=high, lowPrice=low, markPrice=100,
                fundingFeeRate=0.0001, fundingRateGranularity=28800000)


def ticker(symbol='XBTUSDTM'):
    return dict(symbol=symbol, bestBidPrice='99.99', bestAskPrice='100', ts=int(NOW*1e9),
                bestBidSize=1000, bestAskSize=1000)


def rows():
    start = NOW // 3600 * 3600 - 24 * 3600
    bars = [[int((start + i*3600)*1000), 102, 115, 95, 101, 10, 1000] for i in range(24)]
    bars[-1][1:5] = [99, 103, 97, 100]
    return bars


class MarketTests(unittest.TestCase):
    def test_timestamp_units(self):
        for multiplier in (1, 1000, 1_000_000, 1_000_000_000):
            self.assertAlmostEqual(m.epoch_seconds(NOW * multiplier), NOW)
        for value in (0, 'nan', float('inf')):
            with self.assertRaises(ValueError): m.epoch_seconds(value)

    def test_futures_ohlc_order_sort_dedup_and_incomplete(self):
        raw = rows()
        raw.append([int((NOW // 3600 * 3600)*1000), 1, 2, 1, 1, 10])
        result = m.completed_candles(list(reversed(raw)) + [raw[0]], NOW)
        self.assertEqual(len(result), 24)
        self.assertEqual(result[-1]['close'], 100)
        self.assertEqual(result[-1]['high'], 103)

    def test_gaps_stale_and_bad_rows_fail_closed(self):
        raw = rows()
        mutations = [raw[:-1], raw[1:], raw[:10] + raw[11:]]
        extra = copy.deepcopy(raw)
        extra[10][0] -= 1800 * 1000
        mutations.append(extra)
        for data in mutations:
            with self.assertRaises(m.MarketError): m.completed_candles(data, NOW)
        bad = copy.deepcopy(raw)
        bad[-1][2] = 50
        with self.assertRaises(m.MarketError): m.completed_candles(bad, NOW)

    def test_conflicting_duplicates_rejected(self):
        raw = rows()
        duplicate = list(raw[0]); duplicate[1] = 103
        with self.assertRaises(m.MarketError): m.completed_candles(raw + [duplicate], NOW)

    def test_ranks_range_not_price_change(self):
        a, b = contract('AAAUSDTM', 140, 100), contract('BBBUSDTM', 130, 100)
        a['priceChgPct'], b['priceChgPct'] = 0, .30
        low_liquidity = contract('CCCUSDTM', 200, 100, 10)
        inverse = contract('DDDUSDTM', 200, 100); inverse['isInverse'] = True
        ranking = m.rank_contracts([b, low_liquidity, inverse, a])
        self.assertEqual([c['symbol'] for c in ranking], ['AAAUSDTM', 'BBBUSDTM'])
        self.assertAlmostEqual(ranking[0]['range_pct'], 40)

    def test_entry_gates(self):
        # A 20-point high-low on 100 would breach ATR ceiling. Use a wider
        # historical range but narrow recent true range for this valid fixture.
        raw = rows()
        for row in raw[-15:]: row[1:5] = [101, 103, 99, 100]
        raw[-1][1:5] = [99.5, 101, 99, 100]
        candles = m.completed_candles(raw, NOW)
        result = m.analyze(contract(), ticker(), candles, NOW)
        self.assertTrue(result['eligible'], result['reasons'])
        self.assertEqual(result['quote_time'], NOW)
        for key, value, reason in [('ts', int((NOW-121)*1e9), 'stale_quote'),
                                   ('bestAskPrice', '101', 'wide_spread'),
                                   ('bestAskSize', 0, 'missing_top_depth')]:
            quote = ticker(); quote[key] = value
            result = m.analyze(contract(), quote, candles, NOW)
            self.assertFalse(result['eligible'])
            self.assertIn(reason, result['reasons'])
        candles[-1]['open'] = candles[-1]['close']
        self.assertIn('no_completed_rebound', m.analyze(contract(), ticker(), candles, NOW)['reasons'])

    def test_strong_downtrend_gated(self):
        bars = m.completed_candles(rows(), NOW)
        bars[0]['open'] = 130
        result = m.analyze(contract(), ticker(), bars, NOW)
        self.assertFalse(result['eligible'])
        self.assertIn('strong_downtrend', result['reasons'])

    def test_cached_day_still_refreshes_held_quotes(self):
        calls = []
        def get(path, params=None):
            calls.append((path, params))
            if path.endswith('/active'): return [contract(), contract('ETHUSDTM')]
            if path.endswith('/allTickers'): return [ticker(), ticker('ETHUSDTM')]
            return rows()
        previous = {'day': m.datetime.fromtimestamp(NOW, m.ZoneInfo('Europe/Bucharest')).date().isoformat(), 'top5':['XBTUSDTM'], 'scanned_at':NOW-100}
        with patch.object(m, 'public_get', side_effect=get):
            market, scan = m.collect(previous, ['ETHUSDTM'], NOW)
        self.assertEqual(set(market), {'XBTUSDTM', 'ETHUSDTM'})
        self.assertEqual(scan['scanned_at'], NOW-100)
        self.assertEqual(len(calls), 4)
        self.assertNotIn('errors', previous)

    def test_comparison_collects_all_four_held_symbols(self):
        names = ['XBTUSDTM', 'AAAUSDTM', 'BBBUSDTM', 'CCCUSDTM', 'DDDUSDTM']
        def get(path, params=None):
            if path.endswith('/active'): return [contract(s) for s in names]
            if path.endswith('/allTickers'): return [ticker(s) for s in names]
            return rows()
        previous = {'day': m.datetime.fromtimestamp(NOW, m.ZoneInfo('Europe/Bucharest')).date().isoformat(), 'top5': names[:1]}
        with patch.object(m, 'public_get', side_effect=get):
            quotes, _ = m.collect(previous, names[1:], NOW)
        self.assertEqual(set(quotes), set(names))

    def test_no_stale_quote_reuse_on_network_failure(self):
        with patch.object(m, 'public_get', side_effect=m.MarketError('public request failed')):
            market, scan = m.collect({'day':'2026-01-01','top5':['XBTUSDTM']}, ['XBTUSDTM'], NOW)
        self.assertEqual(market, {})
        self.assertIn('global', scan['errors'])
        self.assertEqual(scan['day'], '2026-01-01')

    def test_candle_failure_allows_quote_only_exit_evaluation(self):
        def get(path, params=None):
            if path.endswith('/active'): return [contract()]
            if path.endswith('/allTickers'): return [ticker()]
            raise m.MarketError('stale candle history')
        with patch.object(m, 'public_get', side_effect=get):
            market, scan = m.collect(None, ['XBTUSDTM'], NOW)
        self.assertFalse(market['XBTUSDTM']['eligible'])
        self.assertIs(type(market['XBTUSDTM']['lot_size']), int)
        self.assertEqual(market['XBTUSDTM']['bid_size'], 1000)
        self.assertEqual(market['XBTUSDTM']['ask_size'], 1000)
        self.assertEqual(market['XBTUSDTM']['bid'], 99.99)
        self.assertIn('XBTUSDTM', scan['errors'])

    def test_only_allowlisted_public_requests(self):
        with self.assertRaises(m.MarketError): m.public_get('/api/v1/orders')
        with self.assertRaises(m.MarketError): m.public_get('/api/v1/kline/query', {'symbol':'../../orders'})
        with self.assertRaises(m.MarketError): m.public_get('/api/v1/allTickers', {'token':'x'})

    def test_future_quote_rejected(self):
        quote = ticker(); quote['ts'] = int((NOW + 30)*1e9)
        result = m.analyze(contract(), quote, m.completed_candles(rows(), NOW), NOW)
        self.assertIn('stale_quote', result['reasons'])

    def test_missing_range_fallback_is_bounded(self):
        contracts = [contract(f'C{i}USDTM') for i in range(22)]
        for item in contracts:
            item.pop('highPrice')
        invalid = contract('BADUSDTM'); invalid['turnoverOf24h'] = 'broken'
        calls = []
        def get(path, params=None):
            if path.endswith('/active'): return contracts + [invalid]
            if path.endswith('/allTickers'): return [ticker(c['symbol']) for c in contracts]
            calls.append(params['symbol'])
            return rows()
        with patch.object(m, 'public_get', side_effect=get):
            market, scan = m.collect(None, [], NOW)
        self.assertEqual(scan['range_fallback_count'], 20)
        self.assertEqual(scan['range_unavailable_skipped'], 2)
        self.assertEqual(len(calls), 20)  # analyzed top five reuse fallback candles
        self.assertEqual(len(market), 5)

    def test_redirects_refused(self):
        with self.assertRaises(m.MarketError):
            m._NoRedirect().redirect_request(None, None, 302, '', {}, 'https://elsewhere.invalid')

    def test_integral_lot_validation(self):
        for value in (1, 1.0, '1'):
            self.assertIs(type(m.contract_lot_size(value)), int)
        for value in (True, 0, -1, 1.5, 'nan'):
            with self.assertRaises(ValueError): m.contract_lot_size(value)
            spec = contract(); spec['lotSize'] = value
            self.assertFalse(m.contract_valid(spec))

    def test_adapter_record_can_open_real_paper_engine(self):
        from paper_grid.engine import default_config, initial_state, step
        raw = rows()
        for row in raw[-15:]: row[1:5] = [101, 103, 99, 100]
        raw[-1][1:5] = [99.5, 101, 99, 100]
        spec = contract(); spec['lotSize'] = 1.0; spec['multiplier'] = 0.001
        record = m.analyze(spec, ticker(), m.completed_candles(raw, NOW), NOW)
        self.assertTrue(record['eligible'], record['reasons'])
        self.assertIs(type(record['lot_size']), int)
        config = default_config()
        state, events = step(initial_state(config, NOW), {record['symbol']: record}, NOW, config)
        self.assertIn(record['symbol'], state['positions'], events)
        self.assertGreater(state['positions'][record['symbol']]['contracts'], 0)

    def test_exact_90second_freshness_and_no_future_allowance(self):
        for age, expected_stale in ((90, False), (90.001, True), (-0.001, True)):
            quote = ticker(); quote['ts'] = int((NOW-age)*1e9)
            result = m.analyze(contract(), quote, m.completed_candles(rows(), NOW), NOW)
            self.assertEqual('stale_quote' in result['reasons'], expected_stale)

if __name__ == '__main__':
    unittest.main()
