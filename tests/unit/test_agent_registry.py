import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agensic.engine.agent_registry import AgentRegistry, build_model_fingerprint


class AgentRegistryTests(unittest.TestCase):
    def test_builtin_registry_contains_required_agents(self):
        registry = AgentRegistry()
        agents = {row["agent_id"] for row in registry.list_agents()}
        self.assertIn("cursor", agents)
        self.assertIn("codex", agents)
        self.assertIn("claude_code", agents)
        self.assertIn("opencode", agents)
        self.assertIn("openclaw", agents)
        self.assertIn("windsurf", agents)
        self.assertIn("kiro", agents)
        self.assertIn("gemini_cli", agents)
        self.assertIn("antigravity", agents)

    def test_provider_model_inference(self):
        registry = AgentRegistry()
        inferred = registry.infer_agent_from_provider_model("openai", "gpt-5.3-codex")
        self.assertEqual(inferred.get("agent_id"), "codex")
        self.assertEqual(inferred.get("model_normalized"), "gpt-5-codex")

    def test_lineage_exact_executable_with_model_is_verified(self):
        registry = AgentRegistry()
        match = registry.infer_from_lineage(
            [
                {
                    "pid": 100,
                    "ppid": 1,
                    "comm": "codex",
                    "command": "codex --model gpt-5.3 --agent codex run tests",
                }
            ]
        )
        self.assertIsNotNone(match)
        self.assertEqual(match.agent_id, "codex")
        self.assertEqual(match.evidence_tier, "verified")
        self.assertEqual(match.model_raw, "gpt-5.3")

    def test_lineage_community_token(self):
        registry = AgentRegistry()
        match = registry.infer_from_lineage(
            [
                {
                    "pid": 100,
                    "ppid": 1,
                    "comm": "python",
                    "command": "python run_antigravity_helper.py",
                }
            ]
        )
        self.assertIsNotNone(match)
        self.assertEqual(match.agent_id, "antigravity")
        self.assertEqual(match.evidence_tier, "community")

    def test_model_fingerprint_prefers_normalized(self):
        fp = build_model_fingerprint("codex", "gpt-5-codex", "gpt-5.3")
        self.assertEqual(fp, "codex_gpt-5-codex")

    def test_summary_reports_local_override_without_remote_fields(self):
        with TemporaryDirectory() as tmpdir:
            override_path = Path(tmpdir) / "agent_registry.local.json"
            override_path.write_text(
                '{"version":"local-override","agents":[{"agent_id":"codex","display_name":"Local Codex","status":"verified"}]}',
                encoding="utf-8",
            )
            registry = AgentRegistry(local_override_path=str(override_path))

        summary = registry.summary()
        self.assertEqual(summary["source"], "local_override")
        self.assertEqual(summary["version"], "local-override")
        self.assertNotIn("remote_loaded", summary)
        self.assertNotIn("remote_cache_path", summary)


if __name__ == "__main__":
    unittest.main()
