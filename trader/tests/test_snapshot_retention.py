"""Cache-retention regressions using only deterministic in-memory source APIs."""
from contextlib import closing
import json
import unittest

from trader.research.kucoin_radar import radar
from trader.research.kucoin_replay import run_window
from trader.research.kucoin_snapshot import HistoricalSnapshot


MINUTE, HOUR, DAY = 60000, 3600000, 86400000
NOW = 100 * DAY
HEAVY = ('_cache', '_history', '_prepared_cache', '_record_cache', '_quote_cache')


class Rows(list):
    def fetchone(self):
        return self[0] if self else None


class MemoryConnection:
    def __init__(self, ranges):
        self.ranges = ranges

    def execute(self, query, arguments=()):
        if 'GROUP BY symbol' in query:
            return Rows((pair, start, end-MINUTE) for pair, (start, end, step) in self.ranges.items())
        if 'SELECT raw_json' in query:
            return Rows([dict(raw_json=json.dumps(dict(tickSize=.01, lotSize=1, multiplier=1,
                quoteCurrency='USDT', expireDate=None, isInverse=False, assetClass='CRYPTO')),
                observed_at_ms=NOW)])
        raise AssertionError('Unexpected source query: '+query)


class MemorySource:
    manifest = {'offline_copy': True, 'source': 'kucoin-public', 'candle_volume_unit': 'contracts'}

    def __init__(self, ranges):
        self.ranges = ranges
        self.connection = MemoryConnection(ranges)
        self.reads = []

    def market_bounds(self):
        return None, None

    def candles(self, pair, start, end):
        self.reads.append((pair, start, end))
        first, last, step = self.ranges[pair]
        begin = max(first, start)
        begin += (first-begin) % step
        return [dict(symbol=pair, time_ms=t, open=100, high=101, low=99,
                     close=100, volume=1000, turnover=100000)
                for t in range(begin, min(end, last), step)]

    def funding(self, pair, start, end):
        return []


class RetainedReference(HistoricalSnapshot):
    def _release_invalid_record(self, pair, record):
        pass


def snapshot(kind=HistoricalSnapshot, sparse=1, valid=False):
    ranges = {f'BAD{i:03d}USDTM': (NOW-8*DAY, NOW+10*DAY, 15*MINUTE) for i in range(sparse)}
    if valid:
        ranges['GOODUSDTM'] = NOW-8*DAY, NOW+10*DAY, MINUTE
    return kind(MemorySource(ranges))


class SnapshotRetentionTests(unittest.TestCase):
    def assert_released(self, data, pair):
        for name in HEAVY:
            self.assertNotIn(pair, tuple(getattr(data, name)), name)
        self.assertIn(pair, data._metadata_cache)

    def test_invalid_record_releases_all_heavy_caches_but_remains_usable(self):
        data = snapshot()
        pair = 'BAD000USDTM'
        data.history(pair, NOW)
        record = data.records(NOW)[0]
        self.assertFalse(record['prepared']['valid'])
        self.assert_released(data, pair)
        self.assertEqual(len(record['prepared']['bars']), 10080)
        self.assertEqual(record['prepared']['coverage']['actual'], 672)
        self.assertEqual(record['bars'][0]['close'], 100)
        before = len(data.source.reads)
        reloaded = data.records(NOW)[0]
        self.assertGreater(len(data.source.reads), before)
        self.assertEqual(record, reloaded)

    def test_rejected_only_stream_retains_at_most_current_pair(self):
        data = snapshot(sparse=40)
        for record in data.iter_records(NOW):
            self.assertFalse(record['prepared']['valid'])
            for name in HEAVY:
                self.assertLessEqual(len(getattr(data, name)), 1, name)
        self.assertTrue(all(not getattr(data, name) for name in HEAVY))
        self.assertEqual(len(data._metadata_cache), 40)

    def test_valid_history_keeps_same_time_and_incremental_reuse(self):
        data = snapshot(sparse=0, valid=True)
        first = data.records(NOW)[0]
        self.assertTrue(first['prepared']['valid'])
        reads = len(data.source.reads)
        self.assertIs(data.records(NOW)[0], first)
        later = data.records(NOW+HOUR)[0]
        self.assertEqual(len(data.source.reads), reads)
        self.assertIs(later['prepared']['bars'][1], first['prepared']['bars'][61])
        self.assertIn('GOODUSDTM', data._prepared_cache)

    def test_generator_close_throw_and_consumer_failure_release_invalid(self):
        for action in ('close', 'throw', 'consumer'):
            with self.subTest(action=action):
                data = snapshot()
                iterator = data.iter_records(NOW)
                record = next(iterator)
                self.assertIn(record['pair'], data._prepared_cache)
                if action == 'close':
                    iterator.close()
                elif action == 'throw':
                    with self.assertRaisesRegex(RuntimeError, 'interrupted'):
                        iterator.throw(RuntimeError('interrupted'))
                else:
                    with self.assertRaisesRegex(RuntimeError, 'consumer'):
                        with closing(iterator):
                            raise RuntimeError('consumer')
                self.assert_released(data, record['pair'])
                self.assertEqual(record['prepared']['coverage']['actual'], 672)

    def test_later_eligibility_reloads_and_then_retains_valid_history(self):
        pair = 'NEWUSDTM'
        data = HistoricalSnapshot(MemorySource({pair: (NOW-DAY, NOW+10*DAY, MINUTE)}))
        self.assertFalse(data.records(NOW)[0]['prepared']['valid'])
        self.assert_released(data, pair)
        later = data.records(NOW+7*DAY)[0]
        self.assertTrue(later['prepared']['valid'])
        self.assertIs(data._record_cache[pair][1], later)

    def test_closing_old_generator_does_not_evict_newer_valid_history(self):
        pair = 'NEWUSDTM'
        data = HistoricalSnapshot(MemorySource({pair: (NOW-DAY, NOW+10*DAY, MINUTE)}))
        iterator = data.iter_records(NOW)
        first = next(iterator)
        self.assertFalse(first['prepared']['valid'])
        newer = data.records(NOW+7*DAY)[0]
        iterator.close()
        self.assertTrue(newer['prepared']['valid'])
        self.assertIs(data._record_cache[pair][1], newer)

    def test_full_radar_outputs_match_retained_reference_on_repeated_scans(self):
        bounded, retained = snapshot(valid=True), snapshot(RetainedReference, valid=True)
        for at in (NOW, NOW+HOUR, NOW+2*HOUR, NOW):
            self.assertEqual(radar(bounded.iter_records(at), at), radar(retained.iter_records(at), at))

    def test_full_replay_outputs_match_retained_reference(self):
        options = {'historical_candle_only': True}
        bounded = run_window(snapshot(sparse=2), NOW, NOW+2*HOUR, options)
        retained = run_window(snapshot(RetainedReference, sparse=2), NOW, NOW+2*HOUR, options)
        bounded.pop('performance', None)
        retained.pop('performance', None)
        self.assertEqual(bounded, retained)
        self.assertEqual(bounded['completed_hours'], 2)


if __name__ == '__main__':
    unittest.main()
