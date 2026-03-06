import unittest
from unittest.mock import patch

try:
    from fastapi.testclient import TestClient
    from ghostshell.server.app import app
    from ghostshell.server import deps
    SERVER_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - environment dependent
    TestClient = None
    app = None
    deps = None
    SERVER_IMPORT_ERROR = exc


class _FakeVectorDB:
    def normalize_command(self, value: str) -> str:
        return str(value or "").strip()

    def list_command_store(self, history_file: str = "", include_all: bool = False):
        return {
            "commands": [{"command": "git status", "usage_score": 3}],
            "potential_wrong": [],
        }

    def add_manual_commands(self, commands):
        return {
            "inserted": len(commands),
            "already_present": 0,
            "unblocked_removed": 0,
        }

    def remove_commands_exact(self, commands):
        return {
            "vector_removed": len(commands),
            "guarded": 0,
        }

    def align_history_index_state_to_end(self, history_file: str) -> bool:
        return True


class ServerContractTests(unittest.TestCase):
    AUTH_TOKEN = "test-auth-token"

    @classmethod
    def setUpClass(cls):
        if TestClient is None:
            raise unittest.SkipTest(f"Server dependencies unavailable: {SERVER_IMPORT_ERROR}")
        with patch.object(deps, "rotate_local_auth_token", return_value=cls.AUTH_TOKEN):
            cls.client = TestClient(app)

    def setUp(self):
        deps.reset_shutdown_state()
        deps.set_uvicorn_server(None)
        self._auth_patcher = patch.object(deps, "get_local_auth_token", return_value=self.AUTH_TOKEN)
        self._auth_patcher.start()
        self.client.headers.update({"Authorization": f"Bearer {self.AUTH_TOKEN}"})

    def tearDown(self):
        self._auth_patcher.stop()

    def _request_without_auth(self, method: str, path: str, **kwargs):
        original = self.client.headers.pop("Authorization", None)
        try:
            return self.client.request(method, path, **kwargs)
        finally:
            if original is not None:
                self.client.headers["Authorization"] = original

    def test_status_contract(self):
        with patch.object(deps.engine, "get_bootstrap_status", return_value={"ready": True, "phase": "ready"}):
            response = self.client.get("/status")
            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertEqual(body["status"], "ok")
            self.assertIn("bootstrap", body)
            self.assertIn("shutdown", body)

    def test_local_auth_required_for_status(self):
        with patch.object(deps.logger, "warning") as warning_mock:
            response = self._request_without_auth("GET", "/status")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json().get("detail"), "unauthorized")
        self.assertTrue(any(len(args) >= 5 and args[4] == "auth_missing" for args, _ in warning_mock.call_args_list))

    def test_local_auth_invalid_token_logs_reason(self):
        with patch.object(deps.logger, "warning") as warning_mock:
            response = self._request_without_auth(
                "GET",
                "/status",
                headers={"Authorization": "Bearer wrong-token"},
            )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json().get("detail"), "unauthorized")
        self.assertTrue(any(len(args) >= 5 and args[4] == "auth_invalid" for args, _ in warning_mock.call_args_list))

    def test_local_auth_accepts_custom_header(self):
        response = self._request_without_auth(
            "GET",
            "/status",
            headers={"X-GhostShell-Auth": self.AUTH_TOKEN},
        )
        self.assertEqual(response.status_code, 200)

    def test_local_auth_required_for_command_store_list(self):
        response = self._request_without_auth("GET", "/command_store/list")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json().get("detail"), "unauthorized")

    def test_predict_contract(self):
        async def _fake_get_suggestions(config, req_context, allow_ai=True):
            return (
                [" status", " stash", ""],
                [" status", " stash", ""] + [""] * 17,
                [{"display_text": " status", "accept_text": " status", "accept_mode": "suffix_append", "kind": "normal"}],
                False,
            )

        with patch.object(deps.engine, "get_suggestions", side_effect=_fake_get_suggestions), patch.object(
            deps.engine,
            "get_bootstrap_status",
            return_value={"ready": True, "phase": "ready", "indexed_commands": 10},
        ):
            response = self.client.post(
                "/predict",
                json={
                    "command_buffer": "git",
                    "cursor_position": 3,
                    "working_directory": "/tmp",
                    "shell": "zsh",
                    "allow_ai": False,
                },
            )
            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertIn("suggestions", body)
            self.assertIn("pool", body)
            self.assertIn("pool_meta", body)
            self.assertIn("bootstrap", body)
            self.assertIn("ai_agent", body)
            self.assertIn("ai_provider", body)
            self.assertIn("ai_model", body)

    def test_predict_history_only_forces_no_ai(self):
        observed_allow_ai: list[bool] = []

        async def _fake_get_suggestions(config, req_context, allow_ai=True):
            observed_allow_ai.append(bool(allow_ai))
            return (
                [" status", "", ""],
                [" status"] + [""] * 19,
                [{"display_text": " status", "accept_text": " status", "accept_mode": "suffix_append", "kind": "normal"}],
                False,
            )

        with patch.object(deps, "load_config", return_value={"provider": "history_only"}), patch.object(
            deps,
            "check_and_track_llm_rate_limit",
        ) as rate_limit, patch.object(
            deps.engine,
            "get_suggestions",
            side_effect=_fake_get_suggestions,
        ), patch.object(
            deps.engine,
            "get_bootstrap_status",
            return_value={"ready": True, "phase": "ready"},
        ):
            response = self.client.post(
                "/predict",
                json={
                    "command_buffer": "git",
                    "cursor_position": 3,
                    "working_directory": "/tmp",
                    "shell": "zsh",
                    "allow_ai": True,
                },
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(observed_allow_ai, [False])
            self.assertFalse(rate_limit.called)

    def test_intent_and_assist_contracts(self):
        async def _fake_intent(config, req_context, text):
            return {
                "status": "ok",
                "primary_command": "ls -la",
                "explanation": "List files",
                "alternatives": [],
                "copy_block": "ls -la",
            }

        async def _fake_assist(config, req_context, text):
            return "hello"

        with patch.object(deps.engine, "get_intent_command", side_effect=_fake_intent), patch.object(
            deps.engine,
            "get_general_assistant_reply",
            side_effect=_fake_assist,
        ):
            intent_response = self.client.post(
                "/intent",
                json={
                    "intent_text": "list files",
                    "working_directory": "/tmp",
                    "shell": "zsh",
                },
            )
            self.assertEqual(intent_response.status_code, 200)
            self.assertIn("status", intent_response.json())

            assist_response = self.client.post(
                "/assist",
                json={
                    "prompt_text": "hi",
                    "working_directory": "/tmp",
                    "shell": "zsh",
                },
            )
            self.assertEqual(assist_response.status_code, 200)
            self.assertIn("answer", assist_response.json())

    def test_intent_history_only_returns_refusal_without_llm(self):
        with patch.object(deps, "load_config", return_value={"provider": "history_only"}), patch.object(
            deps,
            "check_and_track_llm_rate_limit",
        ) as rate_limit, patch.object(
            deps.engine,
            "get_intent_command",
        ) as intent_llm:
            response = self.client.post(
                "/intent",
                json={
                    "intent_text": "list files",
                    "working_directory": "/tmp",
                    "shell": "zsh",
                },
            )
            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertEqual(body.get("status"), "refusal")
            self.assertIn("AI is disabled", body.get("explanation", ""))
            self.assertFalse(rate_limit.called)
            self.assertFalse(intent_llm.called)

    def test_assist_history_only_returns_message_without_llm(self):
        with patch.object(deps, "load_config", return_value={"provider": "history_only"}), patch.object(
            deps,
            "check_and_track_llm_rate_limit",
        ) as rate_limit, patch.object(
            deps.engine,
            "get_general_assistant_reply",
        ) as assist_llm:
            response = self.client.post(
                "/assist",
                json={
                    "prompt_text": "hi",
                    "working_directory": "/tmp",
                    "shell": "zsh",
                },
            )
            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertIn("AI is disabled", body.get("answer", ""))
            self.assertFalse(rate_limit.called)
            self.assertFalse(assist_llm.called)

    def test_assist_rate_limit_response(self):
        with patch.object(deps, "check_and_track_llm_rate_limit", return_value=(False, 120, 120)):
            response = self.client.post(
                "/assist",
                json={
                    "prompt_text": "hi",
                    "working_directory": "/tmp",
                    "shell": "zsh",
                },
            )
            self.assertEqual(response.status_code, 429)

    def test_assist_prompt_length_validation(self):
        response = self.client.post(
            "/assist",
            json={
                "prompt_text": "x" * 5001,
                "working_directory": "/tmp",
                "shell": "zsh",
            },
        )
        self.assertEqual(response.status_code, 422)

    def test_command_store_contracts(self):
        fake_db = _FakeVectorDB()

        with patch.object(deps.engine, "_ensure_vector_db", return_value=fake_db):
            list_response = self.client.get("/command_store/list")
            self.assertEqual(list_response.status_code, 200)
            self.assertEqual(list_response.json()["status"], "ok")

            add_response = self.client.post("/command_store/add", json={"commands": ["git status"]})
            self.assertEqual(add_response.status_code, 200)
            self.assertEqual(add_response.json()["status"], "ok")

            remove_response = self.client.post(
                "/command_store/remove",
                json={"commands": ["git status"], "shell": "zsh"},
            )
            self.assertEqual(remove_response.status_code, 200)
            self.assertEqual(remove_response.json()["status"], "ok")

    def test_log_command_accepts_working_directory(self):
        with patch.object(deps.engine, "log_executed_command") as log_exec:
            response = self.client.post(
                "/log_command",
                json={
                    "command": "git status",
                    "source": "runtime",
                    "exit_code": 0,
                    "working_directory": "/tmp/repo-x",
                    "captured_stderr_tail": "fatal: bad revision\n",
                    "captured_output_truncated": True,
                    "provenance_agent_name": "Planner A",
                },
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json().get("status"), "ok")
            self.assertTrue(log_exec.called)
            args = log_exec.call_args[0]
            self.assertEqual(args[0], "git status")
            self.assertEqual(args[1], 0)
            self.assertIsNone(args[2])
            self.assertEqual(args[3], "runtime")
            self.assertEqual(args[4], "/tmp/repo-x")
            self.assertIsInstance(args[5], dict)
            self.assertIn("provenance_last_action", args[5])
            self.assertEqual(args[5].get("captured_stderr_tail"), "fatal: bad revision\n")
            self.assertTrue(args[5].get("captured_output_truncated"))
            self.assertEqual(args[5].get("provenance_agent_name"), "Planner A")

    def test_provenance_runs_contract(self):
        sample = [
            {
                "run_id": "run-1",
                "ts": 1700000000,
                "command": "git status",
                "label": "HUMAN_TYPED",
                "confidence": 0.9,
                "agent": "codex",
                "agent_name": "PlannerA",
                "provider": "openai",
                "model": "gpt-5.3",
                "raw_model": "gpt-5.3",
                "normalized_model": "gpt-5-codex",
                "model_fingerprint": "codex_gpt-5-codex",
                "evidence_tier": "integrated",
                "agent_source": "payload_ai",
                "registry_version": "builtin-2026-02-28",
                "registry_status": "verified",
                "source": "runtime",
                "working_directory": "/tmp",
                "exit_code": 0,
                "duration_ms": 88,
                "shell_pid": 123,
                "evidence": ["last_action=human_typed"],
                "payload": {"provenance_last_action": "human_typed"},
            }
        ]
        with patch.object(deps.engine, "list_command_runs", return_value=sample) as mocked:
            response = self.client.get(
                "/provenance/runs?limit=20&label=HUMAN_TYPED&tier=integrated&agent=codex&agent_name=PlannerA&provider=openai&before_ts=1700000500&before_run_id=run-zzz"
            )
            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertEqual(body.get("status"), "ok")
            self.assertEqual(body.get("total"), 1)
            self.assertIsInstance(body.get("runs"), list)
            kwargs = mocked.call_args.kwargs
            self.assertEqual(kwargs.get("tier"), "integrated")
            self.assertEqual(kwargs.get("agent"), "codex")
            self.assertEqual(kwargs.get("agent_name"), "PlannerA")
            self.assertEqual(kwargs.get("provider"), "openai")
            self.assertEqual(kwargs.get("before_ts"), 1700000500)
            self.assertEqual(kwargs.get("before_run_id"), "run-zzz")

    def test_provenance_runs_semantic_contract(self):
        sample = [
            {
                "run_id": "run-sem-1",
                "ts": 1700000010,
                "command": "git commit -m test",
                "label": "AI_EXECUTED",
                "confidence": 0.99,
                "agent": "codex",
                "agent_name": "PlannerA",
                "provider": "openai",
                "model": "gpt-5.3",
                "raw_model": "gpt-5.3",
                "normalized_model": "gpt-5-codex",
                "model_fingerprint": "codex_gpt-5-codex",
                "evidence_tier": "integrated",
                "agent_source": "payload_ai",
                "registry_version": "builtin-2026-02-28",
                "registry_status": "verified",
                "source": "runtime",
                "working_directory": "/tmp",
                "exit_code": 0,
                "duration_ms": 12,
                "shell_pid": 321,
                "evidence": [],
                "payload": {},
            }
        ]
        with patch.object(deps.engine, "semantic_command_runs", return_value=sample) as mocked:
            response = self.client.get(
                "/provenance/runs/semantic?query=commit&limit=20&tier=integrated&agent=codex&agent_name=PlannerA&provider=openai"
            )
            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertEqual(body.get("status"), "ok")
            self.assertEqual(body.get("total"), 1)
            kwargs = mocked.call_args.kwargs
            self.assertEqual(kwargs.get("query"), "commit")
            self.assertEqual(kwargs.get("tier"), "integrated")
            self.assertEqual(kwargs.get("agent"), "codex")

    def test_provenance_registry_contracts(self):
        with patch.object(
            deps.engine,
            "get_provenance_registry_summary",
            return_value={"version": "builtin-2026-02-28", "source": "builtin", "agent_count": 9},
        ), patch.object(
            deps.engine,
            "list_provenance_registry_agents",
            return_value=[{"agent_id": "codex", "status": "verified", "executables": ["codex"], "aliases": ["codex"]}],
        ), patch.object(
            deps.engine,
            "get_provenance_registry_agent",
            return_value={"agent_id": "codex", "status": "verified"},
        ), patch.object(
            deps.engine,
            "refresh_provenance_registry",
            return_value={"ok": True, "reason": "updated", "updated": True, "version": "v1"},
        ), patch.object(
            deps.engine,
            "verify_provenance_registry_cache",
            return_value={"ok": True, "reason": "signature_valid", "version": "v1", "verified_at": 1700000000, "url": "https://example.test"},
        ):
            summary = self.client.get("/provenance/registry")
            self.assertEqual(summary.status_code, 200)
            self.assertEqual(summary.json().get("status"), "ok")

            agents = self.client.get("/provenance/registry/agents?status=verified")
            self.assertEqual(agents.status_code, 200)
            self.assertEqual(agents.json().get("total"), 1)

            show_agent = self.client.get("/provenance/registry/agents/codex")
            self.assertEqual(show_agent.status_code, 200)
            self.assertEqual(show_agent.json().get("summary", {}).get("agent_id"), "codex")

            refresh = self.client.post("/provenance/registry/refresh?force=true")
            self.assertEqual(refresh.status_code, 200)
            self.assertTrue(bool(refresh.json().get("ok")))

            verify = self.client.get("/provenance/registry/verify")
            self.assertEqual(verify.status_code, 200)
            self.assertTrue(bool(verify.json().get("ok")))

    def test_shutdown_gating_routes(self):
        deps.begin_shutdown("test")

        predict = self.client.post(
            "/predict",
            json={
                "command_buffer": "git",
                "cursor_position": 3,
                "working_directory": "/tmp",
                "shell": "zsh",
                "allow_ai": False,
            },
        )
        self.assertEqual(predict.status_code, 503)
        self.assertEqual(predict.json().get("detail"), "daemon_shutting_down")

        assist = self.client.post(
            "/assist",
            json={
                "prompt_text": "hi",
                "working_directory": "/tmp",
                "shell": "zsh",
            },
        )
        self.assertEqual(assist.status_code, 503)
        self.assertEqual(assist.json().get("detail"), "daemon_shutting_down")

        intent = self.client.post(
            "/intent",
            json={
                "intent_text": "list files",
                "working_directory": "/tmp",
                "shell": "zsh",
            },
        )
        self.assertEqual(intent.status_code, 503)
        self.assertEqual(intent.json().get("detail"), "daemon_shutting_down")

        feedback = self.client.post(
            "/feedback",
            json={
                "command_buffer": "git",
                "accepted_suggestion": " status",
                "accept_mode": "suffix_append",
                "working_directory": "/tmp",
            },
        )
        self.assertEqual(feedback.status_code, 503)
        self.assertEqual(feedback.json().get("detail"), "daemon_shutting_down")

        log_command = self.client.post(
            "/log_command",
            json={
                "command": "git status",
                "source": "runtime",
                "exit_code": 0,
            },
        )
        self.assertEqual(log_command.status_code, 503)
        self.assertEqual(log_command.json().get("detail"), "daemon_shutting_down")

        list_response = self.client.get("/command_store/list")
        self.assertEqual(list_response.status_code, 503)
        self.assertEqual(list_response.json().get("detail"), "daemon_shutting_down")

        add_response = self.client.post("/command_store/add", json={"commands": ["git status"]})
        self.assertEqual(add_response.status_code, 503)
        self.assertEqual(add_response.json().get("detail"), "daemon_shutting_down")

        remove_response = self.client.post(
            "/command_store/remove",
            json={"commands": ["git status"], "shell": "zsh"},
        )
        self.assertEqual(remove_response.status_code, 503)
        self.assertEqual(remove_response.json().get("detail"), "daemon_shutting_down")

        status_response = self.client.get("/status")
        self.assertEqual(status_response.status_code, 200)
        self.assertTrue(bool(status_response.json().get("shutdown", {}).get("shutting_down")))

        shutdown_response = self.client.post("/shutdown")
        self.assertEqual(shutdown_response.status_code, 200)

    def test_shutdown_route_sets_should_exit(self):
        class _FakeServer:
            def __init__(self) -> None:
                self.should_exit = False

        server = _FakeServer()
        deps.set_uvicorn_server(server)
        response = self.client.post("/shutdown")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(server.should_exit)
        self.assertTrue(deps.shutdown_snapshot().get("shutting_down"))


if __name__ == "__main__":
    unittest.main()
