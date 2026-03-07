import unittest

from agensic.server.schemas import Feedback, LogCommandPayload


class FeedbackSchemaTests(unittest.TestCase):
    def test_feedback_schema_accepts_legacy_payload(self):
        payload = Feedback(
            command_buffer="git",
            accepted_suggestion=" status",
            accept_mode="suffix_append",
        )
        self.assertIsNone(payload.working_directory)

    def test_feedback_schema_accepts_working_directory(self):
        payload = Feedback(
            command_buffer="git",
            accepted_suggestion=" status",
            accept_mode="suffix_append",
            working_directory="/tmp/repo",
        )
        self.assertEqual(payload.working_directory, "/tmp/repo")

    def test_log_command_schema_accepts_provenance_payload(self):
        payload = LogCommandPayload(
            command="git status",
            source="runtime",
            exit_code=0,
            duration_ms=128,
            working_directory="/tmp/repo",
            captured_stdout_tail="stdout tail",
            captured_stderr_tail="stderr tail",
            captured_output_truncated=True,
            shell_pid=123,
            provenance_last_action="human_typed",
            provenance_accept_origin="ai",
            provenance_accept_mode="suffix_append",
            provenance_suggestion_kind="normal",
            provenance_manual_edit_after_accept=False,
            provenance_ai_agent="codex",
            provenance_ai_provider="openai",
            provenance_ai_model="gpt-5.3",
            provenance_agent_name="Planner A",
            provenance_agent_hint="codex",
            provenance_model_raw="gpt-5.3",
            provenance_wrapper_id="agensic_ai_exec:trace-1",
            proof_label="AI_EXECUTED",
            proof_agent="codex",
            proof_model="gpt-5.3",
            proof_trace="trace-1",
            proof_timestamp=1700000000,
            proof_signature="abc",
            proof_signer_scope="local-hmac",
            proof_key_fingerprint="deadbeefdeadbeef",
            proof_host_fingerprint="0011223344556677",
        )
        self.assertEqual(payload.command, "git status")
        self.assertEqual(payload.duration_ms, 128)
        self.assertEqual(payload.captured_stdout_tail, "stdout tail")
        self.assertEqual(payload.provenance_ai_agent, "codex")
        self.assertEqual(payload.proof_signer_scope, "local-hmac")


if __name__ == "__main__":
    unittest.main()
