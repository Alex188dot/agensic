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
TRACK_POLL_INTERVAL_SECONDS = 0.15
TRACK_STOP_GRACE_SECONDS = 2.0
TRACK_UNMANAGED_WINDOW_TOKENS = (
    "terminal.app",
    "iterm.app",
    "warp.app",
    "ghostty",
)


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
    detached: bool = False
    finalized: bool = False


def _track_session_state_path() -> str:
    return os.path.join(APP_PATHS.state_dir, "track_session.json")


def _track_transcripts_dir() -> str:
    return os.path.join(APP_PATHS.state_dir, "tracked_sessions")


def _state_store() -> SQLiteStateStore:
    return SQLiteStateStore(APP_PATHS.state_sqlite_path, journal=None)


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


def _cleanup_stale_track_state() -> dict[str, Any]:
    state = _load_track_state()
    if not state:
        return {}

    status = str(state.get("status", "") or "").strip().lower()
    controller_pid = int(state.get("controller_pid", 0) or 0)
    root_pid = int(state.get("root_pid", 0) or 0)
    controller_alive = controller_pid > 0 and _is_pid_alive(controller_pid)
    root_alive = root_pid > 0 and _is_pid_alive(root_pid)
    if status in {"active", "stopping"} and (controller_alive or root_alive):
        return state

    session_id = str(state.get("session_id", "") or "").strip()
    if session_id:
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
                controller_pid=controller_pid or None,
                root_pid=root_pid or None,
                started_at=int(state.get("started_at", 0) or 0),
                ended_at=int(time.time()),
                updated_at=int(time.time()),
                violation_code=str(state.get("violation_code", "") or "stale_session"),
                exit_code=state.get("exit_code"),
            )
        except Exception:
            pass
    _clear_track_state()
    return {}


def get_active_track_state() -> dict[str, Any]:
    return _cleanup_stale_track_state()


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


def _infer_track_model(*, command: list[str], agent: str, env: dict[str, str] | None = None) -> str:
    if _looks_like_codex_launch(command=command, agent=agent):
        return _infer_codex_model(env)
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


def _read_process_snapshot() -> dict[int, dict[str, Any]]:
    try:
        run = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,pgid=,stat=,comm=,args="],
            capture_output=True,
            text=True,
            check=False,
            timeout=1.2,
        )
    except Exception:
        return {}

    out: dict[int, dict[str, Any]] = {}
    for raw_line in (run.stdout or "").splitlines():
        line = str(raw_line or "").strip()
        if not line:
            continue
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
            pgid = int(parts[2])
        except ValueError:
            continue
        out[pid] = {
            "pid": pid,
            "ppid": ppid,
            "pgid": pgid,
            "stat": str(parts[3] or "").strip(),
            "comm": str(parts[4] or "").strip(),
            "args": str(parts[5] or "").strip(),
        }
    return out


def _descendant_rows(snapshot: dict[int, dict[str, Any]], root_pid: int) -> dict[int, dict[str, Any]]:
    if root_pid <= 0 or root_pid not in snapshot:
        return {}
    children: dict[int, list[int]] = {}
    for pid, row in snapshot.items():
        parent = int(row.get("ppid", 0) or 0)
        children.setdefault(parent, []).append(pid)

    out: dict[int, dict[str, Any]] = {}
    stack = [root_pid]
    while stack:
        current = stack.pop()
        if current in out:
            continue
        row = snapshot.get(current)
        if row is None:
            continue
        out[current] = row
        stack.extend(children.get(current, []))
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


class TrackRuntime:
    def __init__(self, launch: TrackLaunch, session_id: str, root_pid: int, transcript_path: str, state_store: SQLiteStateStore):
        self.launch = launch
        self.session_id = session_id
        self.root_pid = int(root_pid)
        self.controller_pid = int(os.getpid())
        self.transcript_path = transcript_path
        self.state_store = state_store
        self.stop_event = threading.Event()
        self._lock = threading.Lock()
        self.root_exit_code: int | None = None
        self.violation_code = ""
        self.processes: dict[int, ObservedProcess] = {
            self.root_pid: ObservedProcess(
                pid=self.root_pid,
                ppid=self.controller_pid,
                command=self.launch.root_command,
                working_directory=self.launch.working_directory,
                started_at=time.time(),
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
        _write_track_state(payload)
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
        self.persist_state(_load_track_state().get("status", "active") or "active")

    def finalize_process(self, proc: ObservedProcess, *, exit_code: int | None = None) -> None:
        if proc.finalized:
            return
        proc.finalized = True
        duration_ms = max(0, int((time.time() - float(proc.started_at or time.time())) * 1000.0))
        working_directory = proc.working_directory or self.launch.working_directory
        trace_id = f"track-{self.session_id}-{proc.pid}"
        ts = int(time.time())
        signature = sign_proof_payload("AI_EXECUTED", self.launch.agent, self.launch.model, trace_id, ts)
        proof_metadata = build_local_proof_metadata()
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
        if exit_code is None:
            payload["track_exit_code_unavailable"] = True

        classification = classify_command_run(proc.command, payload)
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
    while True:
        snapshot = _read_process_snapshot()
        descendants = _descendant_rows(snapshot, runtime.root_pid)
        descendant_ids = set(descendants.keys())
        live_ids = set(snapshot.keys())

        for pid, row in descendants.items():
            if pid not in runtime.processes:
                runtime.processes[pid] = ObservedProcess(
                    pid=pid,
                    ppid=int(row.get("ppid", 0) or 0),
                    command=str(row.get("args", "") or "").strip() or str(row.get("comm", "") or "").strip(),
                    working_directory=_best_effort_cwd(pid),
                    started_at=time.time(),
                )
            if _looks_like_unmanaged_terminal_launch(row):
                runtime.note_violation("unmanaged_child_launch")

        for pid, proc in list(runtime.processes.items()):
            if proc.finalized:
                continue
            if pid in descendant_ids:
                continue
            if pid in live_ids:
                proc.detached = True
                runtime.note_violation("detached_descendants")
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


def run_tracked_command(launch: TrackLaunch) -> int:
    ensure_track_supported()
    _ensure_track_layout()
    active = get_active_track_state()
    if active:
        console.print("[red]A tracked session is already active.[/red]")
        return 1

    transcript_path = os.path.join(_track_transcripts_dir(), f"{uuid.uuid4().hex}.jsonl")
    state_store = _state_store()
    master_fd, slave_fd = os.openpty()
    session_id = uuid.uuid4().hex[:16]
    child_env = _build_tracked_child_env(launch, session_id)
    pid = os.fork()

    if pid == 0:
        try:
            os.setsid()
            try:
                fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
            except Exception:
                pass
            os.dup2(slave_fd, 0)
            os.dup2(slave_fd, 1)
            os.dup2(slave_fd, 2)
            os.close(master_fd)
            if slave_fd > 2:
                os.close(slave_fd)
            os.chdir(launch.working_directory)
            os.execvpe(launch.command[0], launch.command, child_env)
        except FileNotFoundError:
            os.write(2, f"agensic track: command not found: {launch.command[0]}\n".encode("utf-8"))
            os._exit(127)
        except Exception as exc:
            os.write(2, f"agensic track failed: {exc}\n".encode("utf-8"))
            os._exit(1)

    os.close(slave_fd)
    runtime = TrackRuntime(launch=launch, session_id=session_id, root_pid=pid, transcript_path=transcript_path, state_store=state_store)
    runtime.persist_state("active")

    watcher = threading.Thread(target=_watch_tracked_process_tree, args=(runtime,), daemon=True)
    watcher.start()

    old_tty = None
    stdin_fd = None
    stdout_fd = None
    resize_handler = None

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

                ended_pid, wait_status = os.waitpid(pid, os.WNOHANG)
                if ended_pid == pid:
                    if os.WIFEXITED(wait_status):
                        runtime.root_exit_code = int(os.WEXITSTATUS(wait_status))
                    elif os.WIFSIGNALED(wait_status):
                        runtime.root_exit_code = int(128 + os.WTERMSIG(wait_status))
                    else:
                        runtime.root_exit_code = 1
                    break
    finally:
        runtime.stop_event.set()
        watcher.join(timeout=5.0)
        try:
            os.close(master_fd)
        except Exception:
            pass
        if resize_handler is not None:
            try:
                signal.signal(signal.SIGWINCH, resize_handler)
            except Exception:
                pass
        if old_tty is not None and stdin_fd is not None:
            try:
                termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_tty)
            except Exception:
                pass

    final_state = _load_track_state()
    final_status = "stopped" if str(final_state.get("status", "") or "").strip().lower() == "stopping" else "exited"
    runtime.persist_state(final_status, exit_code=runtime.root_exit_code if runtime.root_exit_code is not None else 1)
    _clear_track_state()
    return int(runtime.root_exit_code if runtime.root_exit_code is not None else 1)
