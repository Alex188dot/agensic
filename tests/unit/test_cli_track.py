import importlib
import json
import os
import tempfile
import threading
import time
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

    def _wait_for_active_session(self, temp_paths: ag_paths.AppPaths, timeout: float = 5.0) -> dict[str, object] | None:
        deadline = time.time() + timeout
        store = SQLiteStateStore(temp_paths.state_sqlite_path, journal=None)
        while time.time() < deadline:
            active = store.get_active_tracked_session()
            if active is not None:
                return active
            time.sleep(0.02)
        return None

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

    def test_track_alias_launch_infers_gemini_model_from_settings_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home_dir = Path(temp_dir)
            gemini_dir = home_dir / ".gemini"
            gemini_dir.mkdir()
            (gemini_dir / "settings.json").write_text(
                json.dumps({"model": {"name": "gemini-2.5-pro"}}),
                encoding="utf-8",
            )
            with patch.object(cli_app, "_run_storage_preflight_if_enabled"), patch.object(
                track_module,
                "run_tracked_command",
                return_value=0,
            ) as run_mock, patch.dict(os.environ, {"HOME": str(home_dir)}, clear=False):
                result = self.runner.invoke(app, ["track", "gemini"])

        self.assertEqual(result.exit_code, 0)
        launch = run_mock.call_args.args[0]
        self.assertEqual(launch.model, "gemini-2.5-pro")

    def test_track_raw_launch_infers_claude_model_from_workspace_settings_local_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_dir = Path(temp_dir)
            claude_dir = workspace_dir / ".claude"
            claude_dir.mkdir()
            (claude_dir / "settings.local.json").write_text(
                json.dumps({"model": "claude-sonnet-4-5"}),
                encoding="utf-8",
            )
            with patch.object(cli_app, "_run_storage_preflight_if_enabled"), patch.object(
                track_module,
                "run_tracked_command",
                return_value=0,
            ) as run_mock, patch("os.getcwd", return_value=str(workspace_dir)):
                result = self.runner.invoke(app, ["track", "--", "claude"])

        self.assertEqual(result.exit_code, 0)
        launch = run_mock.call_args.args[0]
        self.assertEqual(launch.agent, "claude_code")
        self.assertEqual(launch.model, "claude-sonnet-4-5")

    def test_track_raw_launch_infers_ollama_model_from_run_subcommand(self):
        with patch.object(cli_app, "_run_storage_preflight_if_enabled"), patch.object(
            track_module,
            "run_tracked_command",
            return_value=0,
        ) as run_mock:
            result = self.runner.invoke(app, ["track", "--", "ollama", "run", "llama3.2"])

        self.assertEqual(result.exit_code, 0)
        launch = run_mock.call_args.args[0]
        self.assertEqual(launch.model, "llama3.2")

    def test_track_raw_launch_infers_openclaw_model_from_openclaw_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home_dir = Path(temp_dir)
            openclaw_dir = home_dir / ".openclaw"
            openclaw_dir.mkdir()
            (openclaw_dir / "openclaw.json").write_text(
                json.dumps({"agents": {"defaults": {"model": {"primary": "anthropic/claude-sonnet-4-5"}}}}),
                encoding="utf-8",
            )
            with patch.object(cli_app, "_run_storage_preflight_if_enabled"), patch.object(
                track_module,
                "run_tracked_command",
                return_value=0,
            ) as run_mock, patch.dict(os.environ, {"HOME": str(home_dir)}, clear=False):
                result = self.runner.invoke(app, ["track", "--agent", "openclaw", "--", "openclaw"])

        self.assertEqual(result.exit_code, 0)
        launch = run_mock.call_args.args[0]
        self.assertEqual(launch.model, "anthropic/claude-sonnet-4-5")

    def test_track_raw_launch_infers_openclaw_model_from_models_json_fallback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home_dir = Path(temp_dir)
            agent_dir = home_dir / ".openclaw" / "agents" / "main" / "agent"
            agent_dir.mkdir(parents=True)
            (agent_dir / "models.json").write_text(
                json.dumps(
                    {
                        "providers": {
                            "qwen-portal": {
                                "models": [
                                    {"id": "coder-model"},
                                ]
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(cli_app, "_run_storage_preflight_if_enabled"), patch.object(
                track_module,
                "run_tracked_command",
                return_value=0,
            ) as run_mock, patch.dict(os.environ, {"HOME": str(home_dir)}, clear=False):
                result = self.runner.invoke(app, ["track", "--agent", "openclaw", "--", "openclaw"])

        self.assertEqual(result.exit_code, 0)
        launch = run_mock.call_args.args[0]
        self.assertEqual(launch.model, "qwen-portal/coder-model")

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
            labels = [str(row.get("label", "") or "") for row in runs]
            commands = [str(row.get("command", "") or "") for row in runs]
            payloads = [dict(row.get("payload", {}) or {}) for row in runs]
            self.assertIn("AI_EXECUTED", labels)
            self.assertTrue(any(command.startswith("zsh -lc ") for command in commands))
            self.assertTrue(any(command.startswith("sleep 0.8") for command in commands))
            self.assertTrue(any(str(item.get("track_session_id", "") or "").strip() for item in payloads))
            self.assertTrue(any(item.get("track_launch_mode") == "raw_command" for item in payloads))

    def test_prune_tracked_transcripts_removes_files_older_than_seven_days(self):
        with self._temp_app_paths() as (_, temp_paths):
            transcript_dir = Path(temp_paths.state_dir) / "tracked_sessions"
            transcript_dir.mkdir(parents=True, exist_ok=True)
            stale_path = transcript_dir / "stale.jsonl"
            fresh_path = transcript_dir / "fresh.jsonl"
            stale_path.write_text("old\n", encoding="utf-8")
            fresh_path.write_text("new\n", encoding="utf-8")

            stale_age = track_module.TRACK_TRANSCRIPT_RETENTION_SECONDS + 10
            stale_mtime = time.time() - stale_age
            os.utime(stale_path, (stale_mtime, stale_mtime))

            result = track_module._prune_tracked_transcripts()

            self.assertEqual(result["removed_files"], 1)
            self.assertFalse(stale_path.exists())
            self.assertTrue(fresh_path.exists())

    def test_prune_tracked_transcripts_enforces_total_size_limit_oldest_first(self):
        with self._temp_app_paths() as (_, temp_paths):
            transcript_dir = Path(temp_paths.state_dir) / "tracked_sessions"
            transcript_dir.mkdir(parents=True, exist_ok=True)
            oldest_path = transcript_dir / "oldest.jsonl"
            protected_path = transcript_dir / "protected.jsonl"
            newest_path = transcript_dir / "newest.jsonl"

            payload = "x" * 10
            oldest_path.write_text(payload, encoding="utf-8")
            protected_path.write_text(payload, encoding="utf-8")
            newest_path.write_text(payload, encoding="utf-8")

            base_time = time.time() - 60
            os.utime(oldest_path, (base_time, base_time))
            os.utime(protected_path, (base_time + 10, base_time + 10))
            os.utime(newest_path, (base_time + 20, base_time + 20))

            with patch.object(track_module, "TRACK_TRANSCRIPT_MAX_TOTAL_BYTES", 15):
                result = track_module._prune_tracked_transcripts(exclude_paths={str(protected_path)})

            self.assertEqual(result["removed_files"], 1)
            self.assertFalse(oldest_path.exists())
            self.assertTrue(protected_path.exists())
            self.assertTrue(newest_path.exists())

    def test_run_tracked_command_uses_app_scoped_provenance_keys(self):
        with self._temp_app_paths() as (_, temp_paths):
            launch = track_module.prepare_track_launch(["--", "zsh", "-lc", "true"])
            code = track_module.run_tracked_command(launch)

            self.assertEqual(code, 0)
            self.assertTrue(Path(temp_paths.provenance_private_key_path).is_file())
            self.assertTrue(Path(temp_paths.provenance_public_key_path).is_file())

    def test_run_tracked_command_records_short_lived_child_process(self):
        with self._temp_app_paths() as (_, temp_paths):
            launch = track_module.prepare_track_launch(["--", "zsh", "-lc", "echo hi; sleep 0.05 & wait"])
            code = track_module.run_tracked_command(launch)

            self.assertEqual(code, 0)
            store = SQLiteStateStore(temp_paths.state_sqlite_path, journal=None)
            commands = [str(row.get("command", "") or "") for row in store.list_command_runs(limit=20)]
            self.assertTrue(any(command.startswith("sleep 0.05") for command in commands), msg=commands)

    def test_run_tracked_command_marks_detached_descendants(self):
        with self._temp_app_paths() as (_, temp_paths):
            daemonize = (
                "python3 -c \"import os,time;"
                "pid=os.fork();"
                "import sys;"
                "time.sleep(0.2) if pid else None;"
                "sys.exit(0) if pid else None;"
                "os.setsid();"
                "time.sleep(1.5)\""
            )
            launch = track_module.prepare_track_launch(["--", "zsh", "-lc", daemonize])
            code = track_module.run_tracked_command(launch)

            self.assertEqual(code, 0)
            store = SQLiteStateStore(temp_paths.state_sqlite_path, journal=None)
            rows = store.list_command_runs(limit=20)
            payloads = [dict(row.get("payload", {}) or {}) for row in rows]
            self.assertTrue(any(payload.get("track_process_detached") for payload in payloads), msg=payloads)
            session = store.get_latest_tracked_session()
            self.assertIsNotNone(session)
            self.assertIn("detached_descendants", str(session.get("violation_code", "") or ""))

    def test_track_stop_uses_sqlite_when_cache_file_is_missing(self):
        with self._temp_app_paths() as (_, temp_paths):
            result_holder: dict[str, int] = {}

            def _run() -> None:
                launch = track_module.prepare_track_launch(["--", "zsh", "-lc", "sleep 30"])
                result_holder["code"] = track_module.run_tracked_command(launch)

            worker = threading.Thread(target=_run)
            worker.start()
            active = self._wait_for_active_session(temp_paths)
            self.assertIsNotNone(active)
            Path(temp_paths.state_dir, "track_session.json").unlink()

            stop_code = track_module.stop_active_track_session()
            worker.join(timeout=10.0)

            self.assertEqual(stop_code, 0)
            self.assertFalse(worker.is_alive())
            self.assertEqual(result_holder.get("code"), 143)
            store = SQLiteStateStore(temp_paths.state_sqlite_path, journal=None)
            session = store.get_tracked_session(str(active.get("session_id", "") or ""))
            self.assertIsNotNone(session)
            self.assertEqual(session["status"], "stopped")

    def test_track_inspect_reports_transcript_and_runs(self):
        with self._temp_app_paths() as (env, temp_paths), patch.object(
            cli_app, "_run_storage_preflight_if_enabled"
        ):
            launch = track_module.prepare_track_launch(["--", "zsh", "-lc", "echo hi"])
            code = track_module.run_tracked_command(launch)
            self.assertEqual(code, 0)

            store = SQLiteStateStore(temp_paths.state_sqlite_path, journal=None)
            session = store.get_latest_tracked_session()
            self.assertIsNotNone(session)
            result = self.runner.invoke(app, ["track", "inspect", str(session["session_id"])], env=env)

        self.assertEqual(result.exit_code, 0)
        self.assertIn("transcript_events=", result.stdout)
        self.assertIn("recorded_runs=", result.stdout)
        self.assertIn("command=", result.stdout)


if __name__ == "__main__":
    unittest.main()
