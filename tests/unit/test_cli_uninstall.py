import importlib
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from typer.testing import CliRunner

cli_app = importlib.import_module("agensic.cli.app")
app = cli_app.app
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


class CliUninstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def test_uninstall_removes_shell_wiring_and_state(self):
        rc_paths = [Path("/tmp/.bash_profile"), Path("/tmp/.bashrc")]

        with TemporaryDirectory() as tmpdir:
            sentinel = Path(tmpdir) / "agensic-shell-uninstalled-1"
            with patch.object(cli_app, "stop") as stop_mock, patch.object(
                cli_app, "_shell_rc_paths", return_value=rc_paths
            ), patch.object(
                cli_app, "_scrub_shell_rc_file", side_effect=[True, False]
            ) as scrub_mock, patch.object(
                cli_app, "_remove_tree_if_exists", side_effect=[True, True, True, True, True]
            ) as remove_tree_mock, patch.object(
                cli_app, "_remove_file_if_exists", side_effect=[True, False, True, True, True, True, True]
            ) as remove_file_mock, patch.object(
                cli_app, "UNINSTALL_SENTINEL", str(sentinel)
            ):
                result = self.runner.invoke(app, ["uninstall", "--yes"])
            sentinel_contents = sentinel.read_text(encoding="utf-8")
        output = ANSI_RE.sub("", result.stdout)

        self.assertEqual(result.exit_code, 0)
        self.assertIn("Removed:", output)
        self.assertIn("Current shell plugin disabled", output)
        self.assertIn(str(cli_app.PLIST_PATH), output)
        self.assertIn(str(cli_app.SYSTEMD_UNIT_PATH), output)
        self.assertIn(str(rc_paths[0]), output)
        self.assertIn(cli_app.CONFIG_DIR, output)
        self.assertIn(cli_app.STATE_DIR, output)
        self.assertIn(cli_app.CACHE_DIR, output)
        self.assertIn(cli_app.INSTALL_DIR, output)
        self.assertIn(cli_app.APP_PATHS.launcher_path, output)
        self.assertIn(cli_app.APP_PATHS.session_start_launcher_path, output)
        self.assertIn(cli_app.APP_PATHS.session_status_launcher_path, output)
        self.assertIn(cli_app.APP_PATHS.session_stop_launcher_path, output)
        self.assertIn(cli_app.LEGACY_CONFIG_DIR, output)
        self.assertEqual(sentinel_contents, "disabled\n")
        stop_mock.assert_called_once()
        self.assertEqual(scrub_mock.call_count, 2)
        self.assertEqual(remove_tree_mock.call_count, 5)
        self.assertEqual(remove_file_mock.call_count, 7)

    def test_uninstall_keep_data_skips_state_deletion(self):
        with patch.object(cli_app, "stop"), patch.object(
            cli_app, "_shell_rc_paths", return_value=[]
        ), patch.object(
            cli_app, "_remove_tree_if_exists", side_effect=[True, True]
        ) as remove_tree_mock, patch.object(
            cli_app, "_remove_file_if_exists", return_value=True
        ):
            result = self.runner.invoke(app, ["uninstall", "--yes", "--keep-data"])

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(remove_tree_mock.call_count, 2)

    def test_uninstall_prompt_does_not_render_none(self):
        with patch.object(cli_app, "stop") as stop_mock:
            result = self.runner.invoke(app, ["uninstall"], input="n\n")

        self.assertEqual(result.exit_code, 1)
        self.assertIn("Uninstall Agensic from this machine and delete local state?", result.stdout)
        self.assertNotIn("None [y/N]", result.stdout)
        stop_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
