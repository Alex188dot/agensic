import asyncio
import importlib
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from agensic.engine.context import RequestContext
from agensic.engine.suggestion_engine import SuggestionEngine

cli_app = importlib.import_module("agensic.cli.app")


class ProviderRoutingTests(unittest.TestCase):
    def setUp(self):
        self.engine = SuggestionEngine.__new__(SuggestionEngine)
        self.engine.vector_db = None

    def test_default_model_includes_new_providers(self):
        self.assertEqual(cli_app._default_model_for_provider("dashscope"), "dashscope/qwen-turbo")
        self.assertEqual(cli_app._default_model_for_provider("minimax"), "minimax/MiniMax-M2.1")
        self.assertEqual(cli_app._default_model_for_provider("deepseek"), "deepseek/deepseek-chat")
        self.assertEqual(cli_app._default_model_for_provider("moonshot"), "moonshot/moonshot-v1-8k")
        self.assertEqual(cli_app._default_model_for_provider("mistral"), "mistral/mistral-small-latest")
        self.assertEqual(cli_app._default_model_for_provider("openrouter"), "openrouter/openai/gpt-4o-mini")
        self.assertEqual(cli_app._default_model_for_provider("xiaomi_mimo"), "xiaomi_mimo/mimo-v2-flash")
        self.assertEqual(cli_app._default_model_for_provider("zai"), "zai/glm-4.7")
        self.assertEqual(cli_app._default_model_for_provider("sagemaker"), "sagemaker/<your-endpoint-name>")

    def test_build_llm_kwargs_enforces_prefix_and_default_base_url(self):
        kwargs = self.engine._build_llm_kwargs(
            {"provider": "dashscope", "model": "qwen-turbo"},
            [{"role": "user", "content": "hello"}],
            temperature=0.3,
        )
        self.assertEqual(kwargs["model"], "dashscope/qwen-turbo")
        self.assertEqual(kwargs["api_base"], "https://dashscope-intl.aliyuncs.com/compatible-mode/v1")

    def test_build_llm_kwargs_preserves_prefixed_model(self):
        kwargs = self.engine._build_llm_kwargs(
            {"provider": "openrouter", "model": "openrouter/google/palm-2-chat-bison"},
            [{"role": "user", "content": "hello"}],
            temperature=0.3,
        )
        self.assertEqual(kwargs["model"], "openrouter/google/palm-2-chat-bison")
        self.assertEqual(kwargs["api_base"], "https://openrouter.ai/api/v1")

    def test_build_llm_kwargs_sagemaker_does_not_force_base_url(self):
        kwargs = self.engine._build_llm_kwargs(
            {"provider": "sagemaker", "model": "<my-endpoint>"},
            [{"role": "user", "content": "hello"}],
            temperature=0.2,
        )
        self.assertEqual(kwargs["model"], "sagemaker/<my-endpoint>")
        self.assertNotIn("api_base", kwargs)

    def test_history_only_bypasses_ai_fallback(self):
        self.engine._get_vector_candidates = lambda _ctx: []
        suggestions, pool, pool_meta, used_ai = asyncio.run(
            self.engine.get_suggestions({"provider": "history_only"}, None, allow_ai=True)
        )
        self.assertEqual(suggestions, ["", "", ""])
        self.assertEqual(len(pool), 20)
        self.assertEqual(pool_meta, [])
        self.assertFalse(used_ai)

    def test_blocked_buffer_bypasses_ai_fallback(self):
        self.engine._get_vector_candidates = lambda _ctx: []

        async def _should_not_call_llm(*_args, **_kwargs):
            raise AssertionError("LLM should not be called for blocked buffer")

        self.engine._privacy_checked_acompletion = _should_not_call_llm
        ctx = RequestContext(
            history_file="",
            cwd="/tmp",
            buffer="rm ",
            shell="bash",
        )
        suggestions, pool, pool_meta, used_ai = asyncio.run(
            self.engine.get_suggestions({"provider": "openai"}, ctx, allow_ai=True)
        )
        self.assertEqual(suggestions, ["", "", ""])
        self.assertEqual(len(pool), 20)
        self.assertEqual(pool_meta, [])
        self.assertFalse(used_ai)

    def test_failed_ai_request_is_not_reported_as_used(self):
        self.engine._get_vector_candidates = lambda _ctx: []
        self.engine._is_blocked_command = lambda _command: False
        self.engine.build_prompt_context = lambda _ctx: "context"
        self.engine._is_provider = lambda _config, _provider: False
        self.engine._build_llm_kwargs = lambda *_args, **_kwargs: {}
        self.engine.privacy_guard = Mock()
        self.engine.privacy_guard.sanitize_text.return_value = SimpleNamespace(text="git unknown")
        self.engine.privacy_guard.sanitize_for_log.side_effect = lambda value: str(value)

        async def _failed_request(*_args, **_kwargs):
            raise RuntimeError("provider unavailable")

        self.engine._privacy_checked_acompletion = _failed_request
        ctx = RequestContext(
            history_file="",
            cwd="/tmp",
            buffer="git unknown",
            shell="bash",
        )
        suggestions, pool, pool_meta, used_ai = asyncio.run(
            self.engine.get_suggestions(
                {"provider": "openai", "model": "gpt-5-mini"},
                ctx,
                allow_ai=True,
            )
        )
        self.assertEqual(suggestions, ["", "", ""])
        self.assertEqual(len(pool), 20)
        self.assertEqual(pool_meta, [])
        self.assertFalse(used_ai)

    def test_unparsable_ai_response_is_not_treated_as_a_suggestion(self):
        self.engine._get_vector_candidates = lambda _ctx: []
        self.engine._is_blocked_command = lambda _command: False
        self.engine.build_prompt_context = lambda _ctx: "context"
        self.engine._is_provider = lambda _config, _provider: False
        self.engine._build_llm_kwargs = lambda *_args, **_kwargs: {}
        self.engine.privacy_guard = Mock()
        self.engine.privacy_guard.sanitize_text.return_value = SimpleNamespace(text="git unknown")
        self.engine.privacy_guard.sanitize_for_log.side_effect = lambda value: str(value)
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="I cannot provide JSON"))]
        )

        async def _unparsable_request(*_args, **_kwargs):
            return response, {"redactions": 0, "flags": []}

        self.engine._privacy_checked_acompletion = _unparsable_request
        ctx = RequestContext("", "/tmp", "git unknown", "bash")
        suggestions, _pool, pool_meta, used_ai = asyncio.run(
            self.engine.get_suggestions(
                {"provider": "openai", "model": "gpt-5-mini"},
                ctx,
                allow_ai=True,
            )
        )
        self.assertEqual(suggestions, ["", "", ""])
        self.assertEqual(pool_meta, [])
        self.assertFalse(used_ai)


if __name__ == "__main__":
    unittest.main()
