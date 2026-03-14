import unittest
from unittest.mock import Mock, patch

from agensic.engine.suggestion_engine import SuggestionEngine


def _classification(label: str) -> dict:
    return {
        "label": label,
        "confidence": 0.95,
        "agent": "",
        "agent_name": "",
        "provider": "",
        "model": "",
        "raw_model": "",
        "normalized_model": "",
        "model_fingerprint": "",
        "evidence_tier": "",
        "agent_source": "",
        "registry_version": "",
        "registry_status": "",
        "evidence": [],
    }


class SuggestionIngestionFilterTests(unittest.TestCase):
    def _build_engine(self) -> tuple[SuggestionEngine, Mock]:
        engine = SuggestionEngine.__new__(SuggestionEngine)
        vector_db = Mock()
        vector_db.is_blocked_command.return_value = False

        engine._ensure_vector_db = Mock(return_value=vector_db)
        engine._maybe_prune_command_runs = Mock()
        engine.state_store = Mock()
        engine.privacy_guard = Mock()
        engine.privacy_guard.sanitize_for_log.side_effect = lambda value: str(value)
        return (engine, vector_db)

    def test_ai_executed_is_excluded_from_suggestion_store_by_default(self):
        engine, vector_db = self._build_engine()
        with patch(
            "agensic.engine.suggestion_engine.classify_command_run",
            return_value=_classification("AI_EXECUTED"),
        ), patch(
            "agensic.engine.suggestion_engine.load_config_file",
            return_value={},
        ):
            engine.log_executed_command(
                "echo hello",
                exit_code=0,
                source="runtime",
                provenance_payload={},
            )

        vector_db.insert_command.assert_not_called()
        engine.state_store.record_command_provenance.assert_called_once()
        kwargs = engine.state_store.record_command_provenance.call_args.kwargs
        self.assertEqual(kwargs.get("label"), "AI_EXECUTED")

    def test_ai_executed_can_be_included_when_flag_enabled(self):
        engine, vector_db = self._build_engine()
        with patch(
            "agensic.engine.suggestion_engine.classify_command_run",
            return_value=_classification("AI_EXECUTED"),
        ), patch(
            "agensic.engine.suggestion_engine.load_config_file",
            return_value={"include_ai_executed_in_suggestions": True},
        ):
            engine.log_executed_command(
                "echo hello",
                exit_code=0,
                source="runtime",
                provenance_payload={},
            )

        vector_db.insert_command.assert_called_once()
        engine.state_store.record_command_provenance.assert_called_once()

    def test_ai_executed_is_excluded_from_suggestion_store_by_default(self):
        engine, vector_db = self._build_engine()
        with patch(
            "agensic.engine.suggestion_engine.classify_command_run",
            return_value=_classification("AI_EXECUTED"),
        ), patch(
            "agensic.engine.suggestion_engine.load_config_file",
            return_value={},
        ):
            engine.log_executed_command(
                "echo hello",
                exit_code=0,
                source="runtime",
                provenance_payload={},
            )

        vector_db.insert_command.assert_not_called()
        engine.state_store.record_command_provenance.assert_called_once()

    def test_non_ai_executed_still_ingests_with_default_config(self):
        engine, vector_db = self._build_engine()
        with patch(
            "agensic.engine.suggestion_engine.classify_command_run",
            return_value=_classification("HUMAN_TYPED"),
        ), patch(
            "agensic.engine.suggestion_engine.load_config_file",
            return_value={},
        ):
            engine.log_executed_command(
                "echo hello",
                exit_code=0,
                source="runtime",
                provenance_payload={},
            )

        vector_db.insert_command.assert_called_once()
        engine.state_store.record_command_provenance.assert_called_once()

    def test_runtime_nonzero_persists_provenance_but_skips_ingestion(self):
        engine, vector_db = self._build_engine()
        with patch(
            "agensic.engine.suggestion_engine.classify_command_run",
            return_value=_classification("HUMAN_TYPED"),
        ), patch(
            "agensic.engine.suggestion_engine.load_config_file",
            return_value={},
        ):
            engine.log_executed_command(
                "python app.py",
                exit_code=9,
                source="runtime",
                provenance_payload={"captured_stderr_tail": "boom\n"},
            )

        vector_db.insert_command.assert_not_called()
        engine.state_store.record_command_provenance.assert_called_once()
        kwargs = engine.state_store.record_command_provenance.call_args.kwargs
        self.assertEqual(kwargs.get("exit_code"), 9)
        self.assertNotIn("captured_stderr_tail", kwargs.get("payload", {}))


if __name__ == "__main__":
    unittest.main()
