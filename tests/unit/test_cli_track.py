import importlib
import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

import agensic.paths as ag_paths
from agensic.state.sqlite_store import SQLiteStateStore

cli_app = importlib.import_module("agensic.cli.app")
track_module = importlib.import_module("agensic.cli.track")
app = cli_app.app


class CliTrackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    @contextmanager
    def _temp_app_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env = {
                "HOME": temp_dir,
                "XDG_CONFIG_HOME": str(Path(temp_dir) / ".config"),
                "XDG_STATE_HOME": str(Path(temp_dir) / ".state"),
                "XDG_CACHE_HOME": str(Path(temp_dir) / ".cache"),
            }
            with patch.dict(os.environ, env, clear=False):
                temp_paths = ag_paths.get_app_paths()
            with patch.object(cli_app, "APP_PATHS", temp_paths), patch.object(
                ag_paths,
                "APP_PATHS",
                temp_paths,
            ), patch.object(
                track_module,
                "APP_PATHS",
                temp_paths,
            ):
                yield env, temp_paths

    def test_track_status_inactive(self):
        with self._temp_app_paths() as (env, _), patch.object(
            cli_app, "_run_storage_preflight_if_enabled"
        ):
            result = self.runner.invoke(app, ["track", "status"], env=env)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("inactive", result.stdout)

    def test_track_stop_inactive(self):
        with self._temp_app_paths() as (env, _), patch.object(
            cli_app, "_run_storage_preflight_if_enabled"
        ):
            result = self.runner.invoke(app, ["track", "stop"], env=env)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("inactive", result.stdout)

    def test_track_alias_launch_resolves_registry_agent(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            cli_app, "_run_storage_preflight_if_enabled"
        ), patch.object(
            track_module,
            "run_tracked_command",
            return_value=0,
        ) as run_mock, patch.dict(os.environ, {"CODEX_HOME": str(Path(temp_dir) / ".codex")}, clear=False):
            result = self.runner.invoke(app, ["track", "codex"])

        self.assertEqual(result.exit_code, 0)
        launch = run_mock.call_args.args[0]
        self.assertEqual(launch.launch_mode, "registry_alias")
        self.assertEqual(launch.command[0], "codex")
        self.assertEqual(launch.agent, "codex")
        self.assertEqual(launch.model, "unknown-model")
        self.assertIn("Codex", launch.agent_name)

    def test_track_alias_launch_infers_codex_model_from_codex_home(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            codex_home = Path(temp_dir) / ".codex"
            codex_home.mkdir()
            (codex_home / "config.toml").write_text('model = "gpt-5.4"\n', encoding="utf-8")
            with patch.object(cli_app, "_run_storage_preflight_if_enabled"), patch.object(
                track_module,
                "run_tracked_command",
                return_value=0,
            ) as run_mock, patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}, clear=False):
                result = self.runner.invoke(app, ["track", "codex"])

        self.assertEqual(result.exit_code, 0)
        launch = run_mock.call_args.args[0]
        self.assertEqual(launch.model, "gpt-5.4")

    def test_track_raw_launch_supports_double_dash(self):
        with patch.object(cli_app, "_run_storage_preflight_if_enabled"), patch.object(
            track_module,
            "run_tracked_command",
            return_value=0,
        ) as run_mock:
            result = self.runner.invoke(app, ["track", "--", "zsh", "-lc", "echo hi"])

        self.assertEqual(result.exit_code, 0)
        launch = run_mock.call_args.args[0]
        self.assertEqual(launch.launch_mode, "raw_command")
        self.assertEqual(launch.command, ["zsh", "-lc", "echo hi"])

    def test_track_alias_launch_honors_explicit_model_override(self):
        with patch.object(cli_app, "_run_storage_preflight_if_enabled"), patch.object(
            track_module,
            "run_tracked_command",
            return_value=0,
        ) as run_mock:
            result = self.runner.invoke(app, ["track", "--model", "gemini-2.5-pro", "codex"])

        self.assertEqual(result.exit_code, 0)
        launch = run_mock.call_args.args[0]
        self.assertEqual(launch.model, "gemini-2.5-pro")

    def test_track_raw_launch_honors_explicit_model_override_for_unmanaged_provider(self):
        with patch.object(cli_app, "_run_storage_preflight_if_enabled"), patch.object(
            track_module,
            "run_tracked_command",
            return_value=0,
        ) as run_mock:
            result = self.runner.invoke(
                app,
                ["track", "--agent", "claude", "--model", "claude-sonnet-4", "--", "claude"],
            )

        self.assertEqual(result.exit_code, 0)
        launch = run_mock.call_args.args[0]
        self.assertEqual(launch.agent, "claude")
        self.assertEqual(launch.model, "claude-sonnet-4")

    def test_run_tracked_command_records_transcript_and_provenance(self):
        with self._temp_app_paths() as (_, temp_paths):
            launch = track_module.prepare_track_launch(["--", "zsh", "-lc", "echo hi; sleep 0.8 & wait"])
            code = track_module.run_tracked_command(launch)

            self.assertEqual(code, 0)
            transcript_dir = Path(temp_paths.state_dir) / "tracked_sessions"
            transcripts = list(transcript_dir.glob("*.jsonl"))
            self.assertTrue(transcripts)
            transcript_payload = transcripts[0].read_text(encoding="utf-8")
            self.assertIn('"direction":"pty"', transcript_payload)

            store = SQLiteStateStore(temp_paths.state_sqlite_path, journal=None)
            runs = store.list_command_runs(limit=20)
            self.assertGreaterEqual(len(runs), 1)
            payloads = [dict(row.get("payload", {}) or {}) for row in runs]
            self.assertTrue(any(str(item.get("track_session_id", "") or "").strip() for item in payloads))
            self.assertTrue(any(item.get("track_launch_mode") == "raw_command" for item in payloads))


if __name__ == "__main__":
    unittest.main()
