import unittest

from privacy_guard import PrivacyGuard


class PrivacyGuardTests(unittest.TestCase):
    def setUp(self):
        self.guard = PrivacyGuard()

    def test_redacts_export_assignment(self):
        result = self.guard.sanitize_text('export OPENAI_API_KEY="sk-test-123456"')
        self.assertIn("export OPENAI_API_KEY=<REDACTED_SECRET>", result.text)
        self.assertNotIn("sk-test-123456", result.text)

    def test_redacts_inline_env_assignment(self):
        payload = "OPENAI_API_KEY=sk-live-123 curl https://api.example.com"
        result = self.guard.sanitize_text(payload)
        self.assertIn("OPENAI_API_KEY=<REDACTED_SECRET>", result.text)
        self.assertNotIn("sk-live-123", result.text)

    def test_redacts_known_aws_secret_assignment(self):
        payload = "AWS_SECRET_ACCESS_KEY=abcd1234 aws s3 ls"
        result = self.guard.sanitize_text(payload)
        self.assertIn("AWS_SECRET_ACCESS_KEY=<REDACTED_SECRET>", result.text)
        self.assertNotIn("abcd1234", result.text)

    def test_redacts_jwt_like_token(self):
        token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhY2NvdW50IjoiYWJjZGVmZ2hpamtsbW5vcHFyIn0.c2lnbmF0dXJlX2Jsb2JfdGhhdF9sb29rc19yYW5kb20"
        result = self.guard.sanitize_text(f"token={token}")
        self.assertIn("<REDACTED_SECRET>", result.text)
        self.assertNotIn(token, result.text)

    def test_redacts_hex_and_base64_like_tokens(self):
        hex_token = "a3" * 24
        b64_token = "QWxhZGRpbjpvcGVuIHNlc2FtZQAAABBBCCCDDDEEEFFF111222333444555"
        result = self.guard.sanitize_text(f"{hex_token} {b64_token}")
        self.assertNotIn(hex_token, result.text)
        self.assertNotIn(b64_token, result.text)
        self.assertGreaterEqual(result.redaction_count, 2)

    def test_redacts_url_credentials(self):
        payload = "curl https://user:pass@example.com/v1/data"
        result = self.guard.sanitize_text(payload)
        self.assertIn("https://<REDACTED_CREDENTIALS>@example.com/v1/data", result.text)
        self.assertNotIn("user:pass@", result.text)

    def test_redacts_dotenv_multiline(self):
        payload = (
            "OPENAI_API_KEY=abc123\n"
            "AWS_SECRET_ACCESS_KEY=xyz987\n"
            "NORMAL_VAR=value\n"
            "# comment"
        )
        result = self.guard.sanitize_text(payload)
        self.assertIn("OPENAI_API_KEY=<REDACTED_SECRET>", result.text)
        self.assertIn("AWS_SECRET_ACCESS_KEY=<REDACTED_SECRET>", result.text)
        self.assertIn("NORMAL_VAR=<REDACTED_SECRET>", result.text)
        self.assertNotIn("abc123", result.text)
        self.assertNotIn("xyz987", result.text)
        self.assertNotIn("value", result.text)

    def test_sanitization_is_idempotent(self):
        payload = (
            "export OPENAI_API_KEY=abc123\n"
            "curl https://user:pass@example.com\n"
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJrZXkiOiJhYmNkZWYifQ.signatureblobforjwtpayload"
        )
        once = self.guard.sanitize_text(payload).text
        twice = self.guard.sanitize_text(once).text
        self.assertEqual(once, twice)


if __name__ == "__main__":
    unittest.main()
