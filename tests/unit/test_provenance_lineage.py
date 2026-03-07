import unittest
from unittest.mock import patch

from agensic.engine.provenance import inspect_process_lineage


class ProvenanceLineageTests(unittest.TestCase):
    def test_lineage_detects_known_agent_hints(self):
        fake_rows = {
            100: {"pid": 100, "ppid": 90, "comm": "zsh", "command": "zsh"},
            90: {"pid": 90, "ppid": 1, "comm": "Cursor", "command": "Cursor --type=renderer"},
            1: {"pid": 1, "ppid": 0, "comm": "launchd", "command": "/sbin/launchd"},
        }

        def _fake_row(pid: int):
            return fake_rows.get(pid)

        with patch("agensic.engine.provenance._ps_row_for_pid", side_effect=_fake_row):
            out = inspect_process_lineage(100, max_depth=10)
        self.assertEqual(len(out["lineage"]), 3)
        self.assertIn("cursor", out["hints"])
        match = out.get("match", {})
        self.assertEqual(match.get("agent_id"), "cursor")

    def test_lineage_handles_loops_safely(self):
        fake_rows = {
            50: {"pid": 50, "ppid": 50, "comm": "zsh", "command": "zsh"},
        }

        with patch(
            "agensic.engine.provenance._ps_row_for_pid",
            side_effect=lambda pid: fake_rows.get(pid),
        ):
            out = inspect_process_lineage(50, max_depth=10)
        self.assertEqual(len(out["lineage"]), 1)

    def test_lineage_extracts_model_from_executable_flags(self):
        fake_rows = {
            200: {"pid": 200, "ppid": 1, "comm": "codex", "command": "codex --model gpt-5.3 run tests"},
            1: {"pid": 1, "ppid": 0, "comm": "launchd", "command": "/sbin/launchd"},
        }
        with patch(
            "agensic.engine.provenance._ps_row_for_pid",
            side_effect=lambda pid: fake_rows.get(pid),
        ):
            out = inspect_process_lineage(200, max_depth=5)
        match = out.get("match", {})
        self.assertEqual(match.get("agent_id"), "codex")
        self.assertEqual(match.get("model_raw"), "gpt-5.3")


if __name__ == "__main__":
    unittest.main()
