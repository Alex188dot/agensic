import os
import shlex
import sys

BLOCKED_EXECUTABLES = {
    "rm",
    "dd",
    "wipefs",
    "shred",
    "fdisk",
    "sfdisk",
    "cfdisk",
    "parted",
    "diskutil",
    "mkfs",
    "newfs",
    "mdadm",
    "zpool",
    "lvremove",
    "vgremove",
    "pvremove",
    "cryptsetup",
    "passwd",
    "chpasswd",
    "usermod",
    "userdel",
    "groupdel",
}

BLOCKED_EXECUTABLE_PREFIXES = ("mkfs.", "mkfs_", "newfs")

GIT_GLOBAL_OPTIONS_WITH_VALUE = {
    "-C",
    "-c",
    "--exec-path",
    "--git-dir",
    "--work-tree",
    "--namespace",
    "--super-prefix",
    "--config-env",
}


def _default_shell_name() -> str:
    if sys.platform.startswith("linux"):
        return "bash"
    return "zsh"


def normalize_shell_name(value: str) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return _default_shell_name()

    base = os.path.basename(raw)
    if sys.platform.startswith("linux") and base == "zsh":
        return "bash"
    if base in {"bash", "zsh", "sh", "fish"}:
        return base
    if base in {"pwsh", "powershell", "powershell.exe", "pwsh.exe"}:
        return "powershell"
    if "powershell" in base:
        return "powershell"
    if base.endswith(".exe") and base[:-4] in {"bash", "zsh", "fish"}:
        return base[:-4]
    return base or _default_shell_name()


def current_shell_name(env: dict[str, str] | None = None, default: str | None = None) -> str:
    source_env = env or os.environ
    raw = str(source_env.get("SHELL", "") or "").strip()
    if not raw:
        raw = str(source_env.get("COMSPEC", "") or "").strip()
    normalized = normalize_shell_name(raw)
    if normalized:
        return normalized
    return normalize_shell_name(default or _default_shell_name())


def normalize_command_pattern(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    try:
        tokens = shlex.split(value, posix=True)
    except Exception:
        tokens = value.split()
    if not tokens:
        return ""
    return os.path.basename(tokens[0]).strip().lower()


def extract_executable_token(command: str) -> str:
    raw = str(command or "").strip()
    if not raw:
        return ""
    try:
        tokens = shlex.split(raw, posix=True)
    except Exception:
        tokens = raw.split()
    if not tokens:
        return ""

    i = 0
    n = len(tokens)
    while i < n:
        token = (tokens[i] or "").strip()
        if not token:
            i += 1
            continue
        if token in {"sudo", "command"}:
            i += 1
            continue
        if token in {"env", "/usr/bin/env"}:
            i += 1
            while i < n:
                env_token = (tokens[i] or "").strip()
                if (
                    not env_token
                    or env_token.startswith("-")
                    or ("=" in env_token and not env_token.startswith("="))
                ):
                    i += 1
                    continue
                break
            continue
        if token.startswith("-"):
            i += 1
            continue
        if "=" in token and not token.startswith("="):
            i += 1
            continue
        return os.path.basename(token).strip().lower()
    return ""


def sanitize_patterns(values) -> list[str]:
    if not isinstance(values, list):
        return []
    clean: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = normalize_command_pattern(str(value))
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        clean.append(normalized)
    return clean


def command_matches_pattern(command: str, patterns: list[str]) -> bool:
    executable = extract_executable_token(command)
    if not executable:
        return False
    for pattern in patterns:
        if executable.startswith(pattern) or pattern.startswith(executable):
            return True
    return False


def token_has_short_flag(token: str, flag: str) -> bool:
    value = str(token or "").strip().lower()
    short = str(flag or "").strip().lower()
    if not value or not short:
        return False
    if value.startswith("--"):
        return False
    if value == f"-{short}":
        return True
    if value.startswith("-") and len(value) > 2:
        return short in value[1:]
    return False


def tokenize_command(command: str) -> list[str]:
    raw = str(command or "").strip()
    if not raw:
        return []
    try:
        return [str(token or "") for token in shlex.split(raw, posix=True)]
    except Exception:
        return [part for part in raw.split() if part]


def extract_executable_with_index(tokens: list[str]) -> tuple[str, int]:
    if not isinstance(tokens, list):
        return ("", -1)
    i = 0
    n = len(tokens)
    while i < n:
        token = str(tokens[i] or "").strip()
        if not token:
            i += 1
            continue
        if token in {"sudo", "command"}:
            i += 1
            continue
        if token in {"env", "/usr/bin/env"}:
            i += 1
            while i < n:
                env_token = str(tokens[i] or "").strip()
                if (
                    not env_token
                    or env_token.startswith("-")
                    or ("=" in env_token and not env_token.startswith("="))
                ):
                    i += 1
                    continue
                break
            continue
        if token.startswith("-"):
            i += 1
            continue
        if "=" in token and not token.startswith("="):
            i += 1
            continue
        return (token, i)
    return ("", -1)


def history_clears_state(args: list[str]) -> bool:
    for raw in args:
        token = str(raw or "").strip().lower()
        if not token:
            continue
        if token == "--clear":
            return True
        if token_has_short_flag(token, "c"):
            return True
    return False


def extract_git_subcommand(args: list[str]) -> tuple[str, list[str]]:
    i = 0
    n = len(args)
    while i < n:
        token = str(args[i] or "").strip()
        if not token:
            i += 1
            continue

        if token == "--":
            i += 1
            break

        if token in GIT_GLOBAL_OPTIONS_WITH_VALUE:
            i += 2
            continue

        if token.startswith(
            (
                "--exec-path=",
                "--git-dir=",
                "--work-tree=",
                "--namespace=",
                "--super-prefix=",
                "--config-env=",
            )
        ):
            i += 1
            continue

        if token.startswith("-C") and token != "-C":
            i += 1
            continue
        if token.startswith("-c") and token != "-c":
            i += 1
            continue

        if token.startswith("-"):
            i += 1
            continue

        subcommand = token.lower()
        remaining = []
        for value in args[i + 1 :]:
            clean = str(value or "").strip().lower()
            if clean:
                remaining.append(clean)
        return (subcommand, remaining)

    return ("", [])


def is_git_destructive_subcommand(args: list[str]) -> bool:
    subcommand, remaining = extract_git_subcommand(args)
    if not subcommand:
        return False

    if subcommand == "reset":
        return "--hard" in remaining

    if subcommand == "clean":
        for token in remaining:
            if token == "--force" or token.startswith("--force="):
                return True
            if token_has_short_flag(token, "f"):
                return True

    return False


def is_blocked_command(command: str) -> bool:
    tokens = tokenize_command(command)
    executable, executable_index = extract_executable_with_index(tokens)
    if not executable:
        return False

    executable_name = os.path.basename(executable).strip().lower()
    if executable_name in BLOCKED_EXECUTABLES:
        return True
    if any(executable_name.startswith(prefix) for prefix in BLOCKED_EXECUTABLE_PREFIXES):
        return True

    args = tokens[executable_index + 1 :] if executable_index >= 0 else []
    if executable_name == "history" and history_clears_state(args):
        return True
    if executable_name == "git" and is_git_destructive_subcommand(args):
        return True
    return False
