import copy
import io
import json
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

from paper_grid import coinglass as c

NOW = 1789084800 + 180


def rows(count=49, total=400, share=.75):
    start = NOW // c.HOUR * c.HOUR - count * c.HOUR
    result = [dict(time=int((start + i * c.HOUR) * 1000),
                   aggregated_long_liquidation_usd=50,
                   aggregated_short_liquidation_usd=50) for i in range(count)]
    result[-1].update(aggregated_long_liquidation_usd=total * share,
                      aggregated_short_liquidation_usd=total * (1 - share))
    return result


class CoinGlassTests(unittest.TestCase):
    def test_burst_uses_prior_hours_only_and_sorted_rows(self):
        feature = c.analyze(list(reversed(rows())), NOW)
        self.assertTrue(feature['eligible'])
        self.assertEqual(feature['baseline_hours'], 48)
        self.assertEqual(feature['burst_ratio'], 4)
        self.assertEqual(feature['long_share'], .75)

    def test_thresholds_and_minimum_sample(self):
        self.assertTrue(c.analyze(rows(25, 300, .6), NOW)['eligible'])
        self.assertFalse(c.analyze(rows(total=299), NOW)['eligible'])
        self.assertFalse(c.analyze(rows(share=.599), NOW)['eligible'])
        with self.assertRaises(c.CoinGlassError): c.analyze(rows(24), NOW)

    def test_incomplete_hour_is_excluded_not_used_as_signal(self):
        raw = rows(48, total=100)
        raw.append(dict(time=int(NOW // c.HOUR * c.HOUR * 1000),
                        aggregated_long_liquidation_usd=1000000,
                        aggregated_short_liquidation_usd=0))
        self.assertFalse(c.analyze(raw, NOW)['eligible'])

    def test_duplicate_gap_future_unaligned_and_stale_rejected(self):
        valid = rows(48)
        duplicate = valid + [valid[-1]]
        gap = valid[:5] + valid[6:]
        future = copy.deepcopy(valid); future[-1]['time'] += 2 * c.HOUR * 1000
        unaligned = copy.deepcopy(valid); unaligned[-1]['time'] += 1000
        stale = copy.deepcopy(valid)
        for row in stale: row['time'] -= 3 * c.HOUR * 1000
        for raw in (duplicate, gap, future, unaligned, stale):
            with self.subTest(raw=raw[-1]), self.assertRaises(c.CoinGlassError): c.analyze(raw, NOW)

    def test_bad_numbers_zeros_and_body_shapes_rejected(self):
        for value in (-1, float('nan'), float('inf'), True, None):
            raw = rows(); raw[10]['aggregated_long_liquidation_usd'] = value
            with self.subTest(value=value), self.assertRaises(c.CoinGlassError): c.analyze(raw, NOW)
        with self.assertRaises(c.CoinGlassError): c.analyze(rows(total=0), NOW)
        raw = rows()
        for row in raw[:-1]:
            row['aggregated_long_liquidation_usd'] = row['aggregated_short_liquidation_usd'] = 0
        for bad in (raw, {}, rows(50), [{}] * 25):
            with self.assertRaises(c.CoinGlassError): c.analyze(bad, NOW)

    def test_symbol_mapping_and_invalid_path_rejected(self):
        self.assertEqual(c.underlying('XBTUSDTM'), 'BTC')
        self.assertEqual(c.underlying('2U2USDTM'), '2U2')
        for value in ('BTC', '../../BTCUSDTM', 'btcusdtm', 'USDTM', None):
            with self.assertRaises(c.CoinGlassError): c.underlying(value)

    def test_collect_serial_bounded_requests_and_missing_key(self):
        fetch = Mock(return_value=rows())
        names = [f'C{i}USDTM' for i in range(c.MAX_SYMBOLS + 1)]
        result = c.collect(names, NOW, api_key='fixture', getter=fetch)
        self.assertEqual(fetch.call_count, c.MAX_SYMBOLS)
        self.assertFalse(result['symbols'][names[-1]]['eligible'])
        self.assertEqual(len(result['errors']), 1)
        with patch.object(c, 'read_api_key', side_effect=c.CoinGlassError('API key unavailable')):
            result = c.collect(['XBTUSDTM'], NOW, getter=Mock(side_effect=AssertionError('no request')))
        self.assertFalse(result['symbols']['XBTUSDTM']['eligible'])

    def test_errors_do_not_leak_injected_transport_details(self):
        result = c.collect(['XBTUSDTM'], NOW, api_key='secret_fixture',
                           getter=Mock(side_effect=RuntimeError('secret_fixture')))
        self.assertNotIn('secret_fixture', json.dumps(result))
        self.assertFalse(result['symbols']['XBTUSDTM']['eligible'])

    def test_cache_reused_only_when_covered_and_younger_than_30_minutes(self):
        fetch = Mock(return_value=rows())
        cache = c.collect(['XBTUSDTM'], NOW, api_key='fixture', getter=fetch)
        result = c.collect(['XBTUSDTM'], NOW + 1799, cache=cache, api_key='fixture', getter=fetch)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(result['fetched_at'], NOW)
        result['symbols']['XBTUSDTM']['eligible'] = False
        self.assertTrue(cache['symbols']['XBTUSDTM']['eligible'])
        c.collect(['XBTUSDTM'], NOW + 1800, cache=cache, api_key='fixture', getter=fetch)
        self.assertEqual(fetch.call_count, 2)
        c.collect(['ETHUSDTM'], NOW + 5, cache=cache, api_key='fixture', getter=fetch)
        self.assertEqual(fetch.call_count, 3)
        c.collect(['XBTUSDTM'], NOW - 1, cache=cache, api_key='fixture', getter=fetch)
        self.assertEqual(fetch.call_count, 4)

    def test_filter_blocks_new_risk_preserves_exit_quotes_and_original(self):
        quote = dict(symbol='XBTUSDTM', eligible=True, add_eligible=True,
                     bid=100, ask=101, bid_size=5, ask_size=6, reasons=[])
        quotes = {'XBTUSDTM': quote}
        features = c.collect(['XBTUSDTM'], NOW, api_key='fixture', getter=Mock(return_value=rows()))
        result = c.apply_filter(quotes, features, NOW)
        self.assertEqual(result, quotes)
        for missing in (None, {}, {'fetched_at': NOW, 'symbols': {}}, features):
            at = NOW + 1800 if missing is features else NOW
            blocked = c.apply_filter(quotes, missing, at)['XBTUSDTM']
            self.assertFalse(blocked['eligible'])
            self.assertFalse(blocked['add_eligible'])
            self.assertEqual([blocked[k] for k in ('bid', 'ask', 'bid_size', 'ask_size')], [100, 101, 5, 6])
        self.assertTrue(quote['eligible'])
        self.assertEqual(quote['reasons'], [])

    def test_filter_never_enables_rejected_base_and_rechecks_feature_integrity(self):
        quotes = [dict(symbol='XBTUSDTM', eligible=False, add_eligible=False, reasons=['base_reject'])]
        features = c.collect(['XBTUSDTM'], NOW, api_key='fixture', getter=Mock(return_value=rows()))
        self.assertFalse(c.apply_filter(quotes, features, NOW)[0]['eligible'])
        quotes[0].update(eligible=True, add_eligible=True)
        for field, value in [('burst_ratio', 2), ('long_share', .5), ('long_share', 2),
                             ('total_usd', 0), ('total_usd', float('nan')),
                             ('latest_hour', NOW), ('latest_hour', NOW - 5 * c.HOUR)]:
            invalid = copy.deepcopy(features)
            invalid['symbols']['XBTUSDTM'][field] = value
            with self.subTest(field=field, value=value):
                self.assertFalse(c.apply_filter(quotes, invalid, NOW)[0]['eligible'])

    def test_key_parser_only_exact_assignment_no_real_file_reads(self):
        text = 'UNRELATED=hidden\nOTHER_COINGLASS_API_KEY=ignored\nCOINGLASS_API_KEY="fixture"\n'
        with patch.object(c.Path, 'open', return_value=io.StringIO(text)):
            self.assertEqual(c.read_api_key('/test/only'), 'fixture')
        for text in ('COINGLASS_API_KEY=\n', 'COINGLASS_API_KEY=a\nCOINGLASS_API_KEY=b\n'):
            with patch.object(c.Path, 'open', return_value=io.StringIO(text)), self.assertRaises(c.CoinGlassError):
                c.read_api_key('/test/only')

    def test_fixed_official_request_header_query_and_bounded_read(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = json.dumps(dict(code='0', data=rows())).encode()
        opener = Mock()
        opener.open.return_value = response
        with patch.object(c.urllib.request, 'build_opener', return_value=opener):
            self.assertEqual(len(c.request_history('XBTUSDTM', NOW, 'fixture')), 49)
        request = opener.open.call_args.args[0]
        parsed = urlparse(request.full_url)
        self.assertEqual(parsed.scheme, 'https')
        self.assertEqual(parsed.netloc, 'open-api-v4.coinglass.com')
        self.assertEqual(parsed.path, '/api/futures/liquidation/aggregated-history')
        query = parse_qs(parsed.query)
        self.assertEqual(query['symbol'], ['BTC'])
        self.assertEqual(query['limit'], ['49'])
        self.assertEqual(query['end_time'], [str(int(NOW * 1000))])
        self.assertEqual(request.get_header('Cg-api-key'), 'fixture')
        self.assertEqual(request.get_method(), 'GET')
        response.read.assert_called_once_with(c.MAX_BODY + 1)
        self.assertEqual(opener.open.call_args.kwargs['timeout'], 5)

    def test_redirect_and_invalid_responses_fail_closed(self):
        with self.assertRaises(c.CoinGlassError):
            c._NoRedirect().redirect_request(None, None, 302, '', {}, 'https://elsewhere.invalid')
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        opener = Mock(); opener.open.return_value = response
        for payload in (b'x' * (c.MAX_BODY + 1), b'invalid', b'{"code":"401","msg":"secret_fixture"}'):
            response.read.return_value = payload
            with patch.object(c.urllib.request, 'build_opener', return_value=opener):
                with self.assertRaises(c.CoinGlassError) as caught:
                    c.request_history('XBTUSDTM', NOW, 'fixture')
            self.assertNotIn('secret_fixture', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
