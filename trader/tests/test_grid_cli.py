import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from trader.research.grid_cli import main
from trader.tests.test_grid_snapshot import make_database


class CliTests(unittest.TestCase):
    def test_scan_is_offline_read_only_and_does_not_invent_a_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            path = make_database(root)
            before = path.read_bytes()
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(['scan', '--snapshot', str(path), '--at-ms', '60000'])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue())['candidates'], [])
            self.assertEqual(path.read_bytes(), before)
            self.assertFalse(Path(str(path)+'-wal').exists())

    def test_bad_database_is_sanitized_without_a_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            path = make_database(root)
            path.write_bytes(b'invalid database')
            output = io.StringIO()
            with contextlib.redirect_stderr(output):
                code = main(['scan', '--snapshot', str(path), '--at-ms', '60000'])
            self.assertEqual(code, 2)
            self.assertNotIn(str(path), output.getvalue())
