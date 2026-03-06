import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "signed_exec.sh"


@unittest.skipUnless(SCRIPT_PATH.exists(), "signed_exec.sh not available in this environment")
class SignedExecScriptTests(unittest.TestCase):
    def _run_script(self, args: list[str]) -> tuple[subprocess.CompletedProcess, list[str]]:
        with tempfile.TemporaryDirectory() as tmpdir:
            capture_path = Path(tmpdir) / "captured_args.txt"
            aiterminal_path = Path(tmpdir) / "aiterminal"
            aiterminal_path.write_text(
                "#!/usr/bin/env bash\n"
                "printf '%s\\n' \"$@\" > \"$AIT_CAPTURE_PATH\"\n",
                encoding="utf-8",
            )
            os.chmod(aiterminal_path, stat.S_IRWXU)

            env = dict(os.environ)
            env["PATH"] = f"{tmpdir}:{env.get('PATH', '')}"
            env["AIT_CAPTURE_PATH"] = str(capture_path)

            result = subprocess.run(
                [str(SCRIPT_PATH), *args],
                capture_output=True,
                text=True,
                check=False,
                env=env,
            )
            captured = []
            if capture_path.exists():
                captured = capture_path.read_text(encoding="utf-8").splitlines()
            return result, captured

    def test_preserves_argv_mode_without_reparsing(self):
        result, captured = self._run_script(
            [
                "--agent",
                "CoDeX",
                "--model",
                "gpt-5.3",
                "--",
                "python3",
                "-c",
                "print('hello world')",
            ]
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(
            captured[:6],
            ["ai-exec", "--agent", "codex", "--model", "gpt-5.3", "--"],
        )
        self.assertEqual(captured[6:], ["python3", "-c", "print('hello world')"])

    def test_command_string_mode_uses_zsh_lc(self):
        result, captured = self._run_script(
            [
                "--agent",
                "ops",
                "--model",
                "custom-v1",
                "--command",
                "echo hi | cat",
            ]
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(
            captured,
            [
                "ai-exec",
                "--agent",
                "ops",
                "--model",
                "custom-v1",
                "--",
                "zsh",
                "-lc",
                "echo hi | cat",
            ],
        )

    def test_defaults_identity_when_missing(self):
        result, captured = self._run_script(["--", "echo", "ok"])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Warning: identity missing", result.stderr)
        self.assertEqual(
            captured,
            [
                "ai-exec",
                "--agent",
                "unknown",
                "--model",
                "unknown-model",
                "--",
                "echo",
                "ok",
            ],
        )

    def test_strict_verify_emits_telling_error_message_when_provenance_not_found(self):
        result, captured = self._run_script(
            [
                "--verify-mode",
                "strict",
                "--verify-max-wait-ms",
                "1",
                "--",
                "echo",
                "ok",
            ]
        )
        self.assertEqual(result.returncode, 86, msg=result.stderr)
        self.assertEqual(
            captured,
            [
                "ai-exec",
                "--agent",
                "unknown",
                "--model",
                "unknown-model",
                "--",
                "echo",
                "ok",
            ],
        )
        self.assertIn("ERROR: terminal-commands enforcement triggered", result.stderr)
        self.assertIn("EXAMPLE (one-off):", result.stderr)
        self.assertIn("./scripts/signed_exec.sh --agent <agent_id>", result.stderr)
        self.assertIn("./scripts/signed_session.sh stop", result.stderr)


if __name__ == "__main__":
    unittest.main()
