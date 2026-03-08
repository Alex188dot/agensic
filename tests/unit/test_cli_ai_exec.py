import unittest
from unittest.mock import patch

from typer.testing import CliRunner

from agensic.cli.app import app


class _MockResponse:
    status_code = 200
    text = ""


class CliAiExecTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def test_ai_exec_defaults_identity_and_warns_once(self):
        with patch("agensic.cli.app.sign_proof_payload", return_value="sig") as mock_sign, patch(
            "agensic.cli.app.build_local_proof_metadata",
            return_value={
                "proof_signer_scope": "local-hmac",
                "proof_key_fingerprint": "deadbeefdeadbeef",
                "proof_host_fingerprint": "cafebabecafebabe",
            },
        ), patch(
            "agensic.cli.app._daemon_auth_headers",
            return_value={},
        ), patch("agensic.cli.app.requests.request", return_value=_MockResponse()) as mock_request:
            result = self.runner.invoke(
                app,
                [
                    "ai-exec",
                    "--",
                    "python3",
                    "-c",
                    "import sys; sys.exit(0)",
                ],
            )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout.count("Warning: ai-exec missing identity"), 1)
        mock_sign.assert_called_once()
        sign_args = mock_sign.call_args.args
        self.assertEqual(sign_args[1], "unknown")
        self.assertEqual(sign_args[2], "unknown-model")

        payload = mock_request.call_args.kwargs["json"]
        self.assertEqual(payload["provenance_ai_agent"], "unknown")
        self.assertEqual(payload["provenance_ai_model"], "unknown-model")
        self.assertGreaterEqual(int(payload.get("duration_ms", -1) or -1), 0)
        self.assertEqual(payload["proof_signer_scope"], "local-hmac")
        self.assertEqual(payload["proof_key_fingerprint"], "deadbeefdeadbeef")
        self.assertEqual(payload["proof_host_fingerprint"], "cafebabecafebabe")

    def test_ai_exec_normalizes_agent_and_propagates_exit_code(self):
        with patch("agensic.cli.app.sign_proof_payload", return_value="sig"), patch(
            "agensic.cli.app.build_local_proof_metadata",
            return_value={
                "proof_signer_scope": "local-hmac",
                "proof_key_fingerprint": "",
                "proof_host_fingerprint": "",
            },
        ), patch(
            "agensic.cli.app._daemon_auth_headers",
            return_value={},
        ), patch("agensic.cli.app.requests.request", return_value=_MockResponse()) as mock_request:
            result = self.runner.invoke(
                app,
                [
                    "ai-exec",
                    "--agent",
                    "CoDeX",
                    "--model",
                    "gpt-5.3",
                    "--",
                    "python3",
                    "-c",
                    "import sys; sys.exit(7)",
                ],
            )

        self.assertEqual(result.exit_code, 7)
        self.assertNotIn("Warning: ai-exec missing identity", result.stdout)
        payload = mock_request.call_args.kwargs["json"]
        self.assertEqual(payload["provenance_ai_agent"], "codex")
        self.assertEqual(payload["proof_agent"], "codex")
        self.assertEqual(payload["provenance_ai_model"], "gpt-5.3")
        self.assertGreaterEqual(int(payload.get("duration_ms", -1) or -1), 0)

    def test_ai_exec_logs_metadata_only_for_nonzero_exit(self):
        with patch("agensic.cli.app.sign_proof_payload", return_value="sig"), patch(
            "agensic.cli.app.build_local_proof_metadata",
            return_value={
                "proof_signer_scope": "local-hmac",
                "proof_key_fingerprint": "",
                "proof_host_fingerprint": "",
            },
        ), patch(
            "agensic.cli.app._daemon_auth_headers",
            return_value={},
        ), patch("agensic.cli.app.requests.request", return_value=_MockResponse()) as mock_request:
            result = self.runner.invoke(
                app,
                [
                    "ai-exec",
                    "--agent",
                    "codex",
                    "--model",
                    "gpt-5",
                    "--",
                    "python3",
                    "-c",
                    "import sys; print('out-line'); print('err-line', file=sys.stderr); sys.exit(9)",
                ],
            )

        self.assertEqual(result.exit_code, 9)
        payload = mock_request.call_args.kwargs["json"]
        self.assertNotIn("captured_stdout_tail", payload)
        self.assertNotIn("captured_stderr_tail", payload)
        self.assertNotIn("captured_output_truncated", payload)

    def test_ai_exec_does_not_store_output_for_zero_exit(self):
        with patch("agensic.cli.app.sign_proof_payload", return_value="sig"), patch(
            "agensic.cli.app.build_local_proof_metadata",
            return_value={
                "proof_signer_scope": "local-hmac",
                "proof_key_fingerprint": "",
                "proof_host_fingerprint": "",
            },
        ), patch(
            "agensic.cli.app._daemon_auth_headers",
            return_value={},
        ), patch("agensic.cli.app.requests.request", return_value=_MockResponse()) as mock_request:
            result = self.runner.invoke(
                app,
                [
                    "ai-exec",
                    "--agent",
                    "codex",
                    "--model",
                    "gpt-5",
                    "--",
                    "python3",
                    "-c",
                    "import sys; print('ok-out'); print('ok-err', file=sys.stderr); sys.exit(0)",
                ],
            )

        self.assertEqual(result.exit_code, 0)
        payload = mock_request.call_args.kwargs["json"]
        self.assertNotIn("captured_stdout_tail", payload)
        self.assertNotIn("captured_stderr_tail", payload)
        self.assertNotIn("captured_output_truncated", payload)


if __name__ == "__main__":
    unittest.main()
