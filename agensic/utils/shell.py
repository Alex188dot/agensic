import os
import shlex


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
