import os
import tempfile
from pathlib import Path


def parse_history_line(raw_line: str) -> str:
    line = (raw_line or "").strip()
    if not line:
        return ""
    if line.startswith(":"):
        parts = line.split(";", 1)
        if len(parts) == 2:
            line = parts[1].strip()
    return line


def rewrite_history_without_commands(
    history_file: str,
    commands_to_remove: set[str],
) -> tuple[int, str]:
    if not history_file:
        return (0, "History file could not be detected for this shell.")

    history_path = Path(history_file).expanduser()
    if not history_path.exists() or not history_path.is_file():
        return (0, f"History file not found: {history_path}")

    removed_lines = 0
    tmp_path = None
    try:
        os.makedirs(str(history_path.parent), exist_ok=True)
        source_stat = history_path.stat()

        with open(history_path, "r", encoding="utf-8", errors="ignore") as src:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=str(history_path.parent),
                prefix=f"{history_path.name}.tmp.",
                delete=False,
            ) as dst:
                tmp_path = dst.name
                for line in src:
                    normalized = parse_history_line(line)
                    if normalized and normalized in commands_to_remove:
                        removed_lines += 1
                        continue
                    dst.write(line)

        os.chmod(tmp_path, source_stat.st_mode)
        os.replace(tmp_path, history_path)
        return (removed_lines, "")
    except Exception as exc:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        return (removed_lines, f"Failed to rewrite history: {exc}")
