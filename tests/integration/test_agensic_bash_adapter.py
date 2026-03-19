import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
AGENSIC_BASH = REPO_ROOT / "agensic.bash"


class AgensicBashAdapterTests(unittest.TestCase):
    def _run_bash(self, body: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        script = textwrap.dedent(
            f"""
            source "{AGENSIC_BASH}"
            {body}
            """
        )
        run_env = dict(os.environ)
        if env:
            run_env.update(env)
        return subprocess.run(
            ["bash", "-lc", script],
            capture_output=True,
            text=True,
            check=False,
            env=run_env,
        )

    def test_non_interactive_source_keeps_adapter_disabled_without_error(self):
        result = self._run_bash(
            """
            printf '%s\\n' "${AGENSIC_BASH_ADAPTER_READY}|${AGENSIC_BASH_BLE_AVAILABLE}"
            """
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), "0|0")

    def test_ble_override_path_is_detected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ble_path = Path(tmpdir) / "ble.sh"
            ble_path.write_text(
                "\n".join(
                    [
                        "BLE_VERSION=mock-ble",
                        "ble-attach() { return 0; }",
                        "ble-bind() { return 0; }",
                        "ble/function#advice() { return 0; }",
                        "ble/widget/redraw-line() { return 0; }",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            result = self._run_bash(
                """
                _agensic_source_ble_if_needed
                printf '%s\\n' "${AGENSIC_BASH_BLE_AVAILABLE}|${AGENSIC_BASH_BLE_LOADED_FROM}"
                """,
                env={"AGENSIC_BLE_SH_PATH": str(ble_path)},
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), f"1|{ble_path}")

    def test_ble_override_registers_widgets(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ble_path = Path(tmpdir) / "ble.sh"
            ble_path.write_text(
                "\n".join(
                    [
                        "BLE_VERSION=mock-ble",
                        "ble-attach() { return 0; }",
                        "ble-bind() { return 0; }",
                        "ble/function#advice() { return 0; }",
                        "ble/widget/redraw-line() { return 0; }",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            result = self._run_bash(
                """
                _agensic_source_ble_if_needed
                _agensic_register_bash_widgets
                printf '%s\\n' "${AGENSIC_BASH_WIDGETS_REGISTERED}"
                """,
                env={"AGENSIC_BLE_SH_PATH": str(ble_path)},
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.splitlines()[0].strip(), "1")


if __name__ == "__main__":
    unittest.main()
