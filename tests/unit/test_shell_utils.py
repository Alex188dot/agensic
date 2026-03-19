import unittest

from agensic.utils.shell import (
    command_matches_pattern,
    current_shell_name,
    extract_executable_token,
    extract_git_subcommand,
    history_clears_state,
    is_blocked_command,
    is_git_destructive_subcommand,
    normalize_shell_name,
    normalize_command_pattern,
    sanitize_patterns,
    tokenize_command,
    token_has_short_flag,
)


class ShellUtilsTests(unittest.TestCase):
    def test_normalize_command_pattern(self):
        self.assertEqual(normalize_command_pattern("docker"), "docker")
        self.assertEqual(normalize_command_pattern("/usr/bin/git status"), "git")

    def test_normalize_shell_name(self):
        self.assertEqual(normalize_shell_name("/bin/zsh"), "zsh")
        self.assertEqual(normalize_shell_name("/usr/bin/bash"), "bash")
        self.assertEqual(normalize_shell_name("pwsh.exe"), "powershell")

    def test_current_shell_name(self):
        self.assertEqual(current_shell_name({"SHELL": "/bin/bash"}), "bash")
        self.assertEqual(current_shell_name({"COMSPEC": "powershell.exe"}), "powershell")

    def test_extract_executable_token(self):
        self.assertEqual(extract_executable_token("git status"), "git")
        self.assertEqual(extract_executable_token("sudo docker ps"), "docker")
        self.assertEqual(extract_executable_token("env FOO=1 python app.py"), "python")

    def test_sanitize_patterns(self):
        self.assertEqual(sanitize_patterns(["git", "git", " docker "]), ["git", "docker"])

    def test_command_matches_pattern(self):
        patterns = ["dock", "kubectl"]
        self.assertTrue(command_matches_pattern("docker ps", patterns))
        self.assertTrue(command_matches_pattern("kubectl get pods", patterns))
        self.assertFalse(command_matches_pattern("python app.py", patterns))

    def test_token_has_short_flag(self):
        self.assertTrue(token_has_short_flag("-ac", "c"))
        self.assertTrue(token_has_short_flag("-f", "f"))
        self.assertFalse(token_has_short_flag("--force", "f"))

    def test_tokenize_command(self):
        self.assertEqual(tokenize_command("git status"), ["git", "status"])
        self.assertEqual(tokenize_command(""), [])

    def test_history_clears_state(self):
        self.assertTrue(history_clears_state(["-ac"]))
        self.assertTrue(history_clears_state(["--clear"]))
        self.assertFalse(history_clears_state(["20"]))

    def test_extract_git_subcommand(self):
        self.assertEqual(
            extract_git_subcommand(["-C", "repo", "reset", "--hard", "HEAD~1"]),
            ("reset", ["--hard", "head~1"]),
        )

    def test_is_git_destructive_subcommand(self):
        self.assertTrue(is_git_destructive_subcommand(["reset", "--hard", "HEAD~1"]))
        self.assertTrue(is_git_destructive_subcommand(["clean", "-fdx"]))
        self.assertFalse(is_git_destructive_subcommand(["clean", "-n"]))

    def test_is_blocked_command(self):
        self.assertTrue(is_blocked_command("rm -rf /tmp/demo"))
        self.assertTrue(is_blocked_command("history -c"))
        self.assertTrue(is_blocked_command("git clean -fdx"))
        self.assertFalse(is_blocked_command("git reset --soft HEAD~1"))
        self.assertFalse(is_blocked_command("echo hello"))


if __name__ == "__main__":
    unittest.main()
