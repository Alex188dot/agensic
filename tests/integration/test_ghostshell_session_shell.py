import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
GHOSTSHELL_ZSH = REPO_ROOT / "ghostshell.zsh"


class GhostshellSessionShellTests(unittest.TestCase):
    def _run_zsh(self, body: str) -> subprocess.CompletedProcess:
        script = textwrap.dedent(
            f"""
            source {GHOSTSHELL_ZSH}
            {body}
            """
        )
        with tempfile.TemporaryDirectory() as temp_home:
            env = dict(os.environ)
            env["HOME"] = temp_home
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
            ghostshell_session_start --agent CoDeX --model gpt-5.3 --agent-name "Planner A" --ttl-minutes 1
            print -r -- "${GHOSTSHELL_AI_SESSION_ACTIVE}|${GHOSTSHELL_AI_SESSION_AGENT}|${GHOSTSHELL_AI_SESSION_MODEL}|${GHOSTSHELL_AI_SESSION_COUNTER}"
            ghostshell_session_stop
            print -r -- "${GHOSTSHELL_AI_SESSION_ACTIVE:-0}|${GHOSTSHELL_AI_SESSION_AGENT:-}|${GHOSTSHELL_AI_SESSION_MODEL:-}|${GHOSTSHELL_AI_SESSION_COUNTER:-}"
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        self.assertIn("1|codex|gpt-5.3|0", lines)
        self.assertIn("0|||", lines)

    def test_auto_expiry_clears_without_followup_command(self):
        result = self._run_zsh(
            """
            ghostshell_session_start --agent codex --model gpt-5.3 --ttl-minutes 1 >/dev/null
            export GHOSTSHELL_AI_SESSION_EXPIRES_TS=$(( $(date +%s) + 1 ))
            _ghostshell_schedule_ai_session_expiry_timer
            sleep 2
            print -r -- "${GHOSTSHELL_AI_SESSION_ACTIVE:-0}"
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("0", [line.strip() for line in result.stdout.splitlines()])

    def test_stop_clears_timer_and_session_state(self):
        result = self._run_zsh(
            """
            ghostshell_session_start --agent codex --model gpt-5.3 --ttl-minutes 1 >/dev/null
            pid_before="${GHOSTSHELL_AI_SESSION_TIMER_PID:-}"
            if [[ -n "$pid_before" ]] && kill -0 "$pid_before" 2>/dev/null; then
              alive_before=1
            else
              alive_before=0
            fi
            ghostshell_session_stop
            sleep 0.05
            if [[ -n "$pid_before" ]] && kill -0 "$pid_before" 2>/dev/null; then
              alive_after=1
            else
              alive_after=0
            fi
            print -r -- "${alive_before}|${alive_after}|${GHOSTSHELL_AI_SESSION_ACTIVE:-0}|${GHOSTSHELL_AI_SESSION_TIMER_PID:-}"
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        self.assertIn("1|0|0|", lines)

    def test_session_traces_are_unique_for_rapid_signing(self):
        result = self._run_zsh(
            """
            ghostshell_session_start --agent codex --model gpt-5.3 --ttl-minutes 1 >/dev/null
            _ghostshell_session_sign_if_active
            first_trace="${GHOSTSHELL_NEXT_PROOF_TRACE:-}"
            _ghostshell_snapshot_pending_execution
            _ghostshell_session_sign_if_active
            second_trace="${GHOSTSHELL_NEXT_PROOF_TRACE:-}"
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

    def test_preexec_preserves_human_edit_pending_state(self):
        result = self._run_zsh(
            """
            GHOSTSHELL_LINE_LAST_ACTION="human_edit"
            _ghostshell_snapshot_pending_execution
            _ghostshell_reset_provenance_line_state
            _ghostshell_preexec_hook "echo hi"
            print -r -- "${GHOSTSHELL_PENDING_LAST_ACTION:-}|${GHOSTSHELL_PENDING_ACCEPTED_ORIGIN:-}"
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        self.assertIn("human_edit|", lines)

    def test_preexec_preserves_human_paste_pending_state(self):
        result = self._run_zsh(
            """
            GHOSTSHELL_LINE_LAST_ACTION="human_paste"
            _ghostshell_snapshot_pending_execution
            _ghostshell_reset_provenance_line_state
            _ghostshell_preexec_hook "echo hi"
            print -r -- "${GHOSTSHELL_PENDING_LAST_ACTION:-}|${GHOSTSHELL_PENDING_ACCEPTED_ORIGIN:-}"
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        self.assertIn("human_paste|", lines)

    def test_preexec_preserves_gs_accept_pending_state(self):
        result = self._run_zsh(
            """
            _ghostshell_set_suggestion_accept_state "gs" "suffix_append" "normal" "" "" ""
            _ghostshell_snapshot_pending_execution
            _ghostshell_reset_provenance_line_state
            _ghostshell_preexec_hook "echo hi"
            print -r -- "${GHOSTSHELL_PENDING_LAST_ACTION:-}|${GHOSTSHELL_PENDING_ACCEPTED_ORIGIN:-}"
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        self.assertIn("suggestion_accept|gs", lines)

    def test_preexec_refreshes_proof_without_clobbering_pending_action(self):
        result = self._run_zsh(
            """
            GHOSTSHELL_PENDING_LAST_ACTION="human_edit"
            GHOSTSHELL_PENDING_ACCEPTED_ORIGIN=""
            GHOSTSHELL_NEXT_PROOF_LABEL="AI_EXECUTED"
            GHOSTSHELL_NEXT_PROOF_AGENT="codex"
            GHOSTSHELL_NEXT_PROOF_MODEL="gpt-5.3"
            GHOSTSHELL_NEXT_PROOF_TRACE="trace-preexec-proof"
            GHOSTSHELL_NEXT_PROOF_TIMESTAMP="123"
            GHOSTSHELL_NEXT_PROOF_SIGNATURE="sig"
            _ghostshell_preexec_hook "echo hi"
            print -r -- "${GHOSTSHELL_PENDING_LAST_ACTION:-}|${GHOSTSHELL_PENDING_PROOF_LABEL:-}|${GHOSTSHELL_PENDING_PROOF_TRACE:-}|${GHOSTSHELL_NEXT_PROOF_TRACE:-}"
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        self.assertIn("human_edit|AI_EXECUTED|trace-preexec-proof|", lines)

    def test_decode_common_escapes_turns_backslash_n_into_newlines(self):
        result = self._run_zsh(
            """
            decoded="$(_ghostshell_decode_common_escapes 'first\\nsecond\\n\\n- bullet')"
            print -r -- "$decoded"
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("first\nsecond\n\n- bullet", result.stdout)
        self.assertNotIn("first\\nsecond", result.stdout)

    def test_build_log_command_json_includes_captured_output_tails(self):
        result = self._run_zsh(
            """
            stdout_path="$(mktemp "${HOME}/stdout.XXXXXX")"
            stderr_path="$(mktemp "${HOME}/stderr.XXXXXX")"
            print -rn -- "hello stdout" > "$stdout_path"
            print -rn -- "hello stderr" > "$stderr_path"
            json="$(_ghostshell_build_log_command_json 'echo hi' '7' 'runtime' '999999999' "$stdout_path" "$stderr_path")"
            print -r -- "$json"
            command rm -f -- "$stdout_path" "$stderr_path"
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn('"captured_stderr_tail":"hello stderr"', result.stdout)
        self.assertNotIn('"captured_stdout_tail"', result.stdout)
        self.assertIn('"duration_ms":86400000', result.stdout)

    def test_build_log_command_json_omits_output_for_zero_exit(self):
        result = self._run_zsh(
            """
            stdout_path="$(mktemp "${HOME}/stdout.XXXXXX")"
            stderr_path="$(mktemp "${HOME}/stderr.XXXXXX")"
            print -rn -- "hello stdout" > "$stdout_path"
            print -rn -- "hello stderr" > "$stderr_path"
            json="$(_ghostshell_build_log_command_json 'echo hi' '0' 'runtime' '123' "$stdout_path" "$stderr_path")"
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
            GHOSTSHELL_FORCE_RUNTIME_OUTPUT_CAPTURE=1
            _ghostshell_begin_runtime_capture "echo hi"
            print -r -- "stdout-line"
            print -u2 -r -- "stderr-line"
            stdout_path="$GHOSTSHELL_RUNTIME_CAPTURE_STDOUT_PATH"
            stderr_path="$GHOSTSHELL_RUNTIME_CAPTURE_STDERR_PATH"
            _ghostshell_end_runtime_capture
            _ghostshell_wait_for_runtime_capture_flush "$stdout_path" "$stderr_path"
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
            GHOSTSHELL_FORCE_RUNTIME_OUTPUT_CAPTURE=1
            _ghostshell_begin_runtime_capture "echo hi"
            during="${FORCE_COLOR}|${CLICOLOR_FORCE}|${PY_COLORS}|${TTY_COMPATIBLE}|${TTY_INTERACTIVE}|${NO_COLOR-__unset__}"
            stdout_path="$GHOSTSHELL_RUNTIME_CAPTURE_STDOUT_PATH"
            stderr_path="$GHOSTSHELL_RUNTIME_CAPTURE_STDERR_PATH"
            _ghostshell_end_runtime_capture
            _ghostshell_wait_for_runtime_capture_flush "$stdout_path" "$stderr_path"
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

    def test_runtime_blocked_command_guard_filters_destructive_commands(self):
        result = self._run_zsh(
            """
            _ghostshell_is_blocked_runtime_command "rm -rf /tmp/demo"; print -r -- "rm=$?"
            _ghostshell_is_blocked_runtime_command "history -c"; print -r -- "history_clear=$?"
            _ghostshell_is_blocked_runtime_command "git reset --hard HEAD~1"; print -r -- "git_reset_hard=$?"
            _ghostshell_is_blocked_runtime_command "git clean -fdx"; print -r -- "git_clean_force=$?"
            _ghostshell_is_blocked_runtime_command "mkfs.ext4 /dev/sdb1"; print -r -- "mkfs=$?"

            _ghostshell_is_blocked_runtime_command "history 20"; print -r -- "history_list=$?"
            _ghostshell_is_blocked_runtime_command "git reset --soft HEAD~1"; print -r -- "git_reset_soft=$?"
            _ghostshell_is_blocked_runtime_command "git clean -n"; print -r -- "git_clean_dry_run=$?"
            _ghostshell_is_blocked_runtime_command "echo hello"; print -r -- "echo=$?"
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
            _ghostshell_should_skip_ghostshell_for_buffer
            print -r -- "skip_shred=$?"

            BUFFER="passwd username "
            _ghostshell_should_skip_ghostshell_for_buffer
            print -r -- "skip_passwd=$?"

            BUFFER="echo hello "
            _ghostshell_should_skip_ghostshell_for_buffer
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
