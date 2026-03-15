import json
import unittest
from unittest.mock import patch

import importlib

app_module = importlib.import_module("agensic.cli.app")


class CliAgentsTuiTests(unittest.TestCase):
    def test_run_agents_tui_invokes_sidecar_with_temp_payload(self):
        class Result:
            returncode = 0

        captured = {}

        def _fake_run(cmd, check):  # noqa: ANN001, FBT002
            captured["cmd"] = cmd
            with open(cmd[3], "r", encoding="utf-8") as handle:
                captured["payload"] = json.load(handle)
            return Result()

        agents = [
            {
                "agent_id": "codex",
                "display_name": "Codex",
                "source": "builtin",
                "status": "verified",
                "executables": ["codex"],
                "aliases": ["codex"],
            }
        ]

        with patch("agensic.cli.app._ensure_provenance_tui_binary", return_value="/tmp/agensic-provenance-tui"), patch(
            "agensic.cli.app._binary_supports_agents_mode", return_value=True
        ), patch("agensic.cli.app._reset_terminal_mouse_reporting"), patch(
            "agensic.cli.app.subprocess.run", side_effect=_fake_run
        ) as run_cmd:
            ok = app_module._run_agents_tui(agents)

        self.assertTrue(ok)
        self.assertEqual(captured["cmd"][:3], ["/tmp/agensic-provenance-tui", "agents", "--input"])
        self.assertEqual(captured["payload"], {"agents": agents})
        self.assertTrue(run_cmd.called)


if __name__ == "__main__":
    unittest.main()
