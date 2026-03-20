import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_HELPERS = REPO_ROOT / "shell" / "agensic_shared.sh"


class SharedShellHelpersTests(unittest.TestCase):
    def _run_bash(self, body: str) -> subprocess.CompletedProcess:
        script = textwrap.dedent(
            f"""
            source "{SHARED_HELPERS}"
            {body}
            """
        )
        return subprocess.run(
            ["bash", "-lc", script],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_canonicalize_buffer_spacing_collapses_unquoted_whitespace(self):
        result = self._run_bash(
            """
            value="$(_agensic_canonicalize_buffer_spacing '  git   status  ')"
            printf '%s\\n' "$value"
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), "git status")

    def test_mark_manual_line_edit_sets_manual_after_accept(self):
        result = self._run_bash(
            """
            AGENSIC_LINE_ACCEPTED_ORIGIN="ag"
            _agensic_mark_manual_line_edit "human_edit"
            printf '%s\\n' "${AGENSIC_LINE_MANUAL_EDIT_AFTER_ACCEPT}|${AGENSIC_LINE_LAST_ACTION}"
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), "1|human_edit")

    def test_snapshot_pending_execution_copies_provenance_state(self):
        result = self._run_bash(
            """
            AGENSIC_LINE_LAST_ACTION="suggestion_accept"
            AGENSIC_LINE_ACCEPTED_ORIGIN="ai"
            AGENSIC_LINE_ACCEPTED_MODE="replace_full"
            AGENSIC_LINE_ACCEPTED_KIND="intent_command"
            AGENSIC_LINE_ACCEPTED_AI_AGENT="codex"
            AGENSIC_LINE_ACCEPTED_AI_PROVIDER="openai"
            AGENSIC_LINE_ACCEPTED_AI_MODEL="gpt-5.3"
            AGENSIC_AI_SESSION_AGENT_NAME="Planner A"
            _agensic_snapshot_pending_execution
            printf '%s\\n' "${AGENSIC_PENDING_LAST_ACTION}|${AGENSIC_PENDING_ACCEPTED_ORIGIN}|${AGENSIC_PENDING_AI_AGENT}|${AGENSIC_PENDING_AGENT_NAME}"
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), "suggestion_accept|ai|codex|Planner A")

    def test_get_file_mtime_supports_temp_file(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            handle.write("hello\n")
            path = handle.name
        try:
            result = self._run_bash(
                f"""
                value="$(_agensic_get_file_mtime "{path}")"
                printf '%s\\n' "$value"
                """
            )
        finally:
            Path(path).unlink(missing_ok=True)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertRegex(result.stdout.strip(), r"^[0-9]+$")


if __name__ == "__main__":
    unittest.main()
