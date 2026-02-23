import unittest
import importlib

cli_app = importlib.import_module("ghostshell.cli.app")


class SetupBudgetTests(unittest.TestCase):
    def test_get_llm_calls_per_line_default_when_missing(self):
        self.assertEqual(cli_app._get_llm_calls_per_line({}), 4)

    def test_get_llm_calls_per_line_default_when_invalid(self):
        self.assertEqual(cli_app._get_llm_calls_per_line({"llm_calls_per_line": "abc"}), 4)
        self.assertEqual(cli_app._get_llm_calls_per_line({"llm_calls_per_line": -1}), 4)

    def test_get_llm_calls_per_line_valid_values(self):
        self.assertEqual(cli_app._get_llm_calls_per_line({"llm_calls_per_line": 0}), 0)
        self.assertEqual(cli_app._get_llm_calls_per_line({"llm_calls_per_line": "7"}), 7)

    def test_with_llm_calls_per_line_clamps_to_zero(self):
        config = {"provider": "openai"}
        updated = cli_app._with_llm_calls_per_line(config, -3)
        self.assertEqual(updated["llm_calls_per_line"], 0)
        self.assertEqual(updated["provider"], "openai")


if __name__ == "__main__":
    unittest.main()
