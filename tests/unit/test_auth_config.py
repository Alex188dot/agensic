import os
import stat
import tempfile
import unittest

from ghostshell.config.auth import (
    AuthTokenCache,
    ensure_auth_token,
    load_auth_payload,
    load_auth_token,
    rotate_auth_token,
    save_auth_token,
)


class AuthConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.auth_path = os.path.join(self.tmpdir.name, "auth.json")

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_save_and_load_auth_token_roundtrip(self):
        save_auth_token("token-123", path=self.auth_path)
        payload = load_auth_payload(path=self.auth_path)
        self.assertIsInstance(payload, dict)
        self.assertEqual(load_auth_token(path=self.auth_path), "token-123")
        self.assertEqual(str(payload.get("auth_token", "")), "token-123")

    def test_auth_file_permissions_are_owner_only(self):
        save_auth_token("token-123", path=self.auth_path)
        mode = stat.S_IMODE(os.stat(self.auth_path).st_mode)
        self.assertEqual(mode, 0o600)

    def test_rotate_replaces_existing_token(self):
        save_auth_token("old-token", path=self.auth_path)
        rotated = rotate_auth_token(path=self.auth_path)
        self.assertTrue(rotated)
        self.assertNotEqual(rotated, "old-token")
        self.assertEqual(load_auth_token(path=self.auth_path), rotated)

    def test_ensure_recovers_from_malformed_file(self):
        os.makedirs(os.path.dirname(self.auth_path), exist_ok=True)
        with open(self.auth_path, "w", encoding="utf-8") as f:
            f.write("{bad-json")
        token = ensure_auth_token(path=self.auth_path)
        self.assertTrue(token)
        self.assertEqual(load_auth_token(path=self.auth_path), token)

    def test_auth_token_cache_reload_after_rotation(self):
        save_auth_token("first-token", path=self.auth_path)
        cache = AuthTokenCache(path=self.auth_path)
        first = cache.get_token()
        self.assertEqual(first, "first-token")
        second = rotate_auth_token(path=self.auth_path)
        cache_reloaded = cache.get_token(force_reload=True)
        self.assertEqual(cache_reloaded, second)


if __name__ == "__main__":
    unittest.main()
