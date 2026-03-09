import os
import tempfile
import time
import unittest
from unittest.mock import patch

from agensic.engine.provenance import (
    PROOF_MAX_AGE_SECONDS,
    build_local_proof_metadata,
    classify_command_run,
    ensure_provenance_keypair,
    sign_proof_payload,
    verify_signed_proof,
)


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
            "proof_signer_scope": "local-ed25519",
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
        self.assertIn("proof_signer_scope=local-ed25519", out["evidence"])
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

    def test_invalid_proof_becomes_invalid_proof_label(self):
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
        self.assertEqual(out["label"], "INVALID_PROOF")
        self.assertFalse(out["proof_valid"])
        self.assertEqual(out["evidence_tier"], "proof_invalid")
        self.assertEqual(out["agent"], "codex")
        self.assertEqual(out["registry_status"], "invalid_proof")
        self.assertIn("proof_claim_unverified=true", out["evidence"])
        self.assertNotIn("proof_signature_present_override", out["evidence"])

    def test_missing_proof_fields_becomes_invalid_proof_without_verifier_call(self):
        payload = {
            "proof_label": "AI_EXECUTED",
            "proof_agent": "codex",
            "proof_trace": "trace-xyz",
            "proof_timestamp": 1700000000,
            "proof_signature": "sig",
        }
        with patch(
            "agensic.engine.provenance.verify_signed_proof",
            return_value=(True, "proof_valid"),
        ) as mock_verify, patch(
            "agensic.engine.provenance.inspect_process_lineage",
            return_value={"lineage": [], "hints": []},
        ):
            out = classify_command_run("echo hi", payload)
        mock_verify.assert_not_called()
        self.assertEqual(out["label"], "INVALID_PROOF")
        self.assertFalse(out["proof_valid"])
        self.assertEqual(out["proof_reason"], "proof_model_missing")
        self.assertIn("proof_model_missing", out["evidence"])

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

    def test_ag_suggested_human_ran(self):
        payload = {
            "provenance_last_action": "suggestion_accept",
            "provenance_accept_origin": "ag",
            "provenance_manual_edit_after_accept": False,
        }
        with patch(
            "agensic.engine.provenance.inspect_process_lineage",
            return_value={"lineage": [], "hints": []},
        ):
            out = classify_command_run("docker ps", payload)
        self.assertEqual(out["label"], "AG_SUGGESTED_HUMAN_RAN")

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

    def test_ed25519_round_trip_and_metadata(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            private_path = os.path.join(tmpdir, "provenance_ed25519.pem")
            public_path = os.path.join(tmpdir, "provenance_ed25519.pub.pem")
            timestamp = int(time.time())
            signature = sign_proof_payload(
                "AI_EXECUTED",
                "codex",
                "gpt-5.3",
                "trace-ed25519",
                timestamp,
                private_path=private_path,
                public_path=public_path,
            )

            ok, reason = verify_signed_proof(
                "AI_EXECUTED",
                "codex",
                "gpt-5.3",
                "trace-ed25519",
                timestamp,
                signature,
                now_ts=timestamp,
                public_path=public_path,
            )
            metadata = build_local_proof_metadata(private_path=private_path, public_path=public_path)

        self.assertTrue(ok)
        self.assertEqual(reason, "proof_valid")
        self.assertEqual(metadata["proof_signer_scope"], "local-ed25519")
        self.assertRegex(metadata["proof_key_fingerprint"], r"^[0-9a-f]{16}$")
        self.assertRegex(metadata["proof_host_fingerprint"], r"^[0-9a-f]{16}$")

    def test_ed25519_proof_rejects_stale_timestamps(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            private_path = os.path.join(tmpdir, "provenance_ed25519.pem")
            public_path = os.path.join(tmpdir, "provenance_ed25519.pub.pem")
            timestamp = int(time.time())
            signature = sign_proof_payload(
                "AI_EXECUTED",
                "codex",
                "gpt-5.3",
                "trace-stale",
                timestamp,
                private_path=private_path,
                public_path=public_path,
            )

            ok, reason = verify_signed_proof(
                "AI_EXECUTED",
                "codex",
                "gpt-5.3",
                "trace-stale",
                timestamp,
                signature,
                now_ts=timestamp + PROOF_MAX_AGE_SECONDS + 1,
                public_path=public_path,
            )

        self.assertFalse(ok)
        self.assertEqual(reason, "proof_timestamp_stale")

    def test_ensure_provenance_keypair_repairs_mismatched_public_key(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            private_path = os.path.join(tmpdir, "provenance_ed25519.pem")
            public_path = os.path.join(tmpdir, "provenance_ed25519.pub.pem")

            sign_proof_payload(
                "AI_EXECUTED",
                "codex",
                "gpt-5.3",
                "trace-repair-init",
                int(time.time()),
                private_path=private_path,
                public_path=public_path,
            )

            # Simulate a stale or copied public key file from another keypair.
            other_private = os.path.join(tmpdir, "other_provenance_ed25519.pem")
            other_public = os.path.join(tmpdir, "other_provenance_ed25519.pub.pem")
            sign_proof_payload(
                "AI_EXECUTED",
                "codex",
                "gpt-5.3",
                "trace-repair-other",
                int(time.time()),
                private_path=other_private,
                public_path=other_public,
            )
            with open(other_public, "rb") as src, open(public_path, "wb") as dst:
                dst.write(src.read())

            ensure_provenance_keypair(private_path=private_path, public_path=public_path)

            timestamp = int(time.time())
            signature = sign_proof_payload(
                "AI_EXECUTED",
                "codex",
                "gpt-5.3",
                "trace-repair-final",
                timestamp,
                private_path=private_path,
                public_path=public_path,
            )
            ok, reason = verify_signed_proof(
                "AI_EXECUTED",
                "codex",
                "gpt-5.3",
                "trace-repair-final",
                timestamp,
                signature,
                now_ts=timestamp,
                public_path=public_path,
            )

        self.assertTrue(ok)
        self.assertEqual(reason, "proof_valid")


if __name__ == "__main__":
    unittest.main()
