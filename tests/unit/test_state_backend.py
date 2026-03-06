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
            store.record_feedback(
                "git status",
                [("git", " status")],
                repo_task_pairs=[("repo_abc", "git status", " status")],
            )
            stats = store.get_command_stats(["git status"])

            self.assertIn("git status", stats)
            self.assertEqual(stats["git status"]["execute_count"], 1)
            self.assertEqual(stats["git status"]["accept_count"], 1)

            context_counts = store.get_feedback_counts(["git"], [" status"])
            self.assertEqual(context_counts[" status"], 1)
            repo_counts = store.get_repo_feedback_counts("repo_abc", "git status", [" status"])
            self.assertEqual(repo_counts[" status"], 1)

            store.record_execute(
                "git status",
                repo_task_pair=("repo_abc", "git status", "git status"),
            )
            repo_execute_counts = store.get_repo_execute_feedback_counts(
                "repo_abc",
                "git status",
                ["git status"],
            )
            self.assertEqual(repo_execute_counts["git status"], 1)

            recorded = store.record_command_provenance(
                command="git status",
                label="HUMAN_TYPED",
                confidence=0.9,
                agent="codex",
                agent_name="Planner A",
                provider="openai",
                model="gpt-5.3",
                raw_model="gpt-5.3",
                normalized_model="gpt-5-codex",
                model_fingerprint="codex_gpt-5-codex",
                evidence_tier="integrated",
                agent_source="payload_ai",
                registry_version="builtin-2026-02-28",
                registry_status="verified",
                source="runtime",
                duration_ms=321,
                shell_pid=123,
                evidence=["last_action=human_typed"],
                payload={"provenance_last_action": "human_typed"},
                ts=1700000000,
                run_id="run-1",
            )
            self.assertTrue(recorded)
            runs = store.list_command_runs(limit=10)
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0]["run_id"], "run-1")
            self.assertEqual(runs[0]["label"], "HUMAN_TYPED")
            self.assertEqual(runs[0]["duration_ms"], 321)
            self.assertEqual(runs[0]["agent_name"], "Planner A")
            self.assertEqual(runs[0]["raw_model"], "gpt-5.3")
            self.assertEqual(runs[0]["normalized_model"], "gpt-5-codex")
            self.assertEqual(runs[0]["evidence_tier"], "integrated")
            self.assertEqual(runs[0]["registry_status"], "verified")
            filtered = store.list_command_runs(limit=10, tier="integrated", agent="codex", agent_name="Planner A", provider="openai")
            self.assertEqual(len(filtered), 1)

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

    def test_command_runs_export_import_and_prune(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "state.sqlite")
            store = SQLiteStateStore(db_path, journal=None)
            store.record_command_provenance(
                command="echo hello",
                label="UNKNOWN",
                confidence=0.3,
                ts=1700000000,
                run_id="run-a",
            )
            payload = store.export_payload()

            second_path = os.path.join(tmp, "state-2.sqlite")
            restored = SQLiteStateStore(second_path, journal=None)
            result = restored.import_payload(payload)
            self.assertGreaterEqual(int(result.get("provenance_imported", 0) or 0), 1)
            runs = restored.list_command_runs(limit=5)
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0]["run_id"], "run-a")

            removed = restored.prune_command_runs(older_than_ts=1700000001)
            self.assertEqual(removed, 1)
            self.assertEqual(restored.list_command_runs(limit=5), [])

    def test_command_run_duration_is_capped_at_24h(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "state.sqlite")
            store = SQLiteStateStore(db_path, journal=None)
            store.record_command_provenance(
                command="sleep 999999",
                label="UNKNOWN",
                confidence=0.1,
                duration_ms=999_999_999,
                ts=1700000000,
                run_id="run-cap",
            )

            runs = store.list_command_runs(limit=5)
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0]["duration_ms"], 86_400_000)

    def test_list_command_runs_keyset_pagination(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "state.sqlite")
            store = SQLiteStateStore(db_path, journal=None)
            for idx in range(5):
                store.record_command_provenance(
                    command=f"echo {idx}",
                    label="UNKNOWN",
                    confidence=0.1,
                    ts=1700000000 + idx,
                    run_id=f"run-{idx}",
                )

            first = store.list_command_runs(limit=2)
            self.assertEqual(len(first), 2)
            second = store.list_command_runs(
                limit=2,
                before_ts=int(first[-1]["ts"]),
                before_run_id=str(first[-1]["run_id"]),
            )
            self.assertEqual(len(second), 2)
            self.assertNotEqual(first[0]["run_id"], second[0]["run_id"])

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
