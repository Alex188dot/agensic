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
    def _run_zsh(self, body: str) -> subprocess.CompletedProcess:
        script = textwrap.dedent(
            f"""
            source {AGENSIC_ZSH}
            {body}
            """
        )
        with tempfile.TemporaryDirectory() as temp_home:
            env = dict(os.environ)
            env["HOME"] = temp_home
            env["AGENSIC_RUNTIME_PYTHON"] = str(PYTHON_BIN)
            return subprocess.run(
                ["zsh", "-c", script],
                capture_output=True,
                text=True,
                check=False,
                env=env,
            )

    def test_session_start_and_stop_mutate_environment(self):
        result = self._run_zsh(
            """
            agensic_session_start --agent CoDeX --model gpt-5.3 --agent-name "Planner A" --ttl-minutes 1
            print -r -- "${AGENSIC_AI_SESSION_ACTIVE}|${AGENSIC_AI_SESSION_AGENT}|${AGENSIC_AI_SESSION_MODEL}|${AGENSIC_AI_SESSION_COUNTER}"
            agensic_session_stop
            print -r -- "${AGENSIC_AI_SESSION_ACTIVE:-0}|${AGENSIC_AI_SESSION_AGENT:-}|${AGENSIC_AI_SESSION_MODEL:-}|${AGENSIC_AI_SESSION_COUNTER:-}"
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        self.assertIn("1|codex|gpt-5.3|0", lines)
        self.assertIn("0|||", lines)

    def test_auto_expiry_clears_without_followup_command(self):
        result = self._run_zsh(
            """
            agensic_session_start --agent codex --model gpt-5.3 --ttl-minutes 1 >/dev/null
            export AGENSIC_AI_SESSION_EXPIRES_TS=$(( $(date +%s) + 1 ))
            _agensic_schedule_ai_session_expiry_timer
            sleep 2
            print -r -- "${AGENSIC_AI_SESSION_ACTIVE:-0}"
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("0", [line.strip() for line in result.stdout.splitlines()])

    def test_stop_clears_timer_and_session_state(self):
        result = self._run_zsh(
            """
            agensic_session_start --agent codex --model gpt-5.3 --ttl-minutes 1 >/dev/null
            pid_before="${AGENSIC_AI_SESSION_TIMER_PID:-}"
            if [[ -n "$pid_before" ]] && kill -0 "$pid_before" 2>/dev/null; then
              alive_before=1
            else
              alive_before=0
            fi
            agensic_session_stop
            sleep 0.05
            if [[ -n "$pid_before" ]] && kill -0 "$pid_before" 2>/dev/null; then
              alive_after=1
            else
              alive_after=0
            fi
            print -r -- "${alive_before}|${alive_after}|${AGENSIC_AI_SESSION_ACTIVE:-0}|${AGENSIC_AI_SESSION_TIMER_PID:-}"
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        self.assertIn("1|0|0|", lines)

    def test_session_traces_are_unique_for_rapid_signing(self):
        result = self._run_zsh(
            """
            agensic_session_start --agent codex --model gpt-5.3 --ttl-minutes 1 >/dev/null
            _agensic_session_sign_if_active
            first_trace="${AGENSIC_NEXT_PROOF_TRACE:-}"
            _agensic_snapshot_pending_execution
            _agensic_session_sign_if_active
            second_trace="${AGENSIC_NEXT_PROOF_TRACE:-}"
            print -r -- "$first_trace"
            print -r -- "$second_trace"
            if [[ "$first_trace" == "$second_trace" ]]; then
              exit 9
            fi
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        traces = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        self.assertGreaterEqual(len(traces), 2)
        self.assertNotEqual(traces[-2], traces[-1])

    def test_shell_syncs_session_started_by_cli_wrapper_path(self):
        result = self._run_zsh(
            f"""
            {PYTHON_BIN} {CLI_PATH} ai-session start --agent codex --model gpt-5.3 --agent-name "Planner A" >/dev/null
            agensic_session_status
            _agensic_session_sign_if_active
            print -r -- "${{AGENSIC_NEXT_PROOF_LABEL:-}}|${{AGENSIC_NEXT_PROOF_AGENT:-}}|${{AGENSIC_NEXT_PROOF_MODEL:-}}"
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        self.assertTrue(any(line.startswith("active agent=codex model=gpt-5.3") for line in lines), msg=lines)
        self.assertIn("AI_EXECUTED|codex|gpt-5.3", lines)

    def test_shell_syncs_cli_stop_and_clears_existing_session(self):
        result = self._run_zsh(
            f"""
            agensic_session_start --agent codex --model gpt-5.3 >/dev/null
            {PYTHON_BIN} {CLI_PATH} ai-session stop >/dev/null
            agensic_session_status
            print -r -- "${{AGENSIC_AI_SESSION_ACTIVE:-0}}"
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        self.assertIn("inactive", lines)
        self.assertIn("0", lines)

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

    def test_preexec_forces_track_launcher_to_human_even_with_active_ai_session(self):
        result = self._run_zsh(
            """
            agensic_session_start --agent codex --model gpt-5.3 --agent-name "Planner A" >/dev/null
            _agensic_preexec_hook "agensic track codex"
            print -r -- "${AGENSIC_PENDING_PROOF_LABEL:-}|${AGENSIC_PENDING_ACCEPTED_ORIGIN:-}|${AGENSIC_NEXT_PROOF_LABEL:-}"
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        self.assertIn("||", lines)

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


if __name__ == "__main__":
    unittest.main()
