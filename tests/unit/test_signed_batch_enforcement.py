import subprocess
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "enforce_signed_batch.sh"


@unittest.skipUnless(SCRIPT_PATH.exists(), "enforce_signed_batch.sh not available in this environment")
class SignedBatchEnforcementTests(unittest.TestCase):
    def _run_guard(self, script_body: str, args: list[str] | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [str(SCRIPT_PATH), *(args or [])],
            input=script_body,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_allows_signed_oneoff_commands(self):
        body = "./scripts/signed_exec.sh --agent codex --model gpt-5.3 --command 'echo hi'\n"
        result = self._run_guard(body)
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_allows_signed_session_block(self):
        body = "\n".join(
            [
                "./scripts/signed_session.sh start --agent codex --model gpt-5.3",
                "echo first",
                "echo second",
                "./scripts/signed_session.sh stop",
                "",
            ]
        )
        result = self._run_guard(body)
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_rejects_raw_command_outside_wrapper_in_strict_mode(self):
        result = self._run_guard("pytest -q\n", args=["--mode", "strict"])
        self.assertEqual(result.returncode, 86)
        self.assertIn("ERROR: terminal-commands batch enforcement triggered", result.stderr)
        self.assertIn("EXAMPLE (one-off):", result.stderr)
        self.assertIn("./scripts/signed_session.sh stop", result.stderr)

    def test_rejects_unclosed_session_in_strict_mode(self):
        body = "\n".join(
            [
                "./scripts/signed_session.sh start --agent codex --model gpt-5.3",
                "echo hi",
                "",
            ]
        )
        result = self._run_guard(body, args=["--mode", "strict"])
        self.assertEqual(result.returncode, 86)
        self.assertIn("session_not_stopped", result.stderr)
        self.assertIn("./scripts/signed_session.sh stop", result.stderr)


if __name__ == "__main__":
    unittest.main()
