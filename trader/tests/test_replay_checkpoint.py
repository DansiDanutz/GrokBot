import copy
import json
import unittest
from unittest.mock import patch

from trader.research.kucoin_replay import MODES, run_window
from trader.tests.test_kucoin_replay import MemorySnapshot, candidate, HOUR, START

IDENTITY = dict(source='source-sha', data='data-sha', registration='registration-sha')
FIXTURES = [dict(symbol=pair, mode='long', range_low=90, range_high=110,
                 grids_buy=5, grids_sell=5, leverage=5, margin_usdt=1200,
                 reserved_margin=200, entry_price=100) for pair in 'ABCD']


class Snapshot(MemorySnapshot):
    def records(self, at_ms, pairs=None):
        return [dict(pair=p, bars=[], market={}) for p in 'ABCD' if pairs is None or p in pairs]

    def candles(self, pair, start, end):
        return [dict(timestamp_ms=t, open=100, high=103, low=97, close=100)
                for t in range(start, end, 60000)]


def scanner(records, asof, running_pairs=(), parameters=None):
    records = list(records)
    return dict(asof_ms=asof, radar=[dict(candidate(r['pair']), score=10+(asof-START)//HOUR)
                for r in records if r['pair'] not in running_pairs],
                rejected=[], coverage={'observed': len(records)})


def profitable_scanner(*args, **kwargs):
    result = scanner(*args, **kwargs)
    for row in result['radar']:
        row['score'] = 1000
    return result


def loaded_scanner(*args, **kwargs):
    result = scanner(*args, **kwargs)
    for row in result['radar']:
        row['setup']['quantity'] = 3
    return result


def semantic(result):
    result = copy.deepcopy(result)
    result.pop('performance', None)
    return result


class CheckpointTests(unittest.TestCase):
    def resumed(self, factory, hours=4, mode='system', parameters=None, chunk=1):
        from trader.research.kucoin_replay import run_chunk
        packet, snapshots = None, []
        while True:
            result = run_chunk(factory(), START, START+hours*HOUR, parameters,
                mode=mode, fixtures=FIXTURES, max_hours=chunk, checkpoint=packet,
                identity=IDENTITY, checkpoint_callback=snapshots.append)
            packet = json.loads(json.dumps(result['checkpoint'], allow_nan=False))
            if result['complete']:
                return result['result'], snapshots
            self.assertIsNone(result['result'])
            self.assertLess(packet['cursor_ms'], START+hours*HOUR)

    @patch('trader.research.kucoin_replay.radar', side_effect=scanner)
    def test_every_mode_matches_uninterrupted_including_random_rng_and_scan_cache(self, unused):
        for mode in MODES:
            with self.subTest(mode=mode):
                expected = run_window(Snapshot(), START, START+4*HOUR, mode=mode, fixtures=FIXTURES)
                actual, packets = self.resumed(Snapshot, mode=mode)
                self.assertEqual(semantic(actual), semantic(expected))
                self.assertEqual(len(packets), 4)
                ids = [(row['bot_id'], row['event_id']) for row in actual['ledger']]
                self.assertEqual(len(ids), len(set(ids)))
                self.assertEqual(actual['start_ms'], START)
                self.assertEqual(actual['end_ms'], START+4*HOUR)

    @patch('trader.research.kucoin_replay.radar', side_effect=profitable_scanner)
    def test_boundary_close_cost_release_and_cash_wait_match(self, unused):
        class Losses(Snapshot):
            def candles(self, pair, start, end):
                return [dict(timestamp_ms=t, open=100, high=100, low=90, close=95)
                        for t in range(start, end, 60000)]
        expected = run_window(Losses(), START, START+3*HOUR)
        actual, _ = self.resumed(Losses, hours=3)
        self.assertEqual(semantic(actual), semantic(expected))
        self.assertTrue(any(row['kind'] == 'stop_loss' for row in actual['ledger']))
        self.assertLess(actual['ending_available_cash'], 2400)
        reasons = [row['reason'] for entry in actual['entry_decisions'] for row in entry['rejected']]
        reasons += [row['reason'] for decision in actual['portfolio_decisions']
                    for row in decision['rejected_candidates']]
        self.assertTrue(any('capital' in reason for reason in reasons))

    @patch('trader.research.kucoin_replay.radar', side_effect=scanner)
    def test_funding_and_historical_missing_minutes_match(self, unused):
        class Gaps(Snapshot):
            historical_candle_only = True
            def bounds(self):
                return START, START+10*HOUR
            def candles(self, pair, start, end):
                return [row for row in super().candles(pair, start, end)
                        if row['timestamp_ms'] % (3*60000)]
            def funding(self, pair, start, end):
                return []
        expected = run_window(Gaps(), START, START+9*HOUR, {'historical_candle_only': True})
        actual, _ = self.resumed(Gaps, hours=9, parameters={'historical_candle_only': True}, chunk=3)
        self.assertEqual(semantic(actual), semantic(expected))
        self.assertGreater(actual['coverage']['execution_missing_minutes'], 0)
        self.assertGreater(actual['coverage']['unknown_funding_settlements'], 0)

    @patch('trader.research.kucoin_replay.radar', side_effect=loaded_scanner)
    def test_liquidation_checkpoint_is_terminal_and_never_reopens(self, unused):
        from trader.research.kucoin_replay import run_chunk
        class Liquidation(Snapshot):
            def candles(self, pair, start, end):
                return [dict(timestamp_ms=t, open=1, high=100, low=1, close=100)
                        for t in range(start, end, 60000)]
        expected = run_window(Liquidation(), START, START+3*HOUR)
        result = run_chunk(Liquidation(), START, START+3*HOUR, identity=IDENTITY, max_hours=1)
        self.assertTrue(result['complete'])
        self.assertEqual(result['result']['status'], 'failed_liquidation')
        self.assertEqual(semantic(result['result']), semantic(expected))
        with patch.object(Liquidation, 'candles', side_effect=AssertionError('terminal replay read')):
            again = run_chunk(Liquidation(), START, START+3*HOUR, identity=IDENTITY,
                              checkpoint=result['checkpoint'])
        self.assertEqual(semantic(again['result']), semantic(expected))

    @patch('trader.research.kucoin_replay.radar', side_effect=scanner)
    def test_checkpoint_binds_identity_parameters_window_seed_and_fixtures(self, unused):
        from trader.research.kucoin_replay import run_chunk
        packet = run_chunk(Snapshot(), START, START+3*HOUR, max_hours=1,
                           fixtures=FIXTURES, identity=IDENTITY)['checkpoint']
        for changed in (dict(identity=dict(IDENTITY, data='different')), dict(parameters={'minimum_grid_net_usdt': 1}),
                        dict(end_ms=START+4*HOUR), dict(seed=42), dict(mode='random_radar_identical_rules'),
                        dict(fixtures=[dict(row, entry_price=99) for row in FIXTURES])):
            arguments = dict(start_ms=START, end_ms=START+3*HOUR, fixtures=FIXTURES,
                             identity=IDENTITY, checkpoint=packet)
            arguments.update(changed)
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, 'binding|identity'):
                run_chunk(Snapshot(), **arguments)
        damaged = copy.deepcopy(packet)
        damaged.pop('payload')
        with self.assertRaises(ValueError):
            run_chunk(Snapshot(), START, START+3*HOUR, fixtures=FIXTURES, identity=IDENTITY, checkpoint=damaged)
        with self.assertRaises(ValueError):
            run_chunk(Snapshot(), START, START+3*HOUR, identity={})

    @patch('trader.research.kucoin_replay.radar', side_effect=scanner)
    def test_checkpoint_json_contains_no_snapshot_or_trusted_prepared_objects(self, unused):
        from trader.research.kucoin_replay import run_chunk
        from trader.research.replay_checkpoint import encode_value, decode_value
        from trader.strategies.grid_types import GridConfig, GridState, Position, Order, FillEvent
        state = GridState(config=GridConfig(pair='A', low=90, high=110), price=100, timestamp_ms=START,
            positions=(Position(1, 1, 1, 100),), orders=(Order(2, -1, 102, True),),
            fill_events=(FillEvent(1, START, 1, 1, 1, 100, .06, 'seed'),))
        original = dict(state=state, seen={1, 2}, tuple=(1, None, False), reserved={'__replay_type__': 'plain'})
        self.assertEqual(decode_value(json.loads(json.dumps(encode_value(original)))), original)
        with self.assertRaises(ValueError):
            encode_value(Snapshot())
        with self.assertRaises(ValueError):
            decode_value({'__replay_type__': 'PreparedHistory', 'fields': {}})
        result = run_chunk(Snapshot(), START, START+2*HOUR, max_hours=1, identity=IDENTITY)
        encoded = json.dumps(result['checkpoint'], allow_nan=False)
        self.assertNotIn('PreparedHistory', encoded)
        self.assertNotIn('connection', encoded)


    @patch('trader.research.kucoin_replay.radar', side_effect=scanner)
    def test_mid_hour_interruption_preserves_last_committed_packet(self, unused):
        from trader.research.kucoin_replay import run_chunk
        first = run_chunk(Snapshot(), START, START+4*HOUR, identity=IDENTITY, max_hours=1)
        saved = json.dumps(first['checkpoint'], sort_keys=True)
        class Interrupted(Snapshot):
            def candles(self, pair, start, end):
                if pair == 'B':
                    raise RuntimeError('external interruption surrogate')
                return super().candles(pair, start, end)
        callbacks = []
        with self.assertRaisesRegex(RuntimeError, 'interruption'):
            run_chunk(Interrupted(), START, START+4*HOUR, identity=IDENTITY,
                      max_hours=1, checkpoint=first['checkpoint'], checkpoint_callback=callbacks.append)
        self.assertEqual(callbacks, [])
        self.assertEqual(json.dumps(first['checkpoint'], sort_keys=True), saved)
        recovered = run_chunk(Snapshot(), START, START+4*HOUR, identity=IDENTITY,
                              checkpoint=first['checkpoint'], max_hours=3)
        expected = run_window(Snapshot(), START, START+4*HOUR)
        self.assertEqual(semantic(recovered['result']), semantic(expected))

    @patch('trader.research.kucoin_replay.radar', side_effect=scanner)
    def test_known_funding_across_chunk_boundary_is_applied_once(self, unused):
        class Funded(Snapshot):
            def funding(self, pair, start, end):
                return [dict(row, rate=.001) for row in super().funding(pair, start, end)]
        expected = run_window(Funded(), START, START+9*HOUR)
        actual, _ = self.resumed(Funded, hours=9, chunk=4)
        self.assertEqual(semantic(actual), semantic(expected))
        settlements = [row for row in actual['ledger'] if row['kind'] == 'funding']
        self.assertTrue(settlements)
        self.assertTrue(any(row['cash'] != 0 for row in settlements))

    @patch('trader.research.kucoin_replay.radar', side_effect=scanner)
    def test_prepared_cache_records_are_reminted_without_serializing_trust(self, unused):
        from trader.research.kucoin_replay import run_chunk
        from trader.strategies.candle_coverage import validate_history, prepare_validated
        class PreparedSnapshot(Snapshot):
            def records(self, at_ms, pairs=None):
                rows = super().records(at_ms, pairs)
                observed = [dict(timestamp_ms=at_ms-60000, open=100, high=101, low=99, close=100, volume=1)]
                prepared = prepare_validated(validate_history(observed), at_ms, window_minutes=1)
                return [dict(row, prepared=prepared) for row in rows]
        first = run_chunk(PreparedSnapshot(), START, START+2*HOUR, identity=IDENTITY, max_hours=1)
        packet = json.loads(json.dumps(first['checkpoint'], allow_nan=False))
        self.assertNotIn('PreparedHistory', json.dumps(packet))
        actual = run_chunk(PreparedSnapshot(), START, START+2*HOUR, identity=IDENTITY, checkpoint=packet)
        expected = run_window(PreparedSnapshot(), START, START+2*HOUR)
        self.assertEqual(semantic(actual['result']), semantic(expected))

    @patch('trader.research.kucoin_replay.radar', side_effect=scanner)
    def test_schema_payload_damage_and_invalid_chunk_size_fail_before_execution(self, unused):
        from trader.research.kucoin_replay import run_chunk
        packet = run_chunk(Snapshot(), START, START+2*HOUR, identity=IDENTITY, max_hours=1)['checkpoint']
        for key, value in (('schema_version', 999), ('cursor_ms', START), ('payload', {}), ('sha256', 'bad')):
            altered = dict(packet, **{key: value})
            with self.subTest(key=key), self.assertRaises(ValueError):
                run_chunk(Snapshot(), START, START+2*HOUR, identity=IDENTITY, checkpoint=altered)
        for size in (0, -1, True, 1.5):
            with self.subTest(size=size), self.assertRaises(ValueError):
                run_chunk(Snapshot(), START, START+2*HOUR, identity=IDENTITY, max_hours=size)


    @patch('trader.research.kucoin_replay.radar', side_effect=scanner)
    def test_checkpoints_follow_decisions_and_corrupt_rng_raises_before_market_reads(self, unused):
        from trader.research.kucoin_replay import run_chunk
        from trader.research.replay_checkpoint import restore_checkpoint, _digest
        first = run_chunk(Snapshot(), START, START+3*HOUR, identity=IDENTITY, max_hours=1)
        raw, control, cache = restore_checkpoint(first['checkpoint'], first['checkpoint']['binding'])
        self.assertEqual(raw['portfolio_decisions'][-1]['asof_ms'], first['checkpoint']['cursor_ms'])
        self.assertEqual(control['cursor_ms'], START+HOUR)
        packet = copy.deepcopy(first['checkpoint'])
        packet['payload']['execution']['rng_state'] = 'invalid'
        packet['sha256'] = _digest({key: value for key, value in packet.items() if key != 'sha256'})
        with patch.object(Snapshot, 'records', side_effect=AssertionError('unexpected data read')):
            with self.assertRaises(ValueError):
                run_chunk(Snapshot(), START, START+3*HOUR, identity=IDENTITY, checkpoint=packet)

    @patch('trader.research.kucoin_replay.radar', side_effect=scanner)
    def test_incomplete_hour_data_error_never_publishes_a_resumable_checkpoint(self, unused):
        from trader.research.kucoin_replay import run_chunk
        class Missing(Snapshot):
            def candles(self, pair, start, end):
                return [] if start >= START+HOUR else super().candles(pair, start, end)
        first = run_chunk(Missing(), START, START+3*HOUR, identity=IDENTITY, max_hours=1)
        callbacks = []
        result = run_chunk(Missing(), START, START+3*HOUR, identity=IDENTITY,
                           checkpoint=first['checkpoint'], checkpoint_callback=callbacks.append)
        self.assertTrue(result['complete'])
        self.assertIsNone(result['checkpoint'])
        self.assertEqual(callbacks, [])
        self.assertEqual(semantic(result['result']), semantic(run_window(Missing(), START, START+3*HOUR)))


    @patch('trader.research.kucoin_replay.radar', side_effect=scanner)
    def test_streaming_reader_resumes_identically_with_causal_cache_rehydration(self, unused):
        class Streamed(Snapshot):
            def iter_records(self, at_ms, pairs=None):
                return iter(self.records(at_ms, pairs))
        expected = run_window(Streamed(), START, START+3*HOUR, mode='random_radar_identical_rules', fixtures=FIXTURES)
        actual, _ = self.resumed(Streamed, hours=3, mode='random_radar_identical_rules')
        self.assertEqual(semantic(actual), semantic(expected))
        self.assertGreater(actual['scan_statistics']['cache_hits'], 0)
