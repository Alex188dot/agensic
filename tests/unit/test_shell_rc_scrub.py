import importlib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

cli_app = importlib.import_module("agensic.cli.app")


class ShellRcScrubTests(unittest.TestCase):
    def test_scrub_removes_managed_block_and_stale_path_wiring(self):
        with TemporaryDirectory() as tmpdir:
            rc_path = Path(tmpdir) / ".zshrc"
            rc_path.write_text(
                "\n".join(
                    [
                        "# >>> agensic ble >>>",
                        "export AGENSIC_LEGACY_BASH_HELPER=1",
                        "# <<< agensic ble <<<",
                        "export PATH=\"$HOME/.agensic/bin:$PATH\"",
                        "alias agensic='python3 /Users/test/.agensic/cli.py'",
                        "source /Users/test/.agensic/agensic.zsh",
                        "source /Users/test/.agensic/agensic.bash",
                        cli_app.SHELL_RC_BLOCK_START,
                        "export PATH=\"/Users/test/.agensic/bin:$PATH\"",
                        "source \"/Users/test/.agensic/agensic.zsh\"",
                        "source \"/Users/test/.agensic/agensic.bash\"",
                        cli_app.SHELL_RC_BLOCK_END,
                        "export KEEP_ME=1",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            changed = cli_app._scrub_shell_rc_file(rc_path)
            updated = rc_path.read_text(encoding="utf-8")

        self.assertTrue(changed)
        self.assertNotIn("alias agensic=", updated)
        self.assertNotIn(".agensic/bin", updated)
        self.assertNotIn("agensic.zsh", updated)
        self.assertNotIn("agensic.bash", updated)
        self.assertNotIn("AGENSIC_LEGACY_BASH_HELPER", updated)
        self.assertIn("export KEEP_ME=1", updated)


if __name__ == "__main__":
    unittest.main()
