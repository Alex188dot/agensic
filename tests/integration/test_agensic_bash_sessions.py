import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
AGENSIC_BASH = REPO_ROOT / "agensic.bash"
PYTHON_BIN = Path(sys.executable)


class AgensicBashSessionsTests(unittest.TestCase):
    def _run_bash(
        self,
        body: str,
        config: dict | None = None,
        local_override: dict | None = None,
    ) -> subprocess.CompletedProcess:
        script = textwrap.dedent(
            f"""
            source "{AGENSIC_BASH}"
            {body}
            """
        )
        with tempfile.TemporaryDirectory() as temp_home:
            config_dir = Path(temp_home) / ".config" / "agensic"
            if config is not None or local_override is not None:
                config_dir.mkdir(parents=True, exist_ok=True)
            if config is not None:
                (config_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
            if local_override is not None:
                (config_dir / "agent_registry.local.json").write_text(
                    json.dumps(local_override),
                    encoding="utf-8",
                )
            env = dict(os.environ)
            env["HOME"] = temp_home
            env["AGENSIC_RUNTIME_PYTHON"] = str(PYTHON_BIN)
            return subprocess.run(
                ["bash", "-lc", script],
                capture_output=True,
                text=True,
                check=False,
                env=env,
            )

    def test_codex_wrapper_invokes_tracked_run_without_buffer_rewrite(self):
        result = self._run_bash(
            """
            agensic() {
              printf 'agensic:%s\\n' "$*"
            }
            codex --help
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("agensic:run codex --help", result.stdout)

    def test_wrapper_bypasses_tracking_when_setting_is_off(self):
        result = self._run_bash(
            """
            mkdir -p "$HOME/bin"
            cat > "$HOME/bin/codex" <<'EOF'
#!/bin/sh
printf 'direct:%s\\n' "$*"
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
        result = self._run_bash(
            """
            mkdir -p "$HOME/bin"
            cat > "$HOME/bin/ollama" <<'EOF'
#!/bin/sh
printf 'ollama:%s\\n' "$*"
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
        result = self._run_bash(
            """
            agensic() {
              printf 'agensic:%s\\n' "$*"
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

    def test_build_log_command_json_includes_full_provenance_payload_and_shell_pid(self):
        result = self._run_bash(
            """
            AGENSIC_PENDING_LAST_ACTION="suggestion_accept"
            AGENSIC_PENDING_ACCEPTED_ORIGIN="ai"
            AGENSIC_PENDING_ACCEPTED_MODE="replace_full"
            AGENSIC_PENDING_ACCEPTED_KIND="intent_command"
            AGENSIC_PENDING_MANUAL_EDIT_AFTER_ACCEPT=0
            AGENSIC_PENDING_AI_AGENT="codex"
            AGENSIC_PENDING_AI_PROVIDER="openai"
            AGENSIC_PENDING_AI_MODEL="gpt-5.3"
            AGENSIC_PENDING_AGENT_NAME="Planner A"
            AGENSIC_PENDING_AGENT_HINT="codex"
            AGENSIC_PENDING_MODEL_RAW="gpt-5.3"
            AGENSIC_PENDING_WRAPPER_ID="agensic_track:sess-1"
            AGENSIC_PENDING_PROOF_LABEL="AI_EXECUTED"
            AGENSIC_PENDING_PROOF_AGENT="codex"
            AGENSIC_PENDING_PROOF_MODEL="gpt-5.3"
            AGENSIC_PENDING_PROOF_TRACE="trace-123"
            AGENSIC_PENDING_PROOF_TIMESTAMP="1700000000"
            AGENSIC_PENDING_PROOF_SIGNATURE="sig"
            AGENSIC_PENDING_PROOF_SIGNER_SCOPE="local-ed25519"
            AGENSIC_PENDING_PROOF_KEY_FINGERPRINT="abc123"
            AGENSIC_PENDING_PROOF_HOST_FINGERPRINT="def456"
            json="$(_agensic_bash_build_log_command_json 'echo hi' '7' 'runtime' '123')"
            printf 'json=%s\\n' "$json"
            printf 'shell=%s\\n' "$$"
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        json_line = next(line for line in lines if line.startswith("json="))
        shell_line = next(line for line in lines if line.startswith("shell="))
        payload = json.loads(json_line.removeprefix("json="))
        self.assertEqual(payload["shell_pid"], int(shell_line.removeprefix("shell=")))
        self.assertEqual(payload["provenance_last_action"], "suggestion_accept")
        self.assertEqual(payload["provenance_accept_origin"], "ai")
        self.assertEqual(payload["provenance_ai_agent"], "codex")
        self.assertEqual(payload["provenance_agent_name"], "Planner A")
        self.assertEqual(payload["proof_label"], "AI_EXECUTED")
        self.assertEqual(payload["proof_trace"], "trace-123")

    def test_preexec_forces_run_launcher_to_human_even_with_pending_ai_proof(self):
        result = self._run_bash(
            """
            _agensic_bash_last_history_entry() { printf '%s\n' 'agensic run codex'; }
            AGENSIC_BASH_AT_PROMPT=1
            AGENSIC_NEXT_PROOF_LABEL="AI_EXECUTED"
            AGENSIC_NEXT_PROOF_AGENT="codex"
            AGENSIC_NEXT_PROOF_MODEL="gpt-5.3"
            _agensic_bash_preexec_trap
            printf '%s\\n' "${AGENSIC_PENDING_LAST_ACTION:-}|${AGENSIC_PENDING_PROOF_LABEL:-}|${AGENSIC_PENDING_ACCEPTED_ORIGIN:-}|${AGENSIC_NEXT_PROOF_LABEL:-}"
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("human_typed|||", result.stdout)

    def test_preexec_forces_provenance_launcher_to_human_even_with_pending_ai_proof(self):
        result = self._run_bash(
            """
            _agensic_bash_last_history_entry() { printf '%s\n' 'agensic provenance'; }
            AGENSIC_BASH_AT_PROMPT=1
            AGENSIC_NEXT_PROOF_LABEL="AI_EXECUTED"
            AGENSIC_NEXT_PROOF_AGENT="codex"
            AGENSIC_NEXT_PROOF_MODEL="gpt-5.3"
            _agensic_bash_preexec_trap
            printf '%s\\n' "${AGENSIC_PENDING_LAST_ACTION:-}|${AGENSIC_PENDING_PROOF_LABEL:-}|${AGENSIC_PENDING_ACCEPTED_ORIGIN:-}|${AGENSIC_NEXT_PROOF_LABEL:-}"
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("human_typed|||", result.stdout)

    def test_preexec_refreshes_proof_without_clobbering_pending_action(self):
        result = self._run_bash(
            """
            _agensic_bash_last_history_entry() { printf '%s\n' 'echo hi'; }
            AGENSIC_BASH_AT_PROMPT=1
            AGENSIC_PENDING_LAST_ACTION="human_edit"
            AGENSIC_PENDING_ACCEPTED_ORIGIN=""
            AGENSIC_NEXT_PROOF_LABEL="AI_EXECUTED"
            AGENSIC_NEXT_PROOF_AGENT="codex"
            AGENSIC_NEXT_PROOF_MODEL="gpt-5.3"
            AGENSIC_NEXT_PROOF_TRACE="trace-preexec-proof"
            AGENSIC_NEXT_PROOF_TIMESTAMP="123"
            AGENSIC_NEXT_PROOF_SIGNATURE="sig"
            _agensic_bash_preexec_trap
            printf '%s\\n' "${AGENSIC_PENDING_LAST_ACTION:-}|${AGENSIC_PENDING_PROOF_LABEL:-}|${AGENSIC_PENDING_PROOF_TRACE:-}|${AGENSIC_NEXT_PROOF_TRACE:-}"
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("human_edit|AI_EXECUTED|trace-preexec-proof|", result.stdout)

    def test_handle_enter_defaults_plain_manual_command_to_human_typed(self):
        result = self._run_bash(
            """
            READLINE_LINE="git status"
            READLINE_POINT=${#READLINE_LINE}
            _agensic_bash_handle_enter
            printf '%s\\n' "${AGENSIC_PENDING_LAST_ACTION:-}|${AGENSIC_PENDING_ACCEPTED_ORIGIN:-}|${AGENSIC_PENDING_PROOF_LABEL:-}"
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), "human_typed||")

    def test_handle_enter_keeps_ai_acceptance_provenance(self):
        result = self._run_bash(
            """
            READLINE_LINE="git status"
            READLINE_POINT=${#READLINE_LINE}
            AGENSIC_LINE_LAST_ACTION="suggestion_accept"
            AGENSIC_LINE_ACCEPTED_ORIGIN="ai"
            AGENSIC_LINE_ACCEPTED_MODE="replace_full"
            _agensic_bash_handle_enter
            printf '%s\\n' "${AGENSIC_PENDING_LAST_ACTION:-}|${AGENSIC_PENDING_ACCEPTED_ORIGIN:-}"
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), "suggestion_accept|ai")


if __name__ == "__main__":
    unittest.main()
