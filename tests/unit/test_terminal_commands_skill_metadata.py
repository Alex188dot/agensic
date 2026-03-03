import re
import unittest
from pathlib import Path


SKILL_ROOT = Path("/Users/alessioleodori/.codex/skills/ghostshell-signed-exec")
SKILL_MD = SKILL_ROOT / "SKILL.md"
OPENAI_YAML = SKILL_ROOT / "agents" / "openai.yaml"


@unittest.skipUnless(SKILL_MD.exists() and OPENAI_YAML.exists(), "terminal-commands skill metadata unavailable")
class TerminalCommandsSkillMetadataTests(unittest.TestCase):
    def test_frontmatter_name_and_description_match_expected(self):
        text = SKILL_MD.read_text(encoding="utf-8")
        frontmatter_match = re.match(r"^---\n(.*?)\n---\n", text, flags=re.DOTALL)
        self.assertIsNotNone(frontmatter_match, msg="SKILL.md frontmatter is missing")
        frontmatter = frontmatter_match.group(1)

        name_match = re.search(r"^name:\s*(.+)$", frontmatter, flags=re.MULTILINE)
        desc_match = re.search(r"^description:\s*(.+)$", frontmatter, flags=re.MULTILINE)
        self.assertIsNotNone(name_match)
        self.assertIsNotNone(desc_match)
        self.assertEqual(name_match.group(1).strip(), "terminal-commands")
        self.assertEqual(
            desc_match.group(1).strip(),
            "ALWAYS use when the agent is about to run shell commands so command provenance is auditable as AI_EXECUTED, including for unmapped or custom agent IDs.",
        )

    def test_openai_prompt_enforces_wrapper_first_and_warn_run_fallback(self):
        text = OPENAI_YAML.read_text(encoding="utf-8")
        self.assertIn('display_name: "Terminal Commands"', text)
        self.assertIn("default_prompt:", text)
        self.assertIn("$terminal-commands", text)
        self.assertIn("If wrappers are unavailable, warn", text)
        self.assertIn("not auditable as AI_EXECUTED", text)

    def test_skill_references_wrapper_scripts_that_exist(self):
        text = SKILL_MD.read_text(encoding="utf-8")
        wrapper_paths = ["scripts/signed_session.sh", "scripts/signed_exec.sh"]
        for rel_path in wrapper_paths:
            with self.subTest(rel_path=rel_path):
                self.assertIn(rel_path, text)
                self.assertTrue((SKILL_ROOT / rel_path).exists(), msg=f"Missing wrapper script: {rel_path}")


if __name__ == "__main__":
    unittest.main()
