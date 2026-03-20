import unittest
import re

from typer.testing import CliRunner

from agensic.cli.app import app


ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


class CliAiExecTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def test_ai_exec_is_removed(self):
        result = self.runner.invoke(
            app,
            [
                "ai-exec",
                "--agent",
                "codex",
                "--model",
                "gpt-5.3",
                "--",
                "python3",
                "-c",
                "print('hello')",
            ],
        )

        self.assertEqual(result.exit_code, 2)
        output = ANSI_RE.sub("", result.stdout)
        self.assertIn("Use `agensic run <agent>` for observed agent sessions.", output)


if __name__ == "__main__":
    unittest.main()
