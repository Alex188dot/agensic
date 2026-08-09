import unittest

from agensic.server.prediction import _cursor_context, _replace_at_cursor, prediction_lines
from agensic.server.schemas import Context


class PredictionProtocolTests(unittest.TestCase):
    def test_cursor_context_preserves_line_suffix(self):
        ctx = Context(
            command_buffer="git st --short",
            cursor_position=6,
            working_directory="/tmp",
            shell="zsh",
        )
        self.assertEqual(_cursor_context(ctx), ("git st", " --short"))

    def test_mid_line_completion_becomes_full_replacement(self):
        adjusted = _replace_at_cursor(
            "git st",
            " --short",
            [
                {
                    "accept_text": "atus",
                    "display_text": "atus",
                    "accept_mode": "suffix_append",
                    "kind": "normal",
                }
            ],
        )
        self.assertEqual(adjusted[0]["accept_text"], "git status --short")
        self.assertEqual(adjusted[0]["accept_mode"], "replace_full")

    def test_line_protocol_echoes_request_id(self):
        rendered = prediction_lines(
            {
                "request_id": "shell-7",
                "used_ai": False,
                "pool_meta": [
                    {
                        "accept_text": " status",
                        "display_text": " status",
                        "accept_mode": "suffix_append",
                        "kind": "normal",
                    }
                ],
            }
        )
        self.assertTrue(rendered.startswith("agensic_predict_v2\n"))
        self.assertIn("request_id=shell-7\n", rendered)
        self.assertIn("pool= status\n", rendered)


if __name__ == "__main__":
    unittest.main()
