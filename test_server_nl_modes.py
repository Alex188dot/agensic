import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import server


class ServerNLModesTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app)

    def test_intent_endpoint_success(self):
        mocked = {
            "status": "ok",
            "primary_command": "docker ps",
            "explanation": "Lists running containers.",
            "alternatives": ["docker container ls"],
            "copy_block": "docker ps",
        }
        with patch.object(server.engine, "get_intent_command", AsyncMock(return_value=mocked)) as fake_method:
            response = self.client.post(
                "/intent",
                json={
                    "intent_text": "show containers",
                    "working_directory": "/tmp",
                    "shell": "zsh",
                    "terminal": "xterm-256color",
                    "platform": "Darwin",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["primary_command"], "docker ps")
        self.assertTrue(fake_method.called)

    def test_assist_endpoint_success(self):
        with patch.object(
            server.engine,
            "get_general_assistant_reply",
            AsyncMock(return_value="Recursion is self-reference."),
        ) as fake_method:
            response = self.client.post(
                "/assist",
                json={
                    "prompt_text": "Explain recursion",
                    "working_directory": "/tmp",
                    "shell": "zsh",
                    "terminal": "xterm-256color",
                    "platform": "Darwin",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["answer"], "Recursion is self-reference.")
        self.assertTrue(fake_method.called)

    def test_intent_validation_error(self):
        response = self.client.post(
            "/intent",
            json={
                "working_directory": "/tmp",
                "shell": "zsh",
            },
        )
        self.assertEqual(response.status_code, 422)

    def test_assist_validation_error(self):
        response = self.client.post(
            "/assist",
            json={
                "working_directory": "/tmp",
                "shell": "zsh",
            },
        )
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
