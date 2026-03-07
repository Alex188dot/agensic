import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any


PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
PRIVATE_EXECUTABLE_MODE = 0o700


def _chmod_best_effort(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except Exception:
        pass


def ensure_private_dir(path: str | Path) -> str:
    target = Path(path).expanduser()
    try:
        os.makedirs(target, exist_ok=True)
    except FileExistsError:
        pass
    _chmod_best_effort(target, PRIVATE_DIR_MODE)
    return str(target)


def enforce_private_file(path: str | Path, executable: bool | None = None) -> None:
    target = Path(path).expanduser()
    try:
        if not target.exists() or not target.is_file() or target.is_symlink():
            return
        current_mode = stat.S_IMODE(target.stat().st_mode)
    except Exception:
        return

    if executable is None:
        executable = bool(current_mode & stat.S_IXUSR)
    desired_mode = PRIVATE_EXECUTABLE_MODE if executable else PRIVATE_FILE_MODE
    if current_mode != desired_mode:
        _chmod_best_effort(target, desired_mode)


def atomic_write_text_private(
    path: str | Path,
    content: str,
    *,
    executable: bool = False,
    encoding: str = "utf-8",
) -> None:
    target = Path(path).expanduser()
    ensure_private_dir(target.parent)
    desired_mode = PRIVATE_EXECUTABLE_MODE if executable else PRIVATE_FILE_MODE
    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(target.parent),
    )
    try:
        os.fchmod(fd, desired_mode)
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(content)
        os.replace(tmp_path, target)
        _chmod_best_effort(target, desired_mode)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def atomic_write_json_private(
    path: str | Path,
    payload: Any,
    *,
    executable: bool = False,
    encoding: str = "utf-8",
    **json_kwargs: Any,
) -> None:
    text = json.dumps(payload, **json_kwargs)
    atomic_write_text_private(
        path,
        text,
        executable=executable,
        encoding=encoding,
    )


def harden_private_tree(root: str | Path) -> None:
    target = Path(root).expanduser()
    if not target.exists():
        return
    if target.is_file():
        ensure_private_dir(target.parent)
        enforce_private_file(target)
        return
    if target.is_symlink():
        return

    ensure_private_dir(target)
    for current_root, dirnames, filenames in os.walk(target):
        current_path = Path(current_root)
        ensure_private_dir(current_path)

        for dirname in list(dirnames):
            dir_path = current_path / dirname
            if dir_path.is_symlink():
                continue
            ensure_private_dir(dir_path)

        for filename in filenames:
            file_path = current_path / filename
            enforce_private_file(file_path)
