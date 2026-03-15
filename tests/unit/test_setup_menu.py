import importlib
import unittest
from unittest.mock import patch


cli_app = importlib.import_module("agensic.cli.app")
track_module = importlib.import_module("agensic.cli.track")


class SetupMenuTests(unittest.TestCase):
    def test_setup_top_menu_uses_sessions_then_autocomplete(self):
        captured_choices: list[list[str]] = []

        def _fake_select(message: str, choices: list[str], **kwargs):  # noqa: ARG001
            captured_choices.append(list(choices))
            return cli_app.BACK_SIGNAL

        with patch.object(cli_app, "ensure_config_dir"), patch.object(
            cli_app, "_clear_uninstall_sentinel"
        ), patch.object(
            cli_app, "_rotate_auth_token_or_exit"
        ), patch.object(
            cli_app, "_setup_select", side_effect=_fake_select
        ):
            cli_app.setup()

        self.assertEqual(captured_choices[0], ["Agensic Sessions", "Agensic Autocomplete"])

    def test_setup_routes_sessions_choice_to_sessions_menu(self):
        with patch.object(cli_app, "ensure_config_dir"), patch.object(
            cli_app, "_clear_uninstall_sentinel"
        ), patch.object(
            cli_app, "_rotate_auth_token_or_exit"
        ), patch.object(
            cli_app,
            "_setup_select",
            side_effect=["Agensic Sessions", cli_app.BACK_SIGNAL],
        ), patch.object(cli_app, "_setup_sessions_menu") as sessions_menu, patch.object(
            cli_app, "_autocomplete_setup_menu"
        ) as autocomplete_menu:
            cli_app.setup()

        sessions_menu.assert_called_once_with()
        autocomplete_menu.assert_not_called()

    def test_sessions_submenu_order(self):
        captured_choices: list[list[str]] = []

        def _fake_select(message: str, choices: list[str], **kwargs):  # noqa: ARG001
            captured_choices.append(list(choices))
            return cli_app.BACK_SIGNAL

        with patch.object(cli_app, "_setup_select", side_effect=_fake_select):
            cli_app._setup_sessions_menu()

        self.assertEqual(
            captured_choices[0],
            ["Show All Agents", "Add custom Agent", "Remove custom Agent", "Rename session", "Remove session"],
        )

    def test_sessions_submenu_routes_show_all_agents(self):
        with patch.object(cli_app, "_setup_select", side_effect=["Show All Agents", cli_app.BACK_SIGNAL]), patch.object(
            cli_app, "_setup_show_all_agents"
        ) as show_all_agents:
            cli_app._setup_sessions_menu()

        show_all_agents.assert_called_once_with()

    def test_setup_show_all_agents_prefers_responsive_tui(self):
        agents = [
            {
                "agent_id": "codex",
                "display_name": "Codex",
                "source": "builtin",
                "status": "verified",
                "executables": ["codex"],
                "aliases": ["codex"],
            }
        ]
        with patch.object(track_module, "list_known_agents", return_value=agents), patch.object(
            cli_app, "_run_agents_tui", return_value=True
        ) as run_tui, patch.object(cli_app, "_render_setup_agents_table") as render_table, patch.object(
            cli_app, "_setup_pause"
        ) as setup_pause:
            cli_app._setup_show_all_agents()

        run_tui.assert_called_once_with(agents)
        render_table.assert_not_called()
        setup_pause.assert_not_called()

    def test_setup_show_all_agents_falls_back_to_table_when_tui_fails(self):
        agents = [
            {
                "agent_id": "codex",
                "display_name": "Codex",
                "source": "builtin",
                "status": "verified",
                "executables": ["codex"],
                "aliases": ["codex"],
            }
        ]
        with patch.object(track_module, "list_known_agents", return_value=agents), patch.object(
            cli_app, "_run_agents_tui", side_effect=RuntimeError("outdated")
        ) as run_tui, patch.object(cli_app, "_render_setup_agents_table") as render_table, patch.object(
            cli_app, "_setup_pause"
        ) as setup_pause:
            cli_app._setup_show_all_agents()

        run_tui.assert_called_once_with(agents)
        render_table.assert_called_once_with(agents)
        setup_pause.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
