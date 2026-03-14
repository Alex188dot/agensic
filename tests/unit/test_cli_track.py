import importlib
import json
import multiprocessing
import os
import subprocess
import tempfile
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


def _run_tracked_command_in_child(env: dict[str, str], command: list[str], result_queue) -> None:
    os.environ.update(env)
    temp_paths = ag_paths.get_app_paths()
    cli_app.APP_PATHS = temp_paths
    ag_paths.APP_PATHS = temp_paths
    track_module.APP_PATHS = temp_paths
    launch = track_module.prepare_track_launch(command)
    result_queue.put(track_module.run_tracked_command(launch))


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

    def _wait_for_active_sessions(
        self,
        temp_paths: ag_paths.AppPaths,
        count: int,
        timeout: float = 8.0,
    ) -> list[dict[str, object]]:
        deadline = time.time() + timeout
        store = SQLiteStateStore(temp_paths.state_sqlite_path, journal=None)
        while time.time() < deadline:
            active = store.list_active_tracked_sessions(limit=200)
            if len(active) >= count:
                return active
            time.sleep(0.02)
        return []

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

    def test_track_status_lists_multiple_active_sessions(self):
        with self._temp_app_paths() as (env, temp_paths), patch.object(
            cli_app, "_run_storage_preflight_if_enabled"
        ):
            ctx = multiprocessing.get_context("spawn")
            queue_one = ctx.Queue()
            queue_two = ctx.Queue()
            worker_one = ctx.Process(
                target=_run_tracked_command_in_child,
                args=(env, ["--", "zsh", "-lc", "sleep 30"], queue_one),
            )
            worker_two = ctx.Process(
                target=_run_tracked_command_in_child,
                args=(env, ["--", "zsh", "-lc", "sleep 30"], queue_two),
            )
            worker_one.start()
            worker_two.start()
            active = self._wait_for_active_sessions(temp_paths, 2)
            self.assertGreaterEqual(len(active), 2)

            result = self.runner.invoke(app, ["track", "status"], env=env)
            self.assertEqual(result.exit_code, 0)
            self.assertIn("active_sessions=2", result.stdout)
            for row in active[:2]:
                self.assertIn(str(row["session_id"]), result.stdout)

            stop_result = self.runner.invoke(app, ["track", "stop", "--all"], env=env)
            worker_one.join(timeout=10.0)
            worker_two.join(timeout=10.0)

        self.assertEqual(stop_result.exit_code, 0)
        self.assertFalse(worker_one.is_alive())
        self.assertFalse(worker_two.is_alive())
        self.assertEqual(worker_one.exitcode, 0)
        self.assertEqual(worker_two.exitcode, 0)
        self.assertEqual(queue_one.get(timeout=1.0), 143)
        self.assertEqual(queue_two.get(timeout=1.0), 143)

    def test_track_stop_requires_session_id_when_multiple_active(self):
        with self._temp_app_paths() as (env, temp_paths), patch.object(
            cli_app, "_run_storage_preflight_if_enabled"
        ):
            ctx = multiprocessing.get_context("spawn")
            queue_one = ctx.Queue()
            queue_two = ctx.Queue()
            worker_one = ctx.Process(
                target=_run_tracked_command_in_child,
                args=(env, ["--", "zsh", "-lc", "sleep 30"], queue_one),
            )
            worker_two = ctx.Process(
                target=_run_tracked_command_in_child,
                args=(env, ["--", "zsh", "-lc", "sleep 30"], queue_two),
            )
            worker_one.start()
            worker_two.start()
            active = self._wait_for_active_sessions(temp_paths, 2)
            self.assertGreaterEqual(len(active), 2)

            result = self.runner.invoke(app, ["track", "stop"], env=env)
            self.assertEqual(result.exit_code, 2)
            self.assertIn("Multiple tracked sessions are active", result.stdout)

            cleanup = self.runner.invoke(app, ["track", "stop", "--all"], env=env)
            worker_one.join(timeout=10.0)
            worker_two.join(timeout=10.0)

        self.assertEqual(cleanup.exit_code, 0)
        self.assertFalse(worker_one.is_alive())
        self.assertFalse(worker_two.is_alive())
        self.assertEqual(queue_one.get(timeout=1.0), 143)
        self.assertEqual(queue_two.get(timeout=1.0), 143)

    def test_stop_track_sessions_targets_selected_session_id(self):
        states = [
            {"session_id": "session-a", "root_pid": 101, "status": "active"},
            {"session_id": "session-b", "root_pid": 202, "status": "active"},
        ]
        with patch.object(track_module, "list_active_track_states", return_value=states), patch.object(
            track_module, "_request_track_session_stop", return_value=0
        ) as stop_mock, patch.object(track_module, "_refresh_track_state_cache"):
            code = track_module.stop_track_sessions("session-b")

        self.assertEqual(code, 0)
        stop_mock.assert_called_once_with(states[1])

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

    def test_track_alias_launch_prefers_gemini_cli_model_flag(self):
        with patch.object(cli_app, "_run_storage_preflight_if_enabled"), patch.object(
            track_module,
            "run_tracked_command",
            return_value=0,
        ) as run_mock:
            result = self.runner.invoke(app, ["track", "gemini", "--model", "gemini-2.5-flash"])

        self.assertEqual(result.exit_code, 0)
        launch = run_mock.call_args.args[0]
        self.assertEqual(launch.model, "gemini-2.5-flash")

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

    def test_track_raw_launch_prefers_claude_model_flag(self):
        with patch.object(cli_app, "_run_storage_preflight_if_enabled"), patch.object(
            track_module,
            "run_tracked_command",
            return_value=0,
        ) as run_mock:
            result = self.runner.invoke(app, ["track", "--", "claude", "--model", "claude-opus-4-1"])

        self.assertEqual(result.exit_code, 0)
        launch = run_mock.call_args.args[0]
        self.assertEqual(launch.agent, "claude_code")
        self.assertEqual(launch.model, "claude-opus-4-1")

    def test_track_alias_launch_infers_opencode_model_from_jsonc_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home_dir = Path(temp_dir)
            config_dir = home_dir / ".config" / "opencode"
            config_dir.mkdir(parents=True)
            (config_dir / "opencode.jsonc").write_text(
                '{\n  // preferred model\n  "model": "claude-sonnet-4.5",\n}\n',
                encoding="utf-8",
            )
            with patch.object(cli_app, "_run_storage_preflight_if_enabled"), patch.object(
                track_module,
                "run_tracked_command",
                return_value=0,
            ) as run_mock, patch.dict(
                os.environ,
                {"HOME": str(home_dir), "XDG_CONFIG_HOME": str(home_dir / ".config")},
                clear=False,
            ):
                result = self.runner.invoke(app, ["track", "opencode"])

        self.assertEqual(result.exit_code, 0)
        launch = run_mock.call_args.args[0]
        self.assertEqual(launch.agent, "opencode")
        self.assertEqual(launch.model, "claude-sonnet-4.5")

    def test_track_alias_launch_prefers_opencode_model_flag(self):
        with patch.object(cli_app, "_run_storage_preflight_if_enabled"), patch.object(
            track_module,
            "run_tracked_command",
            return_value=0,
        ) as run_mock:
            result = self.runner.invoke(app, ["track", "opencode", "-m", "gpt-4.1"])

        self.assertEqual(result.exit_code, 0)
        launch = run_mock.call_args.args[0]
        self.assertEqual(launch.agent, "opencode")
        self.assertEqual(launch.model, "gpt-4.1")

    def test_track_raw_launch_infers_kilo_model_from_project_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_dir = Path(temp_dir)
            (workspace_dir / "kilocode.json").write_text(
                json.dumps({"model": "gemini-2.5-pro"}),
                encoding="utf-8",
            )
            with patch.object(cli_app, "_run_storage_preflight_if_enabled"), patch.object(
                track_module,
                "run_tracked_command",
                return_value=0,
            ) as run_mock, patch("os.getcwd", return_value=str(workspace_dir)):
                result = self.runner.invoke(app, ["track", "--", "kilo"])

        self.assertEqual(result.exit_code, 0)
        launch = run_mock.call_args.args[0]
        self.assertEqual(launch.agent, "kilocode")
        self.assertEqual(launch.model, "gemini-2.5-pro")

    def test_track_raw_launch_infers_github_copilot_model_from_config_dir(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            copilot_home = Path(temp_dir) / ".copilot"
            copilot_home.mkdir()
            (copilot_home / "config.json").write_text(
                json.dumps({"model": "gpt-5"}),
                encoding="utf-8",
            )
            with patch.object(cli_app, "_run_storage_preflight_if_enabled"), patch.object(
                track_module,
                "run_tracked_command",
                return_value=0,
            ) as run_mock, patch.dict(os.environ, {"HOME": str(Path(temp_dir))}, clear=False):
                result = self.runner.invoke(app, ["track", "--", "gh", "copilot"])

        self.assertEqual(result.exit_code, 0)
        launch = run_mock.call_args.args[0]
        self.assertEqual(launch.agent, "github_copilot")
        self.assertEqual(launch.model, "gpt-5")

    def test_track_raw_launch_does_not_treat_generic_gh_as_copilot(self):
        with patch.object(cli_app, "_run_storage_preflight_if_enabled"), patch.object(
            track_module,
            "run_tracked_command",
            return_value=0,
        ) as run_mock:
            result = self.runner.invoke(app, ["track", "--", "gh", "repo", "list"])

        self.assertEqual(result.exit_code, 0)
        launch = run_mock.call_args.args[0]
        self.assertEqual(launch.agent, "unknown")
        self.assertEqual(launch.model, "unknown-model")

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

    def test_track_open_app_launch_recognizes_ollama_agent_and_model(self):
        with patch.object(cli_app, "_run_storage_preflight_if_enabled"), patch.object(
            track_module,
            "run_tracked_command",
            return_value=0,
        ) as run_mock:
            result = self.runner.invoke(
                app,
                [
                    "track",
                    "--",
                    "open",
                    "-j",
                    "-a",
                    "/Applications/Ollama.app",
                    "--args",
                    "run",
                    "sam860/LFM2:1.2b",
                ],
            )

        self.assertEqual(result.exit_code, 0)
        launch = run_mock.call_args.args[0]
        self.assertEqual(launch.agent, "ollama")
        self.assertEqual(launch.agent_name, "Ollama")
        self.assertEqual(launch.model, "sam860/LFM2:1.2b")

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

    def test_build_tracked_child_env_only_injects_tracking_metadata(self):
        with self._temp_app_paths():
            launch = track_module.prepare_track_launch(["--", "zsh", "-lc", "echo hi"])
            child_env = track_module._build_tracked_child_env(launch, "session-policy")

            self.assertEqual(child_env["AGENSIC_TRACK_ACTIVE"], "1")
            self.assertEqual(child_env["AGENSIC_TRACK_SESSION_ID"], "session-policy")
            self.assertEqual(child_env["AGENSIC_TRACK_AGENT"], launch.agent)
            self.assertEqual(child_env["AGENSIC_TRACK_MODEL"], launch.model)
            self.assertNotIn("AGENSIC_TRACK_POLICY_DIR", child_env)
            self.assertNotIn("ZDOTDIR", child_env)
            self.assertNotIn("BASH_ENV", child_env)

    def test_escape_primitive_detector_matches_nohup_and_terminal_automation(self):
        self.assertTrue(
            track_module._looks_like_escape_primitive(
                {"comm": "zsh", "args": "zsh -ic 'nohup sleep 5 >/tmp/x 2>&1 & disown'"}
            )
        )
        self.assertTrue(
            track_module._looks_like_escape_primitive(
                {"comm": "osascript", "args": 'osascript -e \'tell application "Terminal" to do script "sleep 600"\''}
            )
        )
        self.assertTrue(track_module._looks_like_escape_primitive({"comm": "launchctl", "args": "launchctl submit -l demo -- sleep 60"}))

    def test_run_tracked_command_records_transcript_and_provenance(self):
        with self._temp_app_paths() as (_, temp_paths):
            launch = track_module.prepare_track_launch(["--", "zsh", "-lc", "echo hi; sleep 0.8 & wait"])
            code = track_module.run_tracked_command(launch)

            self.assertEqual(code, 0)
            transcript_dir = Path(temp_paths.state_dir) / "tracked_sessions"
            transcripts = list(transcript_dir.glob("*.transcript.jsonl"))
            self.assertTrue(transcripts)
            transcript_payload = transcripts[0].read_text(encoding="utf-8")
            self.assertIn('"direction":"pty"', transcript_payload)
            self.assertIn('"seq":', transcript_payload)

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

    def test_write_transcript_resize_event_records_rows_and_cols(self):
        handle = tempfile.NamedTemporaryFile("w+", encoding="utf-8", delete=False)
        handle.close()
        try:
            with open(handle.name, "a", encoding="utf-8") as transcript:
                track_module._write_transcript_resize_event(
                    transcript,
                    rows=33,
                    cols=120,
                    seq=7,
                )

            payload = Path(handle.name).read_text(encoding="utf-8").strip()
            event = json.loads(payload)
            self.assertEqual(event["direction"], "resize")
            self.assertEqual(event["rows"], 33)
            self.assertEqual(event["cols"], 120)
            self.assertEqual(event["seq"], 7)
        finally:
            Path(handle.name).unlink(missing_ok=True)

    def test_run_tracked_command_records_session_summary_and_events(self):
        with self._temp_app_paths() as (_, temp_paths), tempfile.TemporaryDirectory() as repo_dir:
            subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True, text=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_dir, check=True)
            Path(repo_dir, "README.md").write_text("hello\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=repo_dir, check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo_dir, check=True, capture_output=True, text=True)

            with patch("os.getcwd", return_value=repo_dir):
                launch = track_module.prepare_track_launch(
                    ["--", "zsh", "-lc", "echo changed > README.md; git add README.md; git commit -m 'update readme'"],
                )
            code = track_module.run_tracked_command(launch)

            self.assertEqual(code, 0)
            store = SQLiteStateStore(temp_paths.state_sqlite_path, journal=None)
            session = store.get_latest_tracked_session()
            self.assertIsNotNone(session)
            summary = store.get_session_summary(str(session["session_id"]))
            self.assertIsNotNone(summary)
            self.assertEqual(os.path.realpath(str(summary["repo_root"])), os.path.realpath(repo_dir))
            self.assertGreaterEqual(int(summary["aggregate"].get("command_count", 0) or 0), 1)
            self.assertIn("README.md", list(summary["changes"].get("files_changed", []) or []))
            self.assertTrue(list(summary["changes"].get("commits_created", []) or []))
            event_stream_path = Path(str(summary.get("event_stream_path", "") or ""))
            self.assertTrue(event_stream_path.is_file())
            event_payload = event_stream_path.read_text(encoding="utf-8")
            self.assertIn('"type":"git.snapshot.start"', event_payload)
            self.assertIn('"type":"command.recorded"', event_payload)

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
            launch = track_module.prepare_track_launch(["--", "zsh", "-lc", "echo hi; sleep 0.2 & wait"])
            code = track_module.run_tracked_command(launch)

            self.assertEqual(code, 0)
            store = SQLiteStateStore(temp_paths.state_sqlite_path, journal=None)
            commands = [str(row.get("command", "") or "") for row in store.list_command_runs(limit=20)]
            self.assertTrue(any(command.startswith("sleep 0.2") for command in commands), msg=commands)

    def test_run_tracked_command_observes_session_boundary_escape(self):
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
            self.assertTrue(any(payload.get("track_process_session_escape") for payload in payloads), msg=payloads)
            session = store.get_latest_tracked_session()
            self.assertIsNotNone(session)
            self.assertIn("session_boundary_escape", str(session.get("violation_code", "") or ""))

    def test_run_tracked_command_observes_escape_primitives_without_blocking(self):
        with self._temp_app_paths() as (_, temp_paths):
            launch = track_module.prepare_track_launch(
                ["--", "zsh", "-ic", "nohup sleep 5 >/tmp/agensic-track-test.log 2>&1 & disown; echo should-not-print"]
            )
            code = track_module.run_tracked_command(launch)

            self.assertEqual(code, 0)
            store = SQLiteStateStore(temp_paths.state_sqlite_path, journal=None)
            session = store.get_latest_tracked_session()
            self.assertIsNotNone(session)
            self.assertIn("escape_primitive_blocked", str(session.get("violation_code", "") or ""))

    def test_track_stop_uses_sqlite_when_cache_file_is_missing(self):
        with self._temp_app_paths() as (env, temp_paths):
            ctx = multiprocessing.get_context("spawn")
            result_queue = ctx.Queue()
            worker = ctx.Process(
                target=_run_tracked_command_in_child,
                args=(env, ["--", "zsh", "-lc", "sleep 30"], result_queue),
            )
            worker.start()
            active = self._wait_for_active_session(temp_paths)
            self.assertIsNotNone(active)
            Path(temp_paths.state_dir, "track_session.json").unlink()

            stop_code = track_module.stop_track_sessions()
            worker.join(timeout=10.0)

            self.assertEqual(stop_code, 0)
            self.assertFalse(worker.is_alive())
            self.assertEqual(worker.exitcode, 0)
            self.assertEqual(result_queue.get(timeout=1.0), 143)
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
