import os
import unittest
from unittest.mock import patch

from typer.testing import CliRunner

from agensic.cli.app import app


class CliAiSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

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

    def test_ai_session_stop_emits_unsets(self):
        result = self.runner.invoke(app, ["ai-session", "stop"])
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

    def test_ai_session_status_inactive(self):
        with patch.dict(os.environ, {}, clear=True):
            result = self.runner.invoke(app, ["ai-session", "status"])
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


if __name__ == "__main__":
    unittest.main()
