import copy
import json
from pathlib import Path
import unittest

HOUR = 3_600_000
ASOF = 60 * 24 * HOUR


def read(direction, timeframe, **changes):
    return dict(dict(timeframe=timeframe, direction=direction,
        regime={'up': 'up', 'down': 'down', 'neutral': 'range'}[direction],
        confidence=.9, available=True, asof_ms=ASOF, last_closed_ms=ASOF,
        reason='synthetic chart agreement', confidence_basis='heuristic, not probability'), **changes)


def frames(direction):
    return {timeframe: read(direction, timeframe) for timeframe in ('4h', '1d')}


def charts(btc='up', eth='up', sol='up'):
    return {'XBTUSDTM': frames(btc), 'ETHUSDTM': frames(eth), 'SOLUSDTM': frames(sol)}


def registry():
    evidence = dict(basis='explicit test fixture classification', sources=['fixture://membership'])
    return dict(schema_version=1, coverage_complete=False, reviewed_at='2026-09-11',
                historical_basis='retrospective classification',
                members={'RAYUSDTM': evidence}, non_members={'AAAUSDTM': evidence})


class RegimeTests(unittest.TestCase):
    def gate(self, pair='AAAUSDTM', reads=None, **kwargs):
        from trader.features.regime import gate_entry
        return gate_entry(pair, charts() if reads is None else reads, ASOF, registry(), **kwargs)

    def test_btc_eth_table_of_bull_bear_mixed_and_range(self):
        for btc, eth, expected in (('up', 'up', ['long', 'neutral']),
                ('down', 'down', ['short', 'neutral']), ('up', 'down', ['neutral']),
                ('down', 'up', ['neutral']), ('neutral', 'neutral', ['neutral']),
                ('up', 'neutral', ['neutral'])):
            with self.subTest(btc=btc, eth=eth):
                result = self.gate(reads=charts(btc, eth))
                self.assertEqual(result['allowed_directions'], expected)
                self.assertFalse(result['unknown'])
                self.assertEqual(result['scope'], 'new_entries_only')
                self.assertEqual(result['existing_positions_action'], 'none')
                self.assertEqual(len(result['reads']), 4)

    def test_disagreeing_four_hour_and_daily_is_neutral(self):
        inputs = charts()
        inputs['XBTUSDTM']['4h'] = read('down', '4h')
        result = self.gate(reads=inputs)
        self.assertEqual(result['allowed_directions'], ['neutral'])
        self.assertFalse(result['unknown'])
        self.assertIn('disagree', ' '.join(result['reasons']))

    def test_missing_low_confidence_stale_and_future_read_are_explicit_unknown(self):
        variations = (None, read('up', '4h', confidence=.74), read('up', '4h', confidence=None),
            read('up', '4h', available=False), read('up', '4h', last_closed_ms=ASOF+4*HOUR),
            read('up', '4h', last_closed_ms=ASOF-4*HOUR), read('up', '4h', asof_ms=ASOF+1),
            read('up', '4h', confidence=True), read('up', '4h', regime='down'))
        for value in variations:
            with self.subTest(read=value):
                inputs = charts()
                inputs['XBTUSDTM']['4h'] = value
                result = self.gate(reads=inputs)
                self.assertEqual(result['allowed_directions'], ['neutral'])
                self.assertTrue(result['unknown'])
                self.assertTrue(result['unknown_reasons'])
                self.assertFalse(result['reads'][0]['known'])

    def test_nonfinite_chart_confidence_stays_unknown_and_json_serializable(self):
        for confidence in (float('nan'), float('inf')):
            with self.subTest(confidence=confidence):
                inputs = charts()
                inputs['XBTUSDTM']['4h']['confidence'] = confidence
                result = self.gate(reads=inputs)
                self.assertEqual(result['allowed_directions'], ['neutral'])
                self.assertTrue(result['unknown'])
                json.dumps(result, allow_nan=False)

    def test_minimum_confidence_is_inclusive_and_validated(self):
        inputs = charts()
        inputs['XBTUSDTM']['4h']['confidence'] = .75
        self.assertEqual(self.gate(reads=inputs)['allowed_directions'], ['long', 'neutral'])
        for invalid in (None, True, -1, 1.1, float('nan'), '0.75'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.gate(minimum_confidence=invalid)

    def test_solana_intersection_never_reintroduces_macro_forbidden_direction(self):
        for macro, sol, expected in (('up', 'up', ['long', 'neutral']),
                ('down', 'down', ['short', 'neutral']), ('up', 'down', ['neutral']),
                ('down', 'up', ['neutral']), ('up', 'neutral', ['neutral'])):
            with self.subTest(macro=macro, sol=sol):
                result = self.gate('RAYUSDTM', charts(macro, macro, sol))
                self.assertEqual(result['allowed_directions'], expected)
                self.assertTrue(result['sol_gate']['applied'])
                self.assertEqual(len(result['reads']), 6)
                self.assertEqual(result['membership']['status'], 'member')
                self.assertTrue(result['membership']['sources'])

    def test_missing_sol_limits_member_but_does_not_constrain_known_nonmember(self):
        inputs = charts()
        del inputs['SOLUSDTM']
        member = self.gate('RAYUSDTM', inputs)
        self.assertEqual(member['allowed_directions'], ['neutral'])
        self.assertTrue(member['unknown'])
        nonmember = self.gate('AAAUSDTM', inputs)
        self.assertEqual(nonmember['allowed_directions'], ['long', 'neutral'])
        self.assertFalse(nonmember['unknown'])

    def test_unknown_membership_is_not_inferred_from_absence_or_symbol_spelling(self):
        for pair in ('UNKNOWNUSDTM', 'SOLGUESSUSDTM'):
            with self.subTest(pair=pair):
                result = self.gate(pair)
                self.assertEqual(result['allowed_directions'], ['long', 'neutral'])
                self.assertTrue(result['unknown'])
                self.assertEqual(result['membership']['status'], 'unclassified')
                self.assertFalse(result['sol_gate']['applied'])
                self.assertFalse(result['membership']['coverage_complete'])

    def test_conflicting_or_unsourced_membership_is_unknown(self):
        from trader.features.regime import gate_entry
        for document in (None, {}, dict(registry(), non_members={'RAYUSDTM': registry()['members']['RAYUSDTM']}),
                         dict(registry(), members={'RAYUSDTM': {'basis': 'claim', 'sources': []}})):
            with self.subTest(document=document):
                result = gate_entry('RAYUSDTM', charts(), ASOF, document)
                self.assertEqual(result['allowed_directions'], ['long', 'neutral'])
                self.assertTrue(result['unknown'])

    def test_sol_native_contract_always_applies_its_own_read(self):
        from trader.features.regime import gate_entry
        result = gate_entry('SOLUSDTM', charts('up', 'up', 'down'), ASOF)
        self.assertEqual(result['allowed_directions'], ['neutral'])
        self.assertTrue(result['sol_gate']['applied'])
        self.assertEqual(result['membership']['status'], 'member')

    def test_chart_five_cells_shape_and_requested_direction_are_supported(self):
        inputs = {pair: {'five_cells': value} for pair, value in charts('down', 'down').items()}
        result = self.gate(reads=inputs, requested_direction='long')
        self.assertFalse(result['requested_allowed'])
        self.assertEqual(result['requested_direction'], 'long')
        self.assertTrue(self.gate(reads=inputs, requested_direction='neutral')['requested_allowed'])
        with self.assertRaises(ValueError):
            self.gate(requested_direction='buy')

    def test_decision_is_pure_detached_and_json_serializable(self):
        inputs, membership = charts(), registry()
        before = copy.deepcopy((inputs, membership))
        from trader.features.regime import gate_entry
        result = gate_entry('RAYUSDTM', inputs, ASOF, membership)
        self.assertEqual((inputs, membership), before)
        inputs['XBTUSDTM']['4h']['reason'] = 'changed afterwards'
        self.assertNotIn('changed afterwards', json.dumps(result, allow_nan=False))
        self.assertTrue(all(item['raw_read'] for item in result['reads']))

    def test_registry_documents_partial_coverage_and_sourced_members(self):
        path = Path(__file__).resolve().parents[2]/'config/solana-ecosystem.json'
        document = json.loads(path.read_text())
        self.assertFalse(document['coverage_complete'])
        self.assertIn('RAYUSDTM', document['members'])
        self.assertIn('SOLUSDTM', document['members'])
        for section in ('members', 'non_members'):
            for entry in document[section].values():
                self.assertTrue(entry['basis'])
                self.assertTrue(entry['sources'])
                self.assertTrue(all(source.startswith('https://www.kucoin.com/') for source in entry['sources']))


if __name__ == '__main__':
    unittest.main()
