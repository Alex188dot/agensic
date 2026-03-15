import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock

from agensic.vector_db.command_db import CommandVectorDB


class HistorySyncPolicyTests(unittest.TestCase):
    def _build_db(self) -> CommandVectorDB:
        db = CommandVectorDB.__new__(CommandVectorDB)
        db.state_store = Mock()
        db.state_store.apply_history_counts.return_value = 0
        db.inserted_commands = set()
        db._io_lock = threading.RLock()
        db._set_init_phase = Mock()
        db._set_init_error = Mock()
        db.insert_commands = Mock()
        db.upsert_history_commands = Mock(return_value=0)
        return db

    def test_initialize_from_history_seeds_only_when_store_empty_and_unseeded(self):
        db = self._build_db()
        db.state_store.get_meta.return_value = ""
        db.state_store.count_commands.return_value = 0
        db._read_history_commands_from_offset = Mock(
            return_value=({"git status": 2, "ls": 1}, 123)
        )

        with tempfile.TemporaryDirectory() as tmp:
            history_file = str(Path(tmp) / ".zsh_history")
            Path(history_file).write_text("git status\nls\n", encoding="utf-8")

            result = db.initialize_from_history(history_file)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["imported_commands"], 0)
        db.state_store.apply_history_counts.assert_called_once_with({"git status": 2, "ls": 1})
        db.insert_commands.assert_called_once_with(["git status", "ls"])
        db.state_store.set_meta.assert_called_once_with(
            CommandVectorDB.HISTORY_SEED_COMPLETED_META_KEY,
            "1",
        )
        db.state_store.set_history_index_state.assert_called_once()

    def test_initialize_from_history_skips_when_seed_already_completed(self):
        db = self._build_db()
        db.state_store.get_meta.return_value = "1"
        db.state_store.count_commands.return_value = 0
        db._read_history_commands_from_offset = Mock()

        with tempfile.TemporaryDirectory() as tmp:
            history_file = str(Path(tmp) / ".zsh_history")
            Path(history_file).write_text("git status\n", encoding="utf-8")

            result = db.initialize_from_history(history_file)

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "seed_already_completed")
        db._read_history_commands_from_offset.assert_not_called()
        db.state_store.apply_history_counts.assert_not_called()

    def test_resync_history_imports_only_positive_history_deltas(self):
        db = self._build_db()
        db.state_store.get_command_stats.return_value = {
            "git status": {"history_count": 1},
            "ls": {"history_count": 2},
        }
        db._read_history_commands_from_offset = Mock(
            return_value=({"git status": 3, "ls": 2}, 88)
        )

        with tempfile.TemporaryDirectory() as tmp:
            history_file = str(Path(tmp) / ".zsh_history")
            Path(history_file).write_text("git status\ngit status\ngit status\nls\nls\n", encoding="utf-8")

            result = db.resync_history(history_file)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["imported_commands"], 0)
        self.assertEqual(result["delta_commands"], 1)
        db.state_store.apply_history_counts.assert_called_once_with({"git status": 2})
        db.insert_commands.assert_called_once_with(["git status"])
        db.state_store.set_meta.assert_called_once_with(
            CommandVectorDB.HISTORY_SEED_COMPLETED_META_KEY,
            "1",
        )


if __name__ == "__main__":
    unittest.main()
