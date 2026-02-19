import unittest
from collections import defaultdict

from vector_db import CommandVectorDB


class PrefixCacheTests(unittest.TestCase):
    def _new_db(self) -> CommandVectorDB:
        db = CommandVectorDB.__new__(CommandVectorDB)
        db.command_cache = set()
        db.command_cache_by_exec = defaultdict(set)
        db._history_cache_warmed_for = set()
        db.PREFIX_SCAN_LIMIT = 2000
        return db

    def test_register_commands_populates_lexical_cache(self):
        db = self._new_db()
        db._register_commands(["git status", "git add .", "docker run nginx", "rm -rf tmp"])

        self.assertIn("git status", db.command_cache)
        self.assertIn("git add .", db.command_cache)
        self.assertIn("docker run nginx", db.command_cache)
        # blocked command should be skipped
        self.assertNotIn("rm -rf tmp", db.command_cache)
        self.assertIn("git status", db.command_cache_by_exec["git"])
        self.assertIn("docker run nginx", db.command_cache_by_exec["docker"])

    def test_get_exact_prefix_matches_prefers_lexical_and_ignores_semantic_noise(self):
        db = self._new_db()
        db._register_commands(["git status", "git add .", "docker ps"])

        # semantic results should not be needed for a common lexical prefix
        db.search = lambda query, topk=20: [("grep foo", 0.9), ("go test", 0.8)]

        matches = db.get_exact_prefix_matches("git", topk=10)

        self.assertEqual(matches, ["git add .", "git status"])

    def test_get_exact_prefix_matches_uses_semantic_fallback_when_cache_sparse(self):
        db = self._new_db()
        db._register_commands(["docker ps"])
        db.search = lambda query, topk=20: [("kubectl get pods", 0.9), ("kubectl describe pod x", 0.7)]

        matches = db.get_exact_prefix_matches("kubectl", topk=10)

        self.assertEqual(matches, ["kubectl get pods", "kubectl describe pod x"])


if __name__ == "__main__":
    unittest.main()
