"""Credential hardening with synthetic files only; never read machine credentials."""
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from paper_grid import coinglass as c


class PrivateCoinGlassCredentialsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.path = self.root / 'fixture.env'
        self.path.write_text('COINGLASS_API_KEY=obviously-fake-test-fixture\n')
        self.path.chmod(0o600)

    def test_private_fixture_and_environment_override_are_read(self):
        self.assertEqual(c.read_api_key(self.path), 'obviously-fake-test-fixture')
        with patch.dict(os.environ, {c.SECRETS_FILE_ENV: str(self.path)}):
            self.assertEqual(c.read_api_key(), 'obviously-fake-test-fixture')

    def test_icloud_roots_are_rejected_before_open_direct_and_resolved(self):
        roots = ('Library/Mobile Documents/app/private', 'Library/Mobile Documents/com~apple~CloudDocs',
                 'Library/CloudStorage/iCloudDrive', 'Library/CloudStorage/iCloud Drive',
                 'library/cloudstorage/ICLOUD-user', 'com~apple~CloudDocs/Documents')
        for root in roots:
            cloud = self.root / root / 'fixture.env'
            for resolved in (False, True):
                with self.subTest(root=root, resolved=resolved):
                    target = self.path if resolved else cloud
                    with patch.object(Path, 'resolve', return_value=cloud), \
                         patch.object(Path, 'open', side_effect=AssertionError('unexpected legacy read')) as old_open, \
                         patch.object(os, 'open', side_effect=AssertionError('unexpected descriptor open')) as opened:
                        with self.assertRaisesRegex(c.CoinGlassError, 'iCloud.*not allowed'):
                            c.read_api_key(target)
                        old_open.assert_not_called()
                        opened.assert_not_called()

    def test_unsafe_permissions_rejected_before_file_bytes_are_read(self):
        for permissions in (0o400, 0o644, 0o660, 0o700, 0o1600):
            with self.subTest(permissions=oct(permissions)):
                self.path.chmod(permissions)
                with patch.object(Path, 'open', side_effect=AssertionError('unexpected legacy read')), \
                     patch.object(os, 'fdopen', side_effect=AssertionError('unsafe bytes read')) as reader:
                    with self.assertRaisesRegex(c.CoinGlassError, 'API key unavailable'):
                        c.read_api_key(self.path)
                    reader.assert_not_called()

    def test_wrong_owner_is_rejected_before_file_bytes_are_read(self):
        metadata = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=os.getuid()+1, st_size=10)
        with patch.object(os, 'fstat', return_value=metadata), \
             patch.object(Path, 'open', side_effect=AssertionError('unexpected legacy read')), \
             patch.object(os, 'fdopen', side_effect=AssertionError('unsafe bytes read')) as reader:
            with self.assertRaisesRegex(c.CoinGlassError, 'API key unavailable'):
                c.read_api_key(self.path)
            reader.assert_not_called()

    def test_nonregular_file_and_symlink_leaf_or_parent_are_rejected(self):
        link = self.root / 'fixture-link.env'
        link.symlink_to(self.path)
        parent_link = self.root / 'alias'
        parent_link.symlink_to(self.root, target_is_directory=True)
        pipe = self.root / 'fixture-pipe'
        os.mkfifo(pipe, 0o600)
        for path in (self.root, link, parent_link / self.path.name, pipe):
            with self.subTest(name=path.name), \
                 patch.object(Path, 'open', side_effect=AssertionError('unexpected legacy read')), \
                 patch.object(os, 'fdopen', side_effect=AssertionError('unsafe bytes read')) as reader:
                with self.assertRaisesRegex(c.CoinGlassError, 'API key unavailable'):
                    c.read_api_key(path)
                reader.assert_not_called()

    def test_oversized_credentials_rejected_before_read(self):
        self.path.write_text('COINGLASS_API_KEY=obviously-fake-test-fixture\n' + '#' * 16385)
        with patch.object(Path, 'open', side_effect=AssertionError('unexpected legacy read')), \
             patch.object(os, 'fdopen', side_effect=AssertionError('oversized bytes read')) as reader:
            with self.assertRaisesRegex(c.CoinGlassError, 'API key unavailable'):
                c.read_api_key(self.path)
            reader.assert_not_called()

    def test_size_growth_after_metadata_check_is_bounded(self):
        self.path.write_text('COINGLASS_API_KEY=obviously-fake-test-fixture\n' + '#' * 16385)
        metadata = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=os.getuid(), st_size=0)
        with patch.object(os, 'fstat', return_value=metadata):
            with self.assertRaisesRegex(c.CoinGlassError, 'API key unavailable'):
                c.read_api_key(self.path)

    def test_parser_errors_do_not_disclose_paths_or_contents(self):
        for body in ('COINGLASS_API_KEY=obviously-fake-one\nCOINGLASS_API_KEY=obviously-fake-two\n',
                     'COINGLASS_API_KEY=obviously fake invalid\n', b'\xff\xff'):
            self.path.write_bytes(body if isinstance(body, bytes) else body.encode())
            with self.assertRaises(c.CoinGlassError) as caught:
                c.read_api_key(self.path)
            self.assertNotIn(str(self.path), str(caught.exception))
            self.assertNotIn('obviously', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
