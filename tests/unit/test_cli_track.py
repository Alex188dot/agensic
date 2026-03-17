import importlib
import gzip
import json
import multiprocessing
import os
import shlex
import subprocess
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
provenance_module = importlib.import_module("agensic.engine.provenance")
app = cli_app.app


class _FakeDaemonResponse:
    def __init__(self, status_code: int, payload: dict[str, object] | None = None) -> None:
        self.status_code = int(status_code)
        self._payload = dict(payload or {})
        self.text = json.dumps(self._payload)

    def json(self) -> dict[str, object]:
        return dict(self._payload)


def _run_tracked_command_in_child(env: dict[str, str], command: list[str], result_queue) -> None:
    os.environ.update(env)
    temp_paths = ag_paths.get_app_paths()
    cli_app.APP_PATHS = temp_paths
    ag_paths.APP_PATHS = temp_paths
    track_module.APP_PATHS = temp_paths
    launch = _make_test_launch(command[1:] if command and command[0] == "--" else command)
    result_queue.put(track_module.run_tracked_command(launch))


def _make_test_launch(
    command: list[str],
    *,
    agent: str = "codex",
    model: str = "unknown-model",
    agent_name: str = "OpenAI Codex",
) -> track_module.TrackLaunch:
    return track_module.TrackLaunch(
        command=list(command),
        launch_mode="raw_command",
        agent=agent,
        model=model,
        agent_name=agent_name,
        working_directory=os.getcwd(),
        root_command=shlex.join(command),
    )


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
            ), patch.object(
                provenance_module,
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

    @contextmanager
    def _mock_track_daemon(self, temp_paths: ag_paths.AppPaths):
        def _fake_daemon_request(method: str, path: str, timeout: float, **kwargs):
            if method.upper() != "POST" or path != "/log_command":
                return _FakeDaemonResponse(404, {"status": "ignored", "reason": "unsupported"})
            payload = dict(kwargs.get("json") or {})
            store = SQLiteStateStore(temp_paths.state_sqlite_path, journal=None)
            ok, reason = store.verify_tracked_session_capability(
                str(payload.get("track_session_id", "") or "").strip(),
                str(payload.get("track_session_capability", "") or "").strip(),
            )
            if not ok:
                return _FakeDaemonResponse(200, {"status": "ignored", "reason": reason})
            sanitized_payload = {str(k): v for k, v in payload.items() if str(k) != "track_session_capability"}
            sanitized_payload["track_capability_verified"] = True
            classification = track_module.classify_command_run(
                str(payload.get("command", "") or ""),
                sanitized_payload,
                proof_public_path=temp_paths.provenance_public_key_path,
            )
            store.record_command_provenance(
                command=str(payload.get("command", "") or ""),
                label=str(classification.get("label", "UNKNOWN") or "UNKNOWN"),
                confidence=float(classification.get("confidence", 0.0) or 0.0),
                agent=str(classification.get("agent", "") or ""),
                agent_name=str(classification.get("agent_name", "") or ""),
                provider=str(classification.get("provider", "") or ""),
                model=str(classification.get("model", "") or ""),
                raw_model=str(classification.get("raw_model", "") or ""),
                normalized_model=str(classification.get("normalized_model", "") or ""),
                model_fingerprint=str(classification.get("model_fingerprint", "") or ""),
                evidence_tier=str(classification.get("evidence_tier", "") or ""),
                agent_source=str(classification.get("agent_source", "") or ""),
                registry_version=str(classification.get("registry_version", "") or ""),
                registry_status=str(classification.get("registry_status", "") or ""),
                source=str(payload.get("source", "runtime") or "runtime"),
                working_directory=str(payload.get("working_directory", "") or ""),
                exit_code=payload.get("exit_code"),
                duration_ms=payload.get("duration_ms"),
                shell_pid=payload.get("shell_pid"),
                evidence=[str(item) for item in classification.get("evidence", []) if str(item)],
                payload=sanitized_payload,
                run_id=f"{payload.get('track_session_id', '')}:{payload.get('track_process_pid', payload.get('shell_pid', ''))}",
                ts=int(payload.get("proof_timestamp", 0) or 0),
            )
            return _FakeDaemonResponse(200, {"status": "ok"})

        with patch.object(track_module, "_daemon_request", side_effect=_fake_daemon_request):
            yield

    def test_track_status_inactive(self):
        with self._temp_app_paths() as (env, _), patch.object(
            cli_app, "_run_storage_preflight_if_enabled"
        ):
            result = self.runner.invoke(app, ["run", "status"], env=env)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("inactive", result.stdout)

    def test_track_stop_inactive(self):
        with self._temp_app_paths() as (env, _), patch.object(
            cli_app, "_run_storage_preflight_if_enabled"
        ):
            result = self.runner.invoke(app, ["run", "stop"], env=env)
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

            result = self.runner.invoke(app, ["run", "status"], env=env)
            self.assertEqual(result.exit_code, 0)
            self.assertIn("active_sessions=2", result.stdout)
            for row in active[:2]:
                self.assertIn(str(row["session_id"]), result.stdout)

            stop_result = self.runner.invoke(app, ["run", "stop", "--all"], env=env)
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

            result = self.runner.invoke(app, ["run", "stop"], env=env)
            self.assertEqual(result.exit_code, 2)
            self.assertIn("Multiple tracked sessions are active", result.stdout)

            cleanup = self.runner.invoke(app, ["run", "stop", "--all"], env=env)
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
            result = self.runner.invoke(app, ["run", "codex"])

        self.assertEqual(result.exit_code, 0)
        launch = run_mock.call_args.args[0]
        self.assertEqual(launch.launch_mode, "registry_alias")
        self.assertEqual(launch.command[0], "codex")
        self.assertEqual(launch.agent, "codex")
        self.assertEqual(launch.model, "unknown-model")
        self.assertIn("Codex", launch.agent_name)

    def test_add_custom_agent_shorthand_registers_agent(self):
        with self._temp_app_paths() as (env, temp_paths), patch.object(
            cli_app, "_run_storage_preflight_if_enabled"
        ):
            result = self.runner.invoke(app, ["--add_agent", "myagent"], env=env)
            self.assertEqual(result.exit_code, 0)
            self.assertIn("agensic run myagent", result.stdout)
            override_path = Path(temp_paths.agent_registry_local_override_path)
            self.assertTrue(override_path.is_file())
            payload = json.loads(override_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["agents"][0]["agent_id"], "myagent")

    def test_list_known_agents_marks_builtin_and_custom_sources(self):
        with self._temp_app_paths() as (env, _), patch.object(
            cli_app, "_run_storage_preflight_if_enabled"
        ):
            added = self.runner.invoke(app, ["--add_agent", "myagent"], env=env)
            self.assertEqual(added.exit_code, 0)
            agents = track_module.list_known_agents()

        by_id = {str(row["agent_id"]): row for row in agents}
        self.assertEqual(by_id["cursor"]["source"], "builtin")
        self.assertEqual(by_id["cursor"]["status"], "verified")
        self.assertEqual(by_id["codex"]["status"], "community")
        self.assertEqual(by_id["myagent"]["source"], "custom")
        self.assertEqual(by_id["myagent"]["status"], "community")

    def test_add_custom_agent_rejects_builtin_agent(self):
        with self._temp_app_paths() as (env, _), patch.object(
            cli_app, "_run_storage_preflight_if_enabled"
        ):
            result = self.runner.invoke(app, ["--add_agent", "codex"], env=env)

        self.assertEqual(result.exit_code, 2)
        self.assertIn("already mapped", result.stdout)

    def test_run_accepts_custom_agent_after_registration(self):
        with self._temp_app_paths() as (env, _), patch.object(
            cli_app, "_run_storage_preflight_if_enabled"
        ), patch.object(
            track_module,
            "run_tracked_command",
            return_value=0,
        ) as run_mock:
            added = self.runner.invoke(app, ["--add_agent", "myagent"], env=env)
            self.assertEqual(added.exit_code, 0)
            result = self.runner.invoke(app, ["run", "myagent"], env=env)

        self.assertEqual(result.exit_code, 0)
        launch = run_mock.call_args.args[0]
        self.assertEqual(launch.agent, "myagent")
        self.assertEqual(launch.command[0], "myagent")

    def test_remove_custom_agent_deletes_local_override_entry(self):
        with self._temp_app_paths() as (env, temp_paths), patch.object(
            cli_app, "_run_storage_preflight_if_enabled"
        ):
            added = self.runner.invoke(app, ["--add_agent", "myagent"], env=env)
            self.assertEqual(added.exit_code, 0)
            removed = track_module.remove_custom_agent("myagent")

            self.assertEqual(removed["agent_id"], "myagent")
            payload = json.loads(Path(temp_paths.agent_registry_local_override_path).read_text(encoding="utf-8"))
            self.assertEqual(payload["agents"], [])

    def test_run_accepts_qwen_alias_and_uses_qwen_executable(self):
        with patch.object(cli_app, "_run_storage_preflight_if_enabled"), patch.object(
            track_module,
            "run_tracked_command",
            return_value=0,
        ) as run_mock:
            result = self.runner.invoke(app, ["run", "qwen"])

        self.assertEqual(result.exit_code, 0)
        launch = run_mock.call_args.args[0]
        self.assertEqual(launch.agent, "qwen_code")
        self.assertEqual(launch.command[0], "qwen")

    def test_run_accepts_cursor_alias_and_uses_cursor_agent_executable(self):
        with patch.object(cli_app, "_run_storage_preflight_if_enabled"), patch.object(
            track_module,
            "run_tracked_command",
            return_value=0,
        ) as run_mock:
            result = self.runner.invoke(app, ["run", "cursor"])

        self.assertEqual(result.exit_code, 0)
        launch = run_mock.call_args.args[0]
        self.assertEqual(launch.agent, "cursor")
        self.assertEqual(launch.command[0], "agent")

    def test_run_accepts_kiro_alias_and_uses_kiro_cli_executable(self):
        with patch.object(cli_app, "_run_storage_preflight_if_enabled"), patch.object(
            track_module,
            "run_tracked_command",
            return_value=0,
        ) as run_mock:
            result = self.runner.invoke(app, ["run", "kiro"])

        self.assertEqual(result.exit_code, 0)
        launch = run_mock.call_args.args[0]
        self.assertEqual(launch.agent, "kiro")
        self.assertEqual(launch.command[0], "kiro-cli")

    def test_run_accepts_droid_alias_and_uses_droid_executable(self):
        with patch.object(cli_app, "_run_storage_preflight_if_enabled"), patch.object(
            track_module,
            "run_tracked_command",
            return_value=0,
        ) as run_mock:
            result = self.runner.invoke(app, ["run", "droid"])

        self.assertEqual(result.exit_code, 0)
        launch = run_mock.call_args.args[0]
        self.assertEqual(launch.agent, "droid")
        self.assertEqual(launch.command[0], "droid")

    def test_run_rejects_agent_override_option(self):
        with patch.object(cli_app, "_run_storage_preflight_if_enabled"):
            result = self.runner.invoke(app, ["run", "--agent", "foo", "codex"])

        self.assertEqual(result.exit_code, 2)
        self.assertIn("Unsupported run option:", result.stdout)

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
                result = self.runner.invoke(app, ["run", "codex"])

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
                result = self.runner.invoke(app, ["run", "gemini"])

        self.assertEqual(result.exit_code, 0)
        launch = run_mock.call_args.args[0]
        self.assertEqual(launch.model, "gemini-2.5-pro")

    def test_track_alias_launch_prefers_gemini_cli_model_flag(self):
        with patch.object(cli_app, "_run_storage_preflight_if_enabled"), patch.object(
            track_module,
            "run_tracked_command",
            return_value=0,
        ) as run_mock:
            result = self.runner.invoke(app, ["run", "gemini", "--model", "gemini-2.5-flash"])

        self.assertEqual(result.exit_code, 0)
        launch = run_mock.call_args.args[0]
        self.assertEqual(launch.model, "gemini-2.5-flash")

    def test_track_alias_launch_infers_claude_model_from_workspace_settings_local_json(self):
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
                result = self.runner.invoke(app, ["run", "claude"])

        self.assertEqual(result.exit_code, 0)
        launch = run_mock.call_args.args[0]
        self.assertEqual(launch.agent, "claude_code")
        self.assertEqual(launch.model, "claude-sonnet-4-5")

    def test_track_alias_launch_prefers_claude_model_flag(self):
        with patch.object(cli_app, "_run_storage_preflight_if_enabled"), patch.object(
            track_module,
            "run_tracked_command",
            return_value=0,
        ) as run_mock:
            result = self.runner.invoke(app, ["run", "claude", "--model", "claude-opus-4-1"])

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
                result = self.runner.invoke(app, ["run", "opencode"])

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
            result = self.runner.invoke(app, ["run", "opencode", "-m", "gpt-4.1"])

        self.assertEqual(result.exit_code, 0)
        launch = run_mock.call_args.args[0]
        self.assertEqual(launch.agent, "opencode")
        self.assertEqual(launch.model, "gpt-4.1")

    def test_track_alias_launch_infers_kilo_model_from_project_config(self):
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
                result = self.runner.invoke(app, ["run", "kilo"])

        self.assertEqual(result.exit_code, 0)
        launch = run_mock.call_args.args[0]
        self.assertEqual(launch.agent, "kilocode")
        self.assertEqual(launch.model, "gemini-2.5-pro")

    def test_track_alias_launch_infers_github_copilot_model_from_config_dir(self):
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
                result = self.runner.invoke(app, ["run", "copilot"])

        self.assertEqual(result.exit_code, 0)
        launch = run_mock.call_args.args[0]
        self.assertEqual(launch.agent, "github_copilot")
        self.assertEqual(launch.model, "gpt-5")

    def test_track_alias_launch_rejects_unrecognized_agent(self):
        with patch.object(cli_app, "_run_storage_preflight_if_enabled"), patch.object(
            track_module,
            "run_tracked_command",
            return_value=0,
        ) as run_mock:
            result = self.runner.invoke(app, ["run", "gh"])

        self.assertEqual(result.exit_code, 2)
        self.assertIn("is not recognized", result.stdout)
        self.assertIn('agensic --add_agent "gh"', result.stdout)
        run_mock.assert_not_called()

    def test_track_alias_launch_infers_ollama_model_from_run_subcommand(self):
        with patch.object(cli_app, "_run_storage_preflight_if_enabled"), patch.object(
            track_module,
            "run_tracked_command",
            return_value=0,
        ) as run_mock:
            result = self.runner.invoke(app, ["run", "ollama", "run", "llama3.2"])

        self.assertEqual(result.exit_code, 0)
        launch = run_mock.call_args.args[0]
        self.assertEqual(launch.model, "llama3.2")

    def test_track_alias_launch_infers_openclaw_model_from_openclaw_config(self):
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
                result = self.runner.invoke(app, ["run", "openclaw"])

        self.assertEqual(result.exit_code, 0)
        launch = run_mock.call_args.args[0]
        self.assertEqual(launch.model, "anthropic/claude-sonnet-4-5")

    def test_track_alias_launch_infers_openclaw_model_from_models_json_fallback(self):
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
                result = self.runner.invoke(app, ["run", "openclaw"])

        self.assertEqual(result.exit_code, 0)
        launch = run_mock.call_args.args[0]
        self.assertEqual(launch.model, "qwen-portal/coder-model")

    def test_track_alias_launch_prefers_droid_exec_model_flag(self):
        with patch.object(cli_app, "_run_storage_preflight_if_enabled"), patch.object(
            track_module,
            "run_tracked_command",
            return_value=0,
        ) as run_mock:
            result = self.runner.invoke(app, ["run", "droid", "exec", "-m", "sonnet"])

        self.assertEqual(result.exit_code, 0)
        launch = run_mock.call_args.args[0]
        self.assertEqual(launch.agent, "droid")
        self.assertEqual(launch.command[0], "droid")
        self.assertEqual(launch.model, "sonnet")

    def test_track_run_rejects_raw_mode(self):
        with patch.object(cli_app, "_run_storage_preflight_if_enabled"), patch.object(
            track_module,
            "run_tracked_command",
            return_value=0,
        ) as run_mock:
            result = self.runner.invoke(app, ["run", "--", "zsh", "-lc", "echo hi"])

        self.assertEqual(result.exit_code, 2)
        self.assertIn("Agent 'zsh' is not recognized", result.stdout)
        run_mock.assert_not_called()

    def test_track_alias_launch_honors_explicit_model_override(self):
        with patch.object(cli_app, "_run_storage_preflight_if_enabled"), patch.object(
            track_module,
            "run_tracked_command",
            return_value=0,
        ) as run_mock:
            result = self.runner.invoke(app, ["run", "--model", "gemini-2.5-pro", "codex"])

        self.assertEqual(result.exit_code, 0)
        launch = run_mock.call_args.args[0]
        self.assertEqual(launch.model, "gemini-2.5-pro")

    def test_track_alias_launch_honors_explicit_model_override(self):
        with patch.object(cli_app, "_run_storage_preflight_if_enabled"), patch.object(
            track_module,
            "run_tracked_command",
            return_value=0,
        ) as run_mock:
            result = self.runner.invoke(app, ["run", "--model", "claude-sonnet-4", "claude"])

        self.assertEqual(result.exit_code, 0)
        launch = run_mock.call_args.args[0]
        self.assertEqual(launch.agent, "claude_code")
        self.assertEqual(launch.model, "claude-sonnet-4")

    def test_build_tracked_child_env_only_injects_tracking_metadata(self):
        with self._temp_app_paths():
            launch = _make_test_launch(["zsh", "-lc", "echo hi"])
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
        with self._temp_app_paths() as (_, temp_paths), self._mock_track_daemon(temp_paths):
            launch = _make_test_launch(["zsh", "-lc", "echo hi; sleep 0.8 & wait"])
            code = track_module.run_tracked_command(launch)

            self.assertEqual(code, 0)
            transcript_dir = Path(temp_paths.state_dir) / "tracked_sessions"
            transcripts = list(transcript_dir.glob("*.transcript.jsonl.gz"))
            self.assertTrue(transcripts)
            with gzip.open(transcripts[0], "rt", encoding="utf-8") as handle:
                transcript_payload = handle.read()
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
        with self._temp_app_paths() as (_, temp_paths), self._mock_track_daemon(temp_paths), tempfile.TemporaryDirectory() as repo_dir:
            subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True, text=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_dir, check=True)
            Path(repo_dir, "README.md").write_text("hello\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=repo_dir, check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo_dir, check=True, capture_output=True, text=True)

            with patch("os.getcwd", return_value=repo_dir):
                launch = _make_test_launch(
                    ["zsh", "-lc", "echo changed > README.md; git add README.md; git commit -m 'update readme'"],
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
            transcript_path = Path(str(summary.get("transcript_path", "") or ""))
            event_stream_path = Path(str(summary.get("event_stream_path", "") or ""))
            self.assertTrue(str(transcript_path).endswith(".gz"))
            self.assertTrue(str(event_stream_path).endswith(".gz"))
            self.assertTrue(transcript_path.is_file())
            self.assertTrue(event_stream_path.is_file())
            with gzip.open(event_stream_path, "rt", encoding="utf-8") as handle:
                event_payload = handle.read()
            self.assertIn('"type":"git.snapshot.start"', event_payload)
            self.assertIn('"type":"command.recorded"', event_payload)

    def test_run_tracked_command_emits_git_commit_created_before_session_end(self):
        with self._temp_app_paths() as (_, temp_paths), self._mock_track_daemon(temp_paths), tempfile.TemporaryDirectory() as repo_dir:
            subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True, text=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_dir, check=True)
            Path(repo_dir, "README.md").write_text("hello\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=repo_dir, check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo_dir, check=True, capture_output=True, text=True)

            with patch("os.getcwd", return_value=repo_dir):
                launch = _make_test_launch(
                    ["zsh", "-lc", "echo changed > README.md; git add README.md; git commit -m 'update readme'"],
                )
            code = track_module.run_tracked_command(launch)

            self.assertEqual(code, 0)
            store = SQLiteStateStore(temp_paths.state_sqlite_path, journal=None)
            session = store.get_latest_tracked_session()
            self.assertIsNotNone(session)
            summary = store.get_session_summary(str(session["session_id"]))
            self.assertIsNotNone(summary)
            event_stream_path = Path(str(summary.get("event_stream_path", "") or ""))
            self.assertTrue(event_stream_path.is_file())

            with gzip.open(event_stream_path, "rt", encoding="utf-8") as handle:
                events = [json.loads(line) for line in handle if line.strip()]

            event_types = [str(event.get("type", "") or "") for event in events]
            commit_indices = [idx for idx, event_type in enumerate(event_types) if event_type == "git.commit.created"]
            self.assertEqual(len(commit_indices), 1)
            snapshot_end_index = event_types.index("git.snapshot.end")
            self.assertLess(commit_indices[0], snapshot_end_index)

    def test_run_tracked_command_marks_external_commits_as_session_sync(self):
        with self._temp_app_paths() as (_, temp_paths), self._mock_track_daemon(temp_paths), tempfile.TemporaryDirectory() as repo_dir:
            subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True, text=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_dir, check=True)
            Path(repo_dir, "README.md").write_text("hello\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=repo_dir, check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo_dir, check=True, capture_output=True, text=True)

            def _external_commit() -> None:
                time.sleep(0.2)
                Path(repo_dir, "README.md").write_text("external\n", encoding="utf-8")
                subprocess.run(["git", "add", "README.md"], cwd=repo_dir, check=True, capture_output=True, text=True)
                subprocess.run(["git", "commit", "-m", "external commit"], cwd=repo_dir, check=True, capture_output=True, text=True)

            worker = threading.Thread(target=_external_commit)
            worker.start()
            try:
                with patch("os.getcwd", return_value=repo_dir):
                    launch = _make_test_launch(["zsh", "-lc", "sleep 1"])
                code = track_module.run_tracked_command(launch)
                self.assertEqual(code, 0)
            finally:
                worker.join(timeout=5)

            store = SQLiteStateStore(temp_paths.state_sqlite_path, journal=None)
            session = store.get_latest_tracked_session()
            self.assertIsNotNone(session)
            summary = store.get_session_summary(str(session["session_id"]))
            self.assertIsNotNone(summary)
            event_stream_path = Path(str(summary.get("event_stream_path", "") or ""))
            self.assertTrue(event_stream_path.is_file())

            with gzip.open(event_stream_path, "rt", encoding="utf-8") as handle:
                events = [json.loads(line) for line in handle if line.strip()]

            event_types = [str(event.get("type", "") or "") for event in events]
            self.assertIn("git.commit.sess_sync", event_types)
            self.assertNotIn("git.commit.created", event_types)

    def test_loaders_read_compressed_track_artifacts(self):
        with self._temp_app_paths() as (_, temp_paths):
            transcript_dir = Path(temp_paths.state_dir) / "tracked_sessions"
            transcript_dir.mkdir(parents=True, exist_ok=True)
            transcript_path = transcript_dir / "demo.transcript.jsonl.gz"
            event_path = transcript_dir / "demo.events.jsonl.gz"

            with gzip.open(transcript_path, "wt", encoding="utf-8") as handle:
                handle.write('{"ts":1.0,"direction":"pty","data_b64":"aGVsbG8="}\n')
            with gzip.open(event_path, "wt", encoding="utf-8") as handle:
                handle.write(
                    '{"session_id":"demo","seq":1,"ts_wall":1.0,"ts_monotonic_ms":1,"type":"terminal.stdout","payload":{"data_b64":"aGVsbG8="}}\n'
                )

            transcript_events = track_module._load_transcript_events(str(transcript_path))
            session_events = track_module._load_session_events(str(event_path))

            self.assertEqual(len(transcript_events), 1)
            self.assertEqual(transcript_events[0]["data"], b"hello")
            self.assertEqual(len(session_events), 1)
            self.assertEqual(session_events[0]["payload"]["data"], b"hello")

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
            launch = _make_test_launch(["zsh", "-lc", "true"])
            code = track_module.run_tracked_command(launch)

            self.assertEqual(code, 0)
            self.assertTrue(Path(temp_paths.provenance_private_key_path).is_file())
            self.assertTrue(Path(temp_paths.provenance_public_key_path).is_file())

    def test_run_tracked_command_records_short_lived_child_process(self):
        with self._temp_app_paths() as (_, temp_paths), self._mock_track_daemon(temp_paths):
            launch = _make_test_launch(["zsh", "-lc", "echo hi; sleep 0.2 & wait"])
            code = track_module.run_tracked_command(launch)

            self.assertEqual(code, 0)
            store = SQLiteStateStore(temp_paths.state_sqlite_path, journal=None)
            commands = [str(row.get("command", "") or "") for row in store.list_command_runs(limit=20)]
            self.assertTrue(any(command.startswith("sleep 0.2") for command in commands), msg=commands)

    def test_run_tracked_command_observes_session_boundary_escape(self):
        with self._temp_app_paths() as (_, temp_paths), self._mock_track_daemon(temp_paths):
            daemonize = (
                "python3 -c \"import os,time;"
                "pid=os.fork();"
                "import sys;"
                "time.sleep(0.2) if pid else None;"
                "sys.exit(0) if pid else None;"
                "os.setsid();"
                "time.sleep(1.5)\""
            )
            launch = _make_test_launch(["zsh", "-lc", daemonize])
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
            launch = _make_test_launch(
                ["zsh", "-ic", "nohup sleep 5 >/tmp/agensic-track-test.log 2>&1 & disown; echo should-not-print"]
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
        ), self._mock_track_daemon(temp_paths):
            launch = _make_test_launch(["zsh", "-lc", "echo hi"])
            code = track_module.run_tracked_command(launch)
            self.assertEqual(code, 0)

            store = SQLiteStateStore(temp_paths.state_sqlite_path, journal=None)
            session = store.get_latest_tracked_session()
            self.assertIsNotNone(session)
            result = self.runner.invoke(app, ["run", "inspect", str(session["session_id"])], env=env)

        self.assertEqual(result.exit_code, 0)
        self.assertIn("transcript_events=", result.stdout)
        self.assertIn("recorded_runs=", result.stdout)
        self.assertIn("command=", result.stdout)

    def test_time_travel_preview_and_fork_restore_git_checkpoint(self):
        with self._temp_app_paths() as (_, temp_paths), tempfile.TemporaryDirectory() as repo_dir:
            subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True, text=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_dir, check=True)
            tracked = Path(repo_dir) / "tracked.txt"
            tracked.write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=repo_dir, check=True)
            subprocess.run(["git", "commit", "-m", "base"], cwd=repo_dir, check=True, capture_output=True, text=True)

            store = SQLiteStateStore(temp_paths.state_sqlite_path, journal=None)
            store.upsert_tracked_session(
                session_id="sess-tt",
                status="exited",
                launch_mode="registry_alias",
                agent="codex",
                model="test-model",
                agent_name="Codex",
                working_directory=repo_dir,
                root_command="codex",
            )
            store.upsert_session_summary(
                session_id="sess-tt",
                repo_root=repo_dir,
                branch_start="main",
                branch_end="main",
                head_start=subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=repo_dir,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip(),
                head_end=subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=repo_dir,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip(),
            )

            tracked.write_text("changed\n", encoding="utf-8")
            Path(repo_dir, "extra.txt").write_text("new file\n", encoding="utf-8")
            payload = track_module._build_git_checkpoint_payload(repo_dir, seq=5, reason="test")
            self.assertIsNotNone(payload)
            Path(track_module._track_git_checkpoint_path("sess-tt")).parent.mkdir(parents=True, exist_ok=True)
            with open(track_module._track_git_checkpoint_path("sess-tt"), "a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload) + "\n")

            subprocess.run(["git", "reset", "--hard", "HEAD"], cwd=repo_dir, check=True, capture_output=True, text=True)
            subprocess.run(["git", "clean", "-fd"], cwd=repo_dir, check=True, capture_output=True, text=True)

            preview = track_module.preview_time_travel("sess-tt", 5)
            self.assertEqual(preview["status"], "ok")
            self.assertTrue(preview["can_fork"])
            self.assertTrue(preview["exact_match"])

            forked = track_module.fork_time_travel("sess-tt", 5)
            self.assertEqual(forked["status"], "ok")
            current_branch = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=repo_dir,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.assertEqual(current_branch, forked["branch_name"])
            self.assertEqual(tracked.read_text(encoding="utf-8"), "changed\n")
            self.assertEqual(Path(repo_dir, "extra.txt").read_text(encoding="utf-8"), "new file\n")
            self.assertEqual(forked["launch_payload"]["launch_command"], ["agensic", "run", "codex"])

    def test_time_travel_preview_uses_session_artifact_paths_and_snapshot_repo_root(self):
        with self._temp_app_paths() as (_, temp_paths), tempfile.TemporaryDirectory() as repo_dir, tempfile.TemporaryDirectory() as legacy_dir:
            subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True, text=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_dir, check=True)
            tracked = Path(repo_dir) / "tracked.txt"
            tracked.write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=repo_dir, check=True)
            subprocess.run(["git", "commit", "-m", "base"], cwd=repo_dir, check=True, capture_output=True, text=True)

            head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo_dir,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            branch = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=repo_dir,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

            store = SQLiteStateStore(temp_paths.state_sqlite_path, journal=None)
            transcript_path = Path(legacy_dir) / "sess-legacy.transcript.jsonl.gz"
            event_stream_path = Path(legacy_dir) / "sess-legacy.events.jsonl.gz"
            git_checkpoint_path = Path(legacy_dir) / "sess-legacy.git-checkpoints.jsonl.gz"

            store.upsert_tracked_session(
                session_id="sess-legacy",
                status="exited",
                launch_mode="registry_alias",
                agent="codex",
                model="test-model",
                agent_name="Codex",
                working_directory=repo_dir,
                root_command="codex",
                transcript_path=str(transcript_path),
            )
            snapshot = {
                "timestamp": int(time.time()),
                "repo_root": repo_dir,
                "branch": branch,
                "head": head,
            }
            store.upsert_session_summary(
                session_id="sess-legacy",
                repo_root="",
                branch_start=branch,
                branch_end=branch,
                head_start=head,
                head_end=head,
                start_snapshot=snapshot,
                end_snapshot=snapshot,
                event_stream_path=str(event_stream_path),
            )

            payload = track_module._build_git_checkpoint_payload(repo_dir, seq=3, reason="legacy")
            self.assertIsNotNone(payload)
            with gzip.open(git_checkpoint_path, "wt", encoding="utf-8") as handle:
                handle.write(json.dumps(payload) + "\n")

            preview = track_module.preview_time_travel("sess-legacy", 3)

            self.assertEqual(preview["status"], "ok")
            self.assertEqual(preview["repo_root"], repo_dir)
            self.assertEqual(preview["resolved_checkpoint"]["seq"], 3)
            self.assertTrue(preview["exact_match"])

    def test_time_travel_preview_ignores_future_checkpoint_with_stale_seq_when_timestamp_is_known(self):
        with self._temp_app_paths() as (_, temp_paths), tempfile.TemporaryDirectory() as repo_dir:
            subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True, text=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_dir, check=True)
            tracked = Path(repo_dir) / "tracked.txt"
            tracked.write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=repo_dir, check=True)
            subprocess.run(["git", "commit", "-m", "base"], cwd=repo_dir, check=True, capture_output=True, text=True)

            store = SQLiteStateStore(temp_paths.state_sqlite_path, journal=None)
            store.upsert_tracked_session(
                session_id="sess-ts",
                status="exited",
                launch_mode="registry_alias",
                agent="codex",
                model="test-model",
                agent_name="Codex",
                working_directory=repo_dir,
                root_command="codex",
            )
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo_dir,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            store.upsert_session_summary(
                session_id="sess-ts",
                repo_root=repo_dir,
                branch_start="main",
                branch_end="main",
                head_start=head,
                head_end=head,
            )

            checkpoint_path = Path(track_module._track_git_checkpoint_path("sess-ts"))
            checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            rows = [
                {
                    "seq": 7,
                    "timestamp": 100,
                    "reason": "before",
                    "repo_root": repo_dir,
                    "branch": "main",
                    "head": head,
                    "status_porcelain": "",
                    "status_fingerprint": "",
                    "tracked_patch_b64": "",
                    "tracked_patch_sha256": "",
                    "worktree_diff_stat": "",
                    "changed_files": [],
                    "untracked_files": [],
                    "untracked_paths": [],
                    "fingerprint": "old",
                },
                {
                    "seq": 7,
                    "timestamp": 200,
                    "reason": "after",
                    "repo_root": repo_dir,
                    "branch": "main",
                    "head": "later-head",
                    "status_porcelain": "",
                    "status_fingerprint": "",
                    "tracked_patch_b64": "",
                    "tracked_patch_sha256": "",
                    "worktree_diff_stat": "",
                    "changed_files": [],
                    "untracked_files": [],
                    "untracked_paths": [],
                    "fingerprint": "new",
                },
            ]
            with checkpoint_path.open("w", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row) + "\n")

            preview = track_module.preview_time_travel("sess-ts", 7, target_ts=150)

            self.assertEqual(preview["status"], "ok")
            self.assertEqual(preview["resolved_checkpoint"]["head"], head)
            self.assertTrue(preview["exact_match"])


if __name__ == "__main__":
    unittest.main()
