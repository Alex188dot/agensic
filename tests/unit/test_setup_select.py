import importlib
import io
import os
import pty
import unittest
from unittest.mock import patch

cli_app = importlib.import_module("agensic.cli.app")


@unittest.skipIf(cli_app.termios is None, "termios is required for setup selector terminal mode tests")
class SetupSelectTerminalModeTests(unittest.TestCase):
    def test_raw_setup_select_is_enabled_by_default_on_tty(self):
        fake_stdin = unittest.mock.MagicMock()
        fake_stdout = unittest.mock.MagicMock()
        fake_stdin.isatty.return_value = True
        fake_stdout.isatty.return_value = True

        with patch.dict(cli_app.os.environ, {}, clear=True), patch.object(
            cli_app.sys, "stdin", fake_stdin
        ), patch.object(cli_app.sys, "stdout", fake_stdout):
            self.assertTrue(cli_app._can_use_raw_setup_select())

    def test_raw_setup_select_can_be_enabled_explicitly(self):
        fake_stdin = unittest.mock.MagicMock()
        fake_stdout = unittest.mock.MagicMock()
        fake_stdin.isatty.return_value = True
        fake_stdout.isatty.return_value = True

        with patch.dict(cli_app.os.environ, {"AGENSIC_ENABLE_RAW_SETUP_SELECT": "1"}, clear=True), patch.object(
            cli_app.sys, "stdin", fake_stdin
        ), patch.object(cli_app.sys, "stdout", fake_stdout):
            self.assertTrue(cli_app._can_use_raw_setup_select())

    def test_raw_setup_select_can_be_disabled_explicitly(self):
        fake_stdin = unittest.mock.MagicMock()
        fake_stdout = unittest.mock.MagicMock()
        fake_stdin.isatty.return_value = True
        fake_stdout.isatty.return_value = True

        with patch.dict(cli_app.os.environ, {"AGENSIC_DISABLE_RAW_SETUP_SELECT": "1"}, clear=True), patch.object(
            cli_app.sys, "stdin", fake_stdin
        ), patch.object(cli_app.sys, "stdout", fake_stdout):
            self.assertFalse(cli_app._can_use_raw_setup_select())

    def test_render_raw_setup_select_reuses_previous_screen_lines(self):
        fake_stdout = io.StringIO()

        with patch.object(cli_app.sys, "stdout", fake_stdout), patch.object(
            cli_app.shutil, "get_terminal_size", return_value=os.terminal_size((120, 24))
        ):
            rendered_line_count = cli_app._render_raw_setup_select(
                message="Choose one:",
                choices=["Choose AI provider", "Manage command store (add/remove commands)"],
                selected_index=0,
                pointer="👉",
                instruction=" ",
                previous_line_count=0,
            )
            cli_app._render_raw_setup_select(
                message="Choose one:",
                choices=["Choose AI provider", "Manage command store (add/remove commands)"],
                selected_index=1,
                pointer="👉",
                instruction=" ",
                previous_line_count=rendered_line_count,
            )

        rendered = fake_stdout.getvalue()
        self.assertTrue(rendered.startswith("? Choose one:"))
        self.assertIn("\r\033[3A\033[J? Choose one:", rendered)
        self.assertIn("> Choose AI provider", rendered)

    def test_render_raw_setup_select_accounts_for_wrapped_rows(self):
        fake_stdout = io.StringIO()

        with patch.object(cli_app.sys, "stdout", fake_stdout), patch.object(
            cli_app.shutil, "get_terminal_size", return_value=os.terminal_size((20, 24))
        ):
            rendered_line_count = cli_app._render_raw_setup_select(
                message="Choose one:",
                choices=["Short", "12345678901234567890"],
                selected_index=0,
                pointer=">",
                instruction=" ",
                previous_line_count=0,
            )
            cli_app._render_raw_setup_select(
                message="Choose one:",
                choices=["Short", "12345678901234567890"],
                selected_index=1,
                pointer=">",
                instruction=" ",
                previous_line_count=rendered_line_count,
            )

        rendered = fake_stdout.getvalue()
        self.assertEqual(rendered_line_count, 3)
        self.assertIn("\r\033[3A\033[J? Choose one:", rendered)
        self.assertIn("123456789012345...", rendered)

    def test_setup_select_terminal_mode_preserves_output_processing(self):
        master_fd, slave_fd = pty.openpty()
        try:
            original_attrs = cli_app.termios.tcgetattr(slave_fd)

            with cli_app._setup_select_terminal_mode(slave_fd):
                active_attrs = cli_app.termios.tcgetattr(slave_fd)
                self.assertTrue(active_attrs[1] & cli_app.termios.OPOST)
                self.assertFalse(active_attrs[3] & cli_app.termios.ICANON)
                self.assertFalse(active_attrs[3] & cli_app.termios.ECHO)
                self.assertEqual(active_attrs[6][cli_app.termios.VMIN], 1)
                self.assertEqual(active_attrs[6][cli_app.termios.VTIME], 0)

            restored_attrs = cli_app.termios.tcgetattr(slave_fd)
            self.assertEqual(restored_attrs, original_attrs)
        finally:
            os.close(master_fd)
            os.close(slave_fd)

    def test_setup_select_falls_back_when_raw_terminal_mode_fails(self):
        question = unittest.mock.MagicMock()
        question.ask.return_value = "Anthropic"

        with patch.object(cli_app, "_can_use_raw_setup_select", return_value=True), patch.object(
            cli_app, "_setup_select_raw", side_effect=RuntimeError("mode failed")
        ), patch.object(cli_app, "_build_select_question", return_value=question) as build_mock:
            selected = cli_app._setup_select("Select Provider:", ["Anthropic"])

        self.assertEqual(selected, "Anthropic")
        self.assertEqual(build_mock.call_args.kwargs["pointer"], "👉")
        self.assertEqual(build_mock.call_args.kwargs["instruction"], " ")
        question.ask.assert_called_once_with()

    def test_setup_select_prefers_raw_mode_by_default(self):
        fake_stdin = unittest.mock.MagicMock()
        fake_stdout = unittest.mock.MagicMock()
        fake_stdin.isatty.return_value = True
        fake_stdout.isatty.return_value = True

        with patch.dict(cli_app.os.environ, {}, clear=True), patch.object(
            cli_app.sys, "stdin", fake_stdin
        ), patch.object(cli_app.sys, "stdout", fake_stdout), patch.object(
            cli_app, "_setup_select_raw", return_value="Anthropic"
        ) as raw_mock, patch.object(cli_app, "_build_select_question") as build_mock:
            selected = cli_app._setup_select("Select Provider:", ["Anthropic"])

        self.assertEqual(selected, "Anthropic")
        raw_mock.assert_called_once_with(
            "Select Provider:",
            ["Anthropic"],
            pointer="👉",
            instruction=" ",
        )
        build_mock.assert_not_called()

    def test_setup_select_uses_questionary_when_raw_mode_is_disabled(self):
        fake_stdin = unittest.mock.MagicMock()
        fake_stdout = unittest.mock.MagicMock()
        fake_stdin.isatty.return_value = True
        fake_stdout.isatty.return_value = True

        question = unittest.mock.MagicMock()
        question.ask.return_value = "Anthropic"

        with patch.dict(cli_app.os.environ, {"AGENSIC_DISABLE_RAW_SETUP_SELECT": "1"}, clear=True), patch.object(
            cli_app.sys, "stdin", fake_stdin
        ), patch.object(cli_app.sys, "stdout", fake_stdout), patch.object(
            cli_app, "_setup_select_raw"
        ) as raw_mock, patch.object(cli_app, "_build_select_question", return_value=question) as build_mock:
            selected = cli_app._setup_select("Select Provider:", ["Anthropic"])

        self.assertEqual(selected, "Anthropic")
        raw_mock.assert_not_called()
        self.assertEqual(build_mock.call_args.kwargs["pointer"], "👉")
        self.assertEqual(build_mock.call_args.kwargs["instruction"], " ")
        question.ask.assert_called_once_with()

    def test_build_select_question_matches_legacy_questionary_configuration(self):
        with patch.object(cli_app.questionary, "select") as select_mock:
            cli_app._build_select_question(
                message="Choose one:",
                choices=["A", "B"],
                pointer=">",
                instruction=" ",
            )

        self.assertEqual(select_mock.call_args.args[0], "Choose one:")
        self.assertEqual(select_mock.call_args.kwargs["choices"], ["A", "B"])
        self.assertEqual(select_mock.call_args.kwargs["pointer"], ">")
        self.assertEqual(select_mock.call_args.kwargs["instruction"], " ")
        self.assertIn("style", select_mock.call_args.kwargs)
        self.assertNotIn("use_indicator", select_mock.call_args.kwargs)
        self.assertNotIn("use_jk_keys", select_mock.call_args.kwargs)
        self.assertNotIn("use_emacs_keys", select_mock.call_args.kwargs)
        self.assertNotIn("show_description", select_mock.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
