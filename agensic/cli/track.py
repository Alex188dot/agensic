import base64
import errno
import fcntl
import json
import os
import select
import shlex
import signal
import struct
import subprocess
import sys
import termios
import threading
import time
import tomllib
import tty
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psutil
from rich.console import Console

from agensic.engine.provenance import (
    build_local_proof_metadata,
    classify_command_run,
    get_agent_registry,
    sign_proof_payload,
)
from agensic.paths import APP_PATHS, ensure_app_layout, migrate_legacy_layout
from agensic.state.sqlite_store import SQLiteStateStore
from agensic.utils import atomic_write_json_private


console = Console()
TRACK_POLL_INTERVAL_SECONDS = 0.01
TRACK_FINAL_POLL_GRACE_SECONDS = 0.25
TRACK_STOP_GRACE_SECONDS = 2.0
TRACK_INSPECT_TAIL_EVENTS = 8
TRACK_TRANSCRIPT_RETENTION_SECONDS = 7 * 24 * 3600
TRACK_TRANSCRIPT_MAX_TOTAL_BYTES = 1024 * 1024 * 1024
TRACK_POLICY_CHILD_KILL_GRACE_SECONDS = 0.2
TRACK_UNMANAGED_WINDOW_TOKENS = (
    "terminal.app",
    "iterm.app",
    "warp.app",
    "ghostty",
)
TRACK_ESCAPE_PRIMITIVE_TOKENS = (
    "nohup ",
    " disown",
    " disown;",
    " disown&",
    "setsid ",
    "launchctl ",
)
TRACK_TTY_RESET_SEQ = "\x1b[?1000l\x1b[?1002l\x1b[?1003l\x1b[?1004l\x1b[?1006l\x1b[?1015l"


@dataclass
class TrackLaunch:
    command: list[str]
    launch_mode: str
    agent: str
    model: str
    agent_name: str
    working_directory: str
    root_command: str


@dataclass
class ObservedProcess:
    pid: int
    ppid: int
    command: str
    working_directory: str
    started_at: float
    session_id: int = 0
    process_group_id: int = 0
    detached: bool = False
    session_escape: bool = False
    finalized: bool = False


def _track_session_state_path() -> str:
    return os.path.join(APP_PATHS.state_dir, "track_session.json")


def _track_transcripts_dir() -> str:
    return os.path.join(APP_PATHS.state_dir, "tracked_sessions")


def _state_store() -> SQLiteStateStore:
    return SQLiteStateStore(APP_PATHS.state_sqlite_path, journal=None)


def _track_policy_dir(session_id: str) -> str:
    return os.path.join(APP_PATHS.state_dir, "track_policy", str(session_id or "").strip())


def _track_private_key_path() -> str:
    return APP_PATHS.provenance_private_key_path


def _track_public_key_path() -> str:
    return APP_PATHS.provenance_public_key_path


def _policy_violation_message(code: str) -> str:
    clean = str(code or "").strip().lower()
    if clean == "escape_primitive_blocked":
        return "agensic track policy: blocked escape attempt; offending process terminated."
    if clean == "unmanaged_child_launch":
        return "agensic track policy: blocked unmanaged terminal launch."
    if clean == "session_boundary_escape":
        return "agensic track policy: blocked child process that left the tracked session boundary."
    if clean == "detached_descendants":
        return "agensic track policy: blocked detached child process."
    return "agensic track policy: blocked policy violation."


def ensure_track_supported() -> None:
    if sys.platform != "darwin":
        raise RuntimeError("agensic track is currently supported on macOS only.")


def _ensure_track_layout() -> None:
    migrate_legacy_layout()
    ensure_app_layout()
    os.makedirs(_track_transcripts_dir(), mode=0o700, exist_ok=True)


def _is_pid_alive(pid: int) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def _safe_getsid(pid: int) -> int:
    target_pid = int(pid or 0)
    if target_pid <= 0:
        return 0
    try:
        return int(os.getsid(target_pid))
    except Exception:
        return 0


def _safe_getpgid(pid: int) -> int:
    target_pid = int(pid or 0)
    if target_pid <= 0:
        return 0
    try:
        return int(os.getpgid(target_pid))
    except Exception:
        return 0


def _load_track_state() -> dict[str, Any]:
    path = Path(_track_session_state_path())
    if not path.exists() or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_track_state(payload: dict[str, Any]) -> None:
    atomic_write_json_private(
        _track_session_state_path(),
        payload,
        indent=2,
        sort_keys=True,
    )


def _clear_track_state() -> None:
    try:
        Path(_track_session_state_path()).unlink()
    except FileNotFoundError:
        return


def _prune_tracked_transcripts(*, exclude_paths: set[str] | None = None) -> dict[str, int]:
    transcript_dir = Path(_track_transcripts_dir())
    if not transcript_dir.exists() or not transcript_dir.is_dir():
        return {"removed_files": 0, "removed_bytes": 0}

    excluded = {
        str(Path(path).expanduser().resolve(strict=False))
        for path in (exclude_paths or set())
        if str(path or "").strip()
    }
    now = int(time.time())
    removed = 0
    removed_bytes = 0

    candidates: list[tuple[float, Path]] = []
    for transcript_path in transcript_dir.glob("*.jsonl"):
        if not transcript_path.is_file():
            continue
        try:
            stat = transcript_path.stat()
        except OSError:
            continue
        candidates.append((float(stat.st_mtime), transcript_path))

    candidates.sort(key=lambda item: (item[0], item[1].name))

    for _, transcript_path in candidates:
        resolved = str(transcript_path.resolve(strict=False))
        if resolved in excluded:
            continue
        try:
            stat = transcript_path.stat()
        except OSError:
            continue
        if now - int(stat.st_mtime) <= TRACK_TRANSCRIPT_RETENTION_SECONDS:
            continue
        try:
            transcript_path.unlink(missing_ok=True)
            removed += 1
            removed_bytes += int(stat.st_size)
        except OSError:
            continue

    remaining: list[tuple[float, Path, int]] = []
    total_size = 0
    for transcript_path in transcript_dir.glob("*.jsonl"):
        if not transcript_path.is_file():
            continue
        resolved = str(transcript_path.resolve(strict=False))
        if resolved in excluded:
            continue
        try:
            stat = transcript_path.stat()
        except OSError:
            continue
        size = int(stat.st_size)
        total_size += size
        remaining.append((float(stat.st_mtime), transcript_path, size))

    remaining.sort(key=lambda item: (item[0], item[1].name))
    for _, transcript_path, size in remaining:
        if total_size <= TRACK_TRANSCRIPT_MAX_TOTAL_BYTES:
            break
        try:
            transcript_path.unlink(missing_ok=True)
            removed += 1
            removed_bytes += size
            total_size -= size
        except OSError:
            continue

    return {"removed_files": removed, "removed_bytes": removed_bytes}


def _session_cache_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": str(row.get("session_id", "") or "").strip(),
        "status": str(row.get("status", "") or "").strip().lower(),
        "launch_mode": str(row.get("launch_mode", "") or "").strip().lower(),
        "agent": str(row.get("agent", "") or "").strip().lower(),
        "model": str(row.get("model", "") or "").strip(),
        "agent_name": str(row.get("agent_name", "") or "").strip(),
        "working_directory": str(row.get("working_directory", "") or "").strip(),
        "root_command": str(row.get("root_command", "") or "").strip(),
        "transcript_path": str(row.get("transcript_path", "") or "").strip(),
        "controller_pid": int(row.get("controller_pid", 0) or 0),
        "root_pid": int(row.get("root_pid", 0) or 0),
        "started_at": int(row.get("started_at", 0) or 0),
        "ended_at": int(row.get("ended_at", 0) or 0),
        "updated_at": int(row.get("updated_at", 0) or 0),
        "violation_code": str(row.get("violation_code", "") or "").strip().lower(),
        "exit_code": row.get("exit_code"),
    }


def _session_status_payload(payload: dict[str, Any], *, status: str, violation_code: str = "", exit_code: int | None = None) -> dict[str, Any]:
    out = dict(payload)
    out["status"] = str(status or "").strip().lower()
    out["updated_at"] = int(time.time())
    if violation_code:
        out["violation_code"] = str(violation_code or "").strip().lower()
    if exit_code is not None:
        out["exit_code"] = int(exit_code)
    if out["status"] not in {"active", "stopping"}:
        out["ended_at"] = int(time.time())
    return out


def _mark_tracked_session_errored(state: dict[str, Any], violation_code: str) -> None:
    session_id = str(state.get("session_id", "") or "").strip()
    if not session_id:
        return
    try:
        _state_store().upsert_tracked_session(
            session_id=session_id,
            status="errored",
            launch_mode=str(state.get("launch_mode", "") or ""),
            agent=str(state.get("agent", "") or ""),
            model=str(state.get("model", "") or ""),
            agent_name=str(state.get("agent_name", "") or ""),
            working_directory=str(state.get("working_directory", "") or ""),
            root_command=str(state.get("root_command", "") or ""),
            transcript_path=str(state.get("transcript_path", "") or ""),
            controller_pid=int(state.get("controller_pid", 0) or 0) or None,
            root_pid=int(state.get("root_pid", 0) or 0) or None,
            started_at=int(state.get("started_at", 0) or 0),
            ended_at=int(time.time()),
            updated_at=int(time.time()),
            violation_code=str(violation_code or "stale_session"),
            exit_code=state.get("exit_code"),
        )
    except Exception:
        return


def _cleanup_stale_track_state() -> dict[str, Any]:
    cached_state = _load_track_state()
    active = _state_store().get_active_tracked_session()
    if active is None:
        if cached_state:
            _clear_track_state()
        return {}

    state = _session_cache_payload(active)
    status = str(state.get("status", "") or "").strip().lower()
    controller_pid = int(state.get("controller_pid", 0) or 0)
    root_pid = int(state.get("root_pid", 0) or 0)
    controller_alive = controller_pid > 0 and _is_pid_alive(controller_pid)
    root_alive = root_pid > 0 and _is_pid_alive(root_pid)
    if status in {"active", "stopping"} and (controller_alive or root_alive):
        if cached_state != state:
            _write_track_state(state)
        return state

    _mark_tracked_session_errored(state, str(state.get("violation_code", "") or "stale_session"))
    _clear_track_state()
    return {}


def get_active_track_state() -> dict[str, Any]:
    return _cleanup_stale_track_state()


def get_latest_track_session(session_id: str = "") -> dict[str, Any]:
    clean_session_id = str(session_id or "").strip()
    if clean_session_id:
        row = _state_store().get_tracked_session(clean_session_id)
    else:
        row = get_active_track_state() or _state_store().get_latest_tracked_session()
    return _session_cache_payload(dict(row or {})) if row else {}


def _find_registry_descriptor(token: str) -> dict[str, Any] | None:
    registry = get_agent_registry(force_reload=False)
    direct = registry.get_agent(token)
    if direct is not None:
        return direct
    clean = str(token or "").strip().lower()
    if not clean:
        return None
    for agent in registry.list_agents():
        executables = [str(item or "").strip().lower() for item in agent.get("executables", []) if str(item or "").strip()]
        if clean in executables:
            return agent
    return None


def _looks_like_codex_launch(*, command: list[str], agent: str = "") -> bool:
    clean_agent = str(agent or "").strip().lower()
    executable = os.path.basename(str((command or [""])[0] or "").strip()).lower()
    return clean_agent == "codex" or executable == "codex"


def _resolve_codex_home(env: dict[str, str] | None = None) -> Path:
    source_env = env or os.environ
    raw = str(source_env.get("CODEX_HOME", "") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".codex"


def _resolve_home(env: dict[str, str] | None = None) -> Path:
    source_env = env or os.environ
    raw = str(source_env.get("HOME", "") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home()


def _load_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_string_path(payload: Any, *path: str) -> str:
    current = payload
    for key in path:
        if not isinstance(current, dict):
            return ""
        current = current.get(key)
    return str(current or "").strip() if isinstance(current, str) else ""


def _first_string_in_collection(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        for item in value:
            found = _first_string_in_collection(item)
            if found:
                return found
    if isinstance(value, dict):
        for item in value.values():
            found = _first_string_in_collection(item)
            if found:
                return found
    return ""


def _find_upward(start_dir: Path, *parts: str) -> Path | None:
    current = start_dir.expanduser().resolve()
    for candidate_root in (current, *current.parents):
        candidate = candidate_root.joinpath(*parts)
        if candidate.is_file():
            return candidate
    return None


def _infer_codex_model(env: dict[str, str] | None = None) -> str:
    config_path = _resolve_codex_home(env) / "config.toml"
    if not config_path.is_file():
        return ""
    try:
        payload = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    model = payload.get("model")
    return str(model or "").strip()


def _infer_gemini_model(env: dict[str, str] | None = None, cwd: str | None = None) -> str:
    search_root = Path(cwd or os.getcwd())
    candidates: list[Path] = []
    workspace_path = _find_upward(search_root, ".gemini", "settings.json")
    if workspace_path is not None:
        candidates.append(workspace_path)
    candidates.append(_resolve_home(env) / ".gemini" / "settings.json")

    for path in candidates:
        if not path.is_file():
            continue
        payload = _load_json_file(path)
        model = _read_string_path(payload, "model", "name") or _read_string_path(payload, "model")
        if model:
            return model
    return ""


def _infer_claude_code_model(env: dict[str, str] | None = None, cwd: str | None = None) -> str:
    search_root = Path(cwd or os.getcwd())
    candidates: list[Path] = []
    for filename in ("settings.local.json", "settings.json"):
        workspace_path = _find_upward(search_root, ".claude", filename)
        if workspace_path is not None:
            candidates.append(workspace_path)
    home_dir = _resolve_home(env) / ".claude"
    candidates.extend([home_dir / "settings.local.json", home_dir / "settings.json"])

    for path in candidates:
        if not path.is_file():
            continue
        payload = _load_json_file(path)
        model = (
            _read_string_path(payload, "model")
            or _read_string_path(payload, "model", "name")
            or _read_string_path(payload, "env", "ANTHROPIC_MODEL")
            or _read_string_path(payload, "env", "CLAUDE_CODE_MODEL")
        )
        if model:
            return model
    return ""


def _infer_ollama_model(env: dict[str, str] | None = None) -> str:
    config_path = _resolve_home(env) / ".ollama" / "config" / "config.json"
    if not config_path.is_file():
        return ""
    payload = _load_json_file(config_path)
    return (
        _read_string_path(payload, "model")
        or _read_string_path(payload, "defaultModel")
        or _read_string_path(payload, "cli", "model")
        or _read_string_path(payload, "cli", "defaultModel")
        or _read_string_path(payload, "default", "model")
        or _first_string_in_collection(payload)
    )


def _resolve_openclaw_state_dir(env: dict[str, str] | None = None) -> Path:
    source_env = env or os.environ
    explicit_state_dir = str(source_env.get("OPENCLAW_STATE_DIR", "") or "").strip()
    if explicit_state_dir:
        return Path(explicit_state_dir).expanduser()
    explicit_config_path = str(source_env.get("OPENCLAW_CONFIG_PATH", "") or "").strip()
    if explicit_config_path:
        return Path(explicit_config_path).expanduser().parent
    return _resolve_home(env) / ".openclaw"


def _infer_openclaw_model(env: dict[str, str] | None = None) -> str:
    state_dir = _resolve_openclaw_state_dir(env)
    config_path = state_dir / "openclaw.json"
    if config_path.is_file():
        payload = _load_json_file(config_path)
        model = (
            _read_string_path(payload, "agents", "defaults", "model", "primary")
            or _read_string_path(payload, "agents", "defaults", "model")
        )
        if model:
            return model

    agent_models_path = state_dir / "agents" / "main" / "agent" / "models.json"
    if not agent_models_path.is_file():
        return ""
    payload = _load_json_file(agent_models_path)
    providers = payload.get("providers", {}) if isinstance(payload, dict) else {}
    if not isinstance(providers, dict):
        return ""
    for provider_id, provider_payload in providers.items():
        if not isinstance(provider_payload, dict):
            continue
        models = provider_payload.get("models", [])
        if not isinstance(models, list):
            continue
        for model_entry in models:
            if not isinstance(model_entry, dict):
                continue
            model_id = str(model_entry.get("id", "") or "").strip()
            if model_id:
                clean_provider = str(provider_id or "").strip()
                return f"{clean_provider}/{model_id}" if clean_provider else model_id
    return ""


def _infer_inline_track_model(command: list[str]) -> str:
    if not command:
        return ""
    command_text = shlex.join(command)
    registry = get_agent_registry(force_reload=False)
    model_meta = registry.extract_model_provider_from_command(command_text)
    inline_model = str(model_meta.get("model_raw", "") or "").strip()
    if inline_model:
        return inline_model

    executable = os.path.basename(str(command[0] or "").strip()).lower()
    if executable == "ollama" and len(command) >= 3:
        subcommand = str(command[1] or "").strip().lower()
        if subcommand in {"run", "chat", "show", "pull", "push", "create", "cp", "rm"}:
            return str(command[2] or "").strip()
    return ""


def _infer_track_model(*, command: list[str], agent: str, env: dict[str, str] | None = None) -> str:
    inline_model = _infer_inline_track_model(command)
    if inline_model:
        return inline_model

    clean_agent = str(agent or "").strip().lower()
    executable = os.path.basename(str((command or [""])[0] or "").strip()).lower()
    if _looks_like_codex_launch(command=command, agent=agent):
        return _infer_codex_model(env)
    if clean_agent in {"gemini", "gemini_cli"} or executable == "gemini":
        return _infer_gemini_model(env, cwd=os.getcwd())
    if clean_agent in {"claude", "claude_code"} or executable == "claude":
        return _infer_claude_code_model(env, cwd=os.getcwd())
    if clean_agent == "openclaw" or executable == "openclaw":
        return _infer_openclaw_model(env)
    if clean_agent == "ollama" or executable == "ollama":
        return _infer_ollama_model(env)
    return ""


def _build_tracked_child_env(launch: TrackLaunch, session_id: str) -> dict[str, str]:
    env = os.environ.copy()
    policy_dir = _write_track_policy_files(session_id)
    env["AGENSIC_TRACK_ACTIVE"] = "1"
    env["AGENSIC_TRACK_SESSION_ID"] = session_id
    env["AGENSIC_TRACK_AGENT"] = launch.agent
    env["AGENSIC_TRACK_MODEL"] = launch.model
    env["AGENSIC_TRACK_AGENT_NAME"] = launch.agent_name
    env["AGENSIC_TRACK_LAUNCH_MODE"] = launch.launch_mode
    env["AGENSIC_TRACK_POLICY_DIR"] = policy_dir
    env["BASH_ENV"] = os.path.join(policy_dir, "bash_env.sh")
    env["ZDOTDIR"] = policy_dir
    return env


def _write_track_policy_files(session_id: str) -> str:
    policy_dir = Path(_track_policy_dir(session_id))
    policy_dir.mkdir(parents=True, exist_ok=True)

    zsh_policy = policy_dir / ".zshenv"
    bash_policy = policy_dir / "bash_env.sh"

    zsh_policy.write_text(
        """#!/bin/zsh
if [[ -r "$HOME/.zshenv" ]]; then
  source "$HOME/.zshenv"
fi
_agensic_track_deny() {
  print -u2 -- "agensic track policy: blocked: $1"
  return 126
}
nohup() {
  _agensic_track_deny "nohup is blocked inside agensic track"
}
disown() {
  _agensic_track_deny "disown is blocked inside agensic track"
}
open() {
  local joined="${(L)*}"
  if [[ "$joined" == *"terminal"* || "$joined" == *"iterm"* || "$joined" == *"warp"* || "$joined" == *"ghostty"* ]]; then
    _agensic_track_deny "terminal launch is blocked inside agensic track"
    return 126
  fi
  command open "$@"
}
osascript() {
  local joined="${(L)*}"
  if [[ "$joined" == *"tell application \\"terminal\\""* || "$joined" == *"tell application \\"iterm\\""* || "$joined" == *"tell application \\"warp\\""* || "$joined" == *"do script"* ]]; then
    _agensic_track_deny "terminal automation is blocked inside agensic track"
    return 126
  fi
  command osascript "$@"
}
launchctl() {
  _agensic_track_deny "launchctl is blocked inside agensic track"
}
""",
        encoding="utf-8",
    )
    bash_policy.write_text(
        """#!/usr/bin/env bash
_agensic_track_deny() {
  printf '%s\\n' "agensic track policy: blocked: $1" >&2
  return 126
}
nohup() {
  _agensic_track_deny "nohup is blocked inside agensic track"
}
disown() {
  _agensic_track_deny "disown is blocked inside agensic track"
}
open() {
  local joined="${*,,}"
  if [[ "$joined" == *terminal* || "$joined" == *iterm* || "$joined" == *warp* || "$joined" == *ghostty* ]]; then
    _agensic_track_deny "terminal launch is blocked inside agensic track"
    return 126
  fi
  command open "$@"
}
osascript() {
  local joined="${*,,}"
  if [[ "$joined" == *'tell application "terminal"'* || "$joined" == *'tell application "iterm"'* || "$joined" == *'tell application "warp"'* || "$joined" == *'do script'* ]]; then
    _agensic_track_deny "terminal automation is blocked inside agensic track"
    return 126
  fi
  command osascript "$@"
}
launchctl() {
  _agensic_track_deny "launchctl is blocked inside agensic track"
}
""",
        encoding="utf-8",
    )
    zsh_policy.chmod(0o700)
    bash_policy.chmod(0o700)
    return str(policy_dir)


def prepare_track_launch(
    raw_args: list[str],
    *,
    agent_override: str = "",
    model_override: str = "",
    agent_name_override: str = "",
) -> TrackLaunch:
    args = list(raw_args or [])
    if not args:
        raise ValueError("No app or command provided.")

    clean_agent_override = str(agent_override or "").strip().lower()
    clean_model_override = str(model_override or "").strip()
    clean_agent_name_override = str(agent_name_override or "").strip()
    working_directory = os.getcwd()

    if args[0] == "--":
        command = args[1:]
        if not command:
            raise ValueError("No command provided after '--'.")
        descriptor = _find_registry_descriptor(os.path.basename(str(command[0] or "").strip()))
        inferred_agent = str((descriptor or {}).get("agent_id", "") or "").strip().lower()
        inferred_name = str((descriptor or {}).get("display_name", "") or "").strip()
        resolved_agent = clean_agent_override or inferred_agent or "unknown"
        return TrackLaunch(
            command=command,
            launch_mode="raw_command",
            agent=resolved_agent,
            model=clean_model_override or _infer_track_model(command=command, agent=resolved_agent) or "unknown-model",
            agent_name=clean_agent_name_override or inferred_name,
            working_directory=working_directory,
            root_command=shlex.join(command),
        )

    descriptor = _find_registry_descriptor(args[0])
    if descriptor is not None:
        executables = [str(item or "").strip() for item in descriptor.get("executables", []) if str(item or "").strip()]
        executable = executables[0] if executables else str(args[0] or "").strip()
        command = [executable, *args[1:]]
        resolved_agent = clean_agent_override or str(descriptor.get("agent_id", "") or "").strip().lower() or "unknown"
        return TrackLaunch(
            command=command,
            launch_mode="registry_alias",
            agent=resolved_agent,
            model=clean_model_override or _infer_track_model(command=command, agent=resolved_agent) or "unknown-model",
            agent_name=clean_agent_name_override or str(descriptor.get("display_name", "") or "").strip(),
            working_directory=working_directory,
            root_command=shlex.join(command),
        )

    descriptor = _find_registry_descriptor(os.path.basename(str(args[0] or "").strip()))
    inferred_agent = str((descriptor or {}).get("agent_id", "") or "").strip().lower()
    inferred_name = str((descriptor or {}).get("display_name", "") or "").strip()
    resolved_agent = clean_agent_override or inferred_agent or "unknown"
    return TrackLaunch(
        command=args,
        launch_mode="raw_command",
        agent=resolved_agent,
        model=clean_model_override or _infer_track_model(command=args, agent=resolved_agent) or "unknown-model",
        agent_name=clean_agent_name_override or inferred_name,
        working_directory=working_directory,
        root_command=shlex.join(args),
    )


def _format_ts(ts_value: int) -> str:
    ts = int(ts_value or 0)
    if ts <= 0:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def _format_debug_preview(data: bytes, limit: int = 120) -> str:
    text = data.decode("utf-8", errors="replace").replace("\r", "\\r").replace("\n", "\\n")
    return text[:limit] + ("..." if len(text) > limit else "")


def _load_transcript_events(path: str) -> list[dict[str, Any]]:
    target = Path(path).expanduser()
    if not target.is_file():
        return []
    events: list[dict[str, Any]] = []
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    for line in lines:
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        direction = str(payload.get("direction", "") or "").strip()
        data_b64 = str(payload.get("data_b64", "") or "").strip()
        try:
            data = base64.b64decode(data_b64.encode("ascii"), validate=True)
        except Exception:
            data = b""
        events.append(
            {
                "ts": float(payload.get("ts", 0.0) or 0.0),
                "direction": direction,
                "data": data,
            }
        )
    return events


def _find_session_runs(session_id: str, limit: int = 12) -> list[dict[str, Any]]:
    clean_session_id = str(session_id or "").strip()
    if not clean_session_id:
        return []
    rows = _state_store().list_command_runs(limit=200)
    out: list[dict[str, Any]] = []
    for row in rows:
        payload = dict(row.get("payload", {}) or {})
        if str(payload.get("track_session_id", "") or "").strip() != clean_session_id:
            continue
        out.append(row)
        if len(out) >= max(1, int(limit or 12)):
            break
    return out


def print_track_status() -> int:
    state = get_active_track_state()
    if not state:
        console.print("inactive")
        return 0
    console.print(
        "status={status} session_id={session_id} agent={agent} model={model} "
        "agent_name={agent_name} root_pid={root_pid} controller_pid={controller_pid} "
        "started_at={started_at} violation={violation}".format(
            status=str(state.get("status", "") or "inactive"),
            session_id=str(state.get("session_id", "") or "-"),
            agent=str(state.get("agent", "") or "-"),
            model=str(state.get("model", "") or "-"),
            agent_name=str(state.get("agent_name", "") or "-") or "-",
            root_pid=str(state.get("root_pid", "") or "-"),
            controller_pid=str(state.get("controller_pid", "") or "-"),
            started_at=_format_ts(int(state.get("started_at", 0) or 0)),
            violation=str(state.get("violation_code", "") or "-"),
        ),
        highlight=False,
    )
    transcript_path = str(state.get("transcript_path", "") or "").strip()
    if transcript_path:
        console.print(f"transcript={transcript_path}", highlight=False)
    return 0


def stop_active_track_session() -> int:
    state = get_active_track_state()
    if not state:
        console.print("inactive")
        return 0

    session_id = str(state.get("session_id", "") or "").strip()
    root_pid = int(state.get("root_pid", 0) or 0)
    updated = _session_status_payload(state, status="stopping")
    _write_track_state(updated)
    if session_id:
        _state_store().upsert_tracked_session(
            session_id=session_id,
            status="stopping",
            launch_mode=str(updated.get("launch_mode", "") or ""),
            agent=str(updated.get("agent", "") or ""),
            model=str(updated.get("model", "") or ""),
            agent_name=str(updated.get("agent_name", "") or ""),
            working_directory=str(updated.get("working_directory", "") or ""),
            root_command=str(updated.get("root_command", "") or ""),
            transcript_path=str(updated.get("transcript_path", "") or ""),
            controller_pid=int(updated.get("controller_pid", 0) or 0) or None,
            root_pid=root_pid or None,
            started_at=int(updated.get("started_at", 0) or 0),
            updated_at=int(updated.get("updated_at", 0) or time.time()),
            violation_code=str(updated.get("violation_code", "") or ""),
            exit_code=updated.get("exit_code"),
        )

    if root_pid > 0:
        try:
            os.killpg(root_pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except Exception as exc:
            console.print(f"[red]Failed to stop tracked session:[/red] {exc}")
            return 1

        deadline = time.monotonic() + TRACK_STOP_GRACE_SECONDS
        while time.monotonic() < deadline:
            if not _is_pid_alive(root_pid):
                break
            time.sleep(0.05)
        if _is_pid_alive(root_pid):
            try:
                os.killpg(root_pid, signal.SIGKILL)
            except Exception:
                pass

    console.print(f"stop_requested session_id={session_id or '-'}", highlight=False)
    return 0


def inspect_track_session(session_id: str = "", *, replay: bool = False, tail_events: int = TRACK_INSPECT_TAIL_EVENTS) -> int:
    state = get_latest_track_session(session_id)
    if not state:
        console.print("[red]No tracked session found.[/red]")
        return 1

    console.print(
        "session_id={session_id} status={status} agent={agent} model={model} launch_mode={launch_mode} "
        "root_pid={root_pid} controller_pid={controller_pid}".format(
            session_id=str(state.get("session_id", "") or "-"),
            status=str(state.get("status", "") or "-"),
            agent=str(state.get("agent", "") or "-"),
            model=str(state.get("model", "") or "-"),
            launch_mode=str(state.get("launch_mode", "") or "-"),
            root_pid=str(state.get("root_pid", "") or "-"),
            controller_pid=str(state.get("controller_pid", "") or "-"),
        ),
        highlight=False,
    )
    console.print(
        "started_at={started_at} ended_at={ended_at} exit_code={exit_code} violation={violation}".format(
            started_at=_format_ts(int(state.get("started_at", 0) or 0)),
            ended_at=_format_ts(int(state.get("ended_at", 0) or 0)),
            exit_code=str(state.get("exit_code", "-") if state.get("exit_code") is not None else "-"),
            violation=str(state.get("violation_code", "") or "-"),
        ),
        highlight=False,
    )
    console.print(f"command={str(state.get('root_command', '') or '-')}", highlight=False)
    transcript_path = str(state.get("transcript_path", "") or "").strip()
    if transcript_path:
        console.print(f"transcript={transcript_path}", highlight=False)

    events = _load_transcript_events(transcript_path) if transcript_path else []
    if replay:
        if not events:
            console.print("transcript_replay=unavailable", highlight=False)
        else:
            console.print(f"transcript_replay_events={len(events)}", highlight=False)
            chunks = [event["data"].decode("utf-8", errors="replace") for event in events if bytes(event.get("data", b""))]
            console.print("".join(chunks), highlight=False, soft_wrap=True)
    else:
        pty_events = [event for event in events if str(event.get("direction", "") or "") == "pty"]
        stdin_events = [event for event in events if str(event.get("direction", "") or "") == "stdin"]
        console.print(
            "transcript_events={total} pty_events={pty_count} stdin_events={stdin_count}".format(
                total=len(events),
                pty_count=len(pty_events),
                stdin_count=len(stdin_events),
            ),
            highlight=False,
        )
        tail = events[-max(1, int(tail_events or TRACK_INSPECT_TAIL_EVENTS)) :] if events else []
        for idx, event in enumerate(tail, start=1):
            preview = _format_debug_preview(bytes(event.get("data", b"")))
            console.print(
                f"tail[{idx}] direction={str(event.get('direction', '') or '-')} ts={float(event.get('ts', 0.0) or 0.0):.6f} data={preview}",
                highlight=False,
            )

    runs = _find_session_runs(str(state.get("session_id", "") or ""))
    if runs:
        console.print(f"recorded_runs={len(runs)}", highlight=False)
        for row in runs:
            payload = dict(row.get("payload", {}) or {})
            console.print(
                "run label={label} exit={exit_code} detached={detached} command={command}".format(
                    label=str(row.get("label", "") or "-"),
                    exit_code=str(row.get("exit_code", "-") if row.get("exit_code") is not None else "-"),
                    detached="1" if payload.get("track_process_detached") else "0",
                    command=str(row.get("command", "") or "-"),
                ),
                highlight=False,
            )
    return 0


def _write_transcript_event(handle: Any, direction: str, data: bytes) -> None:
    event = {
        "ts": round(time.time(), 6),
        "direction": str(direction or "").strip(),
        "data_b64": base64.b64encode(bytes(data)).decode("ascii"),
    }
    handle.write(json.dumps(event, separators=(",", ":")) + "\n")
    handle.flush()


def _best_effort_cwd(pid: int) -> str:
    if pid <= 0:
        return ""
    try:
        run = subprocess.run(
            ["lsof", "-a", "-d", "cwd", "-Fn", "-p", str(pid)],
            capture_output=True,
            text=True,
            check=False,
            timeout=0.8,
        )
    except Exception:
        return ""
    if run.returncode != 0:
        return ""
    for line in (run.stdout or "").splitlines():
        if line.startswith("n"):
            return str(line[1:] or "").strip()
    return ""


def _process_command(proc: psutil.Process) -> str:
    try:
        cmdline = proc.cmdline()
    except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
        cmdline = []
    except Exception:
        cmdline = []
    if cmdline:
        return shlex.join([str(part) for part in cmdline if str(part)])

    try:
        name = str(proc.name() or "").strip()
    except Exception:
        name = ""
    if name:
        return name
    return ""


def _read_live_process_tree(root_pid: int) -> dict[int, dict[str, Any]]:
    if root_pid <= 0:
        return {}
    try:
        root_proc = psutil.Process(root_pid)
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return {}
    except Exception:
        return {}

    out: dict[int, dict[str, Any]] = {}
    processes: list[psutil.Process] = [root_proc]
    try:
        processes.extend(root_proc.children(recursive=True))
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        pass
    except Exception:
        pass

    for proc in processes:
        try:
            with proc.oneshot():
                pid = int(proc.pid)
                ppid = int(proc.ppid())
                command = _process_command(proc)
                cwd = ""
                try:
                    cwd = str(proc.cwd() or "").strip()
                except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
                    cwd = ""
                except Exception:
                    cwd = ""
                if not cwd:
                    cwd = _best_effort_cwd(pid)
                started_at = 0.0
                try:
                    started_at = float(proc.create_time() or 0.0)
                except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
                    started_at = 0.0
                except Exception:
                    started_at = 0.0
                out[pid] = {
                    "pid": pid,
                    "ppid": ppid,
                    "comm": str(proc.name() or "").strip(),
                    "args": command,
                    "working_directory": cwd,
                    "started_at": started_at,
                    "session_id": _safe_getsid(pid),
                    "process_group_id": _safe_getpgid(pid),
                }
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
        except Exception:
            continue
    return out


def _looks_like_unmanaged_terminal_launch(row: dict[str, Any]) -> bool:
    haystack = " ".join(
        [
            str(row.get("comm", "") or "").strip().lower(),
            str(row.get("args", "") or "").strip().lower(),
        ]
    )
    if not haystack:
        return False
    if "open -a" in haystack and any(token in haystack for token in TRACK_UNMANAGED_WINDOW_TOKENS):
        return True
    return any(token in haystack for token in TRACK_UNMANAGED_WINDOW_TOKENS)


def _looks_like_escape_primitive(row: dict[str, Any]) -> bool:
    haystack = " ".join(
        [
            str(row.get("comm", "") or "").strip().lower(),
            str(row.get("args", "") or "").strip().lower(),
        ]
    )
    if not haystack:
        return False
    if any(token in haystack for token in TRACK_ESCAPE_PRIMITIVE_TOKENS):
        return True
    if "osascript" in haystack and "do script" in haystack and any(
        token in haystack for token in (*TRACK_UNMANAGED_WINDOW_TOKENS, "terminal", "iterm", "warp", "ghostty")
    ):
        return True
    if "open -a" in haystack and any(token in haystack for token in TRACK_UNMANAGED_WINDOW_TOKENS):
        return True
    return False


def _leaves_allowed_session(row: dict[str, Any], *, root_session_id: int) -> bool:
    session_id = int(row.get("session_id", 0) or 0)
    if root_session_id <= 0 or session_id <= 0:
        return False
    return session_id != root_session_id


class TrackRuntime:
    def __init__(self, launch: TrackLaunch, session_id: str, root_pid: int, transcript_path: str, state_store: SQLiteStateStore):
        self.launch = launch
        self.session_id = session_id
        self.root_pid = int(root_pid)
        self.root_session_id = _safe_getsid(root_pid) or int(root_pid)
        self.root_process_group_id = _safe_getpgid(root_pid) or int(root_pid)
        self.controller_pid = int(os.getpid())
        self.transcript_path = transcript_path
        self.state_store = state_store
        self.stop_event = threading.Event()
        self._lock = threading.Lock()
        self.root_exit_code: int | None = None
        self.violation_code = ""
        self.private_key_path = _track_private_key_path()
        self.public_key_path = _track_public_key_path()
        self.blocked_pids: set[int] = set()
        self.root_termination_requested = threading.Event()
        self.policy_notice_message = ""
        self.policy_notice_emitted = False
        self.processes: dict[int, ObservedProcess] = {
            self.root_pid: ObservedProcess(
                pid=self.root_pid,
                ppid=self.controller_pid,
                command=self.launch.root_command,
                working_directory=self.launch.working_directory,
                started_at=time.time(),
                session_id=self.root_session_id,
                process_group_id=self.root_process_group_id,
            )
        }

    def session_payload(self, status: str, *, exit_code: int | None = None) -> dict[str, Any]:
        payload = {
            "session_id": self.session_id,
            "status": str(status or "").strip().lower(),
            "launch_mode": self.launch.launch_mode,
            "agent": self.launch.agent,
            "model": self.launch.model,
            "agent_name": self.launch.agent_name,
            "working_directory": self.launch.working_directory,
            "root_command": self.launch.root_command,
            "transcript_path": self.transcript_path,
            "controller_pid": self.controller_pid,
            "root_pid": self.root_pid,
            "started_at": int(self.processes[self.root_pid].started_at),
            "updated_at": int(time.time()),
            "violation_code": self.violation_code,
        }
        if status not in {"active", "stopping"}:
            payload["ended_at"] = int(time.time())
        if exit_code is not None:
            payload["exit_code"] = int(exit_code)
        return payload

    def persist_state(self, status: str, *, exit_code: int | None = None) -> None:
        payload = self.session_payload(status, exit_code=exit_code)
        self.state_store.upsert_tracked_session(
            session_id=self.session_id,
            status=payload["status"],
            launch_mode=self.launch.launch_mode,
            agent=self.launch.agent,
            model=self.launch.model,
            agent_name=self.launch.agent_name,
            working_directory=self.launch.working_directory,
            root_command=self.launch.root_command,
            transcript_path=self.transcript_path,
            controller_pid=self.controller_pid,
            root_pid=self.root_pid,
            started_at=int(payload.get("started_at", 0) or 0),
            ended_at=int(payload.get("ended_at", 0) or 0),
            updated_at=int(payload.get("updated_at", 0) or time.time()),
            violation_code=self.violation_code,
            exit_code=exit_code,
        )
        _write_track_state(payload)

    def note_violation(self, code: str) -> None:
        clean = str(code or "").strip().lower()
        if not clean:
            return
        with self._lock:
            if self.violation_code == clean:
                return
            if self.violation_code:
                self.violation_code = f"{self.violation_code},{clean}"
            else:
                self.violation_code = clean
        state = self.state_store.get_tracked_session(self.session_id) or {}
        current_status = str(state.get("status", "") or "active").strip().lower() or "active"
        self.persist_state(current_status)

    def kill_process(self, pid: int) -> bool:
        target_pid = int(pid or 0)
        if target_pid <= 0 or target_pid in self.blocked_pids:
            return not _is_pid_alive(target_pid)
        self.blocked_pids.add(target_pid)
        try:
            target = psutil.Process(target_pid)
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            return True
        except Exception:
            target = None

        processes: list[psutil.Process] = []
        if target is not None:
            try:
                processes = target.children(recursive=True)
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                processes = []
            except Exception:
                processes = []
            processes.append(target)

        for proc in reversed(processes):
            try:
                proc.terminate()
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
            except Exception:
                continue

        _, alive = psutil.wait_procs(processes, timeout=TRACK_POLICY_CHILD_KILL_GRACE_SECONDS)
        for proc in alive:
            try:
                proc.kill()
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
            except Exception:
                continue
        _, alive = psutil.wait_procs(alive, timeout=TRACK_POLICY_CHILD_KILL_GRACE_SECONDS)
        return not alive and not _is_pid_alive(target_pid)

    def request_root_termination(self, code: str) -> None:
        self.note_violation(code)
        if not self.policy_notice_message:
            self.policy_notice_message = _policy_violation_message(code)
        self.root_termination_requested.set()

    def terminate_session_for_violation(self, code: str, *, pid: int = 0) -> None:
        self.note_violation(code)
        if not self.policy_notice_message:
            self.policy_notice_message = _policy_violation_message(code)
        killed = True
        if pid > 0:
            killed = self.kill_process(pid)
        if code in {"unmanaged_child_launch", "session_boundary_escape", "detached_descendants"} or not killed:
            self.request_root_termination(code)

    def finalize_process(self, proc: ObservedProcess, *, exit_code: int | None = None) -> None:
        if proc.finalized:
            return
        proc.finalized = True
        duration_ms = max(0, int((time.time() - float(proc.started_at or time.time())) * 1000.0))
        working_directory = proc.working_directory or self.launch.working_directory
        trace_id = f"track-{self.session_id}-{proc.pid}"
        ts = int(time.time())
        signature = sign_proof_payload(
            "AI_EXECUTED",
            self.launch.agent,
            self.launch.model,
            trace_id,
            ts,
            private_path=self.private_key_path,
            public_path=self.public_key_path,
        )
        proof_metadata = build_local_proof_metadata(
            private_path=self.private_key_path,
            public_path=self.public_key_path,
        )
        payload = {
            "shell_pid": int(proc.pid),
            "provenance_last_action": "track_session",
            "provenance_accept_origin": "ai",
            "provenance_accept_mode": "replace_full",
            "provenance_suggestion_kind": "track_session",
            "provenance_manual_edit_after_accept": False,
            "provenance_ai_agent": self.launch.agent,
            "provenance_ai_provider": "",
            "provenance_ai_model": self.launch.model,
            "provenance_agent_name": self.launch.agent_name,
            "provenance_agent_hint": self.launch.agent,
            "provenance_model_raw": self.launch.model,
            "provenance_wrapper_id": f"agensic_track:{self.session_id}",
            "proof_label": "AI_EXECUTED",
            "proof_agent": self.launch.agent,
            "proof_model": self.launch.model,
            "proof_trace": trace_id,
            "proof_timestamp": ts,
            "proof_signature": signature,
            "proof_signer_scope": str(proof_metadata.get("proof_signer_scope", "") or ""),
            "proof_key_fingerprint": str(proof_metadata.get("proof_key_fingerprint", "") or ""),
            "proof_host_fingerprint": str(proof_metadata.get("proof_host_fingerprint", "") or ""),
            "track_session_id": self.session_id,
            "track_root_pid": self.root_pid,
            "track_process_pid": proc.pid,
            "track_parent_pid": proc.ppid,
            "track_launch_mode": self.launch.launch_mode,
        }
        if self.violation_code:
            payload["track_violation_code"] = self.violation_code
        if proc.detached:
            payload["track_process_detached"] = True
        if proc.session_escape:
            payload["track_process_session_escape"] = True
            payload["track_root_session_id"] = self.root_session_id
            payload["track_process_session_id"] = proc.session_id
            payload["track_root_process_group_id"] = self.root_process_group_id
            payload["track_process_group_id"] = proc.process_group_id
        if exit_code is None:
            payload["track_exit_code_unavailable"] = True

        classification = classify_command_run(
            proc.command,
            payload,
            proof_public_path=self.public_key_path,
        )
        self.state_store.record_command_provenance(
            command=proc.command,
            label=str(classification.get("label", "UNKNOWN") or "UNKNOWN"),
            confidence=float(classification.get("confidence", 0.0) or 0.0),
            agent=str(classification.get("agent", "") or ""),
            agent_name=str(classification.get("agent_name", "") or ""),
            provider=str(classification.get("provider", "") or ""),
            model=str(classification.get("model", "") or ""),
            raw_model=str(classification.get("raw_model", "") or ""),
            normalized_model=str(classification.get("normalized_model", "") or ""),
            model_fingerprint=str(classification.get("model_fingerprint", "") or ""),
            evidence_tier=str(classification.get("evidence_tier", "") or ""),
            agent_source=str(classification.get("agent_source", "") or ""),
            registry_version=str(classification.get("registry_version", "") or ""),
            registry_status=str(classification.get("registry_status", "") or ""),
            source="runtime",
            working_directory=working_directory,
            exit_code=exit_code,
            duration_ms=duration_ms,
            shell_pid=proc.pid,
            evidence=[str(item) for item in classification.get("evidence", []) if str(item)],
            payload=payload,
            run_id=f"{self.session_id}:{proc.pid}",
            ts=ts,
        )


def _watch_tracked_process_tree(runtime: TrackRuntime) -> None:
    detached_finalize_deadline = 0.0
    while True:
        descendants = _read_live_process_tree(runtime.root_pid)
        descendant_ids = set(descendants.keys())

        for pid, row in descendants.items():
            existing = runtime.processes.get(pid)
            if existing is None:
                runtime.processes[pid] = ObservedProcess(
                    pid=pid,
                    ppid=int(row.get("ppid", 0) or 0),
                    command=str(row.get("args", "") or "").strip() or str(row.get("comm", "") or "").strip(),
                    working_directory=str(row.get("working_directory", "") or "").strip() or _best_effort_cwd(pid),
                    started_at=float(row.get("started_at", 0.0) or time.time()),
                    session_id=int(row.get("session_id", 0) or 0),
                    process_group_id=int(row.get("process_group_id", 0) or 0),
                )
                existing = runtime.processes[pid]
            else:
                command = str(row.get("args", "") or "").strip() or str(row.get("comm", "") or "").strip()
                if command and (not existing.command or existing.command.startswith("(")):
                    existing.command = command
                working_directory = str(row.get("working_directory", "") or "").strip()
                if working_directory:
                    existing.working_directory = working_directory
                existing.ppid = int(row.get("ppid", 0) or existing.ppid or 0)
                existing.session_id = int(row.get("session_id", 0) or existing.session_id or 0)
                existing.process_group_id = int(row.get("process_group_id", 0) or existing.process_group_id or 0)
            if pid != runtime.root_pid and _leaves_allowed_session(row, root_session_id=runtime.root_session_id):
                existing.session_escape = True
                runtime.terminate_session_for_violation("session_boundary_escape", pid=pid)
            if _looks_like_escape_primitive(row):
                runtime.terminate_session_for_violation("escape_primitive_blocked", pid=pid)
            if _looks_like_unmanaged_terminal_launch(row):
                runtime.terminate_session_for_violation("unmanaged_child_launch", pid=pid)

        if runtime.root_exit_code is not None and detached_finalize_deadline <= 0:
            detached_finalize_deadline = time.monotonic() + TRACK_FINAL_POLL_GRACE_SECONDS

        for pid, proc in list(runtime.processes.items()):
            if proc.finalized:
                continue
            if pid in descendant_ids:
                continue
            if _is_pid_alive(pid):
                proc.detached = True
                runtime.terminate_session_for_violation("detached_descendants", pid=pid)
                if detached_finalize_deadline > 0 and time.monotonic() >= detached_finalize_deadline:
                    runtime.finalize_process(proc, exit_code=None)
                continue
            exit_code = runtime.root_exit_code if pid == runtime.root_pid else None
            runtime.finalize_process(proc, exit_code=exit_code)

        all_finalized = all(proc.finalized for proc in runtime.processes.values())
        if runtime.root_exit_code is not None and all_finalized:
            break
        if runtime.stop_event.is_set() and all_finalized:
            break
        time.sleep(TRACK_POLL_INTERVAL_SECONDS)


def _apply_winsize(master_fd: int, stdin_fd: int) -> None:
    try:
        raw = fcntl.ioctl(stdin_fd, termios.TIOCGWINSZ, struct.pack("HHHH", 0, 0, 0, 0))
        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, raw)
    except Exception:
        return


def _drain_master_output(master_fd: int, transcript: Any, stdout_fd: int | None, *, timeout_seconds: float = 0.2) -> None:
    deadline = time.monotonic() + max(0.0, float(timeout_seconds or 0.0))
    while time.monotonic() < deadline:
        try:
            ready, _, _ = select.select([master_fd], [], [], 0.02)
        except Exception:
            return
        if master_fd not in ready:
            continue
        try:
            data = os.read(master_fd, 4096)
        except OSError as exc:
            if exc.errno == errno.EIO:
                return
            raise
        if not data:
            return
        _write_transcript_event(transcript, "pty", data)
        if stdout_fd is not None:
            os.write(stdout_fd, data)
        else:
            sys.stdout.write(data.decode("utf-8", errors="replace"))
            sys.stdout.flush()


def _emit_terminal_reset(stdout_fd: int | None) -> None:
    if stdout_fd is None:
        return
    try:
        os.write(stdout_fd, TRACK_TTY_RESET_SEQ.encode("utf-8"))
    except Exception:
        return


def run_tracked_command(launch: TrackLaunch) -> int:
    ensure_track_supported()
    _ensure_track_layout()
    _prune_tracked_transcripts()
    active = get_active_track_state()
    if active:
        console.print("[red]A tracked session is already active.[/red]")
        return 1

    transcript_path = os.path.join(_track_transcripts_dir(), f"{uuid.uuid4().hex}.jsonl")
    state_store = _state_store()
    master_fd, slave_fd = os.openpty()
    session_id = uuid.uuid4().hex[:16]
    child_env = _build_tracked_child_env(launch, session_id)
    proc: subprocess.Popen[bytes] | None = None
    try:
        proc = subprocess.Popen(
            launch.command,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=launch.working_directory,
            env=child_env,
            start_new_session=True,
            close_fds=True,
        )
    except FileNotFoundError:
        console.print(f"[red]agensic track: command not found:[/red] {launch.command[0]}")
        try:
            os.close(master_fd)
        except Exception:
            pass
        try:
            os.close(slave_fd)
        except Exception:
            pass
        return 127
    except Exception as exc:
        console.print(f"[red]agensic track failed:[/red] {exc}")
        try:
            os.close(master_fd)
        except Exception:
            pass
        try:
            os.close(slave_fd)
        except Exception:
            pass
        return 1
    finally:
        try:
            os.close(slave_fd)
        except Exception:
            pass

    pid = int(proc.pid)
    runtime = TrackRuntime(launch=launch, session_id=session_id, root_pid=pid, transcript_path=transcript_path, state_store=state_store)
    runtime.persist_state("active")

    watcher = threading.Thread(target=_watch_tracked_process_tree, args=(runtime,), daemon=True)
    watcher.start()

    old_tty = None
    stdin_fd = None
    stdout_fd = None
    resize_handler = None
    policy_notice = ""

    try:
        if sys.stdin.isatty():
            stdin_fd = sys.stdin.fileno()
            stdout_fd = sys.stdout.fileno()
            old_tty = termios.tcgetattr(stdin_fd)
            tty.setraw(stdin_fd)
            _apply_winsize(master_fd, stdin_fd)

            def _on_resize(signum: int, frame: Any) -> None:
                _apply_winsize(master_fd, stdin_fd)

            resize_handler = signal.getsignal(signal.SIGWINCH)
            signal.signal(signal.SIGWINCH, _on_resize)

        with open(transcript_path, "a", encoding="utf-8") as transcript:
            while True:
                read_fds = [master_fd]
                if stdin_fd is not None:
                    read_fds.append(stdin_fd)
                ready, _, _ = select.select(read_fds, [], [], 0.1)

                if stdin_fd is not None and stdin_fd in ready:
                    data = os.read(stdin_fd, 4096)
                    if data:
                        _write_transcript_event(transcript, "stdin", data)
                        os.write(master_fd, data)

                if master_fd in ready:
                    try:
                        data = os.read(master_fd, 4096)
                    except OSError as exc:
                        if exc.errno == errno.EIO:
                            data = b""
                        else:
                            raise
                    if data:
                        _write_transcript_event(transcript, "pty", data)
                        if stdout_fd is not None:
                            os.write(stdout_fd, data)
                        else:
                            sys.stdout.write(data.decode("utf-8", errors="replace"))
                            sys.stdout.flush()

                if runtime.root_termination_requested.is_set():
                    try:
                        os.killpg(runtime.root_pid, signal.SIGTERM)
                    except Exception:
                        pass

                exit_code = proc.poll()
                if exit_code is not None:
                    runtime.root_exit_code = int(128 + abs(exit_code)) if exit_code < 0 else int(exit_code)
                    _drain_master_output(master_fd, transcript, stdout_fd)
                    break
    finally:
        policy_notice = runtime.policy_notice_message if runtime.violation_code else ""
        runtime.stop_event.set()
        watcher.join(timeout=5.0)
        if resize_handler is not None:
            try:
                signal.signal(signal.SIGWINCH, resize_handler)
            except Exception:
                pass
        _emit_terminal_reset(stdout_fd)
        if old_tty is not None and stdin_fd is not None:
            try:
                termios.tcsetattr(stdin_fd, termios.TCSAFLUSH, old_tty)
            except Exception:
                pass
        try:
            os.close(master_fd)
        except Exception:
            pass
        if policy_notice and not runtime.policy_notice_emitted:
            try:
                sys.stderr.write(f"\n{policy_notice}\n")
                sys.stderr.flush()
            except Exception:
                pass
            runtime.policy_notice_emitted = True

    final_state = state_store.get_tracked_session(session_id) or _load_track_state()
    final_status = "stopped" if str(final_state.get("status", "") or "").strip().lower() == "stopping" else "exited"
    runtime.persist_state(final_status, exit_code=runtime.root_exit_code if runtime.root_exit_code is not None else 1)
    _prune_tracked_transcripts(exclude_paths={transcript_path})
    _clear_track_state()
    return int(runtime.root_exit_code if runtime.root_exit_code is not None else 1)
