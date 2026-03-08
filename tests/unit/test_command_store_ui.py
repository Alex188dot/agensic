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


if __name__ == "__main__":
    unittest.main()
