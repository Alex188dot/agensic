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
    if not argv or argv[0] in {"-h", "--help"}:
        print("Usage: cli.py ai-session [OPTIONS] COMMAND [ARGS]...")
        print()
        print("Manage AI session signing context")
        print()
        print("Commands:")
        print("  start")
        print("  stop")
        print("  status")
        return 0

    command = argv[0]
    if command == "start":
        values, parse_code = _parse_ai_session_options(argv[1:])
        if parse_code == 0:
            print("usage: cli.py ai-session start [--agent <agent>] [--model <model>] [--agent-name <name>] [--ttl-minutes <1-1440>]")
            return 0
        if parse_code > 0:
            return parse_code
        clean_agent, clean_model, defaulted = _normalize_signing_identity(values["agent"], values["model"])
        if defaulted:
            print(
                "Warning: ai-session start missing identity; defaulting to agent=unknown model=unknown-model"
            )
        ttl_raw = str(values["ttl_minutes"] or "120").strip()
        if not ttl_raw.isdigit() or not (1 <= int(ttl_raw) <= 1440):
            print("agensic ai-session: --ttl-minutes must be an integer between 1 and 1440", file=sys.stderr)
            return 2
        now_ts = int(time.time())
        expires_ts = now_ts + int(ttl_raw) * 60
        session_id = uuid.uuid4().hex[:16]
        state_values = {
            "AGENSIC_AI_SESSION_ACTIVE": "1",
            "AGENSIC_AI_SESSION_AGENT": clean_agent,
            "AGENSIC_AI_SESSION_MODEL": clean_model,
            "AGENSIC_AI_SESSION_AGENT_NAME": str(values["agent_name"] or "").strip(),
            "AGENSIC_AI_SESSION_ID": session_id,
            "AGENSIC_AI_SESSION_STARTED_TS": str(now_ts),
            "AGENSIC_AI_SESSION_EXPIRES_TS": str(expires_ts),
            "AGENSIC_AI_SESSION_COUNTER": "0",
            "AGENSIC_AI_SESSION_TIMER_PID": "",
            "AGENSIC_AI_SESSION_OWNER_SHELL_PID": _current_ai_session_owner_shell_pid(),
        }
        _write_ai_session_state(state_values)
        print("\n".join(_shell_export_line(key, state_values.get(key, "")) for key in AI_SESSION_ENV_KEYS))
        return 0

    if command == "stop":
        _clear_ai_session_state()
        print("\n".join(f"unset {name}" for name in AI_SESSION_ENV_KEYS))
        return 0

    if command == "status":
        state_path = Path(get_app_paths().ai_session_state_path)
        if not state_path.exists():
            print("inactive")
            return 0
        values: dict[str, str] = {}
        for raw_line in state_path.read_text(encoding="utf-8").splitlines():
            if "\t" not in raw_line:
                continue
            key, value = raw_line.split("\t", 1)
            if key in AI_SESSION_ENV_KEYS:
                values[key] = value
        active = str(values.get("AGENSIC_AI_SESSION_ACTIVE", "") or "").strip() == "1"
        if not active:
            print("inactive")
            return 0
        now_ts = int(time.time())
        expires_ts = int(str(values.get("AGENSIC_AI_SESSION_EXPIRES_TS", "0") or "0") or "0")
        remaining = max(0, expires_ts - now_ts) if expires_ts > 0 else 0
        agent = str(values.get("AGENSIC_AI_SESSION_AGENT", "") or "").strip()
        model = str(values.get("AGENSIC_AI_SESSION_MODEL", "") or "").strip()
        session_id = str(values.get("AGENSIC_AI_SESSION_ID", "") or "").strip()
        name = str(values.get("AGENSIC_AI_SESSION_AGENT_NAME", "") or "").strip()
        print(
            f"active agent={agent} model={model} agent_name={name or '-'} session_id={session_id or '-'} remaining_seconds={remaining}"
        )
        return 0

    print(f"Unknown ai-session command: {command}", file=sys.stderr)
    return 2


if len(sys.argv) > 1 and sys.argv[1] in {"ai-session", "session"}:
    raise SystemExit(_run_ai_session_bootstrap(sys.argv[2:]))

from agensic.cli.app import app, main, show_shortcuts

__all__ = [
    "app",
    "main",
    "show_shortcuts",
]

if __name__ == "__main__":
    app()
