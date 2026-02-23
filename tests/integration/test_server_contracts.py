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
    @classmethod
    def setUpClass(cls):
        if TestClient is None:
            raise unittest.SkipTest(f"Server dependencies unavailable: {SERVER_IMPORT_ERROR}")
        cls.client = TestClient(app)

    def test_status_contract(self):
        with patch.object(deps.engine, "get_bootstrap_status", return_value={"ready": True, "phase": "ready"}):
            response = self.client.get("/status")
            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertEqual(body["status"], "ok")
            self.assertIn("bootstrap", body)

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


if __name__ == "__main__":
    unittest.main()
