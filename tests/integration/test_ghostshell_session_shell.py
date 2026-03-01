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


if __name__ == "__main__":
    unittest.main()
