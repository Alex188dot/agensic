import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "signed_session.sh"


@unittest.skipUnless(SCRIPT_PATH.exists(), "signed_session.sh not available in this environment")
class SignedSessionScriptTests(unittest.TestCase):
    def _env(self, temp_dir: str) -> dict[str, str]:
        env = dict(os.environ)
        env["HOME"] = temp_dir
        env["XDG_CONFIG_HOME"] = str(Path(temp_dir) / ".config")
        env["XDG_STATE_HOME"] = str(Path(temp_dir) / ".state")
        env["XDG_CACHE_HOME"] = str(Path(temp_dir) / ".cache")
        env["AGENSIC_CLI_PYTHON"] = sys.executable
        return env

    def test_start_status_stop_round_trip_works_without_eval(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env = self._env(temp_dir)

            start = subprocess.run(
                [str(SCRIPT_PATH), "start", "--agent", "CoDeX", "--model", "gpt-5.3", "--agent-name", "Planner A"],
                capture_output=True,
                text=True,
                check=False,
                cwd=REPO_ROOT,
                env=env,
            )
            self.assertEqual(start.returncode, 0, msg=start.stderr)
            self.assertIn("export AGENSIC_AI_SESSION_AGENT=codex", start.stdout)

            status = subprocess.run(
                [str(SCRIPT_PATH), "status"],
                capture_output=True,
                text=True,
                check=False,
                cwd=REPO_ROOT,
                env=env,
            )
            self.assertEqual(status.returncode, 0, msg=status.stderr)
            self.assertIn("active agent=codex model=gpt-5.3", status.stdout)
            self.assertIn("agent_name=Planner A", status.stdout)

            stop = subprocess.run(
                [str(SCRIPT_PATH), "stop"],
                capture_output=True,
                text=True,
                check=False,
                cwd=REPO_ROOT,
                env=env,
            )
            self.assertEqual(stop.returncode, 0, msg=stop.stderr)
            self.assertIn("unset AGENSIC_AI_SESSION_ACTIVE", stop.stdout)

            final_status = subprocess.run(
                [str(SCRIPT_PATH), "status"],
                capture_output=True,
                text=True,
                check=False,
                cwd=REPO_ROOT,
                env=env,
            )
            self.assertEqual(final_status.returncode, 0, msg=final_status.stderr)
            self.assertIn("inactive", final_status.stdout)


if __name__ == "__main__":
    unittest.main()
