import json
import os
import secrets
import tempfile
import time
from pathlib import Path

from .loader import CONFIG_DIR


AUTH_FILE = os.path.join(CONFIG_DIR, "auth.json")
AUTH_VERSION = 1
AUTH_TOKEN_BYTES = 32
HEADER_AUTHORIZATION = "Authorization"
HEADER_CUSTOM_AUTH = "X-GhostShell-Auth"


def _normalize_auth_payload(payload: object) -> dict | None:
    if not isinstance(payload, dict):
        return None
    token = str(payload.get("auth_token", "") or "").strip()
    if not token:
        return None
    version = int(payload.get("version", AUTH_VERSION) or AUTH_VERSION)
    created_at = int(payload.get("created_at", int(time.time())) or int(time.time()))
    last_rotated_at = int(payload.get("last_rotated_at", created_at) or created_at)
    return {
        "version": version,
        "created_at": created_at,
        "last_rotated_at": last_rotated_at,
        "auth_token": token,
    }


def _auth_payload_for_token(token: str) -> dict:
    now = int(time.time())
    return {
        "version": AUTH_VERSION,
        "created_at": now,
        "last_rotated_at": now,
        "auth_token": str(token or "").strip(),
    }


def generate_auth_token() -> str:
    return secrets.token_urlsafe(AUTH_TOKEN_BYTES)


def load_auth_payload(path: str | None = None) -> dict | None:
    target = Path(path or AUTH_FILE).expanduser()
    if not target.exists() or not target.is_file():
        return None
    try:
        with open(target, "r", encoding="utf-8") as f:
            parsed = json.load(f)
    except Exception:
        return None
    return _normalize_auth_payload(parsed)


def load_auth_token(path: str | None = None) -> str:
    payload = load_auth_payload(path=path)
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("auth_token", "") or "").strip()


def save_auth_token(token: str, path: str | None = None) -> dict:
    clean_token = str(token or "").strip()
    if not clean_token:
        raise ValueError("auth token must not be empty")

    target = Path(path or AUTH_FILE).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = _auth_payload_for_token(clean_token)

    fd, tmp_path = tempfile.mkstemp(prefix=".auth.", suffix=".json", dir=str(target.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.write("\n")
        os.replace(tmp_path, target)
        os.chmod(target, 0o600)
    except Exception:
        try:
            os.remove(tmp_path)
        except Exception:
            pass
        raise
    return payload


def rotate_auth_token(path: str | None = None) -> str:
    token = generate_auth_token()
    save_auth_token(token, path=path)
    return token


def ensure_auth_token(path: str | None = None) -> str:
    existing = load_auth_token(path=path)
    if existing:
        return existing
    return rotate_auth_token(path=path)


def build_auth_headers(token: str) -> dict[str, str]:
    clean_token = str(token or "").strip()
    if not clean_token:
        return {}
    return {
        HEADER_AUTHORIZATION: f"Bearer {clean_token}",
        HEADER_CUSTOM_AUTH: clean_token,
    }


class AuthTokenCache:
    def __init__(self, path: str | None = None) -> None:
        self.path = str(Path(path or AUTH_FILE).expanduser())
        self._cached_token = ""
        self._cached_mtime_ns = -1

    def _stat_mtime_ns(self) -> int:
        try:
            return int(os.stat(self.path).st_mtime_ns)
        except Exception:
            return -1

    def get_token(self, force_reload: bool = False) -> str:
        mtime_ns = self._stat_mtime_ns()
        if not force_reload and self._cached_token and mtime_ns == self._cached_mtime_ns:
            return self._cached_token

        token = load_auth_token(path=self.path)
        if not token:
            token = ensure_auth_token(path=self.path)
            mtime_ns = self._stat_mtime_ns()
        self._cached_token = token
        self._cached_mtime_ns = mtime_ns
        return token
