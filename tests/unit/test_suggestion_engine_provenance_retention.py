import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ghostshell.engine.suggestion_engine import (
    COMMAND_PROVENANCE_RETENTION_DAYS,
    COMMAND_PROVENANCE_RETENTION_SECONDS,
    SuggestionEngine,
    command_provenance_prune_cutoff,
)


class _StateStoreStub:
    def __init__(self) -> None:
        self.calls: list[int] = []

    def prune_command_runs(self, cutoff: int) -> int:
        self.calls.append(int(cutoff))
        return 1


class SuggestionEngineProvenanceRetentionTests(unittest.TestCase):
    def test_retention_constants_are_365_days(self):
        self.assertEqual(COMMAND_PROVENANCE_RETENTION_DAYS, 365)
        self.assertEqual(COMMAND_PROVENANCE_RETENTION_SECONDS, 365 * 24 * 3600)

    def test_cutoff_helper_uses_365_day_window(self):
        now_ts = 2_000_000_000
        self.assertEqual(
            command_provenance_prune_cutoff(now_ts),
            now_ts - (365 * 24 * 3600),
        )

    def test_maybe_prune_uses_365_day_cutoff(self):
        engine = SuggestionEngine.__new__(SuggestionEngine)
        engine.state_store = _StateStoreStub()
        engine._last_command_runs_prune_ts = 0
        engine.privacy_guard = SimpleNamespace(sanitize_for_log=lambda value: value)

        with patch("ghostshell.engine.suggestion_engine.time.time", return_value=2_000_000_000):
            engine._maybe_prune_command_runs()

        self.assertEqual(
            engine.state_store.calls,
            [2_000_000_000 - COMMAND_PROVENANCE_RETENTION_SECONDS],
        )


if __name__ == "__main__":
    unittest.main()
