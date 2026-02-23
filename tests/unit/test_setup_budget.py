import unittest
import importlib

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


if __name__ == "__main__":
    unittest.main()
