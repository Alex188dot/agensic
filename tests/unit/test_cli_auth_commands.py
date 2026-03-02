import unittest
import importlib
from unittest.mock import patch

from typer.testing import CliRunner

cli_app = importlib.import_module("ghostshell.cli.app")
app = cli_app.app


class CliAuthCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def test_auth_rotate_command_rotates_token(self):
        with patch.object(cli_app, "rotate_auth_token") as rotate_mock, patch.object(
            cli_app, "load_auth_payload", return_value={"last_rotated_at": 1700000000}
        ), patch.object(
            cli_app._DAEMON_AUTH_CACHE,
            "get_token",
            return_value="token",
        ):
            result = self.runner.invoke(app, ["auth", "rotate"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Local auth token rotated", result.stdout)
        rotate_mock.assert_called_once()

    def test_auth_status_command_prints_metadata(self):
        with patch.object(cli_app.Path, "exists", return_value=True), patch.object(
            cli_app.Path, "is_file", return_value=True
        ), patch.object(
            cli_app.Path, "stat"
        ) as stat_mock, patch.object(
            cli_app,
            "load_auth_payload",
            return_value={"created_at": 1700000000, "last_rotated_at": 1700000100, "auth_token": "x"},
        ):
            stat_mock.return_value.st_mtime = 1700000200
            result = self.runner.invoke(app, ["auth", "status"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("auth status: present", result.stdout)
        self.assertIn("created_at:", result.stdout)
        self.assertIn("last_rotated_at:", result.stdout)

    def test_auth_status_json(self):
        with patch.object(cli_app.Path, "exists", return_value=False), patch.object(
            cli_app.Path, "is_file", return_value=False
        ):
            result = self.runner.invoke(app, ["auth", "status", "--json"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn('"exists": false', result.stdout)

    def test_root_help_shows_auth_subcommands_and_hides_group_entry(self):
        result = self.runner.invoke(app, ["--help"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("auth rotate", result.stdout)
        self.assertIn("auth status", result.stdout)
        self.assertNotIn("provenance-registry", result.stdout)
        self.assertNotIn("ai-session", result.stdout)
        self.assertNotIn("│ auth            ", result.stdout)
        self.assertNotIn("\n│ wrap ", result.stdout)


if __name__ == "__main__":
    unittest.main()
