import importlib
import unittest
from unittest.mock import patch

cli_app = importlib.import_module("agensic.cli.app")


class CommandStoreUiTests(unittest.TestCase):
    def test_format_command_store_choice_marks_reason_in_light_red(self):
        tokens = cli_app._format_command_store_choice(
            {
                "command": "aitermina setup",
                "usage_score": 1,
                "reason": "looks like typo of 'aiterminal'",
            },
            show_reason=True,
            low_cutoff=None,
            high_cutoff=None,
        )

        self.assertIn(
            ("class:potential-wrong-reason", " (looks like typo of 'aiterminal')"),
            tokens,
        )

    def test_checkbox_prompt_does_not_duplicate_section_titles_in_header(self):
        captured = {}

        def _fake_layout(_ic, token_fn):
            captured["tokens"] = token_fn()
            return object()

        with patch.object(cli_app.common, "create_inquirer_layout", side_effect=_fake_layout), patch.object(
            cli_app, "Application", return_value=object()
        ), patch.object(cli_app, "Question", side_effect=lambda app: app):
            cli_app._checkbox_without_invert(
                "Select commands to remove:",
                choices=[cli_app.questionary.Choice("git status", value="git status")],
            )

        prompt_text = "".join(text for _style, text in captured["tokens"])
        self.assertNotIn("Potential wrong commands", prompt_text)
        self.assertNotIn("Commands", prompt_text)

    def test_checkbox_prompt_disables_cpr_on_prompt_output(self):
        captured = {}

        class _FakeOutput:
            enable_cpr = True

        def _fake_application(*args, **kwargs):
            captured["output"] = kwargs["output"]
            return object()

        with patch.object(cli_app.common, "create_inquirer_layout", return_value=object()), patch.object(
            cli_app, "create_output", return_value=_FakeOutput()
        ), patch.object(cli_app, "Application", side_effect=_fake_application), patch.object(
            cli_app, "Question", side_effect=lambda app: app
        ):
            cli_app._checkbox_without_invert(
                "Select commands to remove:",
                choices=[cli_app.questionary.Choice("git status", value="git status")],
            )

        self.assertFalse(captured["output"].enable_cpr)

    def test_manage_command_store_redraws_clean_screen_after_action(self):
        with patch.object(cli_app, "_load_config", return_value={"autocomplete_enabled": True}), patch.object(
            cli_app, "_ensure_command_store_backend_ready", return_value=True
        ), patch.object(
            cli_app, "_setup_select", side_effect=["Remove commands", cli_app.BACK_SIGNAL]
        ), patch.object(cli_app, "_manage_command_store_remove"), patch.object(
            cli_app.console, "print"
        ), patch.object(cli_app.console, "clear") as clear_mock:
            cli_app._manage_command_store()

        self.assertEqual(clear_mock.call_count, 2)

    def test_manage_command_store_blocks_when_autocomplete_is_off(self):
        with patch.object(cli_app, "_load_config", return_value={"autocomplete_enabled": False}), patch.object(
            cli_app, "_ensure_command_store_backend_ready"
        ) as backend_ready, patch.object(cli_app.console, "print") as print_mock:
            cli_app._manage_command_store()

        backend_ready.assert_not_called()
        self.assertTrue(any("Autocomplete is turned off" in str(args[0]) for args, _ in print_mock.call_args_list))


if __name__ == "__main__":
    unittest.main()
