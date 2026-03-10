import unittest
from unittest.mock import patch

from typer.testing import CliRunner

import importlib

app_module = importlib.import_module("agensic.cli.app")
app = app_module.app


class CliProvenanceTuiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def test_provenance_tui_invokes_sidecar(self):
        with patch("agensic.cli.app._run_provenance_tui", return_value=True) as run_tui:
            result = self.runner.invoke(app, ["provenance", "--tui"])
        self.assertEqual(result.exit_code, 0)
        run_tui.assert_called_once()

    def test_provenance_tui_export_defaults_output_path(self):
        with patch("agensic.cli.app._default_export_path", return_value="/tmp/default-prov.json"), patch(
            "agensic.cli.app._run_provenance_tui",
            return_value=True,
        ) as run_tui:
            result = self.runner.invoke(app, ["provenance", "--tui", "--export", "json"])
        self.assertEqual(result.exit_code, 0)
        kwargs = run_tui.call_args.kwargs
        self.assertEqual(kwargs.get("out_path"), "/tmp/default-prov.json")
        self.assertIn("Exported provenance rows to:", result.stdout)

    def test_provenance_tui_export_falls_back_when_sidecar_fails(self):
        with patch("agensic.cli.app._run_provenance_tui", return_value=False), patch(
            "agensic.cli.app._fallback_export_provenance"
        ) as fallback:
            result = self.runner.invoke(
                app,
                [
                    "provenance",
                    "--tui",
                    "--export",
                    "json",
                    "--out",
                    "/tmp/provenance-test.json",
                ],
            )
        self.assertEqual(result.exit_code, 0)
        fallback.assert_called_once()

    def test_run_provenance_tui_passes_dash_prefixed_auth_token_safely(self):
        class Result:
            returncode = 0

        with patch("agensic.cli.app._ensure_provenance_tui_binary", return_value="/tmp/agensic-provenance-tui"), patch(
            "agensic.cli.app._reset_terminal_mouse_reporting"
        ), patch.object(app_module._DAEMON_AUTH_CACHE, "get_token", return_value="-Ctoken"), patch(
            "agensic.cli.app.subprocess.run", return_value=Result()
        ) as run_cmd:
            ok = app_module._run_provenance_tui(
                limit=5,
                label="",
                contains="",
                since_ts=0,
                tier="",
                agent="",
                agent_name="",
                provider="",
                export_format="",
                out_path="",
            )

        self.assertTrue(ok)
        cmd = run_cmd.call_args.args[0]
        self.assertIn("--auth-token=-Ctoken", cmd)
        self.assertNotIn("--auth-token", cmd)


if __name__ == "__main__":
    unittest.main()
