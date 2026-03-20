import json
import logging
import os
import threading
import time
import hmac
from collections import defaultdict, deque
from typing import Any, Callable

from fastapi import HTTPException, Request

os.environ["TOKENIZERS_PARALLELISM"] = "false"
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

from agensic.engine import RequestContext, SuggestionEngine
from agensic.paths import APP_PATHS, ensure_app_layout, migrate_legacy_layout
from agensic.config.loader import normalize_config_payload
from agensic.config.auth import AuthTokenCache, HEADER_AUTHORIZATION, HEADER_CUSTOM_AUTH
from agensic.privacy import PrivacyGuard
from agensic.utils.history import rewrite_history_without_commands
from agensic.utils.shell import command_matches_pattern, sanitize_patterns

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("agensic")

CONFIG_DIR = APP_PATHS.config_dir
CONFIG_FILE = APP_PATHS.config_file

engine = SuggestionEngine()
privacy_guard = PrivacyGuard()
uvicorn_server = None
_RATE_LIMIT_LOCK = threading.Lock()
_RATE_LIMIT_WINDOW_SECONDS = 60.0
_RATE_LIMIT_STATE: dict[str, deque[float]] = defaultdict(deque)
_ENV_SETTINGS_LOG_LOCK = threading.Lock()
_ENV_SETTINGS_LOGGED = False
_AUTH_CACHE = AuthTokenCache()


class ShutdownCoordinator:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.shutting_down = False
        self.shutdown_reason = ""
        self.shutdown_started_monotonic = 0.0
        self.active_requests = 0
        self.active_background_jobs = 0

    def begin_shutdown(self, reason: str = "") -> bool:
        reason_value = str(reason or "").strip() or "unknown"
        with self._lock:
            if self.shutting_down:
                return False
            self.shutting_down = True
            self.shutdown_reason = reason_value
            self.shutdown_started_monotonic = time.monotonic()
            return True

    def is_shutting_down(self) -> bool:
        with self._lock:
            return bool(self.shutting_down)

    def try_acquire_request_slot(self) -> bool:
        with self._lock:
            if self.shutting_down:
                return False
            self.active_requests += 1
            return True

    def release_request_slot(self) -> None:
        with self._lock:
            if self.active_requests > 0:
                self.active_requests -= 1

    def acquire_background_job_slot(self) -> None:
        with self._lock:
            self.active_background_jobs += 1

    def release_background_job_slot(self) -> None:
        with self._lock:
            if self.active_background_jobs > 0:
                self.active_background_jobs -= 1

    def active_jobs_total(self) -> int:
        with self._lock:
            return int(self.active_requests + self.active_background_jobs)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "shutting_down": bool(self.shutting_down),
                "reason": self.shutdown_reason,
                "shutdown_started_monotonic": float(self.shutdown_started_monotonic),
                "active_requests": int(self.active_requests),
                "active_background_jobs": int(self.active_background_jobs),
                "active_jobs_total": int(self.active_requests + self.active_background_jobs),
            }

    def reset(self) -> None:
        with self._lock:
            self.shutting_down = False
            self.shutdown_reason = ""
            self.shutdown_started_monotonic = 0.0
            self.active_requests = 0
            self.active_background_jobs = 0


shutdown_coordinator = ShutdownCoordinator()


def set_uvicorn_server(server):
    global uvicorn_server
    uvicorn_server = server


def log_parallelism_settings_once() -> None:
    global _ENV_SETTINGS_LOGGED
    with _ENV_SETTINGS_LOG_LOCK:
        if _ENV_SETTINGS_LOGGED:
            return
        _ENV_SETTINGS_LOGGED = True
    logger.info(
        "Parallelism settings: TOKENIZERS_PARALLELISM=%s OMP_NUM_THREADS=%s MKL_NUM_THREADS=%s OPENBLAS_NUM_THREADS=%s VECLIB_MAXIMUM_THREADS=%s NUMEXPR_NUM_THREADS=%s",
        os.environ.get("TOKENIZERS_PARALLELISM", ""),
        os.environ.get("OMP_NUM_THREADS", ""),
        os.environ.get("MKL_NUM_THREADS", ""),
        os.environ.get("OPENBLAS_NUM_THREADS", ""),
        os.environ.get("VECLIB_MAXIMUM_THREADS", ""),
        os.environ.get("NUMEXPR_NUM_THREADS", ""),
    )


def begin_shutdown(reason: str) -> bool:
    started = shutdown_coordinator.begin_shutdown(reason)
    if started:
        logger.info("Shutdown phase started (reason=%s)", str(reason or "unknown"))
    return started


def shutdown_snapshot() -> dict[str, Any]:
    return shutdown_coordinator.snapshot()


def reject_if_shutting_down() -> None:
    if shutdown_coordinator.is_shutting_down():
        raise HTTPException(status_code=503, detail="daemon_shutting_down")


def enter_request_or_503() -> None:
    if not shutdown_coordinator.try_acquire_request_slot():
        raise HTTPException(status_code=503, detail="daemon_shutting_down")


def release_request_slot() -> None:
    shutdown_coordinator.release_request_slot()


def run_background_task(task: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
    shutdown_coordinator.acquire_background_job_slot()
    try:
        task(*args, **kwargs)
    finally:
        shutdown_coordinator.release_background_job_slot()


def wait_for_active_jobs_to_drain(timeout_seconds: float = 5.0, poll_interval_seconds: float = 0.05) -> bool:
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    while time.monotonic() < deadline:
        if shutdown_coordinator.active_jobs_total() == 0:
            return True
        time.sleep(max(0.01, float(poll_interval_seconds)))
    return shutdown_coordinator.active_jobs_total() == 0


def reset_shutdown_state() -> None:
    shutdown_coordinator.reset()


def load_config() -> dict:
    migrate_legacy_layout()
    ensure_app_layout()
    if not os.path.exists(CONFIG_FILE):
        return normalize_config_payload({})
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return normalize_config_payload({})
    if not isinstance(payload, dict):
        return normalize_config_payload({})
    return normalize_config_payload(payload)


def get_client_id(request) -> str:
    client = getattr(request, "client", None)
    host = getattr(client, "host", "") if client else ""
    if host:
        return str(host)
    return "unknown"


def check_and_track_llm_rate_limit(config: dict, client_id: str) -> tuple[bool, int, int]:
    limit = int(config.get("llm_requests_per_minute", 120) or 120)
    now = time.monotonic()
    with _RATE_LIMIT_LOCK:
        queue = _RATE_LIMIT_STATE[client_id]
        cutoff = now - _RATE_LIMIT_WINDOW_SECONDS
        while queue and queue[0] < cutoff:
            queue.popleft()
        if len(queue) >= limit:
            return (False, len(queue), limit)
        queue.append(now)
        return (True, len(queue), limit)


def get_history_file(shell: str) -> str:
    home = os.path.expanduser("~")
    shell_name = str(shell or "").strip().lower()
    if sys.platform.startswith("linux"):
        if not shell_name or any(name in shell_name for name in ("bash", "zsh", "sh")):
            return os.path.join(home, ".bash_history")
        return ""
    if "zsh" in shell_name:
        return os.path.join(home, ".zsh_history")
    if "bash" in shell_name:
        return os.path.join(home, ".bash_history")
    return ""


def disabled_patterns_from_config(config: dict) -> list[str]:
    values = config.get("disabled_command_patterns", [])
    return sanitize_patterns(values)


def autocomplete_enabled_from_config(config: dict) -> bool:
    return bool(config.get("autocomplete_enabled", True))


def command_matches_disabled_pattern(command: str, patterns: list[str]) -> bool:
    return command_matches_pattern(command, patterns)


def normalize_unique_commands(commands: list[str], vector_db) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in commands:
        normalized = vector_db.normalize_command(str(raw or ""))
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out


def ensure_local_auth_token() -> str:
    return _AUTH_CACHE.get_token(force_reload=True)


def get_local_auth_token() -> str:
    return _AUTH_CACHE.get_token()


def _extract_bearer_token(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw.lower().startswith("bearer "):
        return raw[7:].strip()
    return ""


def request_has_valid_auth(request: Request) -> bool:
    expected = get_local_auth_token()
    if not expected:
        return False

    custom_token = str(request.headers.get(HEADER_CUSTOM_AUTH, "") or "").strip()
    if custom_token and hmac.compare_digest(custom_token, expected):
        return True

    bearer = _extract_bearer_token(request.headers.get(HEADER_AUTHORIZATION, ""))
    if bearer and hmac.compare_digest(bearer, expected):
        return True
    return False


def auth_failure_reason(request: Request) -> str:
    custom_token = str(request.headers.get(HEADER_CUSTOM_AUTH, "") or "").strip()
    bearer = _extract_bearer_token(request.headers.get(HEADER_AUTHORIZATION, ""))
    if not custom_token and not bearer:
        return "auth_missing"
    return "auth_invalid"


def rotate_local_auth_token() -> str:
    from agensic.config.auth import rotate_auth_token

    token = rotate_auth_token()
    _AUTH_CACHE.get_token(force_reload=True)
    return token


def unauthorized_exception() -> HTTPException:
    return HTTPException(status_code=401, detail="unauthorized")
