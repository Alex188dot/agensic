import unittest

from ghostshell.server.schemas import Feedback


class FeedbackSchemaTests(unittest.TestCase):
    def test_feedback_schema_accepts_legacy_payload(self):
        payload = Feedback(
            command_buffer="git",
            accepted_suggestion=" status",
            accept_mode="suffix_append",
        )
        self.assertIsNone(payload.working_directory)

    def test_feedback_schema_accepts_working_directory(self):
        payload = Feedback(
            command_buffer="git",
            accepted_suggestion=" status",
            accept_mode="suffix_append",
            working_directory="/tmp/repo",
        )
        self.assertEqual(payload.working_directory, "/tmp/repo")


if __name__ == "__main__":
    unittest.main()
