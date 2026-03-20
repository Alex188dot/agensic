import unittest

from typer.testing import CliRunner

from agensic.cli.app import app


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
        self.assertIn("Use `agensic run <agent>` for observed agent sessions.", result.stdout)


if __name__ == "__main__":
    unittest.main()
