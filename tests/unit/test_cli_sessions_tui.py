import importlib
import unittest
from unittest.mock import patch

from typer.testing import CliRunner

app_module = importlib.import_module("agensic.cli.app")
track_module = importlib.import_module("agensic.cli.track")
app = app_module.app


class CliSessionsTuiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def test_sessions_command_uses_tui_by_default(self):
        with patch("agensic.cli.app._run_sessions_tui", return_value=True) as run_tui, patch.object(
            app_module.sys.stdin, "isatty", return_value=True
        ), patch.object(app_module.sys.stdout, "isatty", return_value=True):
            result = self.runner.invoke(app, ["sessions"])

        self.assertEqual(result.exit_code, 0)
        run_tui.assert_called_once_with()

    def test_sessions_command_falls_back_to_text(self):
        with patch("agensic.cli.app._run_sessions_tui", return_value=False), patch.object(
            track_module, "print_sessions_text", return_value=0
        ) as print_text, patch.object(app_module.sys.stdin, "isatty", return_value=True), patch.object(
            app_module.sys.stdout, "isatty", return_value=True
        ):
            result = self.runner.invoke(app, ["sessions"])

        self.assertEqual(result.exit_code, 0)
        print_text.assert_called_once()

    def test_track_inspect_uses_sessions_tui_by_default(self):
        with patch.object(app_module, "_run_storage_preflight_if_enabled"), patch.object(
            track_module, "ensure_track_supported"
        ), patch(
            "agensic.cli.app._run_sessions_tui",
            return_value=True,
        ) as run_tui, patch.object(app_module.sys.stdin, "isatty", return_value=True), patch.object(
            app_module.sys.stdout, "isatty", return_value=True
        ):
            result = self.runner.invoke(app, ["track", "inspect", "sess-1"])

        self.assertEqual(result.exit_code, 0)
        run_tui.assert_called_once_with(session_id="sess-1", replay=False)

    def test_track_inspect_text_uses_text_inspector(self):
        with patch.object(app_module, "_run_storage_preflight_if_enabled"), patch.object(
            track_module, "ensure_track_supported"
        ), patch.object(
            track_module, "inspect_track_session", return_value=0
        ) as inspect_text:
            result = self.runner.invoke(app, ["track", "--text", "inspect", "sess-1"])

        self.assertEqual(result.exit_code, 0)
        inspect_text.assert_called_once_with("sess-1", replay=False, tail_events=8)


if __name__ == "__main__":
    unittest.main()
