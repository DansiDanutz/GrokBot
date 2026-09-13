"""One message for every failure cost two investigations a day.

On 2026-09-13 a full disk surfaced as "API key unavailable". One audit
proposed adding a key that was already present; another proposed dropping a
healthy symbol. The key was valid throughout. Each cause needs a different
first move, so each must be nameable — without disclosing the path or the key.
"""
import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from paper_grid import coinglass as c

KEY = 'COINGLASS_API_KEY=obviously-fake-test-fixture\n'


class CauseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.path = self.root / 'fixture.env'
        self.path.write_text(KEY)
        self.path.chmod(0o600)

    def reason(self, path=None):
        with self.assertRaises(c.CoinGlassError) as caught:
            c.read_api_key(path or self.path)
        return str(caught.exception)

    def test_every_cause_still_reads_as_api_key_unavailable(self):
        """Callers and existing tests match on that prefix; keep the contract."""
        self.path.chmod(0o644)
        self.assertTrue(self.reason().startswith('API key unavailable'))

    def test_permission_mode_names_itself(self):
        self.path.chmod(0o644)
        self.assertIn('mode', self.reason())

    def test_wrong_owner_names_itself(self):
        meta = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=os.getuid() + 1, st_size=10)
        with patch.object(os, 'fstat', return_value=meta):
            self.assertIn('owner', self.reason())

    def test_oversize_names_itself(self):
        self.path.write_text(KEY + '#' * 16385)
        self.path.chmod(0o600)
        self.assertIn('large', self.reason())

    def test_a_full_disk_reads_as_unreadable_not_as_a_missing_key(self):
        """The 2026-09-13 case: an OS error must not impersonate a missing key."""
        with patch.object(os, 'fdopen', side_effect=OSError(28, 'No space left on device')):
            reason = self.reason()
        self.assertIn('unreadable', reason)
        self.assertIn('OSError', reason)

    def test_a_genuinely_absent_key_stays_the_bare_message(self):
        self.path.write_text('UNRELATED=x\n')
        self.path.chmod(0o600)
        self.assertEqual(self.reason(), 'API key unavailable')

    def test_no_cause_ever_discloses_the_path_the_key_or_the_os_text(self):
        cases = [lambda: self.path.chmod(0o644),
                 lambda: self.path.write_text(KEY + '#' * 16385)]
        for prepare in cases:
            prepare()
            reason = self.reason()
            self.assertNotIn(str(self.root), reason)
            self.assertNotIn('obviously-fake', reason)
            self.path.write_text(KEY); self.path.chmod(0o600)
        with patch.object(os, 'fdopen', side_effect=OSError(28, 'No space left on device')):
            reason = self.reason()
        self.assertNotIn('No space left on device', reason)
        self.assertNotIn(str(self.root), reason)
