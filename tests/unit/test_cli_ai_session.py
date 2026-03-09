import os
import tempfile
import unittest
from contextlib import contextmanager
import importlib
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

cli_app = importlib.import_module("agensic.cli.app")
import agensic.paths as ag_paths


app = cli_app.app


class CliAiSessionTests(unittest.TestCase):
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
            with patch.object(cli_app, "APP_PATHS", temp_paths), patch.object(ag_paths, "APP_PATHS", temp_paths):
                yield env

    def test_ai_session_start_emits_exports(self):
        result = self.runner.invoke(
            app,
            [
                "ai-session",
                "start",
                "--agent",
                "CoDeX",
                "--model",
                "gpt-5.3",
                "--agent-name",
                "Planner A",
                "--ttl-minutes",
                "30",
            ],
        )
        self.assertEqual(result.exit_code, 0)
        self.assertIn("export AGENSIC_AI_SESSION_ACTIVE=1", result.stdout)
        self.assertIn("export AGENSIC_AI_SESSION_AGENT=codex", result.stdout)
        self.assertIn("export AGENSIC_AI_SESSION_MODEL=gpt-5.3", result.stdout)
        self.assertIn("export AGENSIC_AI_SESSION_AGENT_NAME='Planner A'", result.stdout)
        self.assertIn("export AGENSIC_AI_SESSION_ID=", result.stdout)
        self.assertIn("export AGENSIC_AI_SESSION_STARTED_TS=", result.stdout)
        self.assertIn("export AGENSIC_AI_SESSION_EXPIRES_TS=", result.stdout)
        self.assertIn("export AGENSIC_AI_SESSION_COUNTER=0", result.stdout)
        self.assertIn("export AGENSIC_AI_SESSION_TIMER_PID=''", result.stdout)

    def test_ai_session_start_defaults_identity_when_missing(self):
        result = self.runner.invoke(
            app,
            [
                "ai-session",
                "start",
            ],
        )
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Warning: ai-session start missing identity", result.stdout)
        self.assertIn("export AGENSIC_AI_SESSION_AGENT=unknown", result.stdout)
        self.assertIn("export AGENSIC_AI_SESSION_MODEL=unknown-model", result.stdout)

    def test_ai_session_start_persists_state_for_non_eval_callers(self):
        with self._temp_app_paths() as env:
            result = self.runner.invoke(
                app,
                ["ai-session", "start", "--agent", "CoDeX", "--model", "gpt-5.3"],
                env=env,
            )
            self.assertEqual(result.exit_code, 0)
            state_path = Path(env["XDG_STATE_HOME"]) / "agensic" / "ai_session.env"
            self.assertTrue(state_path.exists())
            payload = state_path.read_text(encoding="utf-8")
            self.assertIn("AGENSIC_AI_SESSION_ACTIVE\t1", payload)
            self.assertIn("AGENSIC_AI_SESSION_AGENT\tcodex", payload)
            self.assertIn("AGENSIC_AI_SESSION_MODEL\tgpt-5.3", payload)

    def test_ai_session_stop_emits_unsets(self):
        with self._temp_app_paths() as env:
            start = self.runner.invoke(
                app,
                ["ai-session", "start", "--agent", "codex", "--model", "gpt-5.3"],
                env=env,
            )
            self.assertEqual(start.exit_code, 0)
            state_path = Path(env["XDG_STATE_HOME"]) / "agensic" / "ai_session.env"
            self.assertTrue(state_path.exists())

            result = self.runner.invoke(app, ["ai-session", "stop"], env=env)
            self.assertEqual(result.exit_code, 0)
            self.assertIn("unset AGENSIC_AI_SESSION_ACTIVE", result.stdout)
            self.assertIn("unset AGENSIC_AI_SESSION_AGENT", result.stdout)
            self.assertIn("unset AGENSIC_AI_SESSION_MODEL", result.stdout)
            self.assertIn("unset AGENSIC_AI_SESSION_AGENT_NAME", result.stdout)
            self.assertIn("unset AGENSIC_AI_SESSION_ID", result.stdout)
            self.assertIn("unset AGENSIC_AI_SESSION_STARTED_TS", result.stdout)
            self.assertIn("unset AGENSIC_AI_SESSION_EXPIRES_TS", result.stdout)
            self.assertIn("unset AGENSIC_AI_SESSION_COUNTER", result.stdout)
            self.assertIn("unset AGENSIC_AI_SESSION_TIMER_PID", result.stdout)
            self.assertFalse(state_path.exists())

    def test_ai_session_status_inactive(self):
        with self._temp_app_paths() as env:
            result = self.runner.invoke(app, ["ai-session", "status"], env=env)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("inactive", result.stdout)

    def test_ai_session_status_active(self):
        with patch.dict(
            os.environ,
            {
                "AGENSIC_AI_SESSION_ACTIVE": "1",
                "AGENSIC_AI_SESSION_AGENT": "codex",
                "AGENSIC_AI_SESSION_MODEL": "gpt-5.3",
                "AGENSIC_AI_SESSION_AGENT_NAME": "Planner A",
                "AGENSIC_AI_SESSION_ID": "abc123",
                "AGENSIC_AI_SESSION_EXPIRES_TS": "4102444800",  # 2100-01-01
            },
            clear=True,
        ):
            result = self.runner.invoke(app, ["ai-session", "status"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("active", result.stdout)
        self.assertIn("agent=codex", result.stdout)
        self.assertIn("model=gpt-5.3", result.stdout)
        self.assertIn("agent_name=Planner A", result.stdout)
        self.assertIn("session_id=abc123", result.stdout)

    def test_ai_session_status_reads_persisted_state_when_shell_env_is_inactive(self):
        with self._temp_app_paths() as env:
            start = self.runner.invoke(
                app,
                ["ai-session", "start", "--agent", "codex", "--model", "gpt-5.3", "--agent-name", "Planner A"],
                env=env,
            )
            self.assertEqual(start.exit_code, 0)

            result = self.runner.invoke(app, ["ai-session", "status"], env=env)
            self.assertEqual(result.exit_code, 0)
            self.assertIn("active", result.stdout)
            self.assertIn("agent=codex", result.stdout)
            self.assertIn("model=gpt-5.3", result.stdout)
            self.assertIn("agent_name=Planner A", result.stdout)


if __name__ == "__main__":
    unittest.main()
