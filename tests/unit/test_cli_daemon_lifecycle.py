import importlib
import unittest
from unittest.mock import MagicMock, patch


cli_app = importlib.import_module("agensic.cli.app")


class DaemonLifecycleTests(unittest.TestCase):
    def test_stop_systemd_user_service_returns_true_for_active_linux_unit(self):
        active = MagicMock(returncode=0)
        stop = MagicMock(returncode=0)

        with patch.object(cli_app.sys, "platform", "linux"), patch.object(
            cli_app.os.path, "exists", side_effect=lambda path: path == cli_app.SYSTEMD_UNIT_PATH
        ), patch.object(cli_app.subprocess, "run", side_effect=[active, stop]) as run_mock:
            stopped = cli_app._stop_systemd_user_service()

        self.assertTrue(stopped)
        self.assertEqual(
            run_mock.call_args_list[0].args[0],
            ["systemctl", "--user", "is-active", "--quiet", "agensic-daemon.service"],
        )
        self.assertEqual(
            run_mock.call_args_list[1].args[0],
            ["systemctl", "--user", "stop", "agensic-daemon.service"],
        )

    def test_stop_uses_systemd_service_stop_before_falling_back(self):
        with patch.object(cli_app.sys, "platform", "linux"), patch.object(
            cli_app, "_stop_systemd_user_service", return_value=True
        ) as stop_service_mock, patch.object(
            cli_app, "_wait_for_port_close", side_effect=[True]
        ), patch.object(
            cli_app, "is_port_open", return_value=False
        ), patch.object(
            cli_app, "_read_pid_file", return_value=None
        ), patch.object(
            cli_app, "_cleanup_legacy_daemon_artifacts"
        ), patch.object(
            cli_app.os.path, "exists", return_value=False
        ), patch.object(
            cli_app.console, "print"
        ) as print_mock:
            cli_app.stop()

        stop_service_mock.assert_called_once_with()
        print_mock.assert_any_call("[red]✓ Stopped.[/red]")


if __name__ == "__main__":
    unittest.main()
