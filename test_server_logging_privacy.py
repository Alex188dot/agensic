import unittest
from unittest.mock import AsyncMock, patch

import server
from engine import SuggestionEngine


class _FakeVectorDB:
    def record_feedback(self, buffer: str, accepted: str):
        return None


class ServerLoggingPrivacyTests(unittest.IsolatedAsyncioTestCase):
    async def test_predict_logs_sanitized_buffer(self):
        ctx = server.Context(
            command_buffer='export OPENAI_API_KEY="abc123"',
            cursor_position=0,
            working_directory="/tmp",
            shell="zsh",
            allow_ai=False,
            trigger_source="test",
        )

        with patch.object(
            server.engine,
            "get_suggestions",
            new=AsyncMock(return_value=(["", "", ""], ["" for _ in range(20)], False)),
        ):
            with self.assertLogs("ghostshell", level="INFO") as captured:
                await server.predict_completion(ctx)

        joined = "\n".join(captured.output)
        self.assertNotIn("abc123", joined)
        self.assertIn("<REDACTED_SECRET>", joined)
        self.assertIn("redactions=", joined)

    async def test_engine_feedback_logs_are_sanitized(self):
        engine = SuggestionEngine()
        with patch.object(engine, "_ensure_vector_db", return_value=_FakeVectorDB()):
            with self.assertLogs("ghostshell.engine", level="INFO") as captured:
                engine.log_feedback("export OPENAI_API_KEY=abc123", " && echo ok")

        joined = "\n".join(captured.output)
        self.assertNotIn("abc123", joined)
        self.assertIn("<REDACTED_SECRET>", joined)


if __name__ == "__main__":
    unittest.main()
