import os
import tempfile
import unittest

from ghostshell.state import EventJournal, SnapshotManager, SQLiteStateStore


class StateBackendTests(unittest.TestCase):
    def test_sqlite_counters_and_feedback(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "state.sqlite")
            journal = EventJournal(os.path.join(tmp, "events"))
            store = SQLiteStateStore(db_path, journal=journal)

            store.record_execute("git status")
            store.record_feedback("git status", [("git", " status")])
            stats = store.get_command_stats(["git status"])

            self.assertIn("git status", stats)
            self.assertEqual(stats["git status"]["execute_count"], 1)
            self.assertEqual(stats["git status"]["accept_count"], 1)

            context_counts = store.get_feedback_counts(["git"], [" status"])
            self.assertEqual(context_counts[" status"], 1)

    def test_snapshot_restore_and_journal_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "state.sqlite")
            events_dir = os.path.join(tmp, "events")
            snapshots_dir = os.path.join(tmp, "snapshots")
            journal = EventJournal(events_dir)
            store = SQLiteStateStore(db_path, journal=journal)

            store.record_execute("python app.py")
            manager = SnapshotManager(db_path, snapshots_dir)
            row = manager.create_snapshot()
            self.assertTrue(os.path.exists(row["snapshot_path"]))

            store.record_execute("python app.py")
            store.record_feedback("python app.py", [("python", " app.py")])

            ok, _, _ = manager.restore_latest()
            self.assertTrue(ok)

            restored_store = SQLiteStateStore(db_path, journal=journal)
            replay = journal.replay(
                lambda event: restored_store.apply_event(event, append_to_journal=False),
                since_ts=int(row.get("snapshot_ts", 0) or 0),
            )
            self.assertGreaterEqual(int(replay.get("total", 0) or 0), 1)

            stats = restored_store.get_command_stats(["python app.py"])
            self.assertEqual(stats["python app.py"]["execute_count"], 2)

    def test_event_idempotency_with_applied_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "state.sqlite")
            store = SQLiteStateStore(db_path, journal=None)

            event = {
                "event_id": "evt-1",
                "type": "command_execute",
                "command": "ls -la",
                "delta": 1,
                "ts": 1700000000,
            }
            changed_first = store.apply_event(event, append_to_journal=False)
            changed_second = store.apply_event(event, append_to_journal=False)

            self.assertTrue(changed_first)
            self.assertFalse(changed_second)
            stats = store.get_command_stats(["ls -la"])
            self.assertEqual(stats["ls -la"]["execute_count"], 1)

    def test_history_index_state_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "state.sqlite")
            store = SQLiteStateStore(db_path, journal=None)

            history_file = "/tmp/.zsh_history"
            store.set_history_index_state(
                history_file=history_file,
                inode=123,
                device=456,
                offset=789,
                updated_at=1700000000,
            )
            row = store.get_history_index_state(history_file)

            self.assertIsNotNone(row)
            self.assertEqual(row["inode"], 123)
            self.assertEqual(row["device"], 456)
            self.assertEqual(row["offset"], 789)


if __name__ == "__main__":
    unittest.main()
