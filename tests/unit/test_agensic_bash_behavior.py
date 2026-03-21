import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
AGENSIC_BASH = REPO_ROOT / "agensic.bash"


class AgensicBashBehaviorTests(unittest.TestCase):
    def _run_bash(self, body: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        script = textwrap.dedent(
            f"""
            source "{AGENSIC_BASH}"
            {body}
            """
        )
        run_env = dict(os.environ)
        if env:
            run_env.update(env)
        return subprocess.run(
            ["bash", "-lc", script],
            capture_output=True,
            text=True,
            check=False,
            env=run_env,
        )

    def test_self_insert_keeps_matching_suffix_without_refetch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            helper_path = Path(tmpdir) / "helper.py"
            helper_path.write_text(
                "\n".join(
                    [
                        "raise SystemExit('helper should not be called')",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            result = self._run_bash(
                """
                AGENSIC_BASH_READLINE_AVAILABLE=1
                AGENSIC_CLIENT_HELPER="$TEST_HELPER"
                AGENSIC_RUNTIME_PYTHON="python3"
                READLINE_LINE="cod"
                READLINE_POINT=${#READLINE_LINE}
                AGENSIC_LAST_BUFFER="co"
                AGENSIC_SUGGESTION_BUFFER="co"
                AGENSIC_SUGGESTIONS=("dex")
                AGENSIC_DISPLAY_TEXTS=("dex")
                AGENSIC_ACCEPT_MODES=("suffix_append")
                AGENSIC_SUGGESTION_KINDS=("normal")
                AGENSIC_SUGGESTION_INDEX=1
                _agensic_bash_after_self_insert
                printf '%s\\n' "${AGENSIC_SUGGESTIONS[0]}|${AGENSIC_SUGGESTION_INDEX}|${AGENSIC_BASH_LAST_INFO_MESSAGE}"
                """,
                env={"TEST_HELPER": str(helper_path)},
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), "dex|1|codex")

    def test_self_insert_refetches_once_visible_suffix_is_exhausted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            helper_path = Path(tmpdir) / "helper.py"
            helper_path.write_text(
                "\n".join(
                    [
                        "import json",
                        "print(json.dumps({",
                        "  'ok': True,",
                        "  'pool': [' status'],",
                        "  'display': [' status'],",
                        "  'modes': ['suffix_append'],",
                        "  'kinds': ['normal'],",
                        "  'used_ai': False,",
                        "  'ai_agent': '',",
                        "  'ai_provider': '',",
                        "  'ai_model': '',",
                        "}))",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            result = self._run_bash(
                """
                AGENSIC_BASH_READLINE_AVAILABLE=1
                AGENSIC_CLIENT_HELPER="$TEST_HELPER"
                AGENSIC_RUNTIME_PYTHON="python3"
                READLINE_LINE="codex"
                READLINE_POINT=${#READLINE_LINE}
                AGENSIC_LAST_BUFFER="co"
                AGENSIC_SUGGESTION_BUFFER="co"
                AGENSIC_SUGGESTIONS=("dex")
                AGENSIC_DISPLAY_TEXTS=("dex")
                AGENSIC_ACCEPT_MODES=("suffix_append")
                AGENSIC_SUGGESTION_KINDS=("normal")
                AGENSIC_SUGGESTION_INDEX=1
                _agensic_bash_after_self_insert
                printf '%s\\n' "${AGENSIC_SUGGESTIONS[0]}|${AGENSIC_SUGGESTION_INDEX}|${AGENSIC_BASH_LAST_INFO_MESSAGE}"
                """,
                env={"TEST_HELPER": str(helper_path)},
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.splitlines()[0], " status|1|codex status")
