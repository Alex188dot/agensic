import unittest
from unittest.mock import MagicMock, patch

from engine import RequestContext, SuggestionEngine
from vector_db import CommandVectorDB


class _DummyLock:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeCollection:
    def __init__(self):
        self.fetch_calls = []
        self.inserted = []
        self.updated = []

    def fetch(self, ids):
        self.fetch_calls.append(list(ids))
        return {}

    def insert(self, docs):
        if isinstance(docs, list):
            self.inserted.extend(docs)
        else:
            self.inserted.append(docs)

    def update(self, doc):
        self.updated.append(doc)


class _FakeModel:
    def encode(self, values, show_progress_bar=False):
        if isinstance(values, str):
            values = [values]
        return [[0.0] * 384 for _ in values]


class _FakeVectorDB:
    def __init__(self):
        self.insert_command = MagicMock()

    @staticmethod
    def is_blocked_command(command: str) -> bool:
        return CommandVectorDB.is_blocked_command(command)


class CommandGuardrailTests(unittest.TestCase):
    def _new_db(self):
        db = CommandVectorDB.__new__(CommandVectorDB)
        db._is_closed = False
        db._io_lock = _DummyLock()
        db.collection = _FakeCollection()
        db.feedback_collection = _FakeCollection()
        db.model = _FakeModel()
        db.inserted_commands = set()
        db.dimensions = 384
        db.SCORE_ALPHA = CommandVectorDB.SCORE_ALPHA
        db.SCORE_BETA = CommandVectorDB.SCORE_BETA
        db.SCORE_EXECUTE = CommandVectorDB.SCORE_EXECUTE
        db.SCORE_HISTORY = CommandVectorDB.SCORE_HISTORY
        return db

    def test_is_blocked_command(self):
        self.assertTrue(CommandVectorDB.is_blocked_command("rm -rf tmp"))
        self.assertTrue(CommandVectorDB.is_blocked_command("/bin/rm file.txt"))
        self.assertTrue(CommandVectorDB.is_blocked_command("sudo rm file.txt"))
        self.assertTrue(CommandVectorDB.is_blocked_command("command rm file.txt"))

        self.assertFalse(CommandVectorDB.is_blocked_command("rmdir tmp"))
        self.assertFalse(CommandVectorDB.is_blocked_command("grep rm notes.txt"))
        self.assertFalse(CommandVectorDB.is_blocked_command("echo rm"))

    def test_insert_command_skips_blocked(self):
        db = self._new_db()
        db._increment_execute_count = MagicMock()

        db.insert_command("rm -rf /tmp/x")
        db.insert_command("echo hi")

        db._increment_execute_count.assert_called_once_with("echo hi")

    def test_upsert_history_commands_excludes_blocked(self):
        db = self._new_db()

        inserted = db.upsert_history_commands({"rm -rf /tmp/x": 2, "echo hi": 1})

        self.assertEqual(inserted, 1)
        self.assertIn("echo hi", db.inserted_commands)
        self.assertNotIn("rm -rf /tmp/x", db.inserted_commands)
        self.assertEqual(len(db.collection.fetch_calls), 1)
        self.assertEqual(len(db.collection.fetch_calls[0]), 1)

    def test_record_feedback_skips_blocked(self):
        db = self._new_db()
        db._increment_command_feedback = MagicMock()
        db._increment_context_feedback = MagicMock()

        db.record_feedback("rm", " -rf /tmp/x")
        db._increment_command_feedback.assert_not_called()

        db.record_feedback("echo", " hi")
        db._increment_command_feedback.assert_called_once()

    def test_get_exact_prefix_matches_filters_blocked(self):
        db = self._new_db()
        db.search = MagicMock(
            return_value=[
                ("rm -rf /tmp/x", 0.9),
                ("rmdir mydir", 0.8),
                ("run-job", 0.7),
            ]
        )

        matches = db.get_exact_prefix_matches("r", topk=20)

        self.assertNotIn("rm -rf /tmp/x", matches)
        self.assertIn("rmdir mydir", matches)
        self.assertIn("run-job", matches)


class EngineGuardrailTests(unittest.TestCase):
    def test_runtime_exit_code_gate(self):
        engine = SuggestionEngine.__new__(SuggestionEngine)
        fake_db = _FakeVectorDB()
        engine.vector_db = fake_db
        engine._ensure_vector_db = lambda: fake_db

        engine.log_executed_command("echo hi", exit_code=1, source="runtime")
        engine.log_executed_command("echo hi", exit_code=0, source="runtime")
        engine.log_executed_command("sudo rm -rf /tmp/x", exit_code=0, source="runtime")

        fake_db.insert_command.assert_called_once_with("echo hi")


class EngineAISafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_ai_suggestions_filter_blocked(self):
        engine = SuggestionEngine.__new__(SuggestionEngine)
        engine.vector_db = None
        engine._get_vector_candidates = lambda ctx: []
        engine.build_prompt_context = lambda ctx: ""

        class _Msg:
            content = '{"option_1":"rm -rf /tmp/x","option_2":"cho hello","option_3":"sudo rm foo"}'

        class _Choice:
            message = _Msg()

        class _Resp:
            choices = [_Choice()]

        with patch("engine.acompletion", return_value=_Resp()):
            suggestions, pool, used_ai = await engine.get_suggestions(
                {"provider": "openai", "model": "gpt-5-mini"},
                RequestContext(history_file="", cwd="/tmp", buffer="e", shell="zsh"),
            )

        self.assertEqual(suggestions[0], "cho hello")
        self.assertEqual(suggestions[1], "")
        self.assertEqual(suggestions[2], "")
        self.assertEqual(len(pool), 20)
        self.assertTrue(used_ai)

    async def test_no_vector_match_with_allow_ai_false_skips_llm(self):
        engine = SuggestionEngine.__new__(SuggestionEngine)
        engine.vector_db = None
        engine._get_vector_candidates = lambda ctx: []

        with patch("engine.acompletion") as mocked_completion:
            suggestions, pool, used_ai = await engine.get_suggestions(
                {"provider": "openai", "model": "gpt-5-mini"},
                RequestContext(history_file="", cwd="/tmp", buffer="ros2 ", shell="zsh"),
                allow_ai=False,
            )

        mocked_completion.assert_not_called()
        self.assertEqual(suggestions, ["", "", ""])
        self.assertEqual(len(pool), 20)
        self.assertTrue(all(item == "" for item in pool))
        self.assertFalse(used_ai)

    async def test_vector_match_ignores_allow_ai_and_skips_llm(self):
        engine = SuggestionEngine.__new__(SuggestionEngine)
        engine.vector_db = None
        engine._get_vector_candidates = lambda ctx: [" status", " stash"]
        engine._filter_blocked_candidates = lambda buffer, candidates: candidates
        engine.build_prompt_context = lambda ctx: ""

        with patch("engine.acompletion") as mocked_completion:
            suggestions, pool, used_ai = await engine.get_suggestions(
                {"provider": "openai", "model": "gpt-5-mini"},
                RequestContext(history_file="", cwd="/tmp", buffer="git", shell="zsh"),
                allow_ai=False,
            )

        mocked_completion.assert_not_called()
        self.assertEqual(suggestions[0], " status")
        self.assertEqual(suggestions[1], " stash")
        self.assertEqual(suggestions[2], "")
        self.assertEqual(len(pool), 20)
        self.assertFalse(used_ai)


class EngineIntentModeTests(unittest.IsolatedAsyncioTestCase):
    async def test_intent_prompt_includes_environment_context(self):
        engine = SuggestionEngine()
        captured = {}

        class _Msg:
            content = '{"status":"ok","primary_command":"docker ps","explanation":"Lists containers.","alternatives":["docker container ls"]}'

        class _Choice:
            message = _Msg()

        class _Resp:
            choices = [_Choice()]

        async def _fake_completion(**kwargs):
            captured["kwargs"] = kwargs
            return _Resp()

        with patch("engine.acompletion", side_effect=_fake_completion):
            result = await engine.get_intent_command(
                {"provider": "openai", "model": "gpt-5-mini"},
                RequestContext(
                    history_file="",
                    cwd="/tmp/work",
                    buffer="",
                    shell="zsh",
                    terminal="xterm-256color",
                    platform_name="Darwin",
                ),
                "show running docker containers",
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["primary_command"], "docker ps")
        user_message = captured["kwargs"]["messages"][1]["content"]
        self.assertIn("terminal: xterm-256color", user_message)
        self.assertIn("shell: zsh", user_message)
        self.assertIn("cwd: /tmp/work", user_message)

    async def test_intent_blocks_destructive_command(self):
        engine = SuggestionEngine()

        class _Msg:
            content = '{"status":"ok","primary_command":"rm -rf /tmp/x","explanation":"Deletes files.","alternatives":[]}'

        class _Choice:
            message = _Msg()

        class _Resp:
            choices = [_Choice()]

        with patch("engine.acompletion", return_value=_Resp()):
            result = await engine.get_intent_command(
                {"provider": "openai", "model": "gpt-5-mini"},
                RequestContext(history_file="", cwd="/tmp", buffer="", shell="zsh"),
                "delete temporary files",
            )

        self.assertEqual(result["status"], "refusal")
        self.assertEqual(result["primary_command"], "")

    async def test_general_assistant_uses_exact_system_prompt(self):
        engine = SuggestionEngine()
        captured = {}

        class _Msg:
            content = "Recursion is when a function calls itself."

        class _Choice:
            message = _Msg()

        class _Resp:
            choices = [_Choice()]

        async def _fake_completion(**kwargs):
            captured["kwargs"] = kwargs
            return _Resp()

        with patch("engine.acompletion", side_effect=_fake_completion):
            answer = await engine.get_general_assistant_reply(
                {"provider": "openai", "model": "gpt-5-mini"},
                RequestContext(history_file="", cwd="/tmp", buffer="", shell="zsh"),
                "Explain recursion simply.",
            )

        self.assertIn("Recursion", answer)
        self.assertEqual(captured["kwargs"]["messages"][0]["content"], "You are a helpful assistant.")


if __name__ == "__main__":
    unittest.main()
