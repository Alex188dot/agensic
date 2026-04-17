"""Cross-platform detection helpers.

Consolidates the scattered ``sys.platform`` / ``os.name`` checks into a
single module so the rest of the codebase can import from here instead of
repeating the same logic.
"""

import os
import platform
import sys


def is_windows() -> bool:
    return sys.platform == "win32"


def is_macos() -> bool:
    return sys.platform == "darwin"


def is_linux() -> bool:
    return sys.platform.startswith("linux")


def machine() -> str:
    """Return the machine architecture string (e.g. ``arm64``, ``x86_64``).

    Uses :func:`platform.machine` which works on all platforms including
    Windows (unlike ``os.uname().machine`` which does not exist on Windows).
    """
    return platform.machine().strip().lower()


def platform_tag() -> str:
    """Return a short platform identifier like ``darwin-arm64`` or ``windows-x64``."""
    m = machine()
    if is_macos() and m in {"arm64", "aarch64"}:
        return "darwin-arm64"
    if is_macos() and m in {"x86_64", "amd64"}:
        return "darwin-x64"
    if is_linux() and m in {"x86_64", "amd64"}:
        return "linux-x64"
    if is_linux() and m in {"arm64", "aarch64"}:
        return "linux-arm64"
    if is_windows() and m in {"x86_64", "amd64"}:
        return "windows-x64"
    if is_windows() and m in {"arm64", "aarch64"}:
        return "windows-arm64"
    return f"{sys.platform}-{m or 'unknown'}"


def platform_rust_target() -> str:
    """Return the Rust target triple for the current platform, or ``""`` if unknown."""
    m = machine()
    if is_macos() and m in {"arm64", "aarch64"}:
        return "aarch64-apple-darwin"
    if is_macos() and m in {"x86_64", "amd64"}:
        return "x86_64-apple-darwin"
    if is_linux() and m in {"x86_64", "amd64"}:
        return "x86_64-unknown-linux-gnu"
    if is_linux() and m in {"arm64", "aarch64"}:
        return "aarch64-unknown-linux-gnu"
    if is_windows() and m in {"x86_64", "amd64"}:
        return "x86_64-pc-windows-msvc"
    if is_windows() and m in {"arm64", "aarch64"}:
        return "aarch64-pc-windows-msvc"
    return ""


def binary_suffix() -> str:
    """Return ``".exe"`` on Windows, ``""`` elsewhere."""
    return ".exe" if is_windows() else ""


def venv_python_path(venv_dir: str) -> str:
    """Return the Python executable path inside a virtualenv.

    On Unix: ``<venv_dir>/bin/python``
    On Windows: ``<venv_dir>/Scripts/python.exe``
    """
    if is_windows():
        return os.path.join(venv_dir, "Scripts", "python.exe")
    return os.path.join(venv_dir, "bin", "python")


def default_shell_name() -> str:
    """Return the default shell name for the current platform."""
    if is_windows():
        return "powershell"
    if is_linux():
        return "bash"
    return "zsh"
