import unittest
import re

from typer.testing import CliRunner

from agensic.cli.app import app


ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


class CliAiSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def test_ai_session_start_is_removed(self):
        result = self.runner.invoke(
            app,
            [
                "ai-session",
                "start",
                "--agent",
                "codex",
                "--model",
                "gpt-5.3",
            ],
        )

        self.assertEqual(result.exit_code, 2)
        output = ANSI_RE.sub("", result.stdout)
        self.assertIn("Use `agensic run <agent>` for observed agent sessions.", output)

    def test_ai_session_stop_is_removed(self):
        result = self.runner.invoke(app, ["ai-session", "stop"])

        self.assertEqual(result.exit_code, 2)
        output = ANSI_RE.sub("", result.stdout)
        self.assertIn("Use `agensic run <agent>` for observed agent sessions.", output)

    def test_ai_session_status_is_removed(self):
        result = self.runner.invoke(app, ["ai-session", "status"])

        self.assertEqual(result.exit_code, 2)
        output = ANSI_RE.sub("", result.stdout)
        self.assertIn("Use `agensic run <agent>` for observed agent sessions.", output)


if __name__ == "__main__":
    unittest.main()
