import unittest
from unittest.mock import patch

from agensic.engine.provenance import classify_command_run


class ProvenanceClassifierTests(unittest.TestCase):
    def test_proof_valid_forces_ai_executed(self):
        payload = {
            "provenance_last_action": "human_typed",
            "provenance_agent_name": "Planner A",
            "proof_label": "AI_EXECUTED",
            "proof_agent": "codex",
            "proof_model": "gpt-5.3",
            "proof_trace": "abc",
            "proof_timestamp": 1700000000,
            "proof_signature": "sig",
            "proof_signer_scope": "local-hmac",
            "proof_key_fingerprint": "abc123abc123abc1",
            "proof_host_fingerprint": "def456def456def4",
        }
        with patch(
            "agensic.engine.provenance.verify_signed_proof",
            return_value=(True, "proof_valid"),
        ), patch(
            "agensic.engine.provenance.inspect_process_lineage",
            return_value={"lineage": [], "hints": []},
        ):
            out = classify_command_run("echo hi", payload)
        self.assertEqual(out["label"], "AI_EXECUTED")
        self.assertTrue(out["proof_valid"])
        self.assertEqual(out["evidence_tier"], "proof")
        self.assertEqual(out["model_fingerprint"], "codex_gpt-5-codex")
        self.assertEqual(out["registry_status"], "verified")
        self.assertEqual(out["agent_name"], "Planner A")
        self.assertIn("proof_signer_scope=local-hmac", out["evidence"])
        self.assertIn("proof_key_fingerprint=abc123abc123abc1", out["evidence"])
        self.assertIn("proof_host_fingerprint=def456def456def4", out["evidence"])

    def test_human_last_action_wins(self):
        payload = {
            "provenance_last_action": "human_typed",
            "provenance_accept_origin": "ai",
            "provenance_manual_edit_after_accept": True,
        }
        with patch(
            "agensic.engine.provenance.inspect_process_lineage",
            return_value={"lineage": [], "hints": []},
        ):
            out = classify_command_run("git status", payload)
        self.assertEqual(out["label"], "HUMAN_TYPED")

    def test_human_edit_classifies_as_human_typed(self):
        payload = {"provenance_last_action": "human_edit"}
        with patch(
            "agensic.engine.provenance.inspect_process_lineage",
            return_value={"lineage": [], "hints": []},
        ):
            out = classify_command_run("git status", payload)
        self.assertEqual(out["label"], "HUMAN_TYPED")

    def test_human_paste_classifies_as_human_typed(self):
        payload = {"provenance_last_action": "human_paste"}
        with patch(
            "agensic.engine.provenance.inspect_process_lineage",
            return_value={"lineage": [], "hints": []},
        ):
            out = classify_command_run("git status", payload)
        self.assertEqual(out["label"], "HUMAN_TYPED")

    def test_proof_signature_present_overrides_human_typed(self):
        payload = {
            "provenance_last_action": "human_typed",
            "proof_label": "AI_EXECUTED",
            "proof_agent": "codex",
            "proof_model": "gpt-5.3",
            "proof_trace": "session:abc:1:123",
            "proof_timestamp": 1,
            "proof_signature": "present-but-invalid",
        }
        with patch(
            "agensic.engine.provenance.verify_signed_proof",
            return_value=(False, "proof_signature_invalid"),
        ), patch(
            "agensic.engine.provenance.inspect_process_lineage",
            return_value={"lineage": [], "hints": []},
        ):
            out = classify_command_run("echo hi", payload)
        self.assertEqual(out["label"], "AI_EXECUTED")
        self.assertFalse(out["proof_valid"])
        self.assertEqual(out["evidence_tier"], "proof")
        self.assertEqual(out["agent"], "codex")
        self.assertEqual(out["model"], "gpt-5.3")
        self.assertIn("proof_signature_present_override", out["evidence"])

    def test_ai_suggested_human_ran(self):
        payload = {
            "provenance_last_action": "suggestion_accept",
            "provenance_accept_origin": "ai",
            "provenance_manual_edit_after_accept": False,
            "provenance_ai_agent": "codex",
            "provenance_ai_model": "gpt-5.3",
            "provenance_ai_provider": "openai",
        }
        with patch(
            "agensic.engine.provenance.inspect_process_lineage",
            return_value={"lineage": [], "hints": []},
        ):
            out = classify_command_run("npm test", payload)
        self.assertEqual(out["label"], "AI_SUGGESTED_HUMAN_RAN")
        self.assertEqual(out["agent"], "codex")
        self.assertEqual(out["provider"], "openai")
        self.assertEqual(out["model_fingerprint"], "codex_gpt-5-codex")
        self.assertEqual(out["raw_model"], "gpt-5.3")
        self.assertEqual(out["normalized_model"], "gpt-5-codex")

    def test_gs_suggested_human_ran(self):
        payload = {
            "provenance_last_action": "suggestion_accept",
            "provenance_accept_origin": "gs",
            "provenance_manual_edit_after_accept": False,
        }
        with patch(
            "agensic.engine.provenance.inspect_process_lineage",
            return_value={"lineage": [], "hints": []},
        ):
            out = classify_command_run("docker ps", payload)
        self.assertEqual(out["label"], "GS_SUGGESTED_HUMAN_RAN")

    def test_unknown_when_manual_edit_after_accept_without_human_last_action(self):
        payload = {
            "provenance_last_action": "suggestion_accept",
            "provenance_accept_origin": "ai",
            "provenance_manual_edit_after_accept": True,
        }
        with patch(
            "agensic.engine.provenance.inspect_process_lineage",
            return_value={"lineage": [], "hints": []},
        ):
            out = classify_command_run("python app.py", payload)
        self.assertEqual(out["label"], "UNKNOWN")

    def test_wrapper_id_promotes_integrated_tier(self):
        payload = {
            "provenance_last_action": "suggestion_accept",
            "provenance_accept_origin": "ai",
            "provenance_manual_edit_after_accept": False,
            "provenance_ai_agent": "codex",
            "provenance_ai_provider": "openai",
            "provenance_ai_model": "gpt-5.3",
            "provenance_wrapper_id": "agensic_ai_exec:abc",
        }
        with patch(
            "agensic.engine.provenance.inspect_process_lineage",
            return_value={"lineage": [], "hints": [], "match": {}},
        ):
            out = classify_command_run("echo hi", payload)
        self.assertEqual(out["label"], "AI_SUGGESTED_HUMAN_RAN")
        self.assertEqual(out["evidence_tier"], "integrated")
        self.assertGreaterEqual(out["confidence"], 0.92)

    def test_lineage_heuristic_sets_unknown_confidence(self):
        payload = {"shell_pid": 100}
        with patch(
            "agensic.engine.provenance.inspect_process_lineage",
            return_value={
                "lineage": [],
                "hints": ["cursor"],
                "match": {
                    "agent_id": "cursor",
                    "registry_status": "verified",
                    "match_kind": "token",
                    "confidence": 0.60,
                    "evidence_tier": "heuristic",
                    "model_raw": "",
                    "model_normalized": "",
                    "provider": "",
                    "evidence": ["lineage_match=token"],
                },
            },
        ):
            out = classify_command_run("echo hi", payload)
        self.assertEqual(out["label"], "UNKNOWN")
        self.assertEqual(out["agent"], "cursor")
        self.assertEqual(out["evidence_tier"], "heuristic")
        self.assertGreaterEqual(out["confidence"], 0.60)

    def test_proof_valid_with_unmapped_agent_sets_unmapped_signed(self):
        payload = {
            "proof_label": "AI_EXECUTED",
            "proof_agent": "openhands",
            "proof_model": "claude-4",
            "proof_trace": "trace-xyz",
            "proof_timestamp": 1700000000,
            "proof_signature": "sig",
        }
        with patch(
            "agensic.engine.provenance.verify_signed_proof",
            return_value=(True, "proof_valid"),
        ), patch(
            "agensic.engine.provenance.inspect_process_lineage",
            return_value={"lineage": [], "hints": [], "match": {}},
        ):
            out = classify_command_run("echo hi", payload)
        self.assertEqual(out["label"], "AI_EXECUTED")
        self.assertEqual(out["agent"], "openhands")
        self.assertEqual(out["evidence_tier"], "proof")
        self.assertEqual(out["registry_status"], "unmapped_signed")


if __name__ == "__main__":
    unittest.main()
