import importlib
import unittest
from unittest.mock import patch

cli_app = importlib.import_module("agensic.cli.app")


class CliFirstRunTests(unittest.TestCase):
    def test_first_run_enables_boot_then_starts_once(self):
        with patch.object(cli_app, "ensure_config_dir"), patch.object(
            cli_app, "_clear_uninstall_sentinel"
        ), patch.object(
            cli_app, "_rotate_auth_token_or_exit"
        ), patch.object(
            cli_app, "_load_config", return_value={"provider": "history_only"}
        ), patch.object(
            cli_app, "_configure_provider", return_value=True
        ) as configure_provider_mock, patch.object(
            cli_app, "_setup_confirm", return_value=True
        ) as confirm_mock, patch.object(
            cli_app, "_enable_startup_impl"
        ) as enable_startup_mock, patch.object(
            cli_app, "_start_impl"
        ) as start_mock, patch.object(
            cli_app.console, "print"
        ):
            completed = cli_app._run_first_install_onboarding()

        self.assertTrue(completed)
        configure_provider_mock.assert_called_once_with(
            {"provider": "history_only"},
            manage_runtime=False,
            banner_title="Agensic Setup",
        )
        confirm_mock.assert_called_once_with("Enable start on boot (Recommended)?")
        enable_startup_mock.assert_called_once_with(start_now=False)
        start_mock.assert_called_once_with(
            pending_status_message="[yellow]Enabling for the first time, this can take about 1 minute...[/yellow]"
        )

    def test_first_run_starts_once_without_boot_enable(self):
        with patch.object(cli_app, "ensure_config_dir"), patch.object(
            cli_app, "_clear_uninstall_sentinel"
        ), patch.object(
            cli_app, "_rotate_auth_token_or_exit"
        ), patch.object(
            cli_app, "_load_config", return_value={}
        ), patch.object(
            cli_app, "_configure_provider", return_value=True
        ) as configure_provider_mock, patch.object(
            cli_app, "_setup_confirm", return_value=False
        ), patch.object(
            cli_app, "_enable_startup_impl"
        ) as enable_startup_mock, patch.object(
            cli_app, "_start_impl"
        ) as start_mock, patch.object(
            cli_app.console, "print"
        ):
            completed = cli_app._run_first_install_onboarding()

        self.assertTrue(completed)
        configure_provider_mock.assert_called_once_with(
            {},
            manage_runtime=False,
            banner_title="Agensic Setup",
        )
        enable_startup_mock.assert_not_called()
        start_mock.assert_called_once_with(pending_status_message=None)


if __name__ == "__main__":
    unittest.main()
