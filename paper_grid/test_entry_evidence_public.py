"""Publisher accepts pending accounting while stripping private entry details."""
from copy import deepcopy
import json
import unittest

from paper_grid import public_autopilot
from paper_grid import test_public_snapshot as publisher_fixture
from trader.autopilot import policy
from trader.tests.test_autopilot_policy import row, radar

AT, SECRET = publisher_fixture.AT, publisher_fixture.SECRET


def pending_snapshot():
    state, _ = policy.decide(policy.new_state(0), radar(long=[row('TESTUSDTM')]), {}, 1000, 'entry')
    wrapper = state['open_bots'].pop()
    wrapper['engine'].update(closed_ms=2000, reason='RANGE_BREAK',
        funding_schedule_status='PENDING_RECONCILIATION',
        accounting_status='PENDING_FUNDING_RECONCILIATION', recovery_incomplete_at_close=True)
    state['closed_bots'].append(wrapper)
    value = policy.snapshot(state, 2000, {})
    value.update(funding_reconciliation_pending=1, funding_reconciliation_status='PENDING',
                 recovery_reconciliation_pending=1, recovery_reconciliation_status='PENDING',
                 setup_evidence_archive_status='COMPLETE')
    value['closed_bots'][0]['setup_evidence']['private'] = SECRET
    value['closed_bots'][0]['setup_evidence']['costs']['private'] = SECRET
    value['closed_bots'][0]['setup_evidence']['range_evidence']['private'] = SECRET
    value['closed_bots'][0]['setup_evidence']['coinglass_liquidation_clusters']['private'] = SECRET
    return value


class EntryPublicTests(unittest.TestCase):
    def test_compact_projection_carries_provenance_and_unknown_costs(self):
        source = pending_snapshot()
        before = deepcopy(source)
        result = public_autopilot.safe(source)
        evidence = result['closed_bots'][0]['setup_evidence']
        self.assertEqual(evidence['evidence_id'], source['closed_bots'][0]['setup_evidence']['evidence_id'])
        self.assertEqual(evidence['range_provenance']['candles_sha256'], 'a'*64)
        self.assertEqual(evidence['costs']['funding_4h']['status'], 'UNKNOWN')
        self.assertIsNone(evidence['costs']['funding_4h']['constant_rate_seed_position_cost_usdt'])
        self.assertEqual(evidence['costs']['break_even']['status'], 'CONDITIONAL')
        self.assertIsNone(evidence['costs']['break_even']['including_spread_adverse_funding_4h_completed_pairs'])
        self.assertEqual(evidence['coinglass_liquidation_clusters']['status'], 'UNAVAILABLE_NOT_WIRED')
        self.assertIsNone(evidence['coinglass_liquidation_clusters']['cluster_levels'])
        self.assertNotIn('grid_lines', evidence['layout'])
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertEqual(before, source)
        self.assertLess(len(json.dumps(evidence)), 8000)

    def test_rejects_invalid_status_hash_and_boolean(self):
        for path, value in [(('accounting_status',), SECRET),
                            (('recovery_incomplete_at_close',), 1),
                            (('setup_evidence', 'evidence_id'), SECRET)]:
            source = pending_snapshot()
            target = source['closed_bots'][0]
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            with self.assertRaises(ValueError):
                public_autopilot.safe(source)
        source = pending_snapshot()
        source['funding_reconciliation_status'] = 'NO_RECORDED_FAILURE'
        with self.assertRaises(ValueError):
            public_autopilot.safe(source)
        source = pending_snapshot()
        source['closed_bots'][0]['setup_evidence']['coinglass_liquidation_clusters']['status'] = 'AVAILABLE'
        with self.assertRaises(ValueError):
            public_autopilot.safe(source)


class EntryPublisherTests(unittest.TestCase):
    setUp = publisher_fixture.PublicSnapshotTests.setUp
    export = publisher_fixture.PublicSnapshotTests.export

    def test_pending_accounting_exports_and_private_fields_are_removed(self):
        source = self.root / 'autopilot.json'
        source.write_text(json.dumps(pending_snapshot()))
        self.export(autopilot_path=source)
        published = json.loads((self.root / 'site/data/autopilot.json').read_text())
        bot = published['closed_bots'][0]
        self.assertEqual(bot['accounting_status'], 'PENDING_FUNDING_RECONCILIATION')
        self.assertEqual(bot['funding_schedule_status'], 'PENDING_RECONCILIATION')
        self.assertTrue(bot['recovery_incomplete_at_close'])
        self.assertEqual(published['funding_reconciliation_pending'], 1)
        self.assertEqual(published['recovery_reconciliation_status'], 'PENDING')
        self.assertEqual(published['setup_evidence_archive_status'], 'COMPLETE')
        self.assertEqual(published['published_at_ms'], AT*1000)
        self.assertNotIn(SECRET, json.dumps(published))


if __name__ == '__main__':
    unittest.main()
