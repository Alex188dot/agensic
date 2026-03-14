import os
import shlex
import sys
import time
import uuid
from pathlib import Path

from agensic.paths import ensure_app_layout, get_app_paths

AI_SESSION_ENV_KEYS = (
    "AGENSIC_AI_SESSION_ACTIVE",
    "AGENSIC_AI_SESSION_AGENT",
    "AGENSIC_AI_SESSION_MODEL",
    "AGENSIC_AI_SESSION_AGENT_NAME",
    "AGENSIC_AI_SESSION_ID",
    "AGENSIC_AI_SESSION_STARTED_TS",
    "AGENSIC_AI_SESSION_EXPIRES_TS",
    "AGENSIC_AI_SESSION_COUNTER",
    "AGENSIC_AI_SESSION_TIMER_PID",
    "AGENSIC_AI_SESSION_OWNER_SHELL_PID",
)


def _shell_export_line(name: str, value: str) -> str:
    return f"export {name}={shlex.quote(str(value or ''))}"


def _write_ai_session_state(values: dict[str, str]) -> None:
    ensure_app_layout()
    state_path = Path(get_app_paths().ai_session_state_path)
    state_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lines = [f"{key}\t{str(values.get(key, '') or '')}" for key in AI_SESSION_ENV_KEYS]
    payload = "\n".join(lines) + "\n"
    tmp_path = state_path.with_suffix(f"{state_path.suffix}.tmp")
    tmp_path.write_text(payload, encoding="utf-8")
    os.chmod(tmp_path, 0o600)
    os.replace(tmp_path, state_path)


def _clear_ai_session_state() -> None:
    try:
        Path(get_app_paths().ai_session_state_path).unlink()
    except FileNotFoundError:
        return


def _current_ai_session_owner_shell_pid() -> str:
    raw = str(os.environ.get("AGENSIC_SHELL_PID", "") or "").strip()
    if raw.isdigit():
        return raw
    parent_pid = int(os.getppid() or 0)
    return str(parent_pid) if parent_pid > 0 else ""


def _normalize_signing_identity(agent: str, model: str) -> tuple[str, str, bool]:
    clean_agent = str(agent or "").strip().lower()
    clean_model = str(model or "").strip()
    defaulted = False
    if not clean_agent:
        clean_agent = "unknown"
        defaulted = True
    if not clean_model:
        clean_model = "unknown-model"
        defaulted = True
    return clean_agent, clean_model, defaulted


def _parse_ai_session_options(args: list[str]) -> tuple[dict[str, str], int]:
    values = {"agent": "", "model": "", "agent_name": "", "ttl_minutes": "120"}
    idx = 0
    while idx < len(args):
        arg = args[idx]
        if arg in {"-h", "--help"}:
            return values, 0
        if arg not in {"--agent", "--model", "--agent-name", "--ttl-minutes"}:
            print(f"agensic ai-session: unknown option: {arg}", file=sys.stderr)
            return values, 2
        idx += 1
        if idx >= len(args):
            print(f"agensic ai-session: missing value for {arg}", file=sys.stderr)
            return values, 2
        if arg == "--agent":
            values["agent"] = args[idx]
        elif arg == "--model":
            values["model"] = args[idx]
        elif arg == "--agent-name":
            values["agent_name"] = args[idx]
        else:
            values["ttl_minutes"] = args[idx]
        idx += 1
    return values, -1


def _run_ai_session_bootstrap(argv: list[str]) -> int:
    print("ai-session is no longer supported. Use `agensic run <agent>`.", file=sys.stderr)
    return 2


if len(sys.argv) > 1 and sys.argv[1] in {"ai-session", "session"}:
    raise SystemExit(_run_ai_session_bootstrap(sys.argv[2:]))
if len(sys.argv) > 1 and sys.argv[1] in {"ai-exec", "wrap"}:
    print(f"{sys.argv[1]} has been removed. Use `agensic run <agent>`.", file=sys.stderr)
    raise SystemExit(2)

from agensic.cli.app import app, main, show_shortcuts

__all__ = [
    "app",
    "main",
    "show_shortcuts",
]

if __name__ == "__main__":
    app()
