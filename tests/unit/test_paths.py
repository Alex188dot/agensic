import os
import unittest
from unittest.mock import patch

from agensic import paths


class AppPathsTests(unittest.TestCase):
    def test_posix_paths_use_xdg_layout(self):
        with patch.object(paths.sys, "platform", "darwin"), patch.dict(
            os.environ,
            {
                "XDG_CONFIG_HOME": "/tmp/xdg-config",
                "XDG_STATE_HOME": "/tmp/xdg-state",
                "XDG_CACHE_HOME": "/tmp/xdg-cache",
                "XDG_BIN_HOME": "/tmp/xdg-bin",
            },
            clear=False,
        ):
            app_paths = paths.get_app_paths()

        self.assertEqual(app_paths.config_file, "/tmp/xdg-config/agensic/config.json")
        self.assertEqual(app_paths.state_sqlite_path, "/tmp/xdg-state/agensic/state.sqlite")
        self.assertEqual(app_paths.zvec_commands_path, "/tmp/xdg-cache/agensic/zvec_commands")
        self.assertEqual(app_paths.launcher_path, "/tmp/xdg-bin/agensic")
        self.assertEqual(app_paths.shell_support_dir, "/tmp/xdg-state/agensic/install/shell")
        self.assertEqual(app_paths.shell_integration_path, "/tmp/xdg-state/agensic/install/agensic.zsh")
        self.assertEqual(
            app_paths.shell_shared_helpers_path,
            "/tmp/xdg-state/agensic/install/shell/agensic_shared.sh",
        )

    def test_windows_paths_use_appdata_layout(self):
        with patch.object(paths.sys, "platform", "win32"), patch.dict(
            os.environ,
            {
                "APPDATA": r"C:\Users\Test\AppData\Roaming",
                "LOCALAPPDATA": r"C:\Users\Test\AppData\Local",
            },
            clear=False,
        ):
            app_paths = paths.get_app_paths()

        self.assertEqual(
            app_paths.config_file,
            r"C:\Users\Test\AppData\Roaming/Agensic/config.json",
        )
        self.assertEqual(
            app_paths.state_sqlite_path,
            r"C:\Users\Test\AppData\Local/Agensic/State/state.sqlite",
        )
        self.assertEqual(
            app_paths.zvec_commands_path,
            r"C:\Users\Test\AppData\Local/Agensic/Cache/zvec_commands",
        )


if __name__ == "__main__":
    unittest.main()
