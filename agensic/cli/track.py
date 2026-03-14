import base64
import errno
import fcntl
import json
import os
import re
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
TRACK_CHECKPOINT_INTERVAL_MS = 120
TRACK_CHECKPOINT_INTERVAL_EVENTS = 48


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


def _track_private_key_path() -> str:
    return APP_PATHS.provenance_private_key_path


def _track_public_key_path() -> str:
    return APP_PATHS.provenance_public_key_path


def ensure_track_supported() -> None:
    if sys.platform != "darwin":
        raise RuntimeError("agensic run is currently supported on macOS only.")


def _ensure_track_layout() -> None:
    migrate_legacy_layout()
    ensure_app_layout()
    os.makedirs(_track_transcripts_dir(), mode=0o700, exist_ok=True)


def _track_transcript_path(session_id: str) -> str:
    clean_session_id = str(session_id or "").strip() or uuid.uuid4().hex[:16]
    return os.path.join(_track_transcripts_dir(), f"{clean_session_id}.transcript.jsonl")


def _track_event_stream_path(session_id: str) -> str:
    clean_session_id = str(session_id or "").strip() or uuid.uuid4().hex[:16]
    return os.path.join(_track_transcripts_dir(), f"{clean_session_id}.events.jsonl")


def _track_checkpoint_path(session_id: str) -> str:
    clean_session_id = str(session_id or "").strip() or uuid.uuid4().hex[:16]
    return os.path.join(_track_transcripts_dir(), f"{clean_session_id}.checkpoints.jsonl")


def _platform_rust_target() -> str:
    machine = (os.uname().machine if hasattr(os, "uname") else "").strip().lower()
    if sys.platform == "darwin" and machine in {"arm64", "aarch64"}:
        return "aarch64-apple-darwin"
    if sys.platform == "darwin" and machine in {"x86_64", "amd64"}:
        return "x86_64-apple-darwin"
    if sys.platform.startswith("linux") and machine in {"x86_64", "amd64"}:
        return "x86_64-unknown-linux-gnu"
    if sys.platform.startswith("linux") and machine in {"arm64", "aarch64"}:
        return "aarch64-unknown-linux-gnu"
    return ""


def _local_provenance_tui_candidates() -> list[str]:
    explicit = str(os.environ.get("AGENSIC_PROVENANCE_TUI_LOCAL_BIN", "") or "").strip()
    target = _platform_rust_target()
    project_root = str(Path(__file__).resolve().parents[2])
    cwd = os.getcwd()
    candidates = [
        explicit,
        APP_PATHS.provenance_tui_bin,
        os.path.join(cwd, "rust", "provenance_tui", "target", "release", "agensic-provenance-tui"),
        (
            os.path.join(
                cwd,
                "rust",
                "provenance_tui",
                "target",
                target,
                "release",
                "agensic-provenance-tui",
            )
            if target
            else ""
        ),
        os.path.join(project_root, "rust", "provenance_tui", "target", "release", "agensic-provenance-tui"),
        (
            os.path.join(
                project_root,
                "rust",
                "provenance_tui",
                "target",
                target,
                "release",
                "agensic-provenance-tui",
            )
            if target
            else ""
        ),
    ]
    out: list[str] = []
    seen: set[str] = set()
    for path in candidates:
        clean = str(path or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        out.append(clean)
    return out


def _resolve_provenance_tui_binary_for_checkpoints() -> str:
    for candidate in _local_provenance_tui_candidates():
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return ""


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


def _tracked_state_looks_live(state: dict[str, Any]) -> bool:
    status = str(state.get("status", "") or "").strip().lower()
    if status not in {"active", "stopping", "launching"}:
        return False
    controller_pid = int(state.get("controller_pid", 0) or 0)
    root_pid = int(state.get("root_pid", 0) or 0)
    controller_alive = controller_pid > 0 and _is_pid_alive(controller_pid)
    root_alive = root_pid > 0 and _is_pid_alive(root_pid)
    return controller_alive or root_alive


def reconcile_tracked_sessions() -> None:
    for row in _state_store().list_tracked_sessions(limit=500):
        state = _session_cache_payload(row)
        status = str(state.get("status", "") or "").strip().lower()
        if status not in {"active", "stopping", "launching"}:
            continue
        if _tracked_state_looks_live(state):
            continue
        _mark_tracked_session_errored(state, str(state.get("violation_code", "") or "stale_session"))


def _refresh_track_state_cache(active_states: list[dict[str, Any]] | None = None) -> None:
    states = active_states if active_states is not None else list_active_track_states(refresh_cache=False)
    if states:
        _write_track_state(states[0])
    else:
        _clear_track_state()


def list_active_track_states(*, refresh_cache: bool = True) -> list[dict[str, Any]]:
    reconcile_tracked_sessions()
    cached_state = _load_track_state()
    states: list[dict[str, Any]] = []
    for row in _state_store().list_active_tracked_sessions(limit=200):
        state = _session_cache_payload(row)
        if _tracked_state_looks_live(state):
            states.append(state)
            continue
        _mark_tracked_session_errored(state, str(state.get("violation_code", "") or "stale_session"))

    if refresh_cache:
        if states:
            if cached_state != states[0]:
                _write_track_state(states[0])
        elif cached_state:
            _clear_track_state()
    return states


def get_active_track_state() -> dict[str, Any]:
    active_states = list_active_track_states()
    return active_states[0] if active_states else {}


def get_latest_track_session(session_id: str = "") -> dict[str, Any]:
    clean_session_id = str(session_id or "").strip()
    if clean_session_id:
        row = _state_store().get_tracked_session(clean_session_id)
    else:
        active_states = list_active_track_states()
        row = active_states[0] if active_states else _state_store().get_latest_tracked_session()
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


def _find_command_descriptor(command: list[str]) -> dict[str, Any] | None:
    if not command:
        return None
    executable = os.path.basename(str(command[0] or "").strip()).lower()
    if executable == "gh" and len(command) > 1 and str(command[1] or "").strip().lower() == "copilot":
        return _find_registry_descriptor("copilot")
    wrapped = _extract_open_app_context(command)
    if wrapped is not None:
        if wrapped["agent_id"]:
            return _find_registry_descriptor(wrapped["agent_id"])
        wrapped_args = wrapped["wrapped_args"]
        if wrapped_args:
            return _find_command_descriptor(wrapped_args)
    return _find_registry_descriptor(executable)


def _looks_like_codex_launch(*, command: list[str], agent: str = "") -> bool:
    clean_agent = str(agent or "").strip().lower()
    executable = os.path.basename(str((command or [""])[0] or "").strip()).lower()
    return clean_agent == "codex" or executable == "codex"


def _looks_like_github_copilot_launch(*, command: list[str], agent: str = "") -> bool:
    clean_agent = str(agent or "").strip().lower()
    executable = os.path.basename(str((command or [""])[0] or "").strip()).lower()
    if clean_agent in {"github_copilot", "github_copilot_cli"} or executable == "copilot":
        return True
    return executable == "gh" and len(command) > 1 and str(command[1] or "").strip().lower() == "copilot"


def _extract_open_app_context(command: list[str]) -> dict[str, Any] | None:
    if not command:
        return None
    executable = os.path.basename(str(command[0] or "").strip()).lower()
    if executable != "open":
        return None

    app_target = ""
    wrapped_args: list[str] = []
    index = 1
    while index < len(command):
        token = str(command[index] or "").strip()
        lowered = token.lower()
        if lowered == "--args":
            wrapped_args = [str(item or "").strip() for item in command[index + 1 :] if str(item or "").strip()]
            break
        if lowered == "-a" and index + 1 < len(command):
            app_target = str(command[index + 1] or "").strip()
            index += 2
            continue
        index += 1

    app_name = Path(app_target).name.lower()
    if app_name.endswith(".app"):
        app_name = app_name[:-4]

    agent_id = ""
    if app_name == "ollama":
        agent_id = "ollama"

    return {
        "app_target": app_target,
        "app_name": app_name,
        "agent_id": agent_id,
        "wrapped_args": wrapped_args,
    }


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


def _resolve_config_home(env: dict[str, str] | None = None) -> Path:
    source_env = env or os.environ
    raw = str(source_env.get("XDG_CONFIG_HOME", "") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return _resolve_home(env) / ".config"


def _load_json_text(raw_text: str) -> Any:
    text = str(raw_text or "")
    if not text.strip():
        return None
    try:
        return json.loads(text)
    except Exception:
        pass

    def _strip_json_comments(source: str) -> str:
        out: list[str] = []
        in_string = False
        escape = False
        idx = 0
        while idx < len(source):
            ch = source[idx]
            if in_string:
                out.append(ch)
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                idx += 1
                continue
            if ch == '"':
                in_string = True
                out.append(ch)
                idx += 1
                continue
            if ch == "/" and idx + 1 < len(source):
                nxt = source[idx + 1]
                if nxt == "/":
                    idx += 2
                    while idx < len(source) and source[idx] not in "\r\n":
                        idx += 1
                    continue
                if nxt == "*":
                    idx += 2
                    while idx + 1 < len(source) and not (source[idx] == "*" and source[idx + 1] == "/"):
                        idx += 1
                    idx += 2
                    continue
            out.append(ch)
            idx += 1
        return "".join(out)

    def _strip_trailing_commas(source: str) -> str:
        out: list[str] = []
        in_string = False
        escape = False
        idx = 0
        while idx < len(source):
            ch = source[idx]
            if in_string:
                out.append(ch)
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                idx += 1
                continue
            if ch == '"':
                in_string = True
                out.append(ch)
                idx += 1
                continue
            if ch == ",":
                lookahead = idx + 1
                while lookahead < len(source) and source[lookahead].isspace():
                    lookahead += 1
                if lookahead < len(source) and source[lookahead] in "}]":
                    idx += 1
                    continue
            out.append(ch)
            idx += 1
        return "".join(out)

    cleaned = _strip_trailing_commas(_strip_json_comments(text))
    try:
        return json.loads(cleaned)
    except Exception:
        return None


def _load_json_file(path: Path) -> Any:
    try:
        return _load_json_text(path.read_text(encoding="utf-8"))
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


def _read_cli_option_value(command: list[str], *flags: str) -> str:
    if not command:
        return ""
    normalized = {str(flag or "").strip().lower() for flag in flags if str(flag or "").strip()}
    for index, token in enumerate(command):
        current = str(token or "").strip()
        lowered = current.lower()
        if lowered in normalized:
            if index + 1 < len(command):
                return str(command[index + 1] or "").strip()
            return ""
        for flag in normalized:
            prefix = f"{flag}="
            if lowered.startswith(prefix):
                return current[len(prefix) :].strip()
    return ""


def _resolve_config_string(value: str, env: dict[str, str] | None = None) -> str:
    clean = str(value or "").strip()
    if not clean:
        return ""
    source_env = env or os.environ
    for pattern in (
        r"^\{env:([A-Za-z_][A-Za-z0-9_]*)\}$",
        r"^\$([A-Za-z_][A-Za-z0-9_]*)$",
        r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$",
    ):
        match = re.match(pattern, clean)
        if match is not None:
            return str(source_env.get(match.group(1), "") or "").strip()
    return clean


def _read_model_value(payload: Any, env: dict[str, str] | None = None) -> str:
    model = (
        _read_string_path(payload, "model", "name")
        or _read_string_path(payload, "model")
        or _read_string_path(payload, "modelName")
        or _read_string_path(payload, "defaultModel")
        or _read_string_path(payload, "default_model")
        or _read_string_path(payload, "cli", "model")
        or _read_string_path(payload, "cli", "defaultModel")
        or _read_string_path(payload, "default", "model")
        or _read_string_path(payload, "defaults", "model")
    )
    return _resolve_config_string(model, env)


def _load_model_from_path(path: Path, env: dict[str, str] | None = None) -> str:
    if not path.is_file():
        return ""
    return _read_model_value(_load_json_file(path), env)


def _merge_model_candidates(paths: list[Path], env: dict[str, str] | None = None) -> str:
    resolved = ""
    seen: set[str] = set()
    for path in paths:
        key = str(path.expanduser())
        if key in seen:
            continue
        seen.add(key)
        model = _load_model_from_path(path, env)
        if model:
            resolved = model
    return resolved


def _resolve_gemini_system_settings_path(env: dict[str, str] | None = None) -> Path:
    source_env = env or os.environ
    override = str(source_env.get("GEMINI_CLI_SYSTEM_SETTINGS_PATH", "") or "").strip()
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path("/Library/Application Support/GeminiCli/settings.json")
    if sys.platform.startswith("win"):
        program_data = str(source_env.get("PROGRAMDATA", r"C:\ProgramData") or r"C:\ProgramData").strip()
        return Path(program_data) / "gemini-cli" / "settings.json"
    return Path("/etc/gemini-cli/settings.json")


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
    source_env = env or os.environ
    search_root = Path(cwd or os.getcwd())
    candidates: list[Path] = [_resolve_home(env) / ".gemini" / "settings.json"]
    workspace_path = _find_upward(search_root, ".gemini", "settings.json")
    if workspace_path is not None:
        candidates.append(workspace_path)
    candidates.append(_resolve_gemini_system_settings_path(env))
    merged = _merge_model_candidates(candidates, env)
    env_model = (
        str(source_env.get("GEMINI_MODEL", "") or "").strip()
        or str(source_env.get("GOOGLE_GEMINI_MODEL", "") or "").strip()
        or str(source_env.get("GEMINI_DEFAULT_MODEL", "") or "").strip()
    )
    return env_model or merged


def _resolve_claude_managed_settings_path() -> Path:
    if sys.platform == "darwin":
        return Path("/Library/Application Support/ClaudeCode/managed-settings.json")
    if sys.platform.startswith("win"):
        return Path(r"C:\Program Files\ClaudeCode\managed-settings.json")
    return Path("/etc/claude-code/managed-settings.json")


def _infer_claude_code_model(env: dict[str, str] | None = None, cwd: str | None = None) -> str:
    source_env = env or os.environ
    search_root = Path(cwd or os.getcwd())
    candidates: list[Path] = [_resolve_home(env) / ".claude" / "settings.json"]
    shared_project = _find_upward(search_root, ".claude", "settings.json")
    if shared_project is not None:
        candidates.append(shared_project)
    local_project = _find_upward(search_root, ".claude", "settings.local.json")
    if local_project is not None:
        candidates.append(local_project)

    merged = _merge_model_candidates(candidates, env)
    env_model = (
        str(source_env.get("ANTHROPIC_MODEL", "") or "").strip()
        or str(source_env.get("CLAUDE_CODE_MODEL", "") or "").strip()
    )
    managed_model = _load_model_from_path(_resolve_claude_managed_settings_path(), env)
    return managed_model or env_model or merged


def _infer_opencode_model(env: dict[str, str] | None = None, cwd: str | None = None) -> str:
    source_env = env or os.environ
    search_root = Path(cwd or os.getcwd())
    custom_dir = str(source_env.get("OPENCODE_CONFIG_DIR", "") or "").strip()
    candidates: list[Path] = [
        _resolve_config_home(env) / "opencode" / "opencode.json",
        _resolve_config_home(env) / "opencode" / "opencode.jsonc",
    ]
    custom_path = str(source_env.get("OPENCODE_CONFIG", "") or "").strip()
    if custom_path:
        candidates.append(Path(custom_path).expanduser())
    if custom_dir:
        custom_root = Path(custom_dir).expanduser()
        candidates.extend(
            [
                custom_root / "opencode.json",
                custom_root / "opencode.jsonc",
            ]
        )
    for filename in ("opencode.json", "opencode.jsonc"):
        workspace_path = _find_upward(search_root, filename)
        if workspace_path is not None:
            candidates.append(workspace_path)
    inline_payload = _load_json_text(str(source_env.get("OPENCODE_CONFIG_CONTENT", "") or "").strip())
    inline_model = _read_model_value(inline_payload, env)
    env_model = str(source_env.get("OPENCODE_MODEL", "") or "").strip()
    return env_model or inline_model or _merge_model_candidates(candidates, env)


def _infer_kilo_code_model(env: dict[str, str] | None = None, cwd: str | None = None) -> str:
    search_root = Path(cwd or os.getcwd())
    candidates: list[Path] = [
        _resolve_config_home(env) / "kilo" / "config.json",
        _resolve_config_home(env) / "kilo" / "opencode.json",
        _resolve_config_home(env) / "kilo" / "opencode.jsonc",
        _resolve_config_home(env) / "kilocode" / "kilocode.json",
        _resolve_home(env) / ".kilocode" / "config.json",
    ]
    for filename in ("opencode.json", "opencode.jsonc", "kilocode.json"):
        workspace_path = _find_upward(search_root, filename)
        if workspace_path is not None:
            candidates.append(workspace_path)
    for filename in ("opencode.json", "opencode.jsonc", "kilocode.json"):
        workspace_path = _find_upward(search_root, ".opencode", filename)
        if workspace_path is not None:
            candidates.append(workspace_path)
    return _merge_model_candidates(candidates, env)


def _resolve_github_copilot_home(command: list[str], env: dict[str, str] | None = None) -> Path:
    source_env = env or os.environ
    config_dir = _read_cli_option_value(command, "--config-dir")
    if config_dir:
        return Path(config_dir).expanduser()
    env_dir = str(source_env.get("COPILOT_HOME", "") or "").strip()
    if env_dir:
        return Path(env_dir).expanduser()
    return _resolve_home(env) / ".copilot"


def _infer_github_copilot_model(command: list[str], env: dict[str, str] | None = None) -> str:
    source_env = env or os.environ
    env_model = str(source_env.get("COPILOT_MODEL", "") or "").strip()
    config_model = _load_model_from_path(_resolve_github_copilot_home(command, env) / "config.json", env)
    return env_model or config_model


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
    wrapped = _extract_open_app_context(command)
    if wrapped is not None:
        if wrapped["agent_id"] == "ollama":
            wrapped_args = wrapped["wrapped_args"]
            if wrapped_args:
                if len(wrapped_args) >= 2 and wrapped_args[0].lower() in {
                    "run",
                    "chat",
                    "show",
                    "pull",
                    "push",
                    "create",
                    "cp",
                    "rm",
                }:
                    return wrapped_args[1]
            return ""

    executable = os.path.basename(str(command[0] or "").strip()).lower()
    explicit_model = ""
    if executable in {
        "codex",
        "gemini",
        "claude",
        "opencode",
        "kilo",
        "kilocode",
        "ollama",
        "aider",
        "continue",
        "openclaw",
        "copilot",
    } or (executable == "gh" and len(command) > 1 and str(command[1] or "").strip().lower() == "copilot"):
        explicit_model = _read_cli_option_value(command, "--model", "-m")
    if explicit_model:
        return explicit_model

    command_text = shlex.join(command)
    registry = get_agent_registry(force_reload=False)
    model_meta = registry.extract_model_provider_from_command(command_text)
    inline_model = str(model_meta.get("model_raw", "") or "").strip()
    if inline_model:
        return inline_model

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
    if clean_agent == "opencode" or executable == "opencode":
        return _infer_opencode_model(env, cwd=os.getcwd())
    if clean_agent == "kilocode" or executable in {"kilo", "kilocode"}:
        return _infer_kilo_code_model(env, cwd=os.getcwd())
    if _looks_like_github_copilot_launch(command=command, agent=agent):
        return _infer_github_copilot_model(command, env)
    if clean_agent == "openclaw" or executable == "openclaw":
        return _infer_openclaw_model(env)
    if clean_agent == "ollama" or executable == "ollama":
        return _infer_ollama_model(env)
    return ""


def _build_tracked_child_env(launch: TrackLaunch, session_id: str) -> dict[str, str]:
    env = os.environ.copy()
    env["AGENSIC_TRACK_ACTIVE"] = "1"
    env["AGENSIC_TRACK_SESSION_ID"] = session_id
    env["AGENSIC_TRACK_AGENT"] = launch.agent
    env["AGENSIC_TRACK_MODEL"] = launch.model
    env["AGENSIC_TRACK_AGENT_NAME"] = launch.agent_name
    env["AGENSIC_TRACK_LAUNCH_MODE"] = launch.launch_mode
    return env


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
        descriptor = _find_command_descriptor(command)
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

    descriptor = _find_command_descriptor(args)
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


def _run_git_capture(working_directory: str, args: list[str], *, timeout_seconds: float = 1.5) -> tuple[int, str, str]:
    cwd = str(working_directory or "").strip() or None
    try:
        run = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except Exception as exc:
        return (1, "", str(exc))
    return (int(run.returncode), str(run.stdout or ""), str(run.stderr or ""))


def _capture_repo_snapshot(working_directory: str) -> dict[str, Any]:
    repo_root = ""
    branch_name = ""
    head_commit = ""
    status_output = ""
    dirty = False

    code, stdout, _ = _run_git_capture(working_directory, ["rev-parse", "--show-toplevel"])
    if code == 0:
        repo_root = str(stdout or "").strip()
    repo_cwd = repo_root or str(working_directory or "").strip()
    if repo_root:
        code, stdout, _ = _run_git_capture(repo_cwd, ["branch", "--show-current"])
        if code == 0:
            branch_name = str(stdout or "").strip()
        code, stdout, _ = _run_git_capture(repo_cwd, ["rev-parse", "HEAD"])
        if code == 0:
            head_commit = str(stdout or "").strip()
        code, stdout, _ = _run_git_capture(repo_cwd, ["status", "--porcelain"])
        if code == 0:
            status_output = str(stdout or "")
            dirty = bool(status_output.strip())

    changed_files: list[str] = []
    diff_stat = ""
    if repo_root:
        code, stdout, _ = _run_git_capture(repo_cwd, ["diff", "--name-only", "HEAD"])
        if code == 0:
            changed_files = [line.strip() for line in stdout.splitlines() if line.strip()]
        code, stdout, _ = _run_git_capture(repo_cwd, ["diff", "--stat", "HEAD"])
        if code == 0:
            diff_stat = str(stdout or "").strip()

    return {
        "timestamp": int(time.time()),
        "repo_root": repo_root,
        "branch": branch_name,
        "head": head_commit,
        "dirty": dirty,
        "status_porcelain": status_output,
        "changed_files": changed_files,
        "diff_stat": diff_stat,
    }


def _git_changed_files_between(repo_root: str, start_head: str, end_head: str) -> list[str]:
    if not repo_root or not start_head or not end_head or start_head == end_head:
        return []
    code, stdout, _ = _run_git_capture(repo_root, ["diff", "--name-only", f"{start_head}..{end_head}"], timeout_seconds=2.0)
    if code != 0:
        return []
    return [line.strip() for line in stdout.splitlines() if line.strip()]


def _git_diff_stat_between(repo_root: str, start_head: str, end_head: str) -> str:
    if not repo_root or not start_head or not end_head or start_head == end_head:
        return ""
    code, stdout, _ = _run_git_capture(repo_root, ["diff", "--stat", f"{start_head}..{end_head}"], timeout_seconds=2.0)
    return str(stdout or "").strip() if code == 0 else ""


def _git_commits_between(repo_root: str, start_head: str, end_head: str) -> list[dict[str, str]]:
    if not repo_root or not start_head or not end_head or start_head == end_head:
        return []
    code, stdout, _ = _run_git_capture(repo_root, ["log", "--oneline", f"{start_head}..{end_head}"], timeout_seconds=2.0)
    if code != 0:
        return []
    commits: list[dict[str, str]] = []
    for line in stdout.splitlines():
        clean = str(line or "").strip()
        if not clean:
            continue
        sha, _, summary = clean.partition(" ")
        commits.append({"sha": sha, "summary": summary.strip()})
    return commits


def _load_session_events(path: str) -> list[dict[str, Any]]:
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
        item = dict(payload)
        event_payload = item.get("payload", {})
        if isinstance(event_payload, dict):
            data_b64 = str(event_payload.get("data_b64", "") or "").strip()
            if data_b64:
                try:
                    event_payload["data"] = base64.b64decode(data_b64.encode("ascii"), validate=True)
                except Exception:
                    event_payload["data"] = b""
            item["payload"] = event_payload
        events.append(item)
    events.sort(key=lambda event: (float(event.get("ts_monotonic_ms", 0.0) or 0.0), int(event.get("seq", 0) or 0)))
    return events


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


def _find_session_runs(session_id: str, limit: int = 500) -> list[dict[str, Any]]:
    clean_session_id = str(session_id or "").strip()
    if not clean_session_id:
        return []
    rows = _state_store().list_command_runs(limit=max(200, min(500, int(limit or 500))))
    out: list[dict[str, Any]] = []
    for row in rows:
        payload = dict(row.get("payload", {}) or {})
        if str(payload.get("track_session_id", "") or "").strip() != clean_session_id:
            continue
        out.append(row)
        if len(out) >= max(1, int(limit or 500)):
            break
    return out


def print_track_status() -> int:
    active_states = list_active_track_states()
    if not active_states:
        console.print("inactive")
        return 0
    console.print(f"active_sessions={len(active_states)}", highlight=False)
    for state in active_states:
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


def _request_track_session_stop(state: dict[str, Any]) -> int:
    session_id = str(state.get("session_id", "") or "").strip()
    root_pid = int(state.get("root_pid", 0) or 0)
    updated = _session_status_payload(state, status="stopping")
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
            console.print(
                f"[red]Failed to stop tracked session {session_id or '-'}:[/red] {exc}"
            )
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
    return 0


def stop_track_sessions(session_id: str = "", *, stop_all: bool = False) -> int:
    active_states = list_active_track_states()
    if not active_states:
        console.print("inactive")
        return 0

    clean_session_id = str(session_id or "").strip()
    targets: list[dict[str, Any]]
    if stop_all:
        targets = active_states
    elif clean_session_id:
        targets = [state for state in active_states if str(state.get("session_id", "") or "").strip() == clean_session_id]
        if not targets:
            console.print(f"[red]No active tracked session found for session_id={clean_session_id}[/red]")
            return 1
    elif len(active_states) == 1:
        targets = [active_states[0]]
    else:
        console.print(
            "[red]Multiple tracked sessions are active. Use 'agensic run stop <session_id>' or 'agensic run stop --all'.[/red]"
        )
        return 2

    exit_code = 0
    for state in targets:
        exit_code = max(exit_code, _request_track_session_stop(state))
    _refresh_track_state_cache()

    session_ids = ",".join(str(state.get("session_id", "") or "-") for state in targets)
    console.print(f"stop_requested sessions={len(targets)} session_ids={session_ids}", highlight=False)
    return exit_code


def print_sessions_text(limit: int = 20) -> int:
    reconcile_tracked_sessions()
    rows = _state_store().list_session_summaries(limit=max(1, min(200, int(limit or 20))))
    if not rows:
        console.print("no_sessions")
        return 0
    console.print(f"sessions={len(rows)}", highlight=False)
    for row in rows:
        console.print(
            "session_id={session_id} status={status} agent={agent} model={model} started_at={started_at} "
            "repo={repo} branch={branch} exit_code={exit_code} violation={violation}".format(
                session_id=str(row.get("session_id", "") or "-"),
                status=str(row.get("status", "") or "-"),
                agent=str(row.get("agent", "") or "-"),
                model=str(row.get("model", "") or "-"),
                started_at=_format_ts(int(row.get("started_at", 0) or 0)),
                repo=str(row.get("repo_root", "") or "-"),
                branch=str(row.get("branch_end", "") or row.get("branch_start", "") or "-"),
                exit_code=str(row.get("exit_code", "-") if row.get("exit_code") is not None else "-"),
                violation=str(row.get("violation_code", "") or "-"),
            ),
            highlight=False,
        )
    return 0


def inspect_track_session(session_id: str = "", *, replay: bool = False, tail_events: int = TRACK_INSPECT_TAIL_EVENTS) -> int:
    state = _state_store().get_session_summary(session_id) if str(session_id or "").strip() else get_latest_track_session(session_id)
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
    console.print(
        "repo_start={repo_start} branch_start={branch_start} head_start={head_start} repo_end={repo_end} branch_end={branch_end} head_end={head_end}".format(
            repo_start=str((state.get("start_snapshot") or {}).get("repo_root", "") or state.get("repo_root", "") or "-"),
            branch_start=str(state.get("branch_start", "") or (state.get("start_snapshot") or {}).get("branch", "") or "-"),
            head_start=str(state.get("head_start", "") or (state.get("start_snapshot") or {}).get("head", "") or "-"),
            repo_end=str((state.get("end_snapshot") or {}).get("repo_root", "") or state.get("repo_root", "") or "-"),
            branch_end=str(state.get("branch_end", "") or (state.get("end_snapshot") or {}).get("branch", "") or "-"),
            head_end=str(state.get("head_end", "") or (state.get("end_snapshot") or {}).get("head", "") or "-"),
        ),
        highlight=False,
    )
    transcript_path = str(state.get("transcript_path", "") or "").strip()
    if transcript_path:
        console.print(f"transcript={transcript_path}", highlight=False)
    event_stream_path = str(state.get("event_stream_path", "") or "").strip()
    if event_stream_path:
        console.print(f"events={event_stream_path}", highlight=False)

    events = _load_transcript_events(transcript_path) if transcript_path else []
    session_events = _load_session_events(event_stream_path) if event_stream_path else []
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
        console.print(f"session_events={len(session_events)}", highlight=False)
        for idx, event in enumerate(session_events[-max(1, int(tail_events or TRACK_INSPECT_TAIL_EVENTS)) :], start=1):
            payload = dict(event.get("payload", {}) or {})
            preview = ""
            if isinstance(payload.get("data"), (bytes, bytearray)):
                preview = _format_debug_preview(bytes(payload.get("data", b"")))
            console.print(
                "event[{idx}] seq={seq} type={event_type} preview={preview}".format(
                    idx=idx,
                    seq=int(event.get("seq", 0) or 0),
                    event_type=str(event.get("type", "") or "-"),
                    preview=preview or "-",
                ),
                highlight=False,
            )

    runs = _find_session_runs(str(state.get("session_id", "") or ""))
    aggregate = dict(state.get("aggregate", {}) or {})
    changes = dict(state.get("changes", {}) or {})
    if aggregate:
        console.print(
            "aggregate command_count={command_count} subprocess_count={subprocess_count} push_attempts={push_attempts} commits_created={commits_created}".format(
                command_count=int(aggregate.get("command_count", 0) or 0),
                subprocess_count=int(aggregate.get("subprocess_count", 0) or 0),
                push_attempts=int(aggregate.get("push_attempts", 0) or 0),
                commits_created=int(aggregate.get("commits_created", 0) or 0),
            ),
            highlight=False,
        )
        console.print(
            f"label_counts={json.dumps(dict(aggregate.get('provenance_label_counts', {}) or {}), sort_keys=True)}",
            highlight=False,
        )
        suspicious_events = list(aggregate.get("suspicious_events", []) or [])
        console.print(f"suspicious_events={','.join(str(item) for item in suspicious_events) or '-'}", highlight=False)
    if changes:
        console.print(
            "files_changed={count} file_list={files}".format(
                count=len(list(changes.get("files_changed", []) or [])),
                files=",".join(str(item) for item in list(changes.get("files_changed", []) or [])) or "-",
            ),
            highlight=False,
        )
        console.print(
            f"committed_diff_stat={str(changes.get('committed_diff_stat', '') or '-')}",
            highlight=False,
        )
        console.print(
            f"worktree_diff_stat={str(changes.get('worktree_diff_stat', '') or '-')}",
            highlight=False,
        )
        commits = list(changes.get("commits_created", []) or [])
        for idx, commit in enumerate(commits, start=1):
            console.print(
                f"commit[{idx}] sha={str(commit.get('sha', '') or '-')} summary={str(commit.get('summary', '') or '-')}",
                highlight=False,
            )
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


def _write_transcript_event(
    handle: Any,
    direction: str,
    data: bytes,
    *,
    seq: int | None = None,
) -> None:
    event = {
        "ts": round(time.time(), 6),
        "direction": str(direction or "").strip(),
        "data_b64": base64.b64encode(bytes(data)).decode("ascii"),
    }
    if seq is not None:
        event["seq"] = int(seq)
    handle.write(json.dumps(event, separators=(",", ":")) + "\n")
    handle.flush()


def _write_transcript_resize_event(
    handle: Any,
    *,
    rows: int,
    cols: int,
    seq: int | None = None,
) -> None:
    event = {
        "ts": round(time.time(), 6),
        "direction": "resize",
        "rows": max(1, int(rows or 0)),
        "cols": max(1, int(cols or 0)),
    }
    if seq is not None:
        event["seq"] = int(seq)
    handle.write(json.dumps(event, separators=(",", ":")) + "\n")
    handle.flush()


def _start_checkpoint_recorder(checkpoint_path: str) -> subprocess.Popen[str] | None:
    binary = _resolve_provenance_tui_binary_for_checkpoints()
    if not binary or not checkpoint_path.strip():
        return None
    try:
        return subprocess.Popen(
            [
                binary,
                "checkpoints",
                "--out",
                checkpoint_path,
                "--interval-ms",
                str(TRACK_CHECKPOINT_INTERVAL_MS),
                "--interval-events",
                str(TRACK_CHECKPOINT_INTERVAL_EVENTS),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
    except Exception:
        return None


def _send_checkpoint_event(
    recorder: subprocess.Popen[str] | None,
    *,
    direction: str,
    seq: int | None = None,
    rows: int | None = None,
    cols: int | None = None,
    data: bytes | None = None,
) -> subprocess.Popen[str] | None:
    if recorder is None or recorder.stdin is None:
        return None
    payload: dict[str, Any] = {
        "direction": str(direction or "").strip(),
    }
    if seq is not None:
        payload["seq"] = int(seq)
    if rows is not None:
        payload["rows"] = max(1, int(rows or 0))
    if cols is not None:
        payload["cols"] = max(1, int(cols or 0))
    if data:
        payload["data_b64"] = base64.b64encode(bytes(data)).decode("ascii")
    try:
        recorder.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
        if recorder.poll() is not None:
            return None
        return recorder
    except Exception:
        try:
            recorder.stdin.close()
        except Exception:
            pass
        return None


def _stop_checkpoint_recorder(
    recorder: subprocess.Popen[str] | None,
    *,
    final_seq: int | None = None,
) -> None:
    if recorder is None:
        return
    recorder = _send_checkpoint_event(recorder, direction="finish", seq=final_seq)
    try:
        if recorder is not None and recorder.stdin is not None:
            recorder.stdin.close()
    except Exception:
        pass
    try:
        if recorder is not None:
            recorder.wait(timeout=2.0)
    except Exception:
        try:
            if recorder is not None:
                recorder.kill()
        except Exception:
            pass


def _write_session_event(
    handle: Any,
    *,
    session_id: str,
    seq: int,
    started_monotonic: float,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> None:
    event = {
        "session_id": str(session_id or "").strip(),
        "seq": int(seq),
        "ts_wall": round(time.time(), 6),
        "ts_monotonic_ms": int(max(0.0, (time.monotonic() - float(started_monotonic or time.monotonic())) * 1000.0)),
        "type": str(event_type or "").strip(),
        "payload": dict(payload or {}),
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
    def __init__(
        self,
        launch: TrackLaunch,
        session_id: str,
        root_pid: int,
        transcript_path: str,
        event_stream_path: str,
        state_store: SQLiteStateStore,
        start_snapshot: dict[str, Any] | None = None,
    ):
        self.launch = launch
        self.session_id = session_id
        self.root_pid = int(root_pid)
        self.root_session_id = _safe_getsid(root_pid) or int(root_pid)
        self.root_process_group_id = _safe_getpgid(root_pid) or int(root_pid)
        self.controller_pid = int(os.getpid())
        self.transcript_path = transcript_path
        self.event_stream_path = event_stream_path
        self.state_store = state_store
        self.stop_event = threading.Event()
        self._lock = threading.Lock()
        self.root_exit_code: int | None = None
        self.violation_code = ""
        self.started_monotonic = time.monotonic()
        self._event_seq = 0
        self._event_handle: Any | None = None
        self.transcript_event_count = 0
        self.private_key_path = _track_private_key_path()
        self.public_key_path = _track_public_key_path()
        self.start_snapshot = dict(start_snapshot or {})
        self.end_snapshot: dict[str, Any] = {}
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

    def set_event_handle(self, handle: Any) -> None:
        self._event_handle = handle

    def emit_event(self, event_type: str, payload: dict[str, Any] | None = None) -> int | None:
        handle = self._event_handle
        if handle is None or bool(getattr(handle, "closed", False)):
            return None
        with self._lock:
            self._event_seq += 1
            seq = self._event_seq
        try:
            _write_session_event(
                handle,
                session_id=self.session_id,
                seq=seq,
                started_monotonic=self.started_monotonic,
                event_type=event_type,
                payload=payload,
            )
        except ValueError:
            return None
        return seq

    def persist_summary(
        self,
        *,
        aggregate: dict[str, Any] | None = None,
        changes: dict[str, Any] | None = None,
    ) -> None:
        start_snapshot = dict(self.start_snapshot or {})
        end_snapshot = dict(self.end_snapshot or {})
        self.state_store.upsert_session_summary(
            session_id=self.session_id,
            repo_root=str(end_snapshot.get("repo_root", "") or start_snapshot.get("repo_root", "") or ""),
            branch_start=str(start_snapshot.get("branch", "") or ""),
            branch_end=str(end_snapshot.get("branch", "") or ""),
            head_start=str(start_snapshot.get("head", "") or ""),
            head_end=str(end_snapshot.get("head", "") or ""),
            start_snapshot=start_snapshot,
            end_snapshot=end_snapshot,
            aggregate=aggregate,
            changes=changes,
            event_stream_path=self.event_stream_path,
            created_at=int(start_snapshot.get("timestamp", 0) or time.time()),
            updated_at=int(time.time()),
        )

    def build_session_summary(self) -> tuple[dict[str, Any], dict[str, Any]]:
        runs = _find_session_runs(self.session_id, limit=500)
        label_counts: dict[str, int] = {}
        push_attempts = 0
        for row in runs:
            label = str(row.get("label", "") or "UNKNOWN")
            label_counts[label] = int(label_counts.get(label, 0) or 0) + 1
            command = str(row.get("command", "") or "").strip().lower()
            if command.startswith("git push") or " git push" in command:
                push_attempts += 1

        repo_root = str(self.end_snapshot.get("repo_root", "") or self.start_snapshot.get("repo_root", "") or "")
        head_start = str(self.start_snapshot.get("head", "") or "")
        head_end = str(self.end_snapshot.get("head", "") or "")
        committed_files = _git_changed_files_between(repo_root, head_start, head_end)
        worktree_files = [str(item) for item in self.end_snapshot.get("changed_files", []) if str(item)]
        files_changed = sorted({*committed_files, *worktree_files})
        commits_created = _git_commits_between(repo_root, head_start, head_end)

        aggregate = {
            "command_count": len(runs),
            "provenance_label_counts": label_counts,
            "subprocess_count": max(0, len(self.processes) - 1),
            "push_attempts": push_attempts,
            "commits_created": len(commits_created),
            "transcript_event_count": int(self.transcript_event_count),
            "structured_event_count": int(self._event_seq),
            "suspicious_events": [item for item in self.violation_code.split(",") if item],
        }
        changes = {
            "files_changed": files_changed,
            "committed_files": committed_files,
            "worktree_files": worktree_files,
            "committed_diff_stat": _git_diff_stat_between(repo_root, head_start, head_end),
            "worktree_diff_stat": str(self.end_snapshot.get("diff_stat", "") or ""),
            "commits_created": commits_created,
        }
        return (aggregate, changes)

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
        if payload["status"] in {"active", "stopping"}:
            _write_track_state(payload)
        else:
            _refresh_track_state_cache()

    def note_violation(self, code: str) -> None:
        clean = str(code or "").strip().lower()
        if not clean:
            return
        with self._lock:
            existing = [item for item in self.violation_code.split(",") if item]
            if clean in existing:
                return
            existing.append(clean)
            self.violation_code = ",".join(existing)
        self.emit_event("violation.noted", {"code": clean})
        state = self.state_store.get_tracked_session(self.session_id) or {}
        current_status = str(state.get("status", "") or "active").strip().lower() or "active"
        self.persist_state(current_status)

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
        self.emit_event(
            "command.recorded",
            {
                "pid": int(proc.pid),
                "command": proc.command,
                "label": str(classification.get("label", "UNKNOWN") or "UNKNOWN"),
                "exit_code": exit_code,
                "detached": bool(proc.detached),
            },
        )
        self.emit_event(
            "process.exited",
            {
                "pid": int(proc.pid),
                "ppid": int(proc.ppid),
                "command": proc.command,
                "exit_code": exit_code,
                "detached": bool(proc.detached),
                "session_escape": bool(proc.session_escape),
            },
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
                runtime.emit_event(
                    "process.spawned",
                    {
                        "pid": pid,
                        "ppid": int(row.get("ppid", 0) or 0),
                        "command": existing.command,
                        "working_directory": existing.working_directory,
                    },
                )
                command_text = str(existing.command or "").strip().lower()
                if command_text.startswith("git push") or " git push" in command_text:
                    runtime.emit_event(
                        "git.push.attempted",
                        {
                            "pid": pid,
                            "command": existing.command,
                        },
                    )
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
                runtime.note_violation("session_boundary_escape")
            if _looks_like_escape_primitive(row):
                runtime.note_violation("escape_primitive_blocked")
            if _looks_like_unmanaged_terminal_launch(row):
                runtime.note_violation("unmanaged_child_launch")

        if runtime.root_exit_code is not None and detached_finalize_deadline <= 0:
            detached_finalize_deadline = time.monotonic() + TRACK_FINAL_POLL_GRACE_SECONDS

        for pid, proc in list(runtime.processes.items()):
            if proc.finalized:
                continue
            if pid in descendant_ids:
                continue
            if _is_pid_alive(pid):
                proc.detached = True
                runtime.note_violation("detached_descendants")
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


def _apply_winsize(master_fd: int, stdin_fd: int) -> tuple[int, int] | None:
    try:
        raw = fcntl.ioctl(stdin_fd, termios.TIOCGWINSZ, struct.pack("HHHH", 0, 0, 0, 0))
        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, raw)
        rows, cols, _, _ = struct.unpack("HHHH", raw)
        return max(1, int(rows or 0)), max(1, int(cols or 0))
    except Exception:
        return None


def _drain_master_output(
    master_fd: int,
    transcript: Any,
    runtime: TrackRuntime,
    stdout_fd: int | None,
    checkpoint_recorder: subprocess.Popen[str] | None = None,
    *,
    timeout_seconds: float = 0.2,
) -> subprocess.Popen[str] | None:
    deadline = time.monotonic() + max(0.0, float(timeout_seconds or 0.0))
    while time.monotonic() < deadline:
        try:
            ready, _, _ = select.select([master_fd], [], [], 0.02)
        except Exception:
            return checkpoint_recorder
        if master_fd not in ready:
            continue
        try:
            data = os.read(master_fd, 4096)
        except OSError as exc:
            if exc.errno == errno.EIO:
                return checkpoint_recorder
            raise
        if not data:
            return checkpoint_recorder
        seq = runtime.emit_event(
            "terminal.stdout",
            {
                "stream": "stdout",
                "data_b64": base64.b64encode(bytes(data)).decode("ascii"),
                "size": len(data),
            },
        )
        _write_transcript_event(transcript, "pty", data, seq=seq)
        checkpoint_recorder = _send_checkpoint_event(
            checkpoint_recorder,
            direction="pty",
            seq=seq,
            data=data,
        )
        runtime.transcript_event_count += 1
        if stdout_fd is not None:
            os.write(stdout_fd, data)
        else:
            sys.stdout.write(data.decode("utf-8", errors="replace"))
            sys.stdout.flush()
    return checkpoint_recorder


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

    session_id = uuid.uuid4().hex[:16]
    transcript_path = _track_transcript_path(session_id)
    checkpoint_path = _track_checkpoint_path(session_id)
    event_stream_path = _track_event_stream_path(session_id)
    state_store = _state_store()
    start_snapshot = _capture_repo_snapshot(launch.working_directory)
    master_fd, slave_fd = os.openpty()
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
        console.print(f"[red]agensic run: command not found:[/red] {launch.command[0]}")
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
        console.print(f"[red]agensic run failed:[/red] {exc}")
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
    runtime = TrackRuntime(
        launch=launch,
        session_id=session_id,
        root_pid=pid,
        transcript_path=transcript_path,
        event_stream_path=event_stream_path,
        state_store=state_store,
        start_snapshot=start_snapshot,
    )
    runtime.persist_state("active")
    runtime.persist_summary()

    watcher = threading.Thread(target=_watch_tracked_process_tree, args=(runtime,), daemon=True)
    watcher_started = False

    old_tty = None
    stdin_fd = None
    stdout_fd = None
    resize_handler = None
    resize_pending = False
    pending_initial_winsize: tuple[int, int] | None = None
    last_transcript_winsize: tuple[int, int] | None = None
    checkpoint_recorder: subprocess.Popen[str] | None = None
    try:
        if sys.stdin.isatty():
            stdin_fd = sys.stdin.fileno()
            stdout_fd = sys.stdout.fileno()
            console.print(f"agensic session id {session_id}", highlight=False)
            old_tty = termios.tcgetattr(stdin_fd)
            tty.setraw(stdin_fd)
            pending_initial_winsize = _apply_winsize(master_fd, stdin_fd)

            def _on_resize(signum: int, frame: Any) -> None:
                nonlocal resize_pending
                resize_pending = True

            resize_handler = signal.getsignal(signal.SIGWINCH)
            signal.signal(signal.SIGWINCH, _on_resize)

        with open(transcript_path, "a", encoding="utf-8") as transcript, open(
            event_stream_path,
            "a",
            encoding="utf-8",
        ) as event_stream:
            checkpoint_recorder = _start_checkpoint_recorder(checkpoint_path)
            runtime.set_event_handle(event_stream)
            runtime.emit_event(
                "marker.session.started",
                {
                    "session_id": session_id,
                    "agent": runtime.launch.agent,
                    "model": runtime.launch.model,
                    "command": runtime.launch.root_command,
                },
            )
            runtime.emit_event("git.snapshot.start", dict(runtime.start_snapshot))
            if pending_initial_winsize is not None:
                rows, cols = pending_initial_winsize
                seq = runtime.emit_event(
                    "terminal.resize",
                    {
                        "rows": rows,
                        "cols": cols,
                    },
                )
                _write_transcript_resize_event(
                    transcript,
                    rows=rows,
                    cols=cols,
                    seq=seq,
                )
                checkpoint_recorder = _send_checkpoint_event(
                    checkpoint_recorder,
                    direction="resize",
                    seq=seq,
                    rows=rows,
                    cols=cols,
                )
                runtime.transcript_event_count += 1
                last_transcript_winsize = (rows, cols)
            watcher.start()
            watcher_started = True
            while True:
                if stdin_fd is not None and resize_pending:
                    resize_pending = False
                    next_winsize = _apply_winsize(master_fd, stdin_fd)
                    if next_winsize is not None and next_winsize != last_transcript_winsize:
                        rows, cols = next_winsize
                        seq = runtime.emit_event(
                            "terminal.resize",
                            {
                                "rows": rows,
                                "cols": cols,
                            },
                        )
                        _write_transcript_resize_event(
                            transcript,
                            rows=rows,
                            cols=cols,
                            seq=seq,
                        )
                        checkpoint_recorder = _send_checkpoint_event(
                            checkpoint_recorder,
                            direction="resize",
                            seq=seq,
                            rows=rows,
                            cols=cols,
                        )
                        runtime.transcript_event_count += 1
                        last_transcript_winsize = next_winsize
                read_fds = [master_fd]
                if stdin_fd is not None:
                    read_fds.append(stdin_fd)
                ready, _, _ = select.select(read_fds, [], [], 0.1)

                if stdin_fd is not None and stdin_fd in ready:
                    data = os.read(stdin_fd, 4096)
                    if data:
                        seq = runtime.emit_event(
                            "terminal.stdin",
                            {
                                "stream": "stdin",
                                "data_b64": base64.b64encode(bytes(data)).decode("ascii"),
                                "size": len(data),
                            },
                        )
                        _write_transcript_event(transcript, "stdin", data, seq=seq)
                        runtime.transcript_event_count += 1
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
                        seq = runtime.emit_event(
                            "terminal.stdout",
                            {
                                "stream": "stdout",
                                "data_b64": base64.b64encode(bytes(data)).decode("ascii"),
                                "size": len(data),
                            },
                        )
                        _write_transcript_event(transcript, "pty", data, seq=seq)
                        checkpoint_recorder = _send_checkpoint_event(
                            checkpoint_recorder,
                            direction="pty",
                            seq=seq,
                            data=data,
                        )
                        runtime.transcript_event_count += 1
                        if stdout_fd is not None:
                            os.write(stdout_fd, data)
                        else:
                            sys.stdout.write(data.decode("utf-8", errors="replace"))
                            sys.stdout.flush()

                exit_code = proc.poll()
                if exit_code is not None:
                    runtime.root_exit_code = int(128 + abs(exit_code)) if exit_code < 0 else int(exit_code)
                    checkpoint_recorder = _drain_master_output(
                        master_fd,
                        transcript,
                        runtime,
                        stdout_fd,
                        checkpoint_recorder,
                    )
                    break
            runtime.stop_event.set()
            if watcher_started:
                watcher.join(timeout=5.0)
                watcher_started = False
            runtime.end_snapshot = _capture_repo_snapshot(runtime.launch.working_directory)
            runtime.emit_event("git.snapshot.end", dict(runtime.end_snapshot))
            for commit in _git_commits_between(
                str(runtime.end_snapshot.get("repo_root", "") or runtime.start_snapshot.get("repo_root", "") or ""),
                str(runtime.start_snapshot.get("head", "") or ""),
                str(runtime.end_snapshot.get("head", "") or ""),
            ):
                runtime.emit_event("git.commit.created", commit)
            runtime.emit_event(
                "marker.session.finished",
                {
                    "exit_code": runtime.root_exit_code,
                    "violation_code": runtime.violation_code,
                },
            )
    finally:
        runtime.stop_event.set()
        if watcher_started:
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
        _stop_checkpoint_recorder(
            checkpoint_recorder,
            final_seq=runtime._event_seq if "runtime" in locals() else None,
        )

    final_state = state_store.get_tracked_session(session_id) or _load_track_state()
    final_status = "stopped" if str(final_state.get("status", "") or "").strip().lower() == "stopping" else "exited"
    runtime.persist_state(final_status, exit_code=runtime.root_exit_code if runtime.root_exit_code is not None else 1)
    aggregate, changes = runtime.build_session_summary()
    runtime.persist_summary(aggregate=aggregate, changes=changes)
    _prune_tracked_transcripts(exclude_paths={transcript_path, event_stream_path, checkpoint_path})
    _refresh_track_state_cache()
    return int(runtime.root_exit_code if runtime.root_exit_code is not None else 1)
