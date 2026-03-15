import importlib
import unittest

cli_app = importlib.import_module("agensic.cli.app")


class CliDoctorTests(unittest.TestCase):
    def test_doctor_preview_rebuilds_suffix_suggestion(self):
        preview = cli_app._doctor_suggestion_preview(
            "git st",
            {
                "display": ["ash push -u -m \"temp before testing a1b0bf2\""],
                "modes": ["suffix_append"],
            },
        )

        self.assertEqual(preview, "git stash push -u -m \"temp before testing a1b0bf2\"")

    def test_doctor_preview_keeps_replace_full_text(self):
        preview = cli_app._doctor_suggestion_preview(
            "git st",
            {
                "display": ["[dim]git push origin main[/dim]"],
                "modes": ["replace_full"],
            },
        )

        self.assertEqual(preview, "[dim]git push origin main[/dim]")


if __name__ == "__main__":
    unittest.main()
