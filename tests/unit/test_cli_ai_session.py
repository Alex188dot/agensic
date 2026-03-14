import unittest

from typer.testing import CliRunner

from agensic.cli.app import app


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
        self.assertIn("ai-session is no longer supported", result.stdout)
        self.assertIn("agensic run <agent>", result.stdout)

    def test_ai_session_stop_is_removed(self):
        result = self.runner.invoke(app, ["ai-session", "stop"])

        self.assertEqual(result.exit_code, 2)
        self.assertIn("ai-session is no longer supported", result.stdout)

    def test_ai_session_status_is_removed(self):
        result = self.runner.invoke(app, ["ai-session", "status"])

        self.assertEqual(result.exit_code, 2)
        self.assertIn("ai-session is no longer supported", result.stdout)


if __name__ == "__main__":
    unittest.main()
