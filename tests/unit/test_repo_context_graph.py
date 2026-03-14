import os
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from agensic.vector_db.command_db import CommandVectorDB


class RepoContextGraphTests(unittest.TestCase):
    @staticmethod
    def _build_db_for_rerank(
        repo_accept_map: dict[str, int] | None = None,
        repo_execute_map: dict[str, int] | None = None,
        command_stats: dict[str, dict[str, int]] | None = None,
        context_counts: dict[str, int] | None = None,
    ) -> CommandVectorDB:
        db = CommandVectorDB.__new__(CommandVectorDB)
        db.state_store = Mock()
        db.resolve_repo_key = Mock(return_value="repo_123")
        db.state_store.get_command_stats = Mock(return_value=command_stats or {})
        db.state_store.get_feedback_counts = Mock(
            side_effect=lambda context_keys, suffixes: {s: int((context_counts or {}).get(s, 0)) for s in suffixes}
        )
        db.state_store.get_repo_feedback_counts = Mock(
            side_effect=lambda repo_key, task_key, suffixes: {s: int((repo_accept_map or {}).get(s, 0)) for s in suffixes}
        )
        db.state_store.get_repo_execute_feedback_counts = Mock(
            side_effect=lambda repo_key, task_key, commands: {
                cmd: int((repo_execute_map or {}).get(cmd, 0)) for cmd in commands
            }
        )
        db.state_store.get_command_run_counts = Mock(
            side_effect=lambda commands, since_ts=0, labels=None: {
                cmd: 0 for cmd in commands
            }
        )
        db.state_store.get_last_command_run_ts = Mock(
            side_effect=lambda commands, label="", since_ts=0: {
                cmd: 0 for cmd in commands
            }
        )
        return db

    def test_extract_task_key_exec_and_subcmd(self):
        self.assertEqual(CommandVectorDB.extract_task_key("git commit -m 'x'"), "git commit")
        self.assertEqual(CommandVectorDB.extract_task_key("docker compose up -d"), "docker compose")
        self.assertEqual(CommandVectorDB.extract_task_key("python app.py"), "python")

    def test_repo_weight_affects_ranking(self):
        ranked = CommandVectorDB.rerank_suffixes_from_counts(
            candidates=[" status", " checkout main"],
            global_counts={" status": 10, " checkout main": 0},
            context_counts={" status": 8, " checkout main": 0},
            repo_task_counts={" status": 0, " checkout main": 6},
            execute_counts={" status": 0, " checkout main": 0},
            history_counts={" status": 0, " checkout main": 0},
        )
        self.assertEqual(ranked[0], " checkout main")

    def test_resolve_repo_key_git_and_fallback(self):
        db = CommandVectorDB.__new__(CommandVectorDB)
        db._repo_identity_cache = {}

        with tempfile.TemporaryDirectory() as tmp:
            repo = os.path.join(tmp, "repo")
            os.makedirs(repo, exist_ok=True)
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            subprocess.run(
                ["git", "remote", "add", "origin", "git@github.com:acme/agensic.git"],
                cwd=repo,
                check=True,
                capture_output=True,
            )

            key1 = db.resolve_repo_key(repo)
            key2 = db.resolve_repo_key(repo)
            self.assertTrue(key1.startswith("repo_"))
            self.assertEqual(key1, key2)

            non_repo = os.path.join(tmp, "plain")
            os.makedirs(non_repo, exist_ok=True)
            fallback_key = db.resolve_repo_key(non_repo)
            self.assertTrue(fallback_key.startswith("cwd_"))

    def test_repo_confidence_tier_thresholds(self):
        self.assertEqual(CommandVectorDB.repo_confidence_tier(0, 0), "LOW")
        self.assertEqual(CommandVectorDB.repo_confidence_tier(3, 1), "LOW")
        self.assertEqual(CommandVectorDB.repo_confidence_tier(3, 2), "MEDIUM")
        self.assertEqual(CommandVectorDB.repo_confidence_tier(6, 1), "HIGH")

    def test_medium_tier_forces_repo_anchor_on_top1(self):
        db = self._build_db_for_rerank(
            repo_accept_map={" status": 2, " checkout main": 1},
            command_stats={
                "git status": {"accept_count": 0, "execute_count": 0, "history_count": 0},
                "git checkout main": {"accept_count": 0, "execute_count": 0, "history_count": 0},
                "git --help": {"accept_count": 25, "execute_count": 0, "history_count": 0},
            },
        )
        reranked = db.rerank_candidates(
            "git",
            [" status", " checkout main", " --help"],
            working_directory="/tmp/repo",
        )
        self.assertIn(reranked[0], {" status", " checkout main"})

    def test_high_tier_prefers_repo_entries_in_top3(self):
        db = self._build_db_for_rerank(
            repo_accept_map={" status": 3, " checkout main": 2, " add -A": 1},
            command_stats={
                "git status": {"accept_count": 0, "execute_count": 0, "history_count": 0},
                "git checkout main": {"accept_count": 0, "execute_count": 0, "history_count": 0},
                "git add -A": {"accept_count": 0, "execute_count": 0, "history_count": 0},
                "git --help": {"accept_count": 50, "execute_count": 0, "history_count": 0},
            },
        )
        reranked = db.rerank_candidates(
            "git",
            [" --help", " status", " checkout main", " add -A"],
            working_directory="/tmp/repo",
        )
        self.assertIn(" status", reranked[:3])
        self.assertIn(" checkout main", reranked[:3])
        self.assertIn(" add -A", reranked[:3])

    def test_help_dampening_without_help_intent(self):
        db = self._build_db_for_rerank(
            repo_accept_map={},
            command_stats={
                "git --help": {"accept_count": 1, "execute_count": 0, "history_count": 0},
                "git status": {"accept_count": 4, "execute_count": 0, "history_count": 0},
            },
        )
        reranked = db.rerank_candidates(
            "git",
            [" --help", " status"],
            working_directory="/tmp/repo",
        )
        self.assertEqual(reranked[0], " status")

    def test_repo_execute_contribution_is_bounded(self):
        command_stats = {
            "git status": {"accept_count": 0, "execute_count": 0, "history_count": 0},
            "git checkout main": {"accept_count": 0, "execute_count": 0, "history_count": 0},
        }
        db_capped = self._build_db_for_rerank(
            repo_accept_map={},
            repo_execute_map={"git status": 3, "git checkout main": 0},
            command_stats=command_stats,
        )
        db_very_high = self._build_db_for_rerank(
            repo_accept_map={},
            repo_execute_map={"git status": 100, "git checkout main": 0},
            command_stats=command_stats,
        )
        reranked_capped = db_capped.rerank_candidates(
            "git",
            [" status", " checkout main"],
            working_directory="/tmp/repo",
        )
        reranked_high = db_very_high.rerank_candidates(
            "git",
            [" status", " checkout main"],
            working_directory="/tmp/repo",
        )
        self.assertEqual(reranked_capped, reranked_high)

    def test_manual_signal_and_recency_boost_promote_recent_manual_command(self):
        db = self._build_db_for_rerank(
            command_stats={
                "agensic setup": {"accept_count": 0, "execute_count": 0, "history_count": 200},
                "agensic provenance --tui": {"accept_count": 0, "execute_count": 0, "history_count": 5},
            },
        )
        now_ts = 2_000_000_000

        def _command_run_counts(commands, since_ts=0, labels=None):
            out = {cmd: 0 for cmd in commands}
            label_set = {str(v or "").strip() for v in (labels or [])}
            if "HUMAN_TYPED" in label_set:
                out["agensic provenance --tui"] = 6
            if label_set.intersection({"AI_SUGGESTED_HUMAN_RAN", "AG_SUGGESTED_HUMAN_RAN", "AI_EXECUTED"}):
                out["agensic setup"] = 3
            return out

        db.state_store.get_command_run_counts = Mock(side_effect=_command_run_counts)
        db.state_store.get_last_command_run_ts = Mock(
            return_value={
                "agensic setup": now_ts - (14 * 24 * 3600),
                "agensic provenance --tui": now_ts - 3600,
            }
        )

        with patch("agensic.vector_db.command_db.time.time", return_value=now_ts):
            reranked = db.rerank_candidates(
                "agensic",
                [" setup", " provenance --tui"],
                working_directory="/tmp/repo",
            )
        self.assertEqual(reranked[0], " provenance --tui")

    def test_history_signal_is_capped(self):
        score_200 = CommandVectorDB.blend_rank_score(
            rank=0,
            repo_task_count=0,
            context_count=0,
            accept_count=0,
            history_count=200,
        )
        score_2000 = CommandVectorDB.blend_rank_score(
            rank=0,
            repo_task_count=0,
            context_count=0,
            accept_count=0,
            history_count=2000,
        )
        self.assertEqual(score_200, score_2000)


if __name__ == "__main__":
    unittest.main()
