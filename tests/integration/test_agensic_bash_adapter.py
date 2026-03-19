import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
AGENSIC_BASH = REPO_ROOT / "agensic.bash"


class AgensicBashAdapterTests(unittest.TestCase):
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

    def test_non_interactive_source_keeps_adapter_disabled_without_error(self):
        result = self._run_bash(
            """
            printf '%s\\n' "${AGENSIC_BASH_ADAPTER_READY}|${AGENSIC_BASH_READLINE_AVAILABLE}|${AGENSIC_BASH_BACKEND}"
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), "0|0|none")

    def test_prepare_prompt_reserves_preview_line_above_prompt(self):
        result = self._run_bash(
            """
            PS1='$ '
            _agensic_bash_prepare_prompt
            printf '%q\\n' "$PS1"
            printf '%s\\n' "${AGENSIC_BASH_PROMPT_PREPARED}"
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], "$'\\n$ '")
        self.assertEqual(lines[1], "1")

    def test_readline_update_display_renders_preview_without_mutating_buffer(self):
        result = self._run_bash(
            """
            AGENSIC_BASH_READLINE_AVAILABLE=1
            READLINE_LINE="git st"
            READLINE_POINT=6
            AGENSIC_LAST_BUFFER="git st"
            AGENSIC_SUGGESTIONS=("atus")
            AGENSIC_DISPLAY_TEXTS=("atus")
            AGENSIC_ACCEPT_MODES=("suffix_append")
            AGENSIC_SUGGESTION_KINDS=("normal")
            AGENSIC_SUGGESTION_INDEX=1
            _agensic_bash_update_display
            printf '%s\\n' "${READLINE_LINE}|${READLINE_POINT}|${AGENSIC_BASH_LAST_INFO_MESSAGE}"
            """
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), "git st|6|git status")

    def test_readline_update_display_suppresses_preview_for_path_context(self):
        result = self._run_bash(
            """
            AGENSIC_BASH_READLINE_AVAILABLE=1
            READLINE_LINE="cat ./rea"
            READLINE_POINT=${#READLINE_LINE}
            AGENSIC_LAST_BUFFER="cat ./rea"
            AGENSIC_SUGGESTIONS=("dme.md")
            AGENSIC_DISPLAY_TEXTS=("dme.md")
            AGENSIC_ACCEPT_MODES=("suffix_append")
            AGENSIC_SUGGESTION_KINDS=("normal")
            AGENSIC_SUGGESTION_INDEX=1
            _agensic_bash_update_display
            printf '%s\\n' "${READLINE_LINE}|${AGENSIC_BASH_LAST_INFO_MESSAGE}"
            """
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), "cat ./rea|")

    def test_filter_pool_keeps_closest_surviving_match(self):
        result = self._run_bash(
            """
            AGENSIC_BASH_READLINE_AVAILABLE=1
            AGENSIC_LAST_BUFFER="git "
            READLINE_LINE="git sta"
            READLINE_POINT=${#READLINE_LINE}
            AGENSIC_SUGGESTIONS=("status" "stash" "switch")
            AGENSIC_DISPLAY_TEXTS=("status" "stash" "switch")
            AGENSIC_ACCEPT_MODES=("suffix_append" "suffix_append" "suffix_append")
            AGENSIC_SUGGESTION_KINDS=("normal" "normal" "normal")
            AGENSIC_SUGGESTION_INDEX=3
            _agensic_bash_filter_pool
            printf '%s\\n' "${AGENSIC_SUGGESTION_INDEX}|${AGENSIC_SUGGESTIONS[0]}|${AGENSIC_SUGGESTIONS[1]}|${AGENSIC_BASH_LAST_INFO_MESSAGE}"
            """
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), "1|status|stash|git status")

    def test_overlay_message_is_cropped_to_terminal_width(self):
        result = self._run_bash(
            """
            COLUMNS=20
            printf '%s\\n' "$(_agensic_bash_render_overlay_message '12345678901234567890')"
            """,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), "\x1b[32m[Agensic]\x1b[0m \x1b[38;5;245m1234567...\x1b[0m")

    def test_overlay_message_renders_hint_before_suggestion(self):
        result = self._run_bash(
            """
            COLUMNS=80
            printf '%s\\n' "$(_agensic_bash_render_overlay_message 'agensic provenance --tui' '(3/6, Ctrl+P/N)')"
            """,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(
            result.stdout.strip(),
            "\x1b[32m[Agensic]\x1b[0m (3/6, Ctrl+P/N) \x1b[38;5;245magensic provenance --tui\x1b[0m",
        )

    def test_readline_manual_trigger_fetches_suggestions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            helper_path = Path(tmpdir) / "helper.py"
            helper_path.write_text(
                "\n".join(
                    [
                        "import json",
                        "print(json.dumps({",
                        "  'ok': True,",
                        "  'pool': ['atus'],",
                        "  'display': ['atus'],",
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
                READLINE_LINE="git st"
                READLINE_POINT=6
                _agensic_readline_manual_trigger
                printf '%s\\n' "${AGENSIC_SUGGESTIONS[0]}|${AGENSIC_BASH_LAST_INFO_MESSAGE}"
                """,
                env={"TEST_HELPER": str(helper_path)},
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), "atus|git status")

    def test_readline_accept_fetches_and_accepts_suggestion(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            helper_path = Path(tmpdir) / "helper.py"
            helper_path.write_text(
                "\n".join(
                    [
                        "import json",
                        "print(json.dumps({",
                        "  'ok': True,",
                        "  'pool': ['atus'],",
                        "  'display': ['atus'],",
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
                READLINE_LINE="git st"
                READLINE_POINT=6
                _agensic_readline_accept
                printf '%s\\n' "${READLINE_LINE}|${READLINE_POINT}|${AGENSIC_BASH_LAST_INFO_MESSAGE}"
                """,
                env={"TEST_HELPER": str(helper_path)},
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), "git status|10|")

    def test_readline_cycle_next_fetches_when_pool_is_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            helper_path = Path(tmpdir) / "helper.py"
            helper_path.write_text(
                "\n".join(
                    [
                        "import json",
                        "print(json.dumps({",
                        "  'ok': True,",
                        "  'pool': ['atus', ' add'],",
                        "  'display': ['atus', ' add'],",
                        "  'modes': ['suffix_append', 'suffix_append'],",
                        "  'kinds': ['normal', 'normal'],",
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
                READLINE_LINE="git st"
                READLINE_POINT=6
                _agensic_readline_cycle_next
                printf '%s\\n' "${AGENSIC_SUGGESTIONS[0]}|${AGENSIC_SUGGESTIONS[1]}|${AGENSIC_BASH_LAST_INFO_MESSAGE}"
                """,
                env={"TEST_HELPER": str(helper_path)},
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), "atus| add|git status")

    def test_readline_self_insert_fetches_automatically(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            helper_path = Path(tmpdir) / "helper.py"
            helper_path.write_text(
                "\n".join(
                    [
                        "import json",
                        "print(json.dumps({",
                        "  'ok': True,",
                        "  'pool': ['atus'],",
                        "  'display': ['atus'],",
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
                READLINE_LINE="git s"
                READLINE_POINT=5
                _agensic_readline_self_insert_char t
                printf '%s\\n' "${READLINE_LINE}|${READLINE_POINT}|${AGENSIC_SUGGESTIONS[0]}|${AGENSIC_BASH_LAST_INFO_MESSAGE}"
                """,
                env={"TEST_HELPER": str(helper_path)},
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), "git st|6|atus|git status")

    def test_readline_delete_backward_char_refetches_for_shorter_prefix(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            helper_path = Path(tmpdir) / "helper.py"
            helper_path.write_text(
                "\n".join(
                    [
                        "import json",
                        "print(json.dumps({",
                        "  'ok': True,",
                        "  'pool': ['atus'],",
                        "  'display': ['atus'],",
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
                READLINE_LINE="git stu"
                READLINE_POINT=${#READLINE_LINE}
                _agensic_readline_delete_backward_char
                printf '%s\\n' "${READLINE_LINE}|${READLINE_POINT}|${AGENSIC_SUGGESTIONS[0]}|${AGENSIC_BASH_LAST_INFO_MESSAGE}"
                """,
                env={"TEST_HELPER": str(helper_path)},
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), "git st|6|atus|git status")

    def test_readline_partial_accept_does_not_depend_on_ble(self):
        result = self._run_bash(
            """
            AGENSIC_BASH_READLINE_AVAILABLE=1
            READLINE_LINE="git "
            READLINE_POINT=${#READLINE_LINE}
            AGENSIC_LAST_BUFFER="git "
            AGENSIC_SUGGESTIONS=("status --short")
            AGENSIC_DISPLAY_TEXTS=("status --short")
            AGENSIC_ACCEPT_MODES=("suffix_append")
            AGENSIC_SUGGESTION_KINDS=("normal")
            AGENSIC_SUGGESTION_INDEX=1
            _agensic_readline_partial_accept
            printf '%s\\n' "${READLINE_LINE}|${READLINE_POINT}|${#AGENSIC_SUGGESTIONS[@]}"
            """
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), "git status|10|0")

    def test_intent_command_rewrites_buffer_from_helper_response(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            helper_path = Path(tmpdir) / "helper.py"
            helper_path.write_text(
                "\n".join(
                    [
                        "import sys",
                        "print('agensic_shell_lines_v1')",
                        "print('intent')",
                        "print('1')",
                        "print('')",
                        "print('ok')",
                        "print('git status')",
                        "print('Use git status')",
                        "print('')",
                        "print('git status')",
                        "print('')",
                        "print('')",
                        "print('')",
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
                READLINE_LINE="# show repo status"
                READLINE_POINT=${#READLINE_LINE}
                _agensic_bash_handle_enter
                printf '%s\\n' "${READLINE_LINE}"
                """,
                env={"TEST_HELPER": str(helper_path)},
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), "git status")

    def test_preexec_prefers_history_entry_over_bash_command_internal(self):
        result = self._run_bash(
            """
            history -s -- 'agensic start'
            AGENSIC_BASH_AT_PROMPT=1
            BASH_COMMAND='[[ -n "${PROMPT_COMMAND:-}" ]]'
            _agensic_bash_preexec_trap
            printf '%s\\n' "${AGENSIC_LAST_EXECUTED_CMD}"
            """
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), "agensic start")


if __name__ == "__main__":
    unittest.main()
