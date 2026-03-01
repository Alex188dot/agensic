import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path("/Users/alessioleodori/.codex/skills/ghostshell-signed-exec/scripts/signed_exec.sh")


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


if __name__ == "__main__":
    unittest.main()
