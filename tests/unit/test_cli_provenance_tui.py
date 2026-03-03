import unittest
from unittest.mock import patch

from typer.testing import CliRunner

from ghostshell.cli.app import app


class CliProvenanceTuiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def test_provenance_tui_invokes_sidecar(self):
        with patch("ghostshell.cli.app._run_provenance_tui", return_value=True) as run_tui:
            result = self.runner.invoke(app, ["provenance", "--tui"])
        self.assertEqual(result.exit_code, 0)
        run_tui.assert_called_once()

    def test_provenance_tui_export_requires_output_path(self):
        result = self.runner.invoke(app, ["provenance", "--tui", "--export", "json"])
        self.assertEqual(result.exit_code, 2)
        self.assertIn("--out is required", result.stdout)

    def test_provenance_tui_export_falls_back_when_sidecar_fails(self):
        with patch("ghostshell.cli.app._run_provenance_tui", return_value=False), patch(
            "ghostshell.cli.app._fallback_export_provenance"
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


if __name__ == "__main__":
    unittest.main()
