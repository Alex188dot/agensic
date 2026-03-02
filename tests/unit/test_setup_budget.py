import unittest
import importlib
from unittest.mock import patch

cli_app = importlib.import_module("ghostshell.cli.app")


class SetupBudgetTests(unittest.TestCase):
    def test_get_llm_calls_per_line_default_when_missing(self):
        self.assertEqual(cli_app._get_llm_calls_per_line({}), 4)

    def test_get_llm_calls_per_line_default_when_invalid(self):
        self.assertEqual(cli_app._get_llm_calls_per_line({"llm_calls_per_line": "abc"}), 4)
        self.assertEqual(cli_app._get_llm_calls_per_line({"llm_calls_per_line": -1}), 4)
        self.assertEqual(cli_app._get_llm_calls_per_line({"llm_calls_per_line": 999}), 4)

    def test_get_llm_calls_per_line_valid_values(self):
        self.assertEqual(cli_app._get_llm_calls_per_line({"llm_calls_per_line": 0}), 0)
        self.assertEqual(cli_app._get_llm_calls_per_line({"llm_calls_per_line": "7"}), 7)

    def test_with_llm_calls_per_line_clamps_to_zero(self):
        config = {"provider": "openai"}
        updated = cli_app._with_llm_calls_per_line(config, -3)
        self.assertEqual(updated["llm_calls_per_line"], 0)
        self.assertEqual(updated["llm_budget_unlimited"], False)
        self.assertEqual(updated["provider"], "openai")

    def test_with_llm_calls_per_line_caps_at_99(self):
        updated = cli_app._with_llm_calls_per_line({}, 120)
        self.assertEqual(updated["llm_calls_per_line"], 99)
        self.assertEqual(updated["llm_budget_unlimited"], False)

    def test_llm_budget_unlimited_toggle(self):
        self.assertTrue(cli_app._is_llm_budget_unlimited({"llm_budget_unlimited": True}))
        updated = cli_app._with_llm_budget_unlimited({}, True)
        self.assertTrue(updated["llm_budget_unlimited"])

    def test_setup_rotates_local_auth_token(self):
        with patch.object(cli_app, "rotate_auth_token") as rotate_mock, patch.object(
            cli_app, "_setup_select", return_value=cli_app.BACK_SIGNAL
        ), patch.object(cli_app.console, "print"), patch.object(
            cli_app._DAEMON_AUTH_CACHE, "get_token", return_value="test-auth-token"
        ):
            cli_app.setup()
        rotate_mock.assert_called_once()

    def test_start_rotates_local_auth_token(self):
        with patch.object(cli_app, "_rotate_auth_token_or_exit") as rotate_mock, patch.object(
            cli_app, "is_port_open", return_value=True
        ), patch.object(
            cli_app, "_fetch_daemon_status", return_value={"bootstrap": {"ready": True, "indexed_commands": 0}}
        ), patch.object(cli_app.console, "print"):
            cli_app.start()
        rotate_mock.assert_called_once_with("start")


if __name__ == "__main__":
    unittest.main()
