import argparse
import json
import socket
import sys
import urllib.error
import urllib.request
from typing import Any


PREDICT_URL = "http://127.0.0.1:22000/predict"


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, separators=(",", ":")))


def _error(code: str) -> None:
    _emit(
        {
            "ok": False,
            "error_code": code,
            "used_ai": False,
            "pool": [],
            "display": [],
            "modes": [],
            "kinds": [],
        }
    )


def _is_timeout(reason: Any) -> bool:
    if isinstance(reason, socket.timeout):
        return True
    if isinstance(reason, TimeoutError):
        return True
    return "timed out" in str(reason).lower()


def _validate_input(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None

    buffer = str(payload.get("command_buffer", "") or "")
    working_directory = str(payload.get("working_directory", "") or "")
    shell = str(payload.get("shell", "zsh") or "zsh")
    trigger_source = str(payload.get("trigger_source", "unknown") or "unknown")

    try:
        cursor_position = int(payload.get("cursor_position", 0) or 0)
    except (TypeError, ValueError):
        cursor_position = 0

    allow_ai_raw = payload.get("allow_ai", True)
    allow_ai = bool(allow_ai_raw)

    return {
        "command_buffer": buffer,
        "cursor_position": cursor_position,
        "working_directory": working_directory,
        "shell": shell,
        "allow_ai": allow_ai,
        "trigger_source": trigger_source,
    }


def _normalize_predict_response(result: Any) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None

    used_ai = bool(result.get("used_ai", False))
    pool = result.get("pool", result.get("suggestions", []))
    pool_meta = result.get("pool_meta", [])

    seen: set[str] = set()
    clean_pool: list[str] = []
    clean_display: list[str] = []
    clean_modes: list[str] = []
    clean_kinds: list[str] = []

    if isinstance(pool_meta, list):
        for item in pool_meta:
            if not isinstance(item, dict):
                continue
            accept_text = str(item.get("accept_text", "") or "")
            if not accept_text or accept_text in seen:
                continue
            seen.add(accept_text)
            clean_pool.append(accept_text)
            clean_display.append(str(item.get("display_text", accept_text) or accept_text))
            clean_modes.append(str(item.get("accept_mode", "suffix_append") or "suffix_append"))
            clean_kinds.append(str(item.get("kind", "normal") or "normal"))
            if len(clean_pool) >= 20:
                break

    if not clean_pool and isinstance(pool, list):
        for item in pool:
            value = str(item or "")
            if not value or value in seen:
                continue
            seen.add(value)
            clean_pool.append(value)
            clean_display.append(value)
            clean_modes.append("suffix_append")
            clean_kinds.append("normal")
            if len(clean_pool) >= 20:
                break

    return {
        "ok": True,
        "error_code": "",
        "used_ai": used_ai,
        "pool": clean_pool,
        "display": clean_display,
        "modes": clean_modes,
        "kinds": clean_kinds,
    }


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--timeout", type=float, default=3.0)
    args = parser.parse_args()
    timeout = max(0.2, float(args.timeout or 3.0))

    try:
        raw = sys.stdin.read()
    except Exception:
        raw = ""

    if not raw.strip():
        _error("bad_input_json")
        return

    try:
        incoming = json.loads(raw)
    except Exception:
        _error("bad_input_json")
        return

    payload = _validate_input(incoming)
    if payload is None:
        _error("bad_input_shape")
        return

    try:
        req = urllib.request.Request(
            PREDICT_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError:
        _error("predict_http_error")
        return
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if _is_timeout(reason):
            _error("predict_timeout")
        else:
            _error("daemon_unreachable")
        return
    except socket.timeout:
        _error("predict_timeout")
        return
    except TimeoutError:
        _error("predict_timeout")
        return
    except Exception:
        _error("predict_error")
        return

    try:
        parsed = json.loads(body)
    except Exception:
        _error("bad_response_json")
        return

    normalized = _normalize_predict_response(parsed)
    if normalized is None:
        _error("bad_response_shape")
        return

    _emit(normalized)


if __name__ == "__main__":
    main()
