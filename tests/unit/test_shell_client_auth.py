import io
import json
import sys
import unittest
import urllib.error
from contextlib import redirect_stdout
from unittest.mock import patch

import shell_client


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


class ShellClientAuthTests(unittest.TestCase):
    def _run_main_with_stdin(self, stdin_payload: dict) -> dict:
        raw = json.dumps(stdin_payload)
        with patch.object(sys, "argv", ["shell_client.py"]), patch("sys.stdin", io.StringIO(raw)), patch.object(
            shell_client._AUTH_CACHE, "get_token", return_value="test-auth-token"
        ):
            out = io.StringIO()
            with redirect_stdout(out):
                shell_client.main()
        return json.loads(out.getvalue().strip() or "{}")

    def test_returns_auth_failed_on_http_401(self):
        payload = {
            "command_buffer": "git st",
            "cursor_position": 6,
            "working_directory": "/tmp",
            "shell": "zsh",
            "allow_ai": False,
            "trigger_source": "test",
        }
        err = urllib.error.HTTPError(
            shell_client.PREDICT_URL,
            401,
            "unauthorized",
            hdrs=None,
            fp=None,
        )
        with patch("shell_client.urllib.request.urlopen", side_effect=err):
            result = self._run_main_with_stdin(payload)
        self.assertEqual(result.get("ok"), False)
        self.assertEqual(result.get("error_code"), "auth_failed")

    def test_returns_ok_payload_on_success(self):
        payload = {
            "command_buffer": "git st",
            "cursor_position": 6,
            "working_directory": "/tmp",
            "shell": "zsh",
            "allow_ai": False,
            "trigger_source": "test",
        }
        server_reply = {
            "used_ai": False,
            "pool_meta": [
                {
                    "accept_text": " status",
                    "display_text": " status",
                    "accept_mode": "suffix_append",
                    "kind": "normal",
                }
            ],
        }
        with patch("shell_client.urllib.request.urlopen", return_value=_FakeResponse(server_reply)):
            result = self._run_main_with_stdin(payload)
        self.assertEqual(result.get("ok"), True)
        self.assertEqual(result.get("pool"), [" status"])


if __name__ == "__main__":
    unittest.main()
