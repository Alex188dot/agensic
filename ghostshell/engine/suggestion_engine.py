import os
import logging
import json
import re
import shutil
import threading
import time
import platform
import asyncio
import subprocess
from pathlib import Path
from litellm import acompletion
import requests
from ghostshell.state import EventJournal, SnapshotManager, SnapshotScheduler, SQLiteStateStore
from ghostshell.config.loader import (
    DEFAULT_TIMEOUT_SECONDS,
    MAX_TIMEOUT_SECONDS,
    MIN_TIMEOUT_SECONDS,
    load_config_file,
)
from .provenance import (
    classify_command_run,
    get_agent_registry,
    get_registry_agent,
    get_registry_summary,
    list_registry_agents,
    refresh_agent_registry,
    verify_cached_agent_registry,
)
from ghostshell.privacy.guard import PrivacyGuard, PrivacyGuardError
from .context import RequestContext, Settings, SystemInventory

logger = logging.getLogger("ghostshell.engine")

_PROVIDER_PREFIXES: dict[str, str] = {
    "ollama": "ollama/",
    "lm_studio": "lm_studio/",
    "gemini": "gemini/",
    "dashscope": "dashscope/",
    "minimax": "minimax/",
    "deepseek": "deepseek/",
    "moonshot": "moonshot/",
    "mistral": "mistral/",
    "openrouter": "openrouter/",
    "xiaomi_mimo": "xiaomi_mimo/",
    "zai": "zai/",
    "sagemaker": "sagemaker/",
}

_PROVIDER_DEFAULT_BASE_URLS: dict[str, str] = {
    "ollama": "http://localhost:11434",
    "lm_studio": "http://localhost:1234/v1",
    "dashscope": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    "minimax": "https://api.minimax.io/anthropic/v1/messages",
    "moonshot": "https://api.moonshot.ai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}

_PROVIDER_ENV_API_KEYS: dict[str, str] = {
    "groq": "GROQ_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "dashscope": "DASHSCOPE_API_KEY",
    "minimax": "MINIMAX_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "moonshot": "MOONSHOT_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "xiaomi_mimo": "XIAOMI_MIMO_API_KEY",
    "zai": "ZAI_API_KEY",
}

COMMAND_PROVENANCE_RETENTION_DAYS = 365
COMMAND_PROVENANCE_RETENTION_SECONDS = COMMAND_PROVENANCE_RETENTION_DAYS * 24 * 3600


def command_provenance_prune_cutoff(now_ts: int) -> int:
    return int(now_ts or 0) - COMMAND_PROVENANCE_RETENTION_SECONDS


class SuggestionEngine:
    def __init__(self):
        self.inventory = self._get_simple_inventory()
        self.privacy_guard = PrivacyGuard(
            history_max_lines=Settings.llm_history_lines,
        )
        self.vector_db = None
        self._vector_db_lock = threading.Lock()
        self._vector_db_ready = threading.Event()
        self._bootstrap_lock = threading.Lock()
        self._bootstrap_thread = None
        self._bootstrap_history_file = ""
        self._bootstrap_completed_for = ""
        self._bootstrap_error = ""
        self._storage_state = "unknown"
        self._storage_error_code = ""
        self._storage_error_detail = ""
        self._vector_db_retry_not_before = 0.0
        self._vector_db_retry_reason = ""
        self.state_backend = "sqlite"
        self.sqlite_state = "unknown"
        self.journal_state = "unavailable"
        self.snapshot_state = "missing"
        self.auto_recover_attempted = False
        self.auto_recover_result = "skipped"
        self._last_command_runs_prune_ts = 0
        self.state_store = None
        self.event_journal = None
        self.snapshot_manager = None
        self.snapshot_scheduler = None
        self._init_state_runtime()

    def _init_state_runtime(self) -> None:
        home = os.path.expanduser("~/.ghostshell")
        sqlite_path = os.path.join(home, "state.sqlite")
        events_dir = os.path.join(home, "events")
        snapshots_dir = os.path.join(home, "snapshots")
        try:
            self.event_journal = EventJournal(events_dir)
            self.state_store = SQLiteStateStore(sqlite_path, journal=self.event_journal)
            self.snapshot_manager = SnapshotManager(sqlite_path, snapshots_dir)
            self.snapshot_scheduler = SnapshotScheduler(
                self.snapshot_manager,
                self.event_journal,
                interval_seconds=300,
                retention_seconds=24 * 3600,
                max_total_bytes=200 * 1024 * 1024,
            )
            self.journal_state = "healthy"
            self.snapshot_state = self._compute_snapshot_state()

            ok, message = self.state_store.integrity_check()
            if ok:
                mode = self.state_store.access_mode()
                if mode == "read_only":
                    self.sqlite_state = "read_only"
                    self.auto_recover_attempted = False
                    self.auto_recover_result = "skipped"
                    self._set_storage_health(
                        "unknown",
                        "sqlite_read_only",
                        "SQLite opened in read-only mode",
                    )
                else:
                    self.sqlite_state = "healthy"
                    self.auto_recover_attempted = False
                    self.auto_recover_result = "skipped"
            else:
                self.sqlite_state = "recovering"
                self.auto_recover_attempted = True
                result = self.state_store.recover_from_latest_snapshot(
                    self.snapshot_manager,
                    self.event_journal,
                )
                if bool(result.get("ok", False)):
                    self.sqlite_state = "healthy"
                    self.auto_recover_result = "ok"
                    self.snapshot_state = self._compute_snapshot_state()
                else:
                    self.sqlite_state = "corrupt"
                    self.auto_recover_result = "failed"
                    sanitized = self.privacy_guard.sanitize_for_log(
                        str(result.get("restore_error", "") or message)
                    )
                    self.state_store = None
                    self._set_storage_health("corrupt", "sqlite_recover_failed", sanitized)

            if self.snapshot_scheduler is not None:
                self.snapshot_scheduler.start()
        except Exception as exc:
            sanitized = self.privacy_guard.sanitize_for_log(str(exc))
            self.state_store = None
            self.event_journal = None
            self.snapshot_manager = None
            self.snapshot_scheduler = None
            self.sqlite_state = "corrupt"
            self.journal_state = "unavailable"
            self.snapshot_state = "missing"
            self.auto_recover_attempted = True
            self.auto_recover_result = "failed"
            self._set_storage_health("corrupt", "sqlite_init_failed", sanitized)
            logger.error("Failed to initialize SQLite state backend: %s", sanitized)

    def _compute_snapshot_state(self) -> str:
        if self.snapshot_manager is None:
            return "missing"
        try:
            latest = self.snapshot_manager.latest_snapshot()
            if latest is None:
                return "missing"
            ts = int(latest.get("snapshot_ts", 0) or 0)
            if ts <= 0:
                return "missing"
            age_seconds = max(0, int(time.time()) - ts)
            if age_seconds > (2 * 3600):
                return "stale"
            return "healthy"
        except Exception:
            return "missing"

    @staticmethod
    def _classify_storage_issue(message: str) -> tuple[str, str]:
        text = str(message or "").strip()
        low = text.lower()
        corruption_tokens = (
            "segment not found",
            "segment.cc",
            "vector indexer not found",
            "invalid checksum",
            "checksum",
            "corrupt",
            "failed to open index",
        )
        if any(token in low for token in corruption_tokens):
            return ("corrupt", "vector_db_corrupt")
        return ("unknown", "")

    def _set_storage_health(self, state: str, code: str = "", detail: str = "") -> None:
        with self._bootstrap_lock:
            self._storage_state = str(state or "unknown")
            self._storage_error_code = str(code or "")
            self._storage_error_detail = str(detail or "")

    @staticmethod
    def _is_lock_file_error(message: str) -> bool:
        low = str(message or "").lower()
        return "lock file" in low or "can't open lock file" in low

    def _set_vector_db_backoff(self, reason: str, cooldown_seconds: float = 4.0) -> None:
        with self._bootstrap_lock:
            self._vector_db_retry_not_before = time.monotonic() + max(0.5, cooldown_seconds)
            self._vector_db_retry_reason = str(reason or "Vector DB temporarily unavailable")

    def _clear_vector_db_backoff(self) -> None:
        with self._bootstrap_lock:
            self._vector_db_retry_not_before = 0.0
            self._vector_db_retry_reason = ""

    def _vector_db_retry_window(self) -> tuple[float, str]:
        with self._bootstrap_lock:
            return (self._vector_db_retry_not_before, self._vector_db_retry_reason)

    @staticmethod
    def _zvec_lock_paths() -> list[str]:
        root = os.path.expanduser("~/.ghostshell")
        return [
            os.path.join(root, "zvec_commands", "LOCK"),
            os.path.join(root, "zvec_commands", "idmap.0", "LOCK"),
            os.path.join(root, "zvec_feedback_stats", "LOCK"),
            os.path.join(root, "zvec_feedback_stats", "idmap.0", "LOCK"),
        ]

    def _remove_stale_zvec_locks(self) -> int:
        if shutil.which("lsof") is None:
            return 0
        removed = 0
        for path in self._zvec_lock_paths():
            if not os.path.exists(path):
                continue
            owner_pids: list[int] = []
            inspection_failed = False
            try:
                probe = subprocess.run(
                    ["lsof", "-t", path],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=1.2,
                )
                if probe.returncode == 0 and probe.stdout.strip():
                    for line in probe.stdout.splitlines():
                        value = line.strip()
                        if value.isdigit():
                            owner_pids.append(int(value))
            except Exception:
                inspection_failed = True
            if inspection_failed:
                continue

            alive_owners = []
            for pid in owner_pids:
                try:
                    os.kill(pid, 0)
                    alive_owners.append(pid)
                except ProcessLookupError:
                    continue
                except Exception:
                    alive_owners.append(pid)

            if alive_owners:
                continue

            try:
                os.remove(path)
                removed += 1
            except OSError:
                continue
        return removed

    def _ensure_vector_db(self, force_retry: bool = False):
        if self.vector_db is not None:
            return self.vector_db

        with self._vector_db_lock:
            if self.vector_db is None:
                retry_not_before, retry_reason = self._vector_db_retry_window()
                if (
                    not force_retry
                    and retry_not_before > 0
                    and time.monotonic() < retry_not_before
                ):
                    raise RuntimeError(retry_reason or "Vector DB retry backoff active")
                try:
                    from ghostshell.vector_db import CommandVectorDB
                    self.vector_db = CommandVectorDB(state_store=self.state_store)
                    self._vector_db_ready.set()
                    self._clear_vector_db_backoff()
                except Exception as exc:
                    message = self.privacy_guard.sanitize_for_log(str(exc))
                    if self._is_lock_file_error(message):
                        self._set_vector_db_backoff(message, cooldown_seconds=4.0)
                    self.vector_db = None
                    self._vector_db_ready.clear()
                    raise
        return self.vector_db

    def _bootstrap_worker(self, history_file: str):
        try:
            logger.info("Starting vector DB bootstrap in background")
            try:
                vector_db = self._ensure_vector_db()
            except Exception as lock_exc:
                sanitized = self.privacy_guard.sanitize_for_log(str(lock_exc))
                if self._is_lock_file_error(sanitized):
                    removed = self._remove_stale_zvec_locks()
                    if removed > 0:
                        logger.warning(
                            "Removed %d stale zvec lock file(s); retrying bootstrap once",
                            removed,
                        )
                        vector_db = self._ensure_vector_db(force_retry=True)
                    else:
                        self._set_vector_db_backoff(sanitized, cooldown_seconds=4.0)
                        raise
                else:
                    raise
            if history_file:
                vector_db.initialize_from_history(history_file)
            self._set_storage_health("healthy", "", "")
            logger.info("Background history sync complete")
        except Exception as e:
            sanitized = self.privacy_guard.sanitize_for_log(str(e))
            state, code = self._classify_storage_issue(sanitized)
            if state == "corrupt":
                self._set_storage_health("corrupt", code, sanitized)
            else:
                self._set_storage_health("unknown", "", sanitized)
            with self._bootstrap_lock:
                self._bootstrap_error = sanitized
            logger.error(
                "Background history sync failed: %s",
                sanitized,
            )
        finally:
            with self._bootstrap_lock:
                self._bootstrap_completed_for = history_file

    def bootstrap_async(self, history_file: str):
        history_file = (history_file or "").strip()
        if not history_file:
            return

        history_file = os.path.expanduser(history_file)
        with self._bootstrap_lock:
            retry_not_before = self._vector_db_retry_not_before
            if (
                retry_not_before > 0
                and time.monotonic() < retry_not_before
                and self._bootstrap_completed_for == history_file
                and not self._vector_db_ready.is_set()
            ):
                return
            if (
                self._bootstrap_completed_for == history_file
                and self._vector_db_ready.is_set()
            ):
                return

            if (
                self._bootstrap_thread
                and self._bootstrap_thread.is_alive()
                and self._bootstrap_history_file == history_file
            ):
                return

            self._bootstrap_error = ""
            self._bootstrap_history_file = history_file
            self._bootstrap_thread = threading.Thread(
                target=self._bootstrap_worker,
                args=(history_file,),
                daemon=True,
                name="ghostshell-history-index",
            )
            self._bootstrap_thread.start()

    def get_bootstrap_status(self) -> dict:
        with self._bootstrap_lock:
            thread = self._bootstrap_thread
            history_file = self._bootstrap_history_file
            completed_for = self._bootstrap_completed_for
            bootstrap_error = self._bootstrap_error
            storage_state = self._storage_state
            storage_error_code = self._storage_error_code
            storage_error_detail = self._storage_error_detail
            sqlite_state = self.sqlite_state
            journal_state = self.journal_state
            snapshot_state = self.snapshot_state
            auto_recover_attempted = self.auto_recover_attempted
            auto_recover_result = self.auto_recover_result

        running = bool(thread and thread.is_alive())
        ready = bool(
            self._vector_db_ready.is_set()
            and history_file
            and completed_for == history_file
            and not running
        )

        indexed_commands = 0
        if self.vector_db is not None and hasattr(self.vector_db, "inserted_commands"):
            try:
                indexed_commands = len(self.vector_db.inserted_commands)
            except Exception:
                indexed_commands = 0

        phase = "starting"
        model_download_in_progress = False
        model_download_needed = False
        error = ""
        if self.vector_db is not None and hasattr(self.vector_db, "get_init_status"):
            try:
                init_status = self.vector_db.get_init_status()
                phase = str(init_status.get("phase") or phase)
                model_download_in_progress = bool(
                    init_status.get("model_download_in_progress", False)
                )
                model_download_needed = bool(
                    init_status.get("model_download_needed", False)
                )
                error = str(init_status.get("error") or "")
            except Exception:
                pass
        else:
            try:
                from ghostshell.vector_db import get_runtime_init_status

                init_status = get_runtime_init_status()
                phase = str(init_status.get("phase") or phase)
                model_download_in_progress = bool(
                    init_status.get("model_download_in_progress", False)
                )
                model_download_needed = bool(
                    init_status.get("model_download_needed", False)
                )
                error = str(init_status.get("error") or "")
            except Exception:
                pass

        if phase == "error" and error:
            state, code = self._classify_storage_issue(error)
            if state == "corrupt":
                storage_state = "corrupt"
                storage_error_code = code
                storage_error_detail = error

        if bootstrap_error:
            phase = "error"
            error = bootstrap_error
            if storage_state not in {"corrupt"}:
                storage_state = "unknown"
                storage_error_detail = bootstrap_error
        elif ready and phase != "error":
            phase = "ready"
            error = ""
            if storage_state not in {"corrupt"}:
                storage_state = "healthy"
                storage_error_code = ""
                storage_error_detail = ""

        snapshot_state = self._compute_snapshot_state() if self.snapshot_manager is not None else snapshot_state

        return {
            "running": running,
            "ready": ready,
            "history_file": history_file,
            "indexed_commands": indexed_commands,
            "phase": phase,
            "model_download_in_progress": model_download_in_progress,
            "model_download_needed": model_download_needed,
            "error": error,
            "storage_state": storage_state or "unknown",
            "storage_error_code": storage_error_code or "",
            "storage_error_detail": storage_error_detail or "",
            "state_backend": self.state_backend,
            "sqlite_state": sqlite_state or "unknown",
            "journal_state": journal_state or "unavailable",
            "snapshot_state": snapshot_state or "missing",
            "auto_recover_attempted": bool(auto_recover_attempted),
            "auto_recover_result": auto_recover_result or "skipped",
        }

    def export_repair_snapshot(self) -> dict:
        if self.state_store is not None:
            snapshot = self.state_store.export_payload()
            if self.snapshot_manager is not None:
                latest = self.snapshot_manager.latest_snapshot()
                snapshot["latest_snapshot"] = latest or {}
            return snapshot
        vector_db = self._ensure_vector_db()
        return vector_db.export_repair_snapshot(include_feedback=True)

    def import_repair_snapshot(self, payload: dict) -> dict:
        if self.state_store is not None:
            result = self.state_store.import_payload(payload if isinstance(payload, dict) else {})
            self.sqlite_state = "healthy"
            self.auto_recover_result = "ok"
            self._set_storage_health("repaired", "", "")
            return result
        vector_db = self._ensure_vector_db()
        result = vector_db.import_repair_snapshot(payload if isinstance(payload, dict) else {})
        self._set_storage_health("repaired", "", "")
        return result

    def recover_state_from_snapshot(self) -> dict:
        if self.state_store is None or self.snapshot_manager is None:
            return {
                "ok": False,
                "reason": "state_backend_unavailable",
                "replay": {"total": 0, "applied": 0, "skipped": 0},
            }
        result = self.state_store.recover_from_latest_snapshot(
            self.snapshot_manager,
            self.event_journal,
        )
        if bool(result.get("ok", False)):
            self.sqlite_state = "healthy"
            self.auto_recover_attempted = True
            self.auto_recover_result = "ok"
            self._set_storage_health("repaired", "", "")
        else:
            self.sqlite_state = "corrupt"
            self.auto_recover_attempted = True
            self.auto_recover_result = "failed"
            reason = str(result.get("restore_error", "") or "recover_failed")
            self._set_storage_health("corrupt", "sqlite_recover_failed", reason)
        return result

    def _safe_tail(self, path: str, max_lines: int) -> list[str]:
        if not path: return []
        candidate = Path(path).expanduser()
        if not candidate.exists() or not candidate.is_file(): return []
        try:
            # Read minimal amount
            size = candidate.stat().st_size
            # Rough estimation: 100 bytes per line
            read_size = max_lines * 200
            with open(candidate, 'rb') as f:
                if size > read_size:
                    f.seek(-read_size, 2)
                lines = f.read().decode('utf-8', errors='ignore').splitlines()
            return [line.strip() for line in lines[-max_lines:] if line.strip()]
        except Exception:
            return []

    def _list_working_dir(self, path: str, max_items: int = 60) -> list[str]:
        items: list[str] = []
        try:
            with os.scandir(path) as it:
                for entry in it:
                    if entry.name.startswith("."): continue
                    suffix = "/" if entry.is_dir() else ""
                    items.append(entry.name + suffix)
                    if len(items) >= max_items: break
        except OSError:
            return []
        return sorted(items)

    def _get_simple_inventory(self) -> SystemInventory:
        inv = SystemInventory()
        # Scan PATH for common tools (cached in init roughly)
        paths = os.environ.get("PATH", "").split(os.pathsep)
        cmds = set()
        for p in paths[:2]: 
            if os.path.exists(p) and os.path.isdir(p):
                try:
                    # Just grab a handful to populate context, not exhaustively
                    for i, f in enumerate(os.listdir(p)):
                        if i > 20: break
                        cmds.add(f)
                except: pass
        inv.commands = list(cmds)
        
        # Check managers
        if shutil.which("pip"): inv.package_sources.append("pip")
        if shutil.which("npm"): inv.package_sources.append("npm")
        if shutil.which("cargo"): inv.package_sources.append("cargo")
        return inv

    def _filter_blocked_full_commands(self, commands: list[str]) -> list[str]:
        filtered: list[str] = []
        for command in commands:
            normalized = (command or "").strip()
            if not normalized:
                continue
            if self._is_blocked_command(normalized):
                continue
            filtered.append(normalized)
        return filtered

    def _get_vector_candidates(self, ctx: RequestContext) -> list[dict[str, str]]:
        """
        Get command suggestions from the vector database.
        Returns structured candidates for suffix append or full replacement.
        """
        prefix = ctx.buffer.strip()
        if len(prefix) < 2:
            return []

        if ctx.history_file:
            self.bootstrap_async(ctx.history_file)

        if not self._vector_db_ready.is_set() or self.vector_db is None:
            return []

        try:
            matches = self.vector_db.get_prefix_or_semantic_matches(prefix, topk=100)
        except Exception as e:
            sanitized = self.privacy_guard.sanitize_for_log(str(e))
            state, code = self._classify_storage_issue(sanitized)
            if state == "corrupt":
                self._set_storage_health("corrupt", code, sanitized)
            logger.error("Vector DB lookup failed: %s", sanitized)
            return []

        first_mode = matches[0].get("match_mode", "") if matches else ""
        if not matches or first_mode != "prefix":
            try:
                typo_candidate = self.vector_db.get_word_typo_candidate(ctx.buffer)
            except Exception as e:
                logger.warning(
                    "Word typo lookup failed: %s",
                    self.privacy_guard.sanitize_for_log(str(e)),
                )
                typo_candidate = None
            if typo_candidate is not None:
                corrected_prefix = (typo_candidate.get("corrected_prefix", "") or "").strip()
                if corrected_prefix and not self._is_blocked_command(corrected_prefix):
                    return [
                        {
                            "display_text": f" Did you mean: {corrected_prefix}", # extra space is needed otherwise it will be too close to the user command, DO NOT REMOVE IT
                            "accept_text": corrected_prefix,
                            "accept_mode": "replace_full",
                            "kind": "typo_recovery",
                        }
                    ]
            if not matches:
                return []

        candidates: list[dict[str, str]] = []
        if first_mode == "prefix":
            suffixes: list[str] = []
            for item in matches:
                cmd = item.get("command", "")
                if not cmd.startswith(prefix) or cmd == prefix:
                    continue
                suffixes.append(cmd[len(prefix):])
            if self.vector_db is not None and suffixes:
                suffixes = self.vector_db.rerank_candidates(
                    ctx.buffer,
                    suffixes,
                    working_directory=ctx.cwd,
                )
            suffixes = self._filter_blocked_candidates(ctx.buffer, suffixes)
            for suffix in suffixes:
                candidates.append(
                    {
                        "display_text": suffix,
                        "accept_text": suffix,
                        "accept_mode": "suffix_append",
                        "kind": "normal",
                    }
                )
            return candidates

        semantic_mode = first_mode.startswith("semantic")
        full_commands = [item.get("command", "") for item in matches]
        full_commands = self._filter_blocked_full_commands(full_commands)
        for command in full_commands:
            if semantic_mode:
                display = f" Did you mean: {command}" # extra space is needed otherwise it will be too close to the user command, DO NOT REMOVE IT
                kind = "semantic_recovery"
            else:
                display = command
                kind = "normal"
            candidates.append(
                {
                    "display_text": display,
                    "accept_text": command,
                    "accept_mode": "replace_full",
                    "kind": kind,
                }
            )
        return candidates

    def _is_blocked_command(self, command: str) -> bool:
        if self.vector_db is not None:
            return self.vector_db.is_blocked_command(command)
        from ghostshell.vector_db import CommandVectorDB
        return CommandVectorDB.is_blocked_command(command)

    def _filter_blocked_candidates(self, buffer: str, candidates: list[str]) -> list[str]:
        if not candidates:
            return []

        from ghostshell.vector_db import CommandVectorDB

        filtered: list[str] = []
        for suffix in candidates:
            if not suffix:
                continue
            standalone_command = CommandVectorDB.normalize_command(
                CommandVectorDB.canonicalize_shell_spacing(suffix)
            )
            full_command = CommandVectorDB.normalize_command(
                CommandVectorDB.canonicalize_shell_spacing(
                    CommandVectorDB.merge_buffer_and_suffix(buffer, suffix)
                )
            )
            if not full_command and not standalone_command:
                continue
            if standalone_command and self._is_blocked_command(standalone_command):
                continue
            if full_command and self._is_blocked_command(full_command):
                continue
            filtered.append(suffix)
        return filtered

    def build_prompt_context(self, request: RequestContext) -> str:
        history = self._safe_tail(request.history_file, Settings.history_lines)
        cwd_items = self._list_working_dir(request.cwd)

        sanitized_history, _ = self.privacy_guard.sanitize_history_lines(history)
        shell_value = self.privacy_guard.sanitize_text(request.shell, context="prompt_shell").text
        cwd_value = self.privacy_guard.sanitize_text(request.cwd, context="prompt_cwd").text
        buffer_value = self.privacy_guard.sanitize_text(request.buffer, context="prompt_buffer").text

        sanitized_items: list[str] = []
        for item in cwd_items:
            clean_item = self.privacy_guard.sanitize_text(item, context="prompt_cwd_item").text
            if clean_item:
                sanitized_items.append(clean_item)

        lines: list[str] = [
            f"Shell: {shell_value}",
            f"CWD: {cwd_value}",
            f"Buffer: {buffer_value}",
            "",
            "Relevant Executables:",
            ", ".join(self.inventory.commands[:20]) if self.inventory.commands else "(none)",
            "",
            "Recent History:",
            "\n".join(sanitized_history) if sanitized_history else "(none)",
            "",
            "Files in CWD:",
            ", ".join(sanitized_items) if sanitized_items else "(none)",
        ]
        context = "\n".join(lines)
        return self.privacy_guard.sanitize_text(context, context="prompt_context").text

    @staticmethod
    def _parse_json_payload(raw: str) -> dict | None:
        parsed = None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group(0))
                except Exception:
                    parsed = None
        return parsed if isinstance(parsed, dict) else None

    @staticmethod
    def _sanitize_single_line(value: str) -> str:
        cleaned = str(value or "").replace("```", "").replace("\r", " ").replace("\n", " ").strip()
        while "  " in cleaned:
            cleaned = cleaned.replace("  ", " ")
        return cleaned

    def _collect_env_context(self, ctx: RequestContext) -> dict[str, str]:
        raw = {
            "os_name": platform.system() or "unknown",
            "os_version": platform.release() or "unknown",
            "shell": ctx.shell or "unknown",
            "terminal": (ctx.terminal or os.environ.get("TERM", "") or "unknown"),
            "cwd": ctx.cwd or "unknown",
            "platform": ctx.platform_name or platform.platform(),
        }
        sanitized: dict[str, str] = {}
        for key, value in raw.items():
            clean_value = self.privacy_guard.sanitize_text(str(value), context=f"env_{key}").text
            sanitized[key] = clean_value[:200]
        return sanitized

    @staticmethod
    def _parse_optional_dict(value: object, field_name: str) -> dict | None:
        if value is None:
            return None
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            try:
                parsed = json.loads(stripped)
            except Exception:
                logger.warning("Ignoring invalid JSON for %s", field_name)
                return None
            if isinstance(parsed, dict):
                return parsed
        logger.warning("Ignoring non-dict value for %s", field_name)
        return None

    @staticmethod
    def _parse_optional_float(value: object, field_name: str) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            logger.warning("Ignoring invalid numeric value for %s", field_name)
            return None

    @staticmethod
    def _strip_thinking_artifacts(raw: str) -> str:
        text = str(raw or "")
        closing_tag_matches = list(re.finditer(r"</think\s*>", text, flags=re.IGNORECASE))
        if closing_tag_matches:
            text = text[closing_tag_matches[-1].end():]
        text = re.sub(r"<think\b[^>]*>.*?</think\s*>", "", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"</?think\b[^>]*>", "", text, flags=re.IGNORECASE)
        return text.strip()

    def _build_llm_kwargs(
        self,
        config: dict,
        messages: list[dict],
        temperature: float,
        include_json_response_format: bool = False,
    ) -> dict:
        provider = str(config.get("provider", "openai") or "openai").strip().lower()
        model = str(config.get("model", "gpt-5-mini") or "gpt-5-mini").strip()
        api_key = config.get("api_key", None)
        base_url = config.get("base_url", None)

        if provider == "groq":
            if not model.startswith("groq/") and not model.startswith("groq/openai/"):
                model = f"groq/{model}"
        elif provider == "anthropic":
            if not model.startswith("claude"):
                model = "claude-3-5-sonnet-20241022"
        elif provider == "custom":
            # OpenAI-compatible custom endpoints typically use openai/<model>.
            if "/" not in model:
                model = f"openai/{model}"
        else:
            prefix = _PROVIDER_PREFIXES.get(provider)
            if prefix and not model.startswith(prefix):
                model = f"{prefix}{model}"

        if not base_url:
            base_url = _PROVIDER_DEFAULT_BASE_URLS.get(provider, base_url)

        kwargs = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if include_json_response_format:
            kwargs["response_format"] = {"type": "json_object"}

        if api_key:
            env_key = _PROVIDER_ENV_API_KEYS.get(provider)
            if env_key:
                os.environ[env_key] = str(api_key)
            if provider not in {"openai", "groq", "anthropic", "gemini"}:
                kwargs["api_key"] = api_key

        if base_url:
            kwargs["api_base"] = str(base_url).strip()

        headers = self._parse_optional_dict(config.get("headers"), "headers")
        if headers:
            kwargs["headers"] = headers

        extra_body = self._parse_optional_dict(config.get("extra_body"), "extra_body")
        if extra_body:
            kwargs["extra_body"] = extra_body

        timeout = self._parse_optional_float(config.get("timeout"), "timeout")
        if timeout is None:
            timeout = DEFAULT_TIMEOUT_SECONDS
        timeout = max(MIN_TIMEOUT_SECONDS, min(MAX_TIMEOUT_SECONDS, float(timeout)))
        kwargs["timeout"] = timeout

        api_version = config.get("api_version", None)
        if api_version:
            kwargs["api_version"] = str(api_version).strip()

        if provider == "ollama":
            kwargs["think"] = False

        return kwargs

    @staticmethod
    def _is_provider(config: dict, provider_name: str) -> bool:
        return str(config.get("provider", "openai") or "openai").strip().lower() == provider_name

    @staticmethod
    def _derive_ai_agent(provider: str, model: str) -> str:
        registry = get_agent_registry(force_reload=False)
        inferred = registry.infer_agent_from_provider_model(provider=provider, model=model)
        agent = str(inferred.get("agent_id", "") or "").strip().lower()
        if agent:
            return agent
        provider_l = str(provider or "").strip().lower()
        return provider_l or "unknown"

    def get_ai_identity(self, config: dict) -> dict[str, str]:
        provider = str(config.get("provider", "openai") or "openai").strip().lower()
        if provider == "history_only":
            return {"ai_agent": "", "ai_provider": "", "ai_model": ""}
        kwargs = self._build_llm_kwargs(
            config,
            [{"role": "user", "content": "identity"}],
            temperature=0.0,
        )
        model = str(kwargs.get("model", "") or "").strip()
        registry = get_agent_registry(force_reload=False)
        inferred = registry.infer_agent_from_provider_model(provider=provider, model=model)
        agent = str(inferred.get("agent_id", "") or "").strip().lower() or self._derive_ai_agent(provider, model)
        return {
            "ai_agent": agent,
            "ai_provider": provider,
            "ai_model": model,
        }

    def _build_lm_studio_rest_endpoint(self, config: dict) -> str:
        base_url = str(config.get("base_url", "") or "").strip()
        if not base_url:
            return "http://localhost:1234/api/v1/chat"

        normalized = base_url.rstrip("/")
        for suffix in ("/v1/chat/completions", "/v1/responses", "/api/v1/chat", "/api/v1", "/v1"):
            if normalized.endswith(suffix):
                normalized = normalized[: -len(suffix)]
                break
        return f"{normalized}/api/v1/chat"

    async def _privacy_checked_lm_studio_chat(
        self,
        config: dict,
        messages: list[dict],
        temperature: float,
        request_type: str,
    ) -> tuple[str, dict[str, object]]:
        sanitized_messages, redactions, flags = self._sanitize_messages_with_stats(messages)
        try:
            for msg in sanitized_messages:
                if isinstance(msg, dict):
                    content = msg.get("content")
                    if isinstance(content, str):
                        self.privacy_guard.assert_safe_or_raise(content)
        except Exception as exc:
            raise PrivacyGuardError(f"Sanitization failed for {request_type}") from exc

        system_prompt = ""
        text_inputs: list[str] = []
        for msg in sanitized_messages:
            if not isinstance(msg, dict):
                continue
            role = str(msg.get("role", "")).strip().lower()
            content = msg.get("content")
            if not isinstance(content, str):
                continue
            if role == "system":
                if system_prompt:
                    system_prompt = f"{system_prompt}\n{content}"
                else:
                    system_prompt = content
            elif role in {"user", "assistant"}:
                text_inputs.append(content)

        model = str(config.get("model", "local-model") or "local-model").strip()
        if model.startswith("lm_studio/"):
            model = model.split("/", 1)[1]

        payload: dict[str, object] = {
            "model": model,
            "input": "\n".join(text_inputs).strip() if text_inputs else "",
            "temperature": temperature,
            "stream": False,
            "reasoning": "off",
        }
        if system_prompt:
            payload["system_prompt"] = system_prompt

        headers = {"Content-Type": "application/json"}
        api_key = str(config.get("api_key", "") or "").strip()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        extra_headers = self._parse_optional_dict(config.get("headers"), "headers")
        if extra_headers:
            for key, value in extra_headers.items():
                headers[str(key)] = str(value)

        timeout = self._parse_optional_float(config.get("timeout"), "timeout")
        if timeout is None:
            timeout = DEFAULT_TIMEOUT_SECONDS
        request_timeout = max(MIN_TIMEOUT_SECONDS, min(MAX_TIMEOUT_SECONDS, float(timeout)))
        endpoint = self._build_lm_studio_rest_endpoint(config)

        def _do_request() -> requests.Response:
            return requests.post(endpoint, headers=headers, json=payload, timeout=request_timeout)

        response = await asyncio.to_thread(_do_request)
        try:
            response.raise_for_status()
        except requests.RequestException as exc:
            error_text = self.privacy_guard.sanitize_for_log(response.text[:400])
            raise Exception(f"LM Studio REST error: {error_text}") from exc

        try:
            data = response.json()
        except ValueError as exc:
            text = self.privacy_guard.sanitize_for_log(response.text[:400])
            raise Exception(f"LM Studio REST returned non-JSON response: {text}") from exc

        output = data.get("output", [])
        content_parts: list[str] = []
        if isinstance(output, list):
            for item in output:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "message" and isinstance(item.get("content"), str):
                    content_parts.append(item["content"])
        content = "\n".join([part for part in content_parts if part]).strip()

        # Fallbacks in case the server returns an OpenAI-like shape.
        if not content and isinstance(data.get("choices"), list) and data["choices"]:
            first = data["choices"][0]
            if isinstance(first, dict) and isinstance(first.get("message"), dict):
                maybe = first["message"].get("content")
                if isinstance(maybe, str):
                    content = maybe.strip()

        return content, {"redactions": redactions, "flags": flags}

    def _sanitize_messages_with_stats(self, messages: list[dict]) -> tuple[list[dict], int, list[str]]:
        sanitized_messages = self.privacy_guard.sanitize_messages(messages)
        total_redactions = 0
        flags: set[str] = set()
        for idx, msg in enumerate(messages or []):
            if not isinstance(msg, dict):
                continue
            clean_msg = sanitized_messages[idx] if idx < len(sanitized_messages) else dict(msg)
            content = msg.get("content")
            if isinstance(content, str):
                result = self.privacy_guard.sanitize_text(content, context="message")
                clean_msg["content"] = result.text
                total_redactions += result.redaction_count
                flags.update(result.flags)
            elif isinstance(content, list):
                clean_parts = []
                for part in content:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        part_copy = dict(part)
                        result = self.privacy_guard.sanitize_text(part_copy["text"], context="message")
                        part_copy["text"] = result.text
                        total_redactions += result.redaction_count
                        flags.update(result.flags)
                        clean_parts.append(part_copy)
                    else:
                        clean_parts.append(part)
                clean_msg["content"] = clean_parts
            if idx < len(sanitized_messages):
                sanitized_messages[idx] = clean_msg
        return (sanitized_messages, total_redactions, sorted(flags))

    async def _privacy_checked_acompletion(
        self,
        kwargs: dict,
        request_type: str,
    ) -> tuple[object, dict[str, object]]:
        safe_kwargs = dict(kwargs)
        try:
            messages = safe_kwargs.get("messages", [])
            sanitized_messages, redactions, flags = self._sanitize_messages_with_stats(messages)
            for msg in sanitized_messages:
                if isinstance(msg, dict):
                    content = msg.get("content")
                    if isinstance(content, str):
                        self.privacy_guard.assert_safe_or_raise(content)
                    elif isinstance(content, list):
                        for part in content:
                            if isinstance(part, dict) and isinstance(part.get("text"), str):
                                self.privacy_guard.assert_safe_or_raise(part["text"])
            safe_kwargs["messages"] = sanitized_messages
        except Exception as exc:
            raise PrivacyGuardError(f"Sanitization failed for {request_type}") from exc

        try:
            response = await acompletion(**safe_kwargs)
            return (response, {"redactions": redactions, "flags": flags})
        except Exception as first_error:
            if "response_format" not in str(first_error).lower() or "response_format" not in safe_kwargs:
                raise
            retry_kwargs = dict(safe_kwargs)
            retry_kwargs.pop("response_format", None)
            response = await acompletion(**retry_kwargs)
            return (response, {"redactions": redactions, "flags": flags})

    async def get_suggestions(
        self,
        config: dict,
        ctx: RequestContext,
        allow_ai: bool = True,
    ) -> tuple[list[str], list[str], list[dict[str, str]], bool]:
        """
        New paradigm:
        1. Get top 20 exact prefix matches from vector DB
        2. Return first 3 as suggestions + full pool of 20
        3. Only invoke AI if ALL 20 matches are exhausted (user typed something not in history)
        
        Returns:
            tuple: (top_3_suggestions, full_pool_of_20)
        """
        def _pad_pool(values: list[str], size: int = 20) -> list[str]:
            pool = values[:size]
            while len(pool) < size:
                pool.append("")
            return pool

        provider = str(config.get("provider", "openai") or "openai").strip().lower()
        if provider == "history_only":
            allow_ai = False
        buffer_value = str(getattr(ctx, "buffer", "") or "").strip()
        if buffer_value and self._is_blocked_command(buffer_value):
            allow_ai = False
            logger.info("Disabling AI fallback for blocked command buffer")

        # Get vector-based candidates (up to 20)
        vector_candidates = self._get_vector_candidates(ctx)

        # If we have candidates from history, return the top 3 + full pool
        if vector_candidates:
            pool_meta = vector_candidates[:20]
            pool = [entry.get("accept_text", "") for entry in pool_meta]
            suggestions = pool[:3]

            # Pad to 3 if needed
            while len(suggestions) < 3:
                suggestions.append("")

            pool = _pad_pool(pool, size=20)
            logger.debug(f"Vector DB returned {len(vector_candidates)} matches")
            return (suggestions, pool, pool_meta, False)

        # If no vector matches, this is a new/unknown command - invoke AI
        if not allow_ai:
            suggestions = ["", "", ""]
            pool = _pad_pool(suggestions, size=20)
            return (suggestions, pool, [], False)

        logger.info("No vector matches found, invoking AI for new command")

        context_str = self.build_prompt_context(ctx)
        buffer_for_prompt = self.privacy_guard.sanitize_text(ctx.buffer, context="prompt_buffer").text

        system_prompt = (
            "You are a CLI autocomplete engine. "
            "Context provided below (History, CWD). "
            "Provide 3 completions for the user's buffer. "
            "JSON output keys: option_1, option_2, option_3. "
            "Each option must be an object with keys: text, type. "
            "type must be 'add' (append text to current buffer) or 'replace' (replace whole command). "
            "text must be the command text for that type. "
            "Do not output explanations."
            f"\n--- CONTEXT ---\n{context_str}"
        )

        suggestions = ["", "", ""]
        ai_pool_meta: list[dict[str, str]] = []
        privacy_blocked = False

        try:
            raw_model = str(config.get("model", "gpt-5-mini") or "gpt-5-mini")
            model_for_temp = raw_model.split("/")[-1]
            temperature = 1 if model_for_temp.startswith("gpt-5") else 0.3
            request_messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Buffer: {buffer_for_prompt}"},
            ]
            if self._is_provider(config, "lm_studio"):
                raw, privacy_meta = await self._privacy_checked_lm_studio_chat(
                    config,
                    request_messages,
                    temperature=temperature,
                    request_type="suggestions",
                )
            else:
                kwargs = self._build_llm_kwargs(
                    config,
                    request_messages,
                    temperature=temperature,
                    include_json_response_format=True,
                )
                response, privacy_meta = await self._privacy_checked_acompletion(
                    kwargs,
                    request_type="suggestions",
                )
                raw = (response.choices[0].message.content or "").strip()
            if self._is_provider(config, "ollama"):
                raw = self._strip_thinking_artifacts(raw)
            logger.info(
                "LLM request [suggestions] sanitized redactions=%s flags=%s",
                privacy_meta.get("redactions", 0),
                ",".join(privacy_meta.get("flags", [])),
            )
            
            # Parsing logic
            parsed = None
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                # Fallback regex
                match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
                if match:
                    try: parsed = json.loads(match.group(0))
                    except: pass
            
            raw_sugg: list[object] = []
            if isinstance(parsed, dict):
                raw_sugg = [parsed.get("option_1", ""), parsed.get("option_2", ""), parsed.get("option_3", "")]
            else:
                raw_sugg = raw.split("|")

            def _mode_from_type(value: object) -> str:
                normalized = str(value or "").strip().lower()
                if normalized in {"replace", "replace_full"}:
                    return "replace_full"
                return "suffix_append"

            seen_ai: set[tuple[str, str]] = set()
            for idx, item in enumerate(raw_sugg, start=1):
                option_type = "add"
                option_text: object = item
                if isinstance(parsed, dict):
                    type_key = f"option_{idx}_type"
                    if isinstance(item, dict):
                        option_text = item.get("text", item.get("command", item.get("value", "")))
                        option_type = str(item.get("type", item.get("mode", parsed.get(type_key, "add"))) or "add")
                    else:
                        option_type = str(parsed.get(type_key, "add") or "add")

                suggestion = str(option_text or "").strip().replace("```", "").strip()
                if not suggestion:
                    continue
                if (suggestion.startswith('"') and suggestion.endswith('"')) or (
                    suggestion.startswith("'") and suggestion.endswith("'")
                ):
                    suggestion = suggestion[1:-1]
                mode = _mode_from_type(option_type)

                if mode == "suffix_append":
                    if suggestion.startswith(ctx.buffer):
                        suggestion = suggestion[len(ctx.buffer):]
                    filtered = self._filter_blocked_candidates(ctx.buffer, [suggestion])
                    if not filtered:
                        continue
                    accept_text = filtered[0]
                else:
                    filtered = self._filter_blocked_full_commands([suggestion])
                    if not filtered:
                        continue
                    accept_text = filtered[0]

                if not accept_text:
                    continue
                dedupe_key = (mode, accept_text)
                if dedupe_key in seen_ai:
                    continue
                seen_ai.add(dedupe_key)
                ai_pool_meta.append(
                    {
                        "display_text": accept_text,
                        "accept_text": accept_text,
                        "accept_mode": mode,
                        "kind": "normal",
                    }
                )
                if len(ai_pool_meta) >= 20:
                    break

            suggestions = [entry.get("accept_text", "") for entry in ai_pool_meta[:3]]

        except PrivacyGuardError as e:
            logger.warning(
                "LLM request [suggestions] blocked by privacy guard: %s",
                self.privacy_guard.sanitize_for_log(str(e)),
            )
            privacy_blocked = True
            suggestions = []
            ai_pool_meta = []
        except Exception as e:
            logger.error(
                "LLM Error: %s",
                self.privacy_guard.sanitize_for_log(str(e)),
            )
            suggestions = []
            ai_pool_meta = []

        # Pad to 3
        while len(suggestions) < 3:
            suggestions.append("")

        # For AI suggestions, pool mirrors parsed AI metadata.
        pool = _pad_pool([entry.get("accept_text", "") for entry in ai_pool_meta], size=20)
        pool_meta = ai_pool_meta[:20]

        return (suggestions[:3], pool, pool_meta, not privacy_blocked)

    async def get_intent_command(self, config: dict, ctx: RequestContext, intent_text: str) -> dict:
        text = (intent_text or "").strip()
        if not text:
            return {
                "status": "empty",
                "primary_command": "",
                "explanation": "Please add a terminal-related request after '#'.",
                "alternatives": [],
                "copy_block": "",
            }

        env_ctx = self._collect_env_context(ctx)
        safe_user_text = self.privacy_guard.sanitize_text(text, context="intent_user").text
        system_prompt = (
            "You are a command-line intent translator. "
            "Answer ONLY terminal-command related requests. "
            "If the user asks for non-terminal topics, refuse briefly and suggest using '##'. "
            "Prefer safe, non-destructive commands unless destructive behavior is explicitly requested. "
            "Return valid JSON with keys: status, primary_command, explanation, alternatives. "
            "status must be one of: ok, refusal. "
            "primary_command must be a single copy-ready shell command when status=ok, otherwise empty. "
            "explanation must be brief (max 2 sentences). "
            "alternatives must be an array of up to 2 single-line commands."
        )
        user_prompt = (
            f"Environment:\n"
            f"- os_name: {env_ctx['os_name']}\n"
            f"- os_version: {env_ctx['os_version']}\n"
            f"- platform: {env_ctx['platform']}\n"
            f"- shell: {env_ctx['shell']}\n"
            f"- terminal: {env_ctx['terminal']}\n"
            f"- cwd: {env_ctx['cwd']}\n\n"
            f"User request:\n{safe_user_text}"
        )

        result = {
            "status": "refusal",
            "primary_command": "",
            "explanation": "I can only help with terminal commands in '#' mode. Use '##' for general questions.",
            "alternatives": [],
            "copy_block": "",
            "ai_agent": "",
            "ai_provider": "",
            "ai_model": "",
        }

        try:
            ai_identity = self.get_ai_identity(config)
            request_messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
            if self._is_provider(config, "lm_studio"):
                raw, privacy_meta = await self._privacy_checked_lm_studio_chat(
                    config,
                    request_messages,
                    temperature=0.2,
                    request_type="intent",
                )
            else:
                kwargs = self._build_llm_kwargs(
                    config,
                    request_messages,
                    temperature=0.2,
                    include_json_response_format=True,
                )
                response, privacy_meta = await self._privacy_checked_acompletion(
                    kwargs,
                    request_type="intent",
                )
                raw = (response.choices[0].message.content or "").strip()
            if self._is_provider(config, "ollama"):
                raw = self._strip_thinking_artifacts(raw)
            logger.info(
                "LLM request [intent] sanitized redactions=%s flags=%s",
                privacy_meta.get("redactions", 0),
                ",".join(privacy_meta.get("flags", [])),
            )
            parsed = self._parse_json_payload(raw)
            if not parsed:
                result.update(ai_identity)
                return result

            status = str(parsed.get("status", "refusal")).strip().lower()
            primary = self._sanitize_single_line(parsed.get("primary_command", ""))
            explanation = self._sanitize_single_line(parsed.get("explanation", ""))
            alternatives = parsed.get("alternatives", [])
            if not isinstance(alternatives, list):
                alternatives = []

            safe_alternatives: list[str] = []
            for alt in alternatives:
                clean_alt = self._sanitize_single_line(alt)
                if not clean_alt:
                    continue
                if self._is_blocked_command(clean_alt):
                    continue
                if clean_alt not in safe_alternatives:
                    safe_alternatives.append(clean_alt)
                if len(safe_alternatives) >= 2:
                    break

            if primary and self._is_blocked_command(primary):
                status = "refusal"
                primary = ""
                if not explanation:
                    explanation = "I won't suggest unsafe destructive commands in '#' mode."

            if status != "ok" or not primary:
                return {
                    "status": "refusal",
                    "primary_command": "",
                    "explanation": explanation or "I can only help with terminal commands in '#' mode. Use '##' for general questions.",
                    "alternatives": [],
                    "copy_block": "",
                    **ai_identity,
                }

            return {
                "status": "ok",
                "primary_command": primary,
                "explanation": explanation or "Here is a command you can run.",
                "alternatives": safe_alternatives,
                "copy_block": primary,
                **ai_identity,
            }
        except PrivacyGuardError as e:
            logger.warning(
                "LLM request [intent] blocked by privacy guard: %s",
                self.privacy_guard.sanitize_for_log(str(e)),
            )
            return {
                "status": "error",
                "primary_command": "",
                "explanation": "Request blocked by privacy guard. Try a less sensitive prompt.",
                "alternatives": [],
                "copy_block": "",
                "ai_agent": "",
                "ai_provider": "",
                "ai_model": "",
            }
        except Exception as e:
            logger.error(
                "Intent LLM Error: %s",
                self.privacy_guard.sanitize_for_log(str(e)),
            )
            return {
                "status": "error",
                "primary_command": "",
                "explanation": "I couldn't generate a command right now. Try again.",
                "alternatives": [],
                "copy_block": "",
                "ai_agent": "",
                "ai_provider": "",
                "ai_model": "",
            }

    async def get_general_assistant_reply(self, config: dict, ctx: RequestContext, prompt_text: str) -> str:
        text = (prompt_text or "").strip()
        if not text:
            return "Please add a question after '##'."

        env_ctx = self._collect_env_context(ctx)
        safe_text = self.privacy_guard.sanitize_text(text, context="assist_user").text
        user_prompt = (
            f"Environment:\n"
            f"- os_name: {env_ctx['os_name']}\n"
            f"- os_version: {env_ctx['os_version']}\n"
            f"- platform: {env_ctx['platform']}\n"
            f"- shell: {env_ctx['shell']}\n"
            f"- terminal: {env_ctx['terminal']}\n"
            f"- cwd: {env_ctx['cwd']}\n\n"
            f"User request:\n{safe_text}"
        )

        try:
            request_messages = [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": user_prompt},
            ]
            if self._is_provider(config, "lm_studio"):
                content, privacy_meta = await self._privacy_checked_lm_studio_chat(
                    config,
                    request_messages,
                    temperature=0.7,
                    request_type="assist",
                )
            else:
                kwargs = self._build_llm_kwargs(
                    config,
                    request_messages,
                    temperature=0.7,
                )
                response, privacy_meta = await self._privacy_checked_acompletion(
                    kwargs,
                    request_type="assist",
                )
                content = (response.choices[0].message.content or "").strip()
            if self._is_provider(config, "ollama"):
                content = self._strip_thinking_artifacts(content)
            logger.info(
                "LLM request [assist] sanitized redactions=%s flags=%s",
                privacy_meta.get("redactions", 0),
                ",".join(privacy_meta.get("flags", [])),
            )
            return content or "I couldn't generate a response."
        except PrivacyGuardError as e:
            logger.warning(
                "LLM request [assist] blocked by privacy guard: %s",
                self.privacy_guard.sanitize_for_log(str(e)),
            )
            return "Request blocked by privacy guard. Try a less sensitive prompt."
        except Exception as e:
            logger.error(
                "General assistant LLM Error: %s",
                self.privacy_guard.sanitize_for_log(str(e)),
            )
            return "I couldn't generate a response right now. Try again."

    def log_feedback(
        self,
        buffer: str,
        accepted: str,
        accept_mode: str = "suffix_append",
        working_directory: str | None = None,
    ):
        if not buffer:
            return
        mode = (accept_mode or "suffix_append").strip().lower()
        try:
            vector_db = self._ensure_vector_db()
            vector_db.record_feedback(
                buffer,
                accepted,
                mode,
                working_directory=working_directory,
            )
            if mode == "replace_full":
                full_command = (accepted or "").replace("\n", " ").replace("\r", " ").strip()
            else:
                full_command = f"{buffer}{accepted}".replace("\n", " ").replace("\r", " ").strip()
            if full_command:
                sanitized = self.privacy_guard.sanitize_text(full_command, context="log_feedback")
                logger.debug(
                    "Feedback recorded for: %s (redactions=%d)",
                    sanitized.text,
                    sanitized.redaction_count,
                )
        except Exception as e:
            logger.error(
                "Failed to log feedback to vector DB: %s",
                self.privacy_guard.sanitize_for_log(str(e)),
            )
    
    def log_executed_command(
        self,
        command: str,
        exit_code: int | None = None,
        duration_ms: int | None = None,
        source: str = "unknown",
        working_directory: str | None = None,
        provenance_payload: dict | None = None,
    ):
        """
        Log a command that was executed by the user.
        This adds it to the vector database for future suggestions.
        """
        normalized_source = (source or "unknown").strip().lower()
        normalized_command = (command or "").strip()
        if not normalized_command:
            return

        try:
            vector_db = self._ensure_vector_db()
            if vector_db.is_blocked_command(normalized_command):
                logger.debug("Skipping blocked command from runtime logging")
                return
        except Exception as e:
            logger.error(
                "Failed to initialize vector DB for command logging: %s",
                self.privacy_guard.sanitize_for_log(str(e)),
            )
            return

        payload = dict(provenance_payload or {})
        classification_label = "UNKNOWN"
        classification: dict | None = None
        try:
            classification = classify_command_run(normalized_command, payload)
            classification_label = str(classification.get("label", "UNKNOWN") or "UNKNOWN").strip().upper()
        except Exception as e:
            logger.error(
                "Failed to classify command provenance: %s",
                self.privacy_guard.sanitize_for_log(str(e)),
            )
            classification = {}

        if self.state_store is not None:
            try:
                self.state_store.record_command_provenance(
                    command=normalized_command,
                    label=classification_label,
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
                    source=normalized_source,
                    working_directory=str(working_directory or ""),
                    exit_code=exit_code,
                    duration_ms=(max(0, int(duration_ms)) if duration_ms is not None else None),
                    shell_pid=(
                        int(payload.get("shell_pid"))
                        if payload.get("shell_pid", None) is not None
                        else None
                    ),
                    evidence=[str(item) for item in classification.get("evidence", []) if str(item)],
                    payload=payload,
                )
                self._maybe_prune_command_runs()
            except Exception as e:
                logger.error(
                    "Failed to persist command provenance: %s",
                    self.privacy_guard.sanitize_for_log(str(e)),
                )

        try:
            cfg = load_config_file()
        except Exception:
            cfg = {}
        include_ai_executed = bool(cfg.get("include_ai_executed_in_suggestions", False))
        if normalized_source == "runtime" and exit_code != 0:
            logger.debug("Skipping runtime command ingestion into suggestion store after provenance persistence")
            return
        if classification_label == "AI_EXECUTED" and not include_ai_executed:
            logger.debug("Skipping AI_EXECUTED command ingestion into suggestion store")
            return

        try:
            vector_db.insert_command(
                normalized_command,
                working_directory=working_directory,
            )
        except Exception as e:
            logger.error(
                "Failed to log command to vector DB: %s",
                self.privacy_guard.sanitize_for_log(str(e)),
            )
            return

    def _maybe_prune_command_runs(self) -> None:
        if self.state_store is None:
            return
        now_ts = int(time.time())
        # Bound prune cost to at most once per hour.
        if now_ts - int(self._last_command_runs_prune_ts or 0) < 3600:
            return
        cutoff = command_provenance_prune_cutoff(now_ts)
        try:
            self.state_store.prune_command_runs(cutoff)
            self._last_command_runs_prune_ts = now_ts
        except Exception as exc:
            logger.warning(
                "Failed to prune command provenance rows: %s",
                self.privacy_guard.sanitize_for_log(str(exc)),
            )

    def list_command_runs(
        self,
        limit: int = 50,
        label: str = "",
        command_contains: str = "",
        since_ts: int = 0,
        before_ts: int = 0,
        before_run_id: str = "",
        tier: str = "",
        agent: str = "",
        agent_name: str = "",
        provider: str = "",
    ) -> list[dict]:
        if self.state_store is None:
            return []
        try:
            return self.state_store.list_command_runs(
                limit=limit,
                label=label,
                command_contains=command_contains,
                since_ts=since_ts,
                before_ts=before_ts,
                before_run_id=before_run_id,
                tier=tier,
                agent=agent,
                agent_name=agent_name,
                provider=provider,
            )
        except Exception as exc:
            logger.error(
                "Failed to list command provenance rows: %s",
                self.privacy_guard.sanitize_for_log(str(exc)),
            )
            return []

    def count_command_runs(
        self,
        label: str = "",
        command_contains: str = "",
        since_ts: int = 0,
        tier: str = "",
        agent: str = "",
        agent_name: str = "",
        provider: str = "",
    ) -> int:
        if self.state_store is None:
            return 0
        try:
            return int(
                self.state_store.count_command_runs(
                    label=label,
                    command_contains=command_contains,
                    since_ts=since_ts,
                    tier=tier,
                    agent=agent,
                    agent_name=agent_name,
                    provider=provider,
                )
                or 0
            )
        except Exception as exc:
            logger.error(
                "Failed to count command provenance rows: %s",
                self.privacy_guard.sanitize_for_log(str(exc)),
            )
            return 0

    def semantic_command_runs(
        self,
        query: str,
        limit: int = 50,
        since_ts: int = 0,
        label: str = "",
        tier: str = "",
        agent: str = "",
        agent_name: str = "",
        provider: str = "",
    ) -> list[dict]:
        clean_query = str(query or "").strip()
        if not clean_query or self.state_store is None:
            return []
        try:
            vector_db = self._ensure_vector_db()
            ranked_commands = vector_db.search_commands_for_provenance(
                clean_query,
                limit=max(1, min(200, int(limit or 50))),
            )
            if not ranked_commands:
                return []
            return self.state_store.list_latest_runs_for_commands(
                ranked_commands,
                since_ts=since_ts,
                label=label,
                tier=tier,
                agent=agent,
                agent_name=agent_name,
                provider=provider,
                limit=limit,
            )
        except Exception as exc:
            logger.error(
                "Failed semantic provenance search: %s",
                self.privacy_guard.sanitize_for_log(str(exc)),
            )
            return []

    def get_provenance_registry_summary(self) -> dict:
        return get_registry_summary(force_reload=False)

    def list_provenance_registry_agents(self, status_filter: str = "") -> list[dict]:
        return list_registry_agents(status_filter=status_filter, force_reload=False)

    def get_provenance_registry_agent(self, agent_id: str) -> dict | None:
        return get_registry_agent(agent_id, force_reload=False)

    def refresh_provenance_registry(self, config: dict | None = None, force: bool = False) -> dict:
        return refresh_agent_registry(config=config or {}, force=force)

    def verify_provenance_registry_cache(self, config: dict | None = None) -> dict:
        return verify_cached_agent_registry(config=config or {})
    
    def close(self, join_timeout_seconds: float = 20.0, shutdown_reason: str = ""):
        """Clean up resources."""
        with self._bootstrap_lock:
            thread = self._bootstrap_thread

        timeout_seconds = max(0.1, float(join_timeout_seconds))
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=timeout_seconds)
            if thread.is_alive():
                logger.warning(
                    "History bootstrap thread did not finish before shutdown (bootstrap_thread_alive=true timeout_seconds=%.2f reason=%s)",
                    timeout_seconds,
                    str(shutdown_reason or "unknown"),
                )

        if self.vector_db is not None:
            self.vector_db.close()
            self.vector_db = None
            self._vector_db_ready.clear()
        if self.snapshot_scheduler is not None:
            self.snapshot_scheduler.stop()
