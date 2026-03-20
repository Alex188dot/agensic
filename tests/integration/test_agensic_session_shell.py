import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
AGENSIC_ZSH = REPO_ROOT / "agensic.zsh"
CLI_PATH = REPO_ROOT / "cli.py"
PYTHON_BIN = Path(sys.executable)


class AgensicSessionShellTests(unittest.TestCase):
    def _run_zsh(
        self,
        body: str,
        config: dict | None = None,
        local_override: dict | None = None,
    ) -> subprocess.CompletedProcess:
        script = textwrap.dedent(
            f"""
            source {AGENSIC_ZSH}
            {body}
            """
        )
        with tempfile.TemporaryDirectory() as temp_home:
            config_dir = Path(temp_home) / ".config" / "agensic"
            if config is not None or local_override is not None:
                config_dir.mkdir(parents=True, exist_ok=True)
            if config is not None:
                (config_dir / "config.json").write_text(
                    json.dumps(config),
                    encoding="utf-8",
                )
            if local_override is not None:
                (config_dir / "agent_registry.local.json").write_text(
                    json.dumps(local_override),
                    encoding="utf-8",
                )
            env = dict(os.environ)
            env["HOME"] = temp_home
            env["AGENSIC_RUNTIME_PYTHON"] = str(PYTHON_BIN)
            return subprocess.run(
                ["zsh", "-f", "-c", script],
                capture_output=True,
                text=True,
                check=False,
                env=env,
            )

    def test_session_start_is_removed(self):
        result = self._run_zsh(
            """
            agensic_session_start --agent CoDeX --model gpt-5.3 --agent-name "Planner A" --ttl-minutes 1
            print -r -- "code=$?"
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("code=2", result.stdout)
        self.assertIn("agensic_session_start has been removed", result.stderr)

    def test_session_stop_is_removed(self):
        result = self._run_zsh(
            """
            agensic_session_stop
            print -r -- "code=$?"
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("code=2", result.stdout)
        self.assertIn("agensic_session_stop has been removed", result.stderr)

    def test_session_status_is_removed(self):
        result = self._run_zsh(
            """
            agensic_session_status
            print -r -- "code=$?"
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("code=2", result.stdout)
        self.assertIn("agensic_session_status has been removed", result.stderr)

    def test_ai_session_cli_is_removed(self):
        result = self._run_zsh(
            f"""
            {PYTHON_BIN} {CLI_PATH} ai-session start --agent codex --model gpt-5.3 --agent-name "Planner A"
            print -r -- "code=$?"
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("code=2", result.stdout)
        self.assertIn("Use `agensic run <agent>` for observed agent sessions.", result.stderr)

    def test_session_signer_no_longer_arms_ai_proof(self):
        result = self._run_zsh(
            """
            _agensic_session_sign_if_active
            print -r -- "${AGENSIC_NEXT_PROOF_LABEL:-}|${AGENSIC_NEXT_PROOF_AGENT:-}|${AGENSIC_NEXT_PROOF_MODEL:-}"
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        self.assertIn("||", lines)

    def test_preexec_preserves_human_edit_pending_state(self):
        result = self._run_zsh(
            """
            AGENSIC_LINE_LAST_ACTION="human_edit"
            _agensic_snapshot_pending_execution
            _agensic_reset_provenance_line_state
            _agensic_preexec_hook "echo hi"
            print -r -- "${AGENSIC_PENDING_LAST_ACTION:-}|${AGENSIC_PENDING_ACCEPTED_ORIGIN:-}"
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        self.assertIn("human_edit|", lines)

    def test_preexec_preserves_human_paste_pending_state(self):
        result = self._run_zsh(
            """
            AGENSIC_LINE_LAST_ACTION="human_paste"
            _agensic_snapshot_pending_execution
            _agensic_reset_provenance_line_state
            _agensic_preexec_hook "echo hi"
            print -r -- "${AGENSIC_PENDING_LAST_ACTION:-}|${AGENSIC_PENDING_ACCEPTED_ORIGIN:-}"
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        self.assertIn("human_paste|", lines)

    def test_preexec_preserves_ag_accept_pending_state(self):
        result = self._run_zsh(
            """
            _agensic_set_suggestion_accept_state "ag" "suffix_append" "normal" "" "" ""
            _agensic_snapshot_pending_execution
            _agensic_reset_provenance_line_state
            _agensic_preexec_hook "echo hi"
            print -r -- "${AGENSIC_PENDING_LAST_ACTION:-}|${AGENSIC_PENDING_ACCEPTED_ORIGIN:-}"
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        self.assertIn("suggestion_accept|ag", lines)

    def test_preexec_refreshes_proof_without_clobbering_pending_action(self):
        result = self._run_zsh(
            """
            AGENSIC_PENDING_LAST_ACTION="human_edit"
            AGENSIC_PENDING_ACCEPTED_ORIGIN=""
            AGENSIC_NEXT_PROOF_LABEL="AI_EXECUTED"
            AGENSIC_NEXT_PROOF_AGENT="codex"
            AGENSIC_NEXT_PROOF_MODEL="gpt-5.3"
            AGENSIC_NEXT_PROOF_TRACE="trace-preexec-proof"
            AGENSIC_NEXT_PROOF_TIMESTAMP="123"
            AGENSIC_NEXT_PROOF_SIGNATURE="sig"
            _agensic_preexec_hook "echo hi"
            print -r -- "${AGENSIC_PENDING_LAST_ACTION:-}|${AGENSIC_PENDING_PROOF_LABEL:-}|${AGENSIC_PENDING_PROOF_TRACE:-}|${AGENSIC_NEXT_PROOF_TRACE:-}"
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        self.assertIn("human_edit|AI_EXECUTED|trace-preexec-proof|", lines)

    def test_preexec_forces_run_launcher_to_human_even_with_pending_ai_proof(self):
        result = self._run_zsh(
            """
            AGENSIC_NEXT_PROOF_LABEL="AI_EXECUTED"
            AGENSIC_NEXT_PROOF_AGENT="codex"
            AGENSIC_NEXT_PROOF_MODEL="gpt-5.3"
            _agensic_preexec_hook "agensic run codex"
            print -r -- "${AGENSIC_PENDING_LAST_ACTION:-}|${AGENSIC_PENDING_PROOF_LABEL:-}|${AGENSIC_PENDING_ACCEPTED_ORIGIN:-}|${AGENSIC_NEXT_PROOF_LABEL:-}"
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        self.assertIn("human_typed|||", lines)

    def test_preexec_forces_provenance_launcher_to_human_even_with_pending_ai_proof(self):
        result = self._run_zsh(
            """
            AGENSIC_NEXT_PROOF_LABEL="AI_EXECUTED"
            AGENSIC_NEXT_PROOF_AGENT="codex"
            AGENSIC_NEXT_PROOF_MODEL="gpt-5.3"
            _agensic_preexec_hook "agensic provenance --tui"
            print -r -- "${AGENSIC_PENDING_LAST_ACTION:-}|${AGENSIC_PENDING_PROOF_LABEL:-}|${AGENSIC_PENDING_ACCEPTED_ORIGIN:-}|${AGENSIC_NEXT_PROOF_LABEL:-}"
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        self.assertIn("human_typed|||", lines)

    def test_codex_wrapper_invokes_tracked_run_without_buffer_rewrite(self):
        result = self._run_zsh(
            """
            agensic() {
              print -r -- "agensic:$*"
            }
            codex --help
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("agensic:run codex --help", result.stdout)

    def test_claude_wrapper_invokes_tracked_run_without_buffer_rewrite(self):
        result = self._run_zsh(
            """
            agensic() {
              print -r -- "agensic:$*"
            }
            claude --resume 123
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("agensic:run claude --resume 123", result.stdout)

    def test_droid_wrapper_invokes_tracked_run_without_buffer_rewrite(self):
        result = self._run_zsh(
            """
            agensic() {
              print -r -- "agensic:$*"
            }
            droid
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("agensic:run droid", result.stdout)

    def test_wrapper_bypasses_tracking_when_setting_is_off(self):
        result = self._run_zsh(
            """
            mkdir -p "$HOME/bin"
            cat > "$HOME/bin/codex" <<'EOF'
#!/bin/sh
printf 'direct:%s\n' "$*"
EOF
            chmod +x "$HOME/bin/codex"
            PATH="$HOME/bin:$PATH"
            codex --help
            """,
            config={"automatic_agensic_sessions_enabled": False},
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("direct:--help", result.stdout)

    def test_ollama_wrapper_keeps_manual_mode_with_hint(self):
        result = self._run_zsh(
            """
            mkdir -p "$HOME/bin"
            cat > "$HOME/bin/ollama" <<'EOF'
#!/bin/sh
printf 'ollama:%s\n' "$*"
EOF
            chmod +x "$HOME/bin/ollama"
            PATH="$HOME/bin:$PATH"
            ollama run llama3.2
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("ollama:run llama3.2", result.stdout)
        self.assertIn("To enable Agensic Sessions with Ollama, use: agensic run ollama", result.stderr)

    def test_custom_exact_entrypoint_wrapper_invokes_tracked_run(self):
        result = self._run_zsh(
            """
            agensic() {
              print -r -- "agensic:$*"
            }
            my-agent sync
            """,
            local_override={
                "version": "local-test",
                "agents": [
                    {
                        "agent_id": "my-agent",
                        "display_name": "My Agent",
                        "executables": ["my-agent"],
                        "aliases": ["my-agent"],
                        "process_tokens": ["my-agent"],
                        "status": "community",
                    }
                ],
            },
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("agensic:run my-agent sync", result.stdout)

    def test_decode_common_escapes_turns_backslash_n_into_newlines(self):
        result = self._run_zsh(
            """
            decoded="$(_agensic_decode_common_escapes 'first\\nsecond\\n\\n- bullet')"
            print -r -- "$decoded"
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("first\nsecond\n\n- bullet", result.stdout)
        self.assertNotIn("first\\nsecond", result.stdout)

    def test_build_log_command_json_omits_captured_output_fields(self):
        result = self._run_zsh(
            """
            stdout_path="$(mktemp "${HOME}/stdout.XXXXXX")"
            stderr_path="$(mktemp "${HOME}/stderr.XXXXXX")"
            print -rn -- "hello stdout" > "$stdout_path"
            print -rn -- "hello stderr" > "$stderr_path"
            json="$(_agensic_build_log_command_json 'echo hi' '7' 'runtime' '999999999' "$stdout_path" "$stderr_path")"
            print -r -- "$json"
            command rm -f -- "$stdout_path" "$stderr_path"
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertNotIn('"captured_stdout_tail"', result.stdout)
        self.assertNotIn('"captured_stderr_tail"', result.stdout)
        self.assertNotIn('"captured_output_truncated"', result.stdout)
        self.assertIn('"duration_ms":86400000', result.stdout)

    def test_build_log_command_json_omits_output_for_zero_exit(self):
        result = self._run_zsh(
            """
            stdout_path="$(mktemp "${HOME}/stdout.XXXXXX")"
            stderr_path="$(mktemp "${HOME}/stderr.XXXXXX")"
            print -rn -- "hello stdout" > "$stdout_path"
            print -rn -- "hello stderr" > "$stderr_path"
            json="$(_agensic_build_log_command_json 'echo hi' '0' 'runtime' '123' "$stdout_path" "$stderr_path")"
            print -r -- "$json"
            command rm -f -- "$stdout_path" "$stderr_path"
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertNotIn('"captured_stdout_tail"', result.stdout)
        self.assertNotIn('"captured_stderr_tail"', result.stdout)
        self.assertNotIn('"captured_output_truncated"', result.stdout)

    def test_runtime_capture_helpers_record_stdout_and_stderr(self):
        result = self._run_zsh(
            """
            AGENSIC_FORCE_RUNTIME_OUTPUT_CAPTURE=1
            _agensic_begin_runtime_capture "echo hi"
            print -r -- "stdout-line"
            print -u2 -r -- "stderr-line"
            stdout_path="$AGENSIC_RUNTIME_CAPTURE_STDOUT_PATH"
            stderr_path="$AGENSIC_RUNTIME_CAPTURE_STDERR_PATH"
            _agensic_end_runtime_capture
            _agensic_wait_for_runtime_capture_flush "$stdout_path" "$stderr_path"
            stdout_content="$(cat "$stdout_path")"
            stderr_content="$(cat "$stderr_path")"
            print -r -- "stdout_path=${stdout_path}"
            print -r -- "stderr_path=${stderr_path}"
            print -r -- "stdout_file=${stdout_content}"
            print -r -- "stderr_file=${stderr_content}"
            command rm -f -- "$stdout_path" "$stderr_path"
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        self.assertTrue(any(line.startswith("stdout_path=") for line in lines))
        self.assertTrue(any(line.startswith("stderr_path=") for line in lines))
        self.assertIn("stdout_file=stdout-line", lines)
        self.assertIn("stderr_file=stderr-line", lines)

    def test_runtime_capture_temporarily_forces_color_env_and_restores_it(self):
        result = self._run_zsh(
            """
            export FORCE_COLOR=0
            export CLICOLOR_FORCE=0
            export PY_COLORS=0
            export TTY_COMPATIBLE=0
            export TTY_INTERACTIVE=0
            export NO_COLOR=1
            AGENSIC_FORCE_RUNTIME_OUTPUT_CAPTURE=1
            _agensic_begin_runtime_capture "echo hi"
            during="${FORCE_COLOR}|${CLICOLOR_FORCE}|${PY_COLORS}|${TTY_COMPATIBLE}|${TTY_INTERACTIVE}|${NO_COLOR-__unset__}"
            stdout_path="$AGENSIC_RUNTIME_CAPTURE_STDOUT_PATH"
            stderr_path="$AGENSIC_RUNTIME_CAPTURE_STDERR_PATH"
            _agensic_end_runtime_capture
            _agensic_wait_for_runtime_capture_flush "$stdout_path" "$stderr_path"
            after="${FORCE_COLOR}|${CLICOLOR_FORCE}|${PY_COLORS}|${TTY_COMPATIBLE}|${TTY_INTERACTIVE}|${NO_COLOR-__unset__}"
            print -r -- "during=${during}"
            print -r -- "after=${after}"
            command rm -f -- "$stdout_path" "$stderr_path"
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        self.assertIn("during=1|1|1|1|1|__unset__", lines)
        self.assertIn("after=0|0|0|0|0|1", lines)

    def test_runtime_capture_skips_agensic_cli_commands(self):
        result = self._run_zsh(
            """
            AGENSIC_FORCE_RUNTIME_OUTPUT_CAPTURE=1
            _agensic_should_capture_runtime_output "agensic setup"; print -r -- "agensic_alias=$?"
            _agensic_should_capture_runtime_output "python3 $AGENSIC_HOME/cli.py setup"; print -r -- "home_cli=$?"
            _agensic_should_capture_runtime_output "python3 $AGENSIC_SOURCE_DIR/cli.py setup"; print -r -- "source_cli=$?"
            _agensic_should_capture_runtime_output "python3 /tmp/other.py"; print -r -- "other_py=$?"
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        self.assertIn("agensic_alias=1", lines)
        self.assertIn("home_cli=1", lines)
        self.assertIn("source_cli=1", lines)
        self.assertIn("other_py=0", lines)

    def test_runtime_blocked_command_guard_filters_destructive_commands(self):
        result = self._run_zsh(
            """
            _agensic_is_blocked_runtime_command "rm -rf /tmp/demo"; print -r -- "rm=$?"
            _agensic_is_blocked_runtime_command "history -c"; print -r -- "history_clear=$?"
            _agensic_is_blocked_runtime_command "git reset --hard HEAD~1"; print -r -- "git_reset_hard=$?"
            _agensic_is_blocked_runtime_command "git clean -fdx"; print -r -- "git_clean_force=$?"
            _agensic_is_blocked_runtime_command "mkfs.ext4 /dev/sdb1"; print -r -- "mkfs=$?"

            _agensic_is_blocked_runtime_command "history 20"; print -r -- "history_list=$?"
            _agensic_is_blocked_runtime_command "git reset --soft HEAD~1"; print -r -- "git_reset_soft=$?"
            _agensic_is_blocked_runtime_command "git clean -n"; print -r -- "git_clean_dry_run=$?"
            _agensic_is_blocked_runtime_command "echo hello"; print -r -- "echo=$?"
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        self.assertIn("rm=0", lines)
        self.assertIn("history_clear=0", lines)
        self.assertIn("git_reset_hard=0", lines)
        self.assertIn("git_clean_force=0", lines)
        self.assertIn("mkfs=0", lines)
        self.assertIn("history_list=1", lines)
        self.assertIn("git_reset_soft=1", lines)
        self.assertIn("git_clean_dry_run=1", lines)
        self.assertIn("echo=1", lines)

    def test_pause_timer_does_not_register_usr1_trap(self):
        result = self._run_zsh(
            """
            if typeset -f TRAPUSR1 >/dev/null 2>&1; then
              print -r -- "trap_usr1=present"
            else
              print -r -- "trap_usr1=absent"
            fi
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        self.assertIn("trap_usr1=absent", lines)

    def test_blocked_buffer_is_skipped_for_inline_suggestions(self):
        result = self._run_zsh(
            """
            BUFFER="shred -u "
            _agensic_should_skip_agensic_for_buffer
            print -r -- "skip_shred=$?"

            BUFFER="passwd username "
            _agensic_should_skip_agensic_for_buffer
            print -r -- "skip_passwd=$?"

            BUFFER="echo hello "
            _agensic_should_skip_agensic_for_buffer
            print -r -- "skip_echo=$?"
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        self.assertIn("skip_shred=0", lines)
        self.assertIn("skip_passwd=0", lines)
        self.assertIn("skip_echo=1", lines)

    def test_autocomplete_disabled_skips_inline_fetch_but_keeps_runtime_logging_state(self):
        result = self._run_zsh(
            """
            BUFFER="echo hello"
            AGENSIC_LAST_EXECUTED_CMD="echo keep-provenance"
            _agensic_fetch_suggestions 1 "manual_ctrl_space"
            print -r -- "suggestions=${#AGENSIC_SUGGESTIONS[@]}"
            print -r -- "last_cmd=${AGENSIC_LAST_EXECUTED_CMD:-}"
            """,
            config={"autocomplete_enabled": False},
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        self.assertIn("suggestions=0", lines)
        self.assertIn("last_cmd=echo keep-provenance", lines)

    def test_autocomplete_disabled_blocks_hash_modes_locally(self):
        result = self._run_zsh(
            """
            BUFFER="# show git status"
            _agensic_resolve_intent_command "$BUFFER" || true
            BUFFER="## explain ls"
            _agensic_resolve_general_assist "$BUFFER" || true
            """,
            config={"autocomplete_enabled": False},
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Autocomplete is turned off", result.stdout)


if __name__ == "__main__":
    unittest.main()
