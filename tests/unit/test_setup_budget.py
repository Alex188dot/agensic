import unittest
import importlib
from unittest.mock import MagicMock, patch

cli_app = importlib.import_module("agensic.cli.app")


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

    def test_setup_select_uses_emoji_pointer(self):
        question = MagicMock()
        question.ask.return_value = "Choose AI provider"

        with patch.object(cli_app, "_build_select_question", return_value=question) as build_mock:
            cli_app._setup_select("Choose one:", ["Choose AI provider"])

        self.assertEqual(build_mock.call_args.kwargs["pointer"], "👉")

    def test_setup_menu_includes_daemon_launch(self):
        observed_choices = []

        def _fake_select(_message, choices):
            observed_choices.extend(choices)
            if "Agensic Autocomplete" in choices:
                return "Agensic Autocomplete"
            return cli_app.BACK_SIGNAL

        with patch.object(cli_app, "_rotate_auth_token_or_exit"), patch.object(
            cli_app, "_setup_select", side_effect=_fake_select
        ), patch.object(cli_app.console, "print"):
            cli_app.setup()

        self.assertIn("Daemon launch", observed_choices)

    def test_setup_redraws_clean_screen_when_returning_to_main_menu(self):
        with patch.object(cli_app, "_rotate_auth_token_or_exit"), patch.object(
            cli_app, "_setup_select", side_effect=["Agensic Autocomplete", "Daemon launch", cli_app.BACK_SIGNAL]
        ), patch.object(cli_app, "_manage_daemon_launch"), patch.object(
            cli_app.console, "print"
        ), patch.object(cli_app.console, "clear") as clear_mock:
            cli_app.setup()

        self.assertGreaterEqual(clear_mock.call_count, 2)

    def test_configure_provider_history_only_keeps_existing_budget(self):
        saved = {}

        with patch.object(
            cli_app,
            "_setup_select",
            return_value="use without AI (will just use your history)",
        ), patch.object(cli_app, "_save_config", side_effect=lambda config: saved.update(config)), patch.object(
            cli_app.console, "print"
        ):
            completed = cli_app._configure_provider(
                {"llm_calls_per_line": 4, "llm_budget_unlimited": True},
                manage_runtime=False,
            )

        self.assertTrue(completed)
        self.assertEqual(saved["provider"], "history_only")
        self.assertEqual(saved["model"], "history-only")
        self.assertEqual(saved["llm_calls_per_line"], 4)
        self.assertTrue(saved["llm_budget_unlimited"])

    def test_manage_daemon_launch_enables_startup_without_starting_now(self):
        with patch.object(cli_app, "_is_startup_enabled", return_value=False), patch.object(
            cli_app, "_setup_select", return_value="launch at startup (recommended)"
        ), patch.object(cli_app, "_reset_setup_screen"
        ), patch.object(cli_app, "_enable_startup_impl") as enable_mock, patch.object(
            cli_app.console, "print"
        ):
            cli_app._manage_daemon_launch()

        enable_mock.assert_called_once_with(start_now=False)

    def test_manage_daemon_launch_disables_startup_when_requested(self):
        with patch.object(cli_app, "_is_startup_enabled", return_value=True), patch.object(
            cli_app, "_setup_select", return_value="remove from startup"
        ), patch.object(cli_app, "_reset_setup_screen"
        ), patch.object(cli_app, "_disable_startup_impl") as disable_mock, patch.object(
            cli_app.console, "print"
        ):
            cli_app._manage_daemon_launch()

        disable_mock.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
