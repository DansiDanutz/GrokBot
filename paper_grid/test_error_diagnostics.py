"""Bounded private diagnostics without source text, locals or provider messages."""
import json
import unittest
from unittest.mock import patch
from paper_grid import test_experiment as fixtures


class ErrorDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.fx = fixtures.ExperimentTests()
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)

    def test_cycle_failure_keeps_last_twenty_frames_and_tick_without_private_values(self):
        def leaf(depth):
            private_local = 'do-not-record-this-value'
            if depth: return leaf(depth-1)
            raise RuntimeError(private_local)
        self.fx.begin()
        before = self.fx.doc()['accounts']
        self.fx.tick(fixtures.NOW+300, collector=lambda *a, **k: leaf(30))
        doc = self.fx.doc()
        error = doc['errors'][-1]
        self.assertEqual(error['tick_at'], fixtures.NOW+300)
        self.assertEqual(len(error['traceback']), 20)
        self.assertTrue(error['traceback_truncated'])
        self.assertGreater(error['traceback_frame_count'], 20)
        self.assertEqual(error['traceback'][-1]['function'], 'leaf')
        self.assertNotIn('do-not-record-this-value', json.dumps(doc))
        self.assertNotIn('/Users/', json.dumps(error))
        self.assertLess(len(json.dumps(error)), 4096)
        for arm in before:
            self.assertEqual(doc['accounts'][arm]['state'], before[arm]['state'])

    def test_feature_collection_failures_also_capture_the_attempt_timestamp(self):
        def fail(*args, **kwargs):
            raise ValueError('private-provider-body')
        self.fx.begin()
        self.fx.tick(fixtures.NOW+300, feature_collector=fail)
        details = [item for row in self.fx.doc()['errors'] for item in row.get('details', [])]
        self.assertTrue(details)
        for error in details:
            self.assertEqual(error['tick_at'], fixtures.NOW+300)
            self.assertTrue(error['traceback'])
            self.assertNotIn('private-provider-body', json.dumps(error))

    def test_external_frame_paths_and_custom_exception_names_are_not_recorded(self):
        from paper_grid.diagnostics import error_record
        namespace = {}
        exec(compile("def external():\n raise ValueError('private')", '/private/do-not-publish.py', 'exec'), namespace)
        try:
            namespace['external']()
        except Exception as error:
            record = error_record(error, fixtures.NOW)
        self.assertEqual(record['traceback'][-1]['file'], '<external>')
        self.assertNotIn('do-not-publish', json.dumps(record))
        custom = type('sensitive_custom_exception_name', (Exception,), {})()
        self.assertEqual(error_record(custom, fixtures.NOW)['type'], 'Exception')

    def test_source_seal_covers_extracted_diagnostic_and_telemetry_dependencies(self):
        from paper_grid import experiment
        hashes = experiment._code_hashes()
        self.assertIn('diagnostics.py', hashes)
        self.assertIn('telemetry_constants.py', hashes)
