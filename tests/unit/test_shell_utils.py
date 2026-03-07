import unittest

from agensic.utils.shell import (
    command_matches_pattern,
    extract_executable_token,
    normalize_command_pattern,
    sanitize_patterns,
)


class ShellUtilsTests(unittest.TestCase):
    def test_normalize_command_pattern(self):
        self.assertEqual(normalize_command_pattern("docker"), "docker")
        self.assertEqual(normalize_command_pattern("/usr/bin/git status"), "git")

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


if __name__ == "__main__":
    unittest.main()
