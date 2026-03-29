import importlib
import json
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from typer.testing import CliRunner

cli_app = importlib.import_module("agensic.cli.app")
app = cli_app.app
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
CURRENT_VERSION = cli_app.__version__


def _next_patch(version: str) -> str:
    major, minor, patch = (int(part) for part in version.split("."))
    return f"{major}.{minor}.{patch + 1}"


class CliUpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def test_semver_comparison(self):
        self.assertTrue(cli_app._is_newer_version("0.1.0", "0.1.1"))
        self.assertTrue(cli_app._is_newer_version("0.1.9", "0.2.0"))
        self.assertFalse(cli_app._is_newer_version("0.1.1", "0.1.1"))
        self.assertFalse(cli_app._is_newer_version("0.2.0", "0.1.9"))
        self.assertFalse(cli_app._is_newer_version("dev", "0.1.1"))

    def test_fetch_latest_release_uses_cache(self):
        with TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "latest_release.json"
            cache_path.write_text(
                json.dumps(
                    {
                        "checked_at": 1_800_000_000,
                        "release": {
                            "tag_name": "0.1.2",
                            "tarball_url": "https://example.com/agensic.tar.gz",
                            "html_url": "https://example.com/release",
                        },
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(cli_app, "VERSION_CACHE_FILE", str(cache_path)), patch.object(
                cli_app.time, "time", return_value=1_800_000_100
            ), patch.object(cli_app.requests, "get") as get_mock:
                release = cli_app._fetch_latest_release_info()

        self.assertEqual(
            release,
            {
                "version": "0.1.2",
                "tarball_url": "https://example.com/agensic.tar.gz",
                "html_url": "https://example.com/release",
            },
        )
        get_mock.assert_not_called()

    def test_print_update_notice_shows_newer_release(self):
        next_version = _next_patch(CURRENT_VERSION)
        with patch.object(
            cli_app, "_fetch_latest_release_info", return_value={"version": next_version, "tarball_url": "", "html_url": ""}
        ), patch.object(cli_app.console, "print") as print_mock:
            cli_app._print_update_notice_if_available()

        rendered = "\n".join(str(call.args[0]) for call in print_mock.call_args_list)
        self.assertIn(f"{CURRENT_VERSION} -> {next_version}", rendered)
        self.assertIn("agensic update", rendered)

    def test_update_command_reinstalls_latest_release(self):
        next_version = _next_patch(CURRENT_VERSION)
        release = {
            "version": next_version,
            "tarball_url": "https://example.com/agensic.tar.gz",
            "html_url": "https://example.com/release",
        }
        with patch.object(cli_app, "ensure_config_dir"), patch.object(
            cli_app, "_fetch_latest_release_info", return_value=release
        ), patch.object(
            cli_app, "_download_release_tarball"
        ) as download_mock, patch.object(
            cli_app, "_extract_release_tarball", return_value=Path("/tmp/agensic-src")
        ) as extract_mock, patch.object(
            cli_app, "_run_release_installer"
        ) as install_mock:
            result = self.runner.invoke(app, ["update"])

        output = ANSI_RE.sub("", result.stdout)
        self.assertEqual(result.exit_code, 0)
        self.assertIn(f"Updating Agensic from {CURRENT_VERSION} to {next_version}", output)
        self.assertIn(f"Agensic updated successfully: {CURRENT_VERSION} -> {next_version}", output)
        download_mock.assert_called_once()
        extract_mock.assert_called_once()
        install_mock.assert_called_once_with(Path("/tmp/agensic-src"))

    def test_update_command_reports_up_to_date(self):
        with patch.object(cli_app, "ensure_config_dir"), patch.object(
            cli_app, "_fetch_latest_release_info", return_value={"version": CURRENT_VERSION, "tarball_url": "https://example.com/a.tgz", "html_url": ""}
        ), patch.object(cli_app, "_download_release_tarball") as download_mock:
            result = self.runner.invoke(app, ["update"])

        output = ANSI_RE.sub("", result.stdout)
        self.assertEqual(result.exit_code, 0)
        self.assertIn(f"Agensic is already up to date ({CURRENT_VERSION}).", output)
        download_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
