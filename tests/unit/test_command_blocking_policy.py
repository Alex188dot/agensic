import unittest

from ghostshell.vector_db.command_db import CommandVectorDB


class CommandBlockingPolicyTests(unittest.TestCase):
    def _assert_blocked(self, command: str):
        self.assertTrue(
            CommandVectorDB.is_blocked_command(command),
            msg=f"expected blocked: {command}",
        )

    def _assert_allowed(self, command: str):
        self.assertFalse(
            CommandVectorDB.is_blocked_command(command),
            msg=f"expected allowed: {command}",
        )

    def test_blocks_high_risk_executables(self):
        blocked = [
            "rm -rf /tmp/demo",
            "sudo env FOO=1 dd if=/dev/zero of=/dev/disk0 bs=1m count=1",
            "/usr/sbin/mkfs.ext4 /dev/sdb1",
            "command /usr/bin/passwd root",
            "wipefs --all /dev/sda",
        ]
        for command in blocked:
            with self.subTest(command=command):
                self._assert_blocked(command)

    def test_blocks_destructive_history_clear_variants(self):
        blocked = [
            "history -c",
            "history --clear",
            "history -ac",
            "command history -c",
        ]
        for command in blocked:
            with self.subTest(command=command):
                self._assert_blocked(command)

    def test_blocks_destructive_git_variants(self):
        blocked = [
            "git reset --hard HEAD~1",
            "git -C repo reset --hard HEAD~1",
            "git clean -fdx",
            "git clean --force",
        ]
        for command in blocked:
            with self.subTest(command=command):
                self._assert_blocked(command)

    def test_allows_non_destructive_variants(self):
        allowed = [
            "echo hello",
            "history 20",
            "git reset --soft HEAD~1",
            "git clean -n",
            "docker rm my-container",
        ]
        for command in allowed:
            with self.subTest(command=command):
                self._assert_allowed(command)


if __name__ == "__main__":
    unittest.main()
