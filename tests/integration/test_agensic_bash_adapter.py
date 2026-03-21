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

    def test_keyseq_from_bytes_encodes_del_for_readline_binding(self):
        result = self._run_bash(
            """
            raw=$'\\177'
            printf '%s\\n' "$(_agensic_bash_keyseq_from_bytes "$raw")"
            """
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), "\\x7f")

    def test_keyseq_from_bytes_encodes_delete_escape_sequence(self):
        result = self._run_bash(
            """
            raw=$'\\e[3~'
            printf '%s\\n' "$(_agensic_bash_keyseq_from_bytes "$raw")"
            """
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), "\\x1b\\x5b\\x33\\x7e")

    def test_script_context_switches_tab_binding_back_to_native_complete(self):
        result = self._run_bash(
            """
            _agensic_register_readline_widgets >/dev/null 2>&1 || true
            _agensic_bash_sync_tab_binding_for_buffer "python script.py"
            printf '%s\\n' "${AGENSIC_BASH_TAB_BINDING_MODE}"
            bind -q complete
            """,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.splitlines()[0].strip(), "complete")

    def test_non_script_context_restores_agensic_tab_binding(self):
        result = self._run_bash(
            """
            _agensic_register_readline_widgets >/dev/null 2>&1 || true
            _agensic_bash_sync_tab_binding_for_buffer "python script.py"
            _agensic_bash_sync_tab_binding_for_buffer "git st"
            printf '%s\\n' "${AGENSIC_BASH_TAB_BINDING_MODE}"
            bind -X | grep -F '"\\C-i": "_agensic_readline_accept"'
            """,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.splitlines()[0].strip(), "agensic")

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

    def test_readline_manual_trigger_does_not_fetch_suggestions_for_python_script_context(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            helper_path = Path(tmpdir) / "helper.py"
            helper_path.write_text(
                "\n".join(
                    [
                        "from pathlib import Path",
                        "Path(r'$MARKER').write_text('fetched', encoding='utf-8')",
                        "print('{\"ok\": true, \"pool\": [\" main.py\"], \"display\": [\" main.py\"], \"modes\": [\"suffix_append\"], \"kinds\": [\"normal\"], \"used_ai\": false, \"ai_agent\": \"\", \"ai_provider\": \"\", \"ai_model\": \"\"}')",
                        "",
                    ]
                ).replace("$MARKER", str(Path(tmpdir) / "marker.txt")),
                encoding="utf-8",
            )
            result = self._run_bash(
                """
                AGENSIC_BASH_READLINE_AVAILABLE=1
                AGENSIC_CLIENT_HELPER="$TEST_HELPER"
                AGENSIC_RUNTIME_PYTHON="python3"
                READLINE_LINE="python script.py"
                READLINE_POINT=${#READLINE_LINE}
                _agensic_readline_manual_trigger
                if [[ -f "$TEST_MARKER" ]]; then
                    marker=1
                else
                    marker=0
                fi
                printf '%s\\n' "${#AGENSIC_SUGGESTIONS[@]}|${AGENSIC_BASH_LAST_INFO_MESSAGE}|${marker}"
                """,
                env={"TEST_HELPER": str(helper_path), "TEST_MARKER": str(Path(tmpdir) / "marker.txt")},
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), "0||0")

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

    def test_readline_accept_does_not_fetch_suggestions_for_shell_script_context(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            helper_path = Path(tmpdir) / "helper.py"
            helper_path.write_text(
                "\n".join(
                    [
                        "from pathlib import Path",
                        "Path(r'$MARKER').write_text('fetched', encoding='utf-8')",
                        "print('{\"ok\": true, \"pool\": [\" ./install.sh\"], \"display\": [\" ./install.sh\"], \"modes\": [\"suffix_append\"], \"kinds\": [\"normal\"], \"used_ai\": false, \"ai_agent\": \"\", \"ai_provider\": \"\", \"ai_model\": \"\"}')",
                        "",
                    ]
                ).replace("$MARKER", str(Path(tmpdir) / "marker.txt")),
                encoding="utf-8",
            )
            result = self._run_bash(
                """
                AGENSIC_BASH_READLINE_AVAILABLE=1
                AGENSIC_CLIENT_HELPER="$TEST_HELPER"
                AGENSIC_RUNTIME_PYTHON="python3"
                READLINE_LINE="bash install.sh"
                READLINE_POINT=${#READLINE_LINE}
                _agensic_readline_accept
                if [[ -f "$TEST_MARKER" ]]; then
                    marker=1
                else
                    marker=0
                fi
                printf '%s\\n' "${READLINE_LINE}|${#AGENSIC_SUGGESTIONS[@]}|${marker}"
                """,
                env={"TEST_HELPER": str(helper_path), "TEST_MARKER": str(Path(tmpdir) / "marker.txt")},
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), "bash install.sh|0|0")

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

    def test_readline_self_insert_clears_exact_match_without_duplicating_last_character(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            helper_path = Path(tmpdir) / "helper.py"
            helper_path.write_text(
                "\n".join(
                    [
                        "import json",
                        "print(json.dumps({",
                        "  'ok': True,",
                        "  'pool': [],",
                        "  'display': [],",
                        "  'modes': [],",
                        "  'kinds': [],",
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
                READLINE_LINE="agensic sto"
                READLINE_POINT=${#READLINE_LINE}
                AGENSIC_LAST_BUFFER="agensic sto"
                AGENSIC_SUGGESTIONS=("p")
                AGENSIC_DISPLAY_TEXTS=("p")
                AGENSIC_ACCEPT_MODES=("suffix_append")
                AGENSIC_SUGGESTION_KINDS=("normal")
                AGENSIC_SUGGESTION_INDEX=1
                _agensic_readline_self_insert_char p
                printf '%s\\n' "${READLINE_LINE}|${AGENSIC_SUGGESTION_INDEX}|${AGENSIC_BASH_LAST_INFO_MESSAGE}"
                """,
                env={"TEST_HELPER": str(helper_path)},
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), "agensic stop|0|")

    def test_fetch_clears_stale_suggestions_when_daemon_is_unreachable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            helper_path = Path(tmpdir) / "helper.py"
            helper_path.write_text(
                "\n".join(
                    [
                        "import json",
                        "print(json.dumps({",
                        "  'ok': False,",
                        "  'error_code': 'daemon_unreachable',",
                        "  'pool': [],",
                        "  'display': [],",
                        "  'modes': [],",
                        "  'kinds': [],",
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
                READLINE_POINT=${#READLINE_LINE}
                AGENSIC_LAST_BUFFER="git st"
                AGENSIC_SUGGESTIONS=("atus")
                AGENSIC_DISPLAY_TEXTS=("atus")
                AGENSIC_ACCEPT_MODES=("suffix_append")
                AGENSIC_SUGGESTION_KINDS=("normal")
                AGENSIC_SUGGESTION_INDEX=1
                _agensic_bash_fetch_suggestions 1 "typing_auto" 1
                printf '%s\\n' "${#AGENSIC_SUGGESTIONS[@]}|${AGENSIC_BASH_LAST_INFO_MESSAGE}"
                """,
                env={"TEST_HELPER": str(helper_path)},
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), "0|Agensic daemon is not running. Run: agensic start")

    def test_readline_delete_backward_char_clears_suggestions_without_refetch(self):
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
                AGENSIC_LAST_BUFFER="git stu"
                AGENSIC_SUGGESTIONS=("atus")
                AGENSIC_DISPLAY_TEXTS=("atus")
                AGENSIC_ACCEPT_MODES=("suffix_append")
                AGENSIC_SUGGESTION_KINDS=("normal")
                AGENSIC_SUGGESTION_INDEX=1
                _agensic_readline_delete_backward_char
                printf '%s\\n' "${READLINE_LINE}|${READLINE_POINT}|${#AGENSIC_SUGGESTIONS[@]}|${AGENSIC_BASH_LAST_INFO_MESSAGE}"
                """,
                env={"TEST_HELPER": str(helper_path)},
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), "git st|6|0|")

    def test_readline_escape_clears_visible_suggestion_without_mutating_buffer(self):
        result = self._run_bash(
            """
            AGENSIC_BASH_READLINE_AVAILABLE=1
            READLINE_LINE="git st"
            READLINE_POINT=${#READLINE_LINE}
            AGENSIC_LAST_BUFFER="git st"
            AGENSIC_SUGGESTIONS=("atus")
            AGENSIC_DISPLAY_TEXTS=("atus")
            AGENSIC_ACCEPT_MODES=("suffix_append")
            AGENSIC_SUGGESTION_KINDS=("normal")
            AGENSIC_SUGGESTION_INDEX=1
            _agensic_readline_escape
            printf '%s\\n' "${READLINE_LINE}|${READLINE_POINT}|${#AGENSIC_SUGGESTIONS[@]}|${AGENSIC_BASH_LAST_INFO_MESSAGE}"
            """
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), "git st|6|0|")

    def test_readline_delete_char_clears_visible_suggestion_at_end_of_line(self):
        result = self._run_bash(
            """
            AGENSIC_BASH_READLINE_AVAILABLE=1
            READLINE_LINE="git st"
            READLINE_POINT=${#READLINE_LINE}
            AGENSIC_LAST_BUFFER="git st"
            AGENSIC_SUGGESTIONS=("atus")
            AGENSIC_DISPLAY_TEXTS=("atus")
            AGENSIC_ACCEPT_MODES=("suffix_append")
            AGENSIC_SUGGESTION_KINDS=("normal")
            AGENSIC_SUGGESTION_INDEX=1
            _agensic_readline_delete_char
            printf '%s\\n' "${READLINE_LINE}|${READLINE_POINT}|${#AGENSIC_SUGGESTIONS[@]}|${AGENSIC_BASH_LAST_INFO_MESSAGE}"
            """
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), "git st|6|0|")

    def test_common_delete_bindings_include_vte_modifier_variant(self):
        result = self._run_bash(
            """
            _agensic_register_readline_widgets >/dev/null 2>&1 || true
            bind -X | grep -F '"\\e[3;2~": "_agensic_readline_delete_char"'
            """
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)

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

    def test_preexec_clears_visible_suggestions_before_command_runs(self):
        result = self._run_bash(
            """
            history -s -- 'echo hi'
            AGENSIC_BASH_AT_PROMPT=1
            AGENSIC_BASH_READLINE_AVAILABLE=1
            AGENSIC_LAST_BUFFER="git st"
            AGENSIC_SUGGESTIONS=("atus")
            AGENSIC_DISPLAY_TEXTS=("atus")
            AGENSIC_ACCEPT_MODES=("suffix_append")
            AGENSIC_SUGGESTION_KINDS=("normal")
            AGENSIC_SUGGESTION_INDEX=1
            AGENSIC_BASH_LAST_INFO_MESSAGE="git status"
            _agensic_bash_preexec_trap
            printf '%s\\n' "${#AGENSIC_SUGGESTIONS[@]}|${AGENSIC_BASH_LAST_INFO_MESSAGE}"
            """
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), "0|")


if __name__ == "__main__":
    unittest.main()
