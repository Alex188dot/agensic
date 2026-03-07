import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from agensic.utils import atomic_write_json_private, enforce_private_file, ensure_private_dir


DEFAULT_REGISTRY_URL = "https://registry.agensic.ai/v1/agents.json"
DEFAULT_REMOTE_CACHE_PATH = os.path.expanduser("~/.agensic/agent_registry.remote.json")
DEFAULT_REMOTE_META_PATH = os.path.expanduser("~/.agensic/agent_registry.remote.meta.json")


def _normalize(value: Any) -> str:
    return str(value or "").strip()


def _canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def verify_registry_signature(payload: dict[str, Any], signature: str, public_key: str) -> tuple[bool, str]:
    clean_signature = _normalize(signature).lower()
    clean_public_key = _normalize(public_key)

    if not clean_signature:
        return (False, "signature_missing")
    if not clean_public_key:
        return (False, "public_key_missing")

    body = _canonical_json_bytes(payload)
    expected = hmac.new(clean_public_key.encode("utf-8"), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, clean_signature):
        return (False, "signature_invalid")
    return (True, "signature_valid")


def _read_json(path: str) -> dict[str, Any]:
    target = Path(path).expanduser()
    ensure_private_dir(target.parent)
    enforce_private_file(target)
    if not target.exists() or not target.is_file():
        return {}
    try:
        with open(target, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_json(path: str, payload: dict[str, Any]) -> None:
    target = Path(path).expanduser()
    ensure_private_dir(target.parent)
    atomic_write_json_private(target, payload, indent=2, sort_keys=True)


def parse_registry_response(raw: dict[str, Any]) -> tuple[dict[str, Any], str]:
    if not isinstance(raw, dict):
        return ({}, "")

    if isinstance(raw.get("payload"), dict):
        payload = dict(raw.get("payload") or {})
        signature = _normalize(raw.get("signature"))
        return (payload, signature)

    payload = {k: v for k, v in raw.items() if k != "signature"}
    signature = _normalize(raw.get("signature"))
    return (payload, signature)


def refresh_remote_registry(
    url: str,
    public_key: str,
    timeout_seconds: float = 4.0,
    remote_cache_path: str = DEFAULT_REMOTE_CACHE_PATH,
    remote_meta_path: str = DEFAULT_REMOTE_META_PATH,
) -> dict[str, Any]:
    clean_url = _normalize(url)
    if not clean_url:
        return {
            "ok": False,
            "reason": "url_missing",
            "updated": False,
            "version": "",
        }

    req = urllib.request.Request(clean_url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=max(0.5, float(timeout_seconds))) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return {
            "ok": False,
            "reason": f"http_error_{int(exc.code)}",
            "updated": False,
            "version": "",
        }
    except urllib.error.URLError:
        return {
            "ok": False,
            "reason": "network_error",
            "updated": False,
            "version": "",
        }
    except Exception:
        return {
            "ok": False,
            "reason": "request_failed",
            "updated": False,
            "version": "",
        }

    try:
        parsed = json.loads(body)
    except Exception:
        return {
            "ok": False,
            "reason": "invalid_json",
            "updated": False,
            "version": "",
        }

    payload, signature = parse_registry_response(parsed if isinstance(parsed, dict) else {})
    if not isinstance(payload, dict) or not isinstance(payload.get("agents", []), list):
        return {
            "ok": False,
            "reason": "invalid_payload_shape",
            "updated": False,
            "version": "",
        }

    valid, verify_reason = verify_registry_signature(payload, signature, public_key)
    if not valid:
        return {
            "ok": False,
            "reason": verify_reason,
            "updated": False,
            "version": _normalize(payload.get("version")),
        }

    _write_json(remote_cache_path, payload)
    _write_json(
        remote_meta_path,
        {
            "signature": signature,
            "signature_valid": True,
            "verified_at": int(time.time()),
            "url": clean_url,
            "version": _normalize(payload.get("version")),
            "hash_sha256": hashlib.sha256(_canonical_json_bytes(payload)).hexdigest(),
        },
    )

    return {
        "ok": True,
        "reason": "updated",
        "updated": True,
        "version": _normalize(payload.get("version")),
    }


def verify_cached_registry(
    public_key: str,
    remote_cache_path: str = DEFAULT_REMOTE_CACHE_PATH,
    remote_meta_path: str = DEFAULT_REMOTE_META_PATH,
) -> dict[str, Any]:
    payload = _read_json(remote_cache_path)
    meta = _read_json(remote_meta_path)

    if not payload:
        return {
            "ok": False,
            "reason": "cache_missing",
            "version": "",
        }

    signature = _normalize(meta.get("signature"))
    valid, reason = verify_registry_signature(payload, signature, public_key)
    return {
        "ok": bool(valid),
        "reason": reason,
        "version": _normalize(payload.get("version")),
        "verified_at": int(meta.get("verified_at", 0) or 0),
        "url": _normalize(meta.get("url")),
    }
