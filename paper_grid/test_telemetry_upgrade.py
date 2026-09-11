"""Strict, atomic, paper-only seal migration; no production runtime access."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from paper_grid import cli, engine, experiment, telemetry_upgrade as migration
from paper_grid.test_cli import NOW


class TelemetryUpgradeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        c = engine.default_config()
        cli.atomic_json(self.root/'account.json', dict(schema=1, config=c,
                        config_hash=cli.config_fingerprint(c), state=engine.initial_state(c,NOW),
                        paused=True, scan={}, market={}, events=[]))
        experiment.start(self.root, now=NOW)
        experiment.continue_running(self.root, now=NOW+1)
        doc = self.doc()
        doc['code_hashes'] = deepcopy(migration.BASELINE_HASHES)
        doc['observations'] = [{'time': NOW, 'legacy': True}]
        doc['custom_preserve'] = {'evidence': [1,2,3]}
        cli.atomic_json(self.root/experiment.FILE, doc)

    def doc(self):
        return json.loads((self.root/experiment.FILE).read_text())

    def test_default_is_readonly_write_preserves_all_ledger_fields_and_is_idempotent(self):
        path = self.root/experiment.FILE
        original = path.read_bytes()
        self.assertEqual(migration.upgrade(self.root, now=NOW+2)['status'], 'eligible')
        self.assertEqual(path.read_bytes(), original)
        before = self.doc()
        self.assertTrue(migration.upgrade(self.root, now=NOW+2, paper=True)['written'])
        after = self.doc()
        for key, value in before.items():
            if key != 'code_hashes': self.assertEqual(after[key], value, key)
        self.assertEqual(after['code_hashes'], experiment._code_hashes())
        self.assertEqual(after['telemetry_upgrades'][0]['time'], NOW+2)
        original = path.read_bytes()
        self.assertEqual(migration.upgrade(self.root, now=NOW+3, paper=True)['status'], 'already_upgraded')
        self.assertEqual(path.read_bytes(), original)

    def test_unknown_seal_changed_config_frozen_and_bounded_fail_closed(self):
        good = self.doc()
        changes = [dict(code_hashes={'unknown':'seal'}), dict(status='frozen', freeze_reason='manual'),
                   dict(config_hash='different'), dict(continuous=False, end_at=NOW+100),
                   dict(telemetry_upgrades=[{'kind':'unknown'}])]
        for fields in changes:
            with self.subTest(fields=fields):
                cli.atomic_json(self.root/experiment.FILE, dict(good, **fields))
                original = (self.root/experiment.FILE).read_bytes()
                with self.assertRaises(ValueError): migration.upgrade(self.root, now=NOW+2, paper=True)
                self.assertEqual((self.root/experiment.FILE).read_bytes(), original)

    def test_unknown_current_source_and_missing_boundary_rejected(self):
        with patch.object(experiment, '_code_hashes', return_value={'modified':'source'}):
            with self.assertRaises(ValueError): migration.upgrade(self.root, now=NOW+2, paper=True)
        doc = self.doc()
        doc['code_hashes'] = deepcopy(migration.TARGET_HASHES)
        cli.atomic_json(self.root/experiment.FILE, doc)
        with self.assertRaises(ValueError): migration.upgrade(self.root, now=NOW+2, paper=True)

    def test_lock_and_atomic_failure_preserve_existing_document(self):
        before = (self.root/experiment.FILE).read_bytes()
        with cli.locked(self.root), self.assertRaises(RuntimeError):
            migration.upgrade(self.root, now=NOW+2, paper=True)
        with patch.object(cli.os, 'replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): migration.upgrade(self.root, now=NOW+2, paper=True)
        self.assertEqual((self.root/experiment.FILE).read_bytes(), before)

    def test_upgrade_allows_next_tick_without_integrity_freeze(self):
        migration.upgrade(self.root, now=NOW+2, paper=True)
        result = experiment.tick(self.root, now=NOW+300, collector=lambda *a, **k: ({}, {'top5':[]}),
                                 feature_collector=lambda *a, **k: dict(fetched_at=NOW+300,symbols={},errors=[]))
        self.assertEqual(result['experiment']['status'], 'running')
        self.assertIn('equity', self.doc()['observations'][-1])


if __name__ == '__main__': unittest.main()
