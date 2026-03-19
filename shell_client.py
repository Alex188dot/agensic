import argparse
import json
import os
import re
import socket
import sys
import urllib.error
import urllib.request
from typing import Any

from agensic.config.auth import AuthTokenCache, build_auth_headers
from agensic.utils.shell import current_shell_name, normalize_shell_name

PREDICT_URL = "http://127.0.0.1:22000/predict"
INTENT_URL = "http://127.0.0.1:22000/intent"
ASSIST_URL = "http://127.0.0.1:22000/assist"
SHELL_LINES_VERSION = "agensic_shell_lines_v1"
_AUTH_CACHE = AuthTokenCache()


def _safe_line(value: Any) -> str:
    return str(value or "").replace("\r", " ").replace("\n", " ").strip()


def _safe_multiline(value: Any) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n")


def _decode_common_escapes(text: str) -> str:
    if "\\n" not in text and "\\r" not in text and "\\t" not in text:
        return text
    # Preserve common Windows paths like C:\Users\...
    if re.search(r"[A-Za-z]:\\\\", text):
        return text
    return text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n").replace("\\t", "\t")


def _emit_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, separators=(",", ":")))


def _emit_shell_lines_v1(op: str, payload: dict[str, Any]) -> None:
    ok = "1" if bool(payload.get("ok", False)) else "0"
    error_code = _safe_line(payload.get("error_code", ""))
    if op == "intent":
        lines = [
            SHELL_LINES_VERSION,
            "intent",
            ok,
            error_code,
            _safe_line(payload.get("status", "error")) or "error",
            _safe_line(payload.get("primary_command", "")),
            _safe_line(payload.get("explanation", "Could not resolve command mode right now."))
            or "Could not resolve command mode right now.",
            _safe_line(payload.get("alternatives_blob", "")),
            _safe_line(payload.get("copy_block", "")),
            _safe_line(payload.get("ai_agent", "")),
            _safe_line(payload.get("ai_provider", "")),
            _safe_line(payload.get("ai_model", "")),
        ]
    elif op == "assist":
        answer = _safe_multiline(payload.get("answer", ""))
        if not answer and not bool(payload.get("ok", False)):
            answer = "Could not fetch assistant reply right now."
        answer_lines = answer.split("\n") if answer else []
        lines = [
            SHELL_LINES_VERSION,
            "assist",
            ok,
            error_code,
            str(len(answer_lines)),
            *answer_lines,
        ]
    else:
        _emit_json(_predict_error_payload("unsupported_format"))
        return

    print("\n".join(lines))


def _predict_error_payload(code: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error_code": code,
        "used_ai": False,
        "ai_agent": "",
        "ai_provider": "",
        "ai_model": "",
        "pool": [],
        "display": [],
        "modes": [],
        "kinds": [],
    }


def _intent_error_payload(code: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error_code": code,
        "status": "error",
        "primary_command": "",
        "explanation": "Could not resolve command mode right now.",
        "alternatives_blob": "",
        "copy_block": "",
        "ai_agent": "",
        "ai_provider": "",
        "ai_model": "",
    }


def _assist_error_payload(code: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error_code": code,
        "answer": "Could not fetch assistant reply right now.",
    }


def _is_timeout(reason: Any) -> bool:
    if isinstance(reason, socket.timeout):
        return True
    if isinstance(reason, TimeoutError):
        return True
    return "timed out" in str(reason).lower()


def _read_stdin_payload() -> tuple[dict[str, Any], bool, bool]:
    try:
        raw = sys.stdin.read()
    except Exception:
        raw = ""

    if not raw.strip():
        return ({}, False, True)

    try:
        parsed = json.loads(raw)
    except Exception:
        return ({}, True, False)

    if not isinstance(parsed, dict):
        return ({}, True, False)

    return (parsed, True, True)


def _validate_predict_input(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None

    buffer = str(payload.get("command_buffer", "") or "")
    working_directory = str(payload.get("working_directory", "") or "")
    shell = normalize_shell_name(payload.get("shell", current_shell_name()) or current_shell_name())
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


def _build_intent_payload(args: argparse.Namespace, incoming: dict[str, Any]) -> dict[str, Any] | None:
    intent_text = str(args.intent_text or incoming.get("intent_text", "") or "")
    if not intent_text:
        return None

    return {
        "intent_text": intent_text,
        "working_directory": str(args.working_directory or incoming.get("working_directory", "") or ""),
        "shell": normalize_shell_name(args.shell_name or incoming.get("shell", current_shell_name()) or current_shell_name()),
        "terminal": str(args.terminal or incoming.get("terminal", "") or ""),
        "platform": str(args.platform or incoming.get("platform", "") or ""),
    }


def _build_assist_payload(args: argparse.Namespace, incoming: dict[str, Any]) -> dict[str, Any] | None:
    prompt_text = str(args.prompt_text or incoming.get("prompt_text", "") or "")
    if not prompt_text:
        return None

    return {
        "prompt_text": prompt_text,
        "working_directory": str(args.working_directory or incoming.get("working_directory", "") or ""),
        "shell": normalize_shell_name(args.shell_name or incoming.get("shell", current_shell_name()) or current_shell_name()),
        "terminal": str(args.terminal or incoming.get("terminal", "") or ""),
        "platform": str(args.platform or incoming.get("platform", "") or ""),
    }


def _normalize_predict_response(result: Any) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None

    used_ai = bool(result.get("used_ai", False))
    ai_agent = str(result.get("ai_agent", "") or "")
    ai_provider = str(result.get("ai_provider", "") or "")
    ai_model = str(result.get("ai_model", "") or "")
    pool = result.get("pool", result.get("suggestions", []))
    pool_meta = result.get("pool_meta", [])

    seen: set[str] = set()
    clean_pool: list[str] = []
    clean_display: list[str] = []
    clean_modes: list[str] = []
    clean_kinds: list[str] = []

    def _normalize_mode(value: Any) -> str:
        mode = str(value or "suffix_append").strip().lower()
        if mode in {"replace", "replace_full"}:
            return "replace_full"
        if mode in {"add", "suffix_append"}:
            return "suffix_append"
        return "suffix_append"

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
            clean_modes.append(_normalize_mode(item.get("accept_mode", "suffix_append")))
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
        "ai_agent": ai_agent,
        "ai_provider": ai_provider,
        "ai_model": ai_model,
        "pool": clean_pool,
        "display": clean_display,
        "modes": clean_modes,
        "kinds": clean_kinds,
    }


def _normalize_intent_response(result: Any) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None

    status = _safe_line(result.get("status", "error")) or "error"
    primary = _safe_line(result.get("primary_command", ""))
    explanation = _safe_line(result.get("explanation", ""))
    alternatives_raw = result.get("alternatives", [])
    if not isinstance(alternatives_raw, list):
        alternatives_raw = []
    alternatives: list[str] = []
    for item in alternatives_raw:
        clean = _safe_line(item)
        if clean:
            alternatives.append(clean)
        if len(alternatives) >= 2:
            break

    copy_block = _safe_line(result.get("copy_block", primary))
    if not copy_block and primary:
        copy_block = primary

    return {
        "ok": True,
        "error_code": "",
        "status": status,
        "primary_command": primary,
        "explanation": explanation,
        "alternatives_blob": "|||".join(alternatives),
        "copy_block": copy_block,
        "ai_agent": _safe_line(result.get("ai_agent", "")),
        "ai_provider": _safe_line(result.get("ai_provider", "")),
        "ai_model": _safe_line(result.get("ai_model", "")),
    }


def _normalize_assist_response(result: Any) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None

    answer = _safe_multiline(result.get("answer", ""))
    answer = _decode_common_escapes(answer)
    return {
        "ok": True,
        "error_code": "",
        "answer": answer,
    }


def _request_json(
    *,
    url: str,
    payload: dict[str, Any],
    timeout: float,
    auth_token: str,
    op: str,
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        headers = {"Content-Type": "application/json"}
        headers.update(build_auth_headers(auth_token))
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        if int(getattr(exc, "code", 0) or 0) == 401:
            return (None, "auth_failed")
        return (None, f"{op}_http_error")
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if _is_timeout(reason):
            return (None, f"{op}_timeout")
        return (None, "daemon_unreachable")
    except socket.timeout:
        return (None, f"{op}_timeout")
    except TimeoutError:
        return (None, f"{op}_timeout")
    except Exception:
        return (None, f"{op}_error")

    try:
        parsed = json.loads(body)
    except Exception:
        return (None, "bad_response_json")

    if not isinstance(parsed, dict):
        return (None, "bad_response_shape")

    return (parsed, None)


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--op", type=str, default="predict")
    parser.add_argument("--format", dest="output_format", type=str, default="json")
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--auth-token", type=str, default="")
    parser.add_argument("--intent-text", type=str, default="")
    parser.add_argument("--prompt-text", type=str, default="")
    parser.add_argument("--working-directory", type=str, default="")
    parser.add_argument("--shell", dest="shell_name", type=str, default="")
    parser.add_argument("--terminal", type=str, default="")
    parser.add_argument("--platform", type=str, default="")
    args = parser.parse_args()

    op = str(args.op or "predict").strip().lower()
    output_format = str(args.output_format or "json").strip().lower()
    timeout = max(0.2, float(args.timeout or 3.0))

    auth_token = str(args.auth_token or "").strip()
    if not auth_token:
        auth_token = str(os.environ.get("AGENSIC_AUTH_TOKEN", "") or "").strip()
    if not auth_token:
        try:
            auth_token = _AUTH_CACHE.get_token()
        except Exception:
            auth_token = ""

    incoming, has_stdin, stdin_ok = _read_stdin_payload()
    if not stdin_ok:
        if op == "intent":
            payload_out = _intent_error_payload("bad_input_json")
        elif op == "assist":
            payload_out = _assist_error_payload("bad_input_json")
        else:
            payload_out = _predict_error_payload("bad_input_json")
        if output_format == "shell_lines_v1" and op in {"intent", "assist"}:
            _emit_shell_lines_v1(op, payload_out)
        else:
            _emit_json(payload_out)
        return

    if op == "predict":
        if not has_stdin:
            payload_out = _predict_error_payload("bad_input_json")
        else:
            payload = _validate_predict_input(incoming)
            if payload is None:
                payload_out = _predict_error_payload("bad_input_shape")
            else:
                parsed, err = _request_json(
                    url=PREDICT_URL,
                    payload=payload,
                    timeout=timeout,
                    auth_token=auth_token,
                    op="predict",
                )
                if err:
                    payload_out = _predict_error_payload(err)
                else:
                    normalized = _normalize_predict_response(parsed)
                    if normalized is None:
                        payload_out = _predict_error_payload("bad_response_shape")
                    else:
                        payload_out = normalized
    elif op == "intent":
        payload = _build_intent_payload(args, incoming)
        if payload is None:
            payload_out = _intent_error_payload("bad_input_shape")
        else:
            parsed, err = _request_json(
                url=INTENT_URL,
                payload=payload,
                timeout=timeout,
                auth_token=auth_token,
                op="intent",
            )
            if err:
                payload_out = _intent_error_payload(err)
            else:
                normalized = _normalize_intent_response(parsed)
                if normalized is None:
                    payload_out = _intent_error_payload("bad_response_shape")
                else:
                    payload_out = normalized
    elif op == "assist":
        payload = _build_assist_payload(args, incoming)
        if payload is None:
            payload_out = _assist_error_payload("bad_input_shape")
        else:
            parsed, err = _request_json(
                url=ASSIST_URL,
                payload=payload,
                timeout=timeout,
                auth_token=auth_token,
                op="assist",
            )
            if err:
                payload_out = _assist_error_payload(err)
            else:
                normalized = _normalize_assist_response(parsed)
                if normalized is None:
                    payload_out = _assist_error_payload("bad_response_shape")
                else:
                    payload_out = normalized
    else:
        payload_out = _predict_error_payload("bad_op")

    if output_format == "shell_lines_v1" and op in {"intent", "assist"}:
        _emit_shell_lines_v1(op, payload_out)
    else:
        _emit_json(payload_out)


if __name__ == "__main__":
    main()
