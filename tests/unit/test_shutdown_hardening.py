import threading
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from agensic.engine.suggestion_engine import SuggestionEngine
from agensic.server import deps
from agensic.server.app import app as fastapi_app


class ShutdownLifespanTests(unittest.TestCase):
    def setUp(self):
        deps.reset_shutdown_state()

    def test_lifespan_logs_forced_shutdown_when_drain_times_out(self):
        snapshots = [
            {
                "active_jobs_total": 2,
                "active_requests": 1,
                "active_background_jobs": 1,
                "reason": "lifespan",
            },
            {
                "active_jobs_total": 2,
                "active_requests": 1,
                "active_background_jobs": 1,
                "reason": "lifespan",
            },
        ]

        with patch.object(deps, "get_history_file", return_value=""), patch.object(
            deps, "rotate_local_auth_token", return_value="test-auth-token"
        ), patch.object(
            deps, "wait_for_active_jobs_to_drain", return_value=False
        ), patch.object(deps, "shutdown_snapshot", side_effect=snapshots), patch.object(
            deps.logger, "warning"
        ) as warning_mock, patch.object(
            deps.engine, "close"
        ) as close_mock:
            with TestClient(fastapi_app):
                pass

        warning_mock.assert_any_call(
            "forced shutdown with active_jobs=%d active_requests=%d active_background_jobs=%d",
            2,
            1,
            1,
        )
        close_mock.assert_called_once_with(join_timeout_seconds=20.0, shutdown_reason="lifespan")


class SuggestionEngineCloseTests(unittest.TestCase):
    def test_close_uses_timeout_and_logs_when_bootstrap_thread_stuck(self):
        class _StuckThread:
            def __init__(self):
                self.join_timeout = None

            def join(self, timeout=None):
                self.join_timeout = timeout

            def is_alive(self):
                return True

        engine = SuggestionEngine.__new__(SuggestionEngine)
        engine._bootstrap_lock = threading.Lock()
        thread = _StuckThread()
        engine._bootstrap_thread = thread
        engine.vector_db = None
        engine._vector_db_ready = threading.Event()
        engine.snapshot_scheduler = None

        with patch("agensic.engine.suggestion_engine.logger.warning") as warning_mock:
            engine.close(join_timeout_seconds=0.7, shutdown_reason="sigterm")

        self.assertEqual(thread.join_timeout, 0.7)
        warning_mock.assert_called_once()
        warning_message = warning_mock.call_args[0][0]
        self.assertIn("bootstrap_thread_alive=true", warning_message)


if __name__ == "__main__":
    unittest.main()
