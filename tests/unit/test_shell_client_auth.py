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
    def _run_main_raw(self, argv: list[str], stdin_payload: dict | None = None) -> str:
        raw = "" if stdin_payload is None else json.dumps(stdin_payload)
        with patch.object(sys, "argv", ["shell_client.py", *argv]), patch(
            "sys.stdin", io.StringIO(raw)
        ), patch.object(shell_client._AUTH_CACHE, "get_token", return_value="test-auth-token"):
            out = io.StringIO()
            with redirect_stdout(out):
                shell_client.main()
        return out.getvalue()

    def _run_main_json(self, argv: list[str], stdin_payload: dict | None = None) -> dict:
        raw = self._run_main_raw(argv=argv, stdin_payload=stdin_payload).strip()
        return json.loads(raw or "{}")

    def _run_main_lines(self, argv: list[str], stdin_payload: dict | None = None) -> list[str]:
        raw = self._run_main_raw(argv=argv, stdin_payload=stdin_payload)
        return raw.rstrip("\n").split("\n") if raw else []

    def test_predict_returns_auth_failed_on_http_401(self):
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
            result = self._run_main_json(argv=[], stdin_payload=payload)
        self.assertEqual(result.get("ok"), False)
        self.assertEqual(result.get("error_code"), "auth_failed")

    def test_predict_returns_ok_payload_on_success(self):
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
            result = self._run_main_json(argv=[], stdin_payload=payload)
        self.assertEqual(result.get("ok"), True)
        self.assertEqual(result.get("pool"), [" status"])
        self.assertIn("display", result)
        self.assertIn("modes", result)
        self.assertIn("kinds", result)

    def test_intent_json_success(self):
        server_reply = {
            "status": "ok",
            "primary_command": "ls -la",
            "explanation": "List files",
            "alternatives": ["ls", "find . -maxdepth 1"],
            "copy_block": "ls -la",
            "ai_agent": "codex",
            "ai_provider": "openai",
            "ai_model": "gpt-5",
        }
        with patch("shell_client.urllib.request.urlopen", return_value=_FakeResponse(server_reply)):
            result = self._run_main_json(
                argv=[
                    "--op",
                    "intent",
                    "--intent-text",
                    "list files",
                    "--working-directory",
                    "/tmp",
                    "--shell",
                    "zsh",
                    "--terminal",
                    "xterm-256color",
                    "--platform",
                    "Darwin",
                ]
            )
        self.assertEqual(result.get("ok"), True)
        self.assertEqual(result.get("status"), "ok")
        self.assertEqual(result.get("primary_command"), "ls -la")
        self.assertEqual(result.get("alternatives_blob"), "ls|||find . -maxdepth 1")

    def test_intent_shell_lines_v1_success(self):
        server_reply = {
            "status": "ok",
            "primary_command": "git status",
            "explanation": "Shows working tree status",
            "alternatives": ["git st", "git status -sb"],
            "copy_block": "git status",
            "ai_agent": "codex",
            "ai_provider": "openai",
            "ai_model": "gpt-5",
        }
        with patch("shell_client.urllib.request.urlopen", return_value=_FakeResponse(server_reply)):
            lines = self._run_main_lines(
                argv=[
                    "--op",
                    "intent",
                    "--format",
                    "shell_lines_v1",
                    "--intent-text",
                    "show status",
                    "--working-directory",
                    "/tmp",
                    "--shell",
                    "zsh",
                ]
            )
        self.assertEqual(len(lines), 12)
        self.assertEqual(lines[0], "agensic_shell_lines_v1")
        self.assertEqual(lines[1], "intent")
        self.assertEqual(lines[2], "1")
        self.assertEqual(lines[3], "")
        self.assertEqual(lines[4], "ok")
        self.assertEqual(lines[5], "git status")

    def test_intent_shell_lines_v1_auth_failed_uses_fallback(self):
        err = urllib.error.HTTPError(
            shell_client.INTENT_URL,
            401,
            "unauthorized",
            hdrs=None,
            fp=None,
        )
        with patch("shell_client.urllib.request.urlopen", side_effect=err):
            lines = self._run_main_lines(
                argv=[
                    "--op",
                    "intent",
                    "--format",
                    "shell_lines_v1",
                    "--intent-text",
                    "show status",
                    "--working-directory",
                    "/tmp",
                    "--shell",
                    "zsh",
                ]
            )
        self.assertEqual(lines[0], "agensic_shell_lines_v1")
        self.assertEqual(lines[1], "intent")
        self.assertEqual(lines[2], "0")
        self.assertEqual(lines[3], "auth_failed")
        self.assertEqual(lines[4], "error")
        self.assertEqual(lines[6], "Could not resolve command mode right now.")

    def test_intent_sanitizes_newlines_and_limits_alternatives(self):
        server_reply = {
            "status": "ok\n",
            "primary_command": "echo hi\n",
            "explanation": "line1\nline2",
            "alternatives": ["a\n1", "b\r2", "c3"],
            "copy_block": "echo hi\r\n",
            "ai_agent": "co\ndex",
            "ai_provider": "op\renai",
            "ai_model": "gpt\n-5",
        }
        with patch("shell_client.urllib.request.urlopen", return_value=_FakeResponse(server_reply)):
            lines = self._run_main_lines(
                argv=[
                    "--op",
                    "intent",
                    "--format",
                    "shell_lines_v1",
                    "--intent-text",
                    "sanitize",
                    "--working-directory",
                    "/tmp",
                    "--shell",
                    "zsh",
                ]
            )
        self.assertEqual(lines[4], "ok")
        self.assertEqual(lines[5], "echo hi")
        self.assertEqual(lines[6], "line1 line2")
        self.assertEqual(lines[7], "a 1|||b 2")
        self.assertEqual(lines[8], "echo hi")
        self.assertEqual(lines[9], "co dex")
        self.assertEqual(lines[10], "op enai")
        self.assertEqual(lines[11], "gpt -5")

    def test_assist_json_success(self):
        server_reply = {"answer": "## Title\n\nUse `git rebase --onto`."}
        with patch("shell_client.urllib.request.urlopen", return_value=_FakeResponse(server_reply)):
            result = self._run_main_json(
                argv=[
                    "--op",
                    "assist",
                    "--prompt-text",
                    "explain rebase onto",
                    "--working-directory",
                    "/tmp",
                    "--shell",
                    "zsh",
                ]
            )
        self.assertEqual(result.get("ok"), True)
        self.assertEqual(result.get("answer"), "## Title\n\nUse `git rebase --onto`.")

    def test_assist_json_decodes_escaped_newlines(self):
        server_reply = {"answer": "## Title\\n\\n- one\\n- two"}
        with patch("shell_client.urllib.request.urlopen", return_value=_FakeResponse(server_reply)):
            result = self._run_main_json(
                argv=[
                    "--op",
                    "assist",
                    "--prompt-text",
                    "brief",
                    "--working-directory",
                    "/tmp",
                    "--shell",
                    "zsh",
                ]
            )
        self.assertEqual(result.get("ok"), True)
        self.assertEqual(result.get("answer"), "## Title\n\n- one\n- two")

    def test_assist_shell_lines_v1_preserves_markdown_newlines(self):
        server_reply = {"answer": "## Header\n\n```bash\necho hi\n```\n- one\n- two"}
        with patch("shell_client.urllib.request.urlopen", return_value=_FakeResponse(server_reply)):
            lines = self._run_main_lines(
                argv=[
                    "--op",
                    "assist",
                    "--format",
                    "shell_lines_v1",
                    "--prompt-text",
                    "show markdown",
                    "--working-directory",
                    "/tmp",
                    "--shell",
                    "zsh",
                ]
            )
        self.assertEqual(lines[0], "agensic_shell_lines_v1")
        self.assertEqual(lines[1], "assist")
        self.assertEqual(lines[2], "1")
        self.assertEqual(lines[3], "")
        self.assertEqual(lines[4], "7")
        self.assertEqual("\n".join(lines[5:12]), "## Header\n\n```bash\necho hi\n```\n- one\n- two")

    def test_assist_shell_lines_v1_failure_uses_fallback(self):
        with patch("shell_client.urllib.request.urlopen", side_effect=Exception("boom")):
            lines = self._run_main_lines(
                argv=[
                    "--op",
                    "assist",
                    "--format",
                    "shell_lines_v1",
                    "--prompt-text",
                    "hello",
                    "--working-directory",
                    "/tmp",
                    "--shell",
                    "zsh",
                ]
            )
        self.assertEqual(lines[0], "agensic_shell_lines_v1")
        self.assertEqual(lines[1], "assist")
        self.assertEqual(lines[2], "0")
        self.assertEqual(lines[4], "1")
        self.assertEqual(lines[5], "Could not fetch assistant reply right now.")


if __name__ == "__main__":
    unittest.main()
