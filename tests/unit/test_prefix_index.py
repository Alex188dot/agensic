import unittest

from agensic.vector_db.prefix_index import CommandPrefixIndex


class CommandPrefixIndexTests(unittest.TestCase):
    def test_finds_match_beyond_legacy_scan_limit(self):
        index = CommandPrefixIndex()
        for number in range(2500):
            index.add(f"alpha-{number:04d}")
        index.add("zulu-target --fast")
        self.assertEqual(index.search("zulu", limit=5), ["zulu-target --fast"])

    def test_discard_and_clear(self):
        index = CommandPrefixIndex()
        index.add("git status")
        index.add("git stash")
        self.assertTrue(index.discard("git status"))
        self.assertEqual(index.search("git st", limit=5), ["git stash"])
        index.clear()
        self.assertEqual(index.search("git", limit=5), [])


if __name__ == "__main__":
    unittest.main()
