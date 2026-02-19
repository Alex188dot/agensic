import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from engine import RequestContext, SuggestionEngine


def _fake_response(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


class EnginePrivacyTests(unittest.IsolatedAsyncioTestCase):
    async def test_outbound_messages_are_sanitized_and_history_is_capped(self):
        engine = SuggestionEngine()
        engine.bootstrap_async = lambda history_file: None
        captured: dict = {}

        with tempfile.NamedTemporaryFile("w", delete=False) as tmp:
            history_path = tmp.name
            for i in range(30):
                tmp.write(f": 17000000{i}:0;export OPENAI_API_KEY=sk-secret-{i}\n")
                tmp.write(f": 17000000{i}:0;echo line-{i}\n")

        async def fake_acompletion(**kwargs):
            captured["kwargs"] = kwargs
            return _fake_response('{"option_1":" --help","option_2":"","option_3":""}')

        try:
            with patch("engine.acompletion", side_effect=fake_acompletion):
                ctx = RequestContext(
                    history_file=history_path,
                    cwd=os.getcwd(),
                    buffer="OPENAI_API_KEY=sk-inline-123 curl https://user:pass@example.com",
                    shell="zsh",
                )
                suggestions, pool, used_ai = await engine.get_suggestions(
                    {"provider": "openai", "model": "gpt-5-mini"},
                    ctx,
                    allow_ai=True,
                )
        finally:
            os.unlink(history_path)

        self.assertTrue(used_ai)
        self.assertEqual(len(suggestions), 3)
        self.assertEqual(len(pool), 20)
        self.assertIn("kwargs", captured)

        messages = captured["kwargs"]["messages"]
        joined = "\n".join(str(m.get("content", "")) for m in messages if isinstance(m, dict))
        self.assertNotIn("sk-inline-123", joined)
        self.assertNotIn("user:pass@", joined)
        self.assertIn("<REDACTED_SECRET>", joined)
        self.assertIn("<REDACTED_CREDENTIALS>", joined)

        system_content = str(messages[0].get("content", ""))
        self.assertNotIn("sk-secret-", system_content)
        if "Recent History:\n" in system_content and "\n\nFiles in CWD:" in system_content:
            section = system_content.split("Recent History:\n", 1)[1].split("\n\nFiles in CWD:", 1)[0]
            history_lines = [line for line in section.splitlines() if line.strip() and line.strip() != "(none)"]
            self.assertLessEqual(len(history_lines), 12)
            self.assertTrue(
                all(len(line) <= engine.privacy_guard.history_line_max_chars + 3 for line in history_lines)
            )

    async def test_fail_closed_blocks_llm_call(self):
        engine = SuggestionEngine()
        with patch.object(engine.privacy_guard, "sanitize_messages", side_effect=RuntimeError("boom")):
            with patch("engine.acompletion", new_callable=AsyncMock) as mocked_call:
                ctx = RequestContext(
                    history_file="",
                    cwd=os.getcwd(),
                    buffer="git status --short",
                    shell="zsh",
                )
                suggestions, pool, used_ai = await engine.get_suggestions(
                    {"provider": "openai", "model": "gpt-5-mini"},
                    ctx,
                    allow_ai=True,
                )

        mocked_call.assert_not_awaited()
        self.assertFalse(used_ai)
        self.assertEqual(suggestions, ["", "", ""])
        self.assertEqual(len(pool), 20)


if __name__ == "__main__":
    unittest.main()
