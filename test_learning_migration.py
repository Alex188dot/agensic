import unittest

from vector_db import CommandVectorDB


class TestLearningMigration(unittest.TestCase):
    def test_command_doc_id_is_deterministic(self):
        cmd = "git checkout main"
        self.assertEqual(CommandVectorDB.command_doc_id(cmd), CommandVectorDB.command_doc_id(cmd))
        self.assertNotEqual(
            CommandVectorDB.command_doc_id("git checkout main"),
            CommandVectorDB.command_doc_id("git checkout dev"),
        )

    def test_context_key_uses_two_tokens(self):
        self.assertEqual(CommandVectorDB.extract_context_key("git checkout -b foo"), "git checkout")
        self.assertEqual(CommandVectorDB.extract_context_key("git"), "git")
        self.assertEqual(CommandVectorDB.extract_context_key("   \t  "), "")

    def test_blended_score_prefers_contextual_accepts(self):
        candidates = [" status", " stash", " switch"]
        # rank-only order is status, stash, switch
        global_counts = {" status": 0, " stash": 0, " switch": 0}
        context_counts = {" status": 0, " stash": 10, " switch": 1}

        reranked = CommandVectorDB.rerank_suffixes_from_counts(
            candidates=candidates,
            global_counts=global_counts,
            context_counts=context_counts,
        )
        self.assertEqual(reranked[0], " stash")

    def test_ties_preserve_original_order(self):
        candidates = [" status", " stash", " switch"]
        global_counts = {suffix: 0 for suffix in candidates}
        context_counts = {suffix: 0 for suffix in candidates}

        reranked = CommandVectorDB.rerank_suffixes_from_counts(
            candidates=candidates,
            global_counts=global_counts,
            context_counts=context_counts,
        )
        self.assertEqual(reranked, candidates)


if __name__ == "__main__":
    unittest.main()
