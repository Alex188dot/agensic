"""Shared prediction orchestration and the low-overhead shell line protocol."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

from agensic.server import deps
from agensic.server.schemas import Context

PROTOCOL_VERSION = "agensic_predict_v2"
FIELD_SEPARATOR = "\x1f"


def _empty_payload(request_id: str, *, bootstrap: dict | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "request_id": request_id,
        "suggestions": ["", "", ""],
        "pool": [],
        "pool_meta": [],
        "used_ai": False,
        "ai_agent": "",
        "ai_provider": "",
        "ai_model": "",
    }
    if bootstrap is not None:
        payload["bootstrap"] = bootstrap
    return payload


def _cursor_context(ctx: Context) -> tuple[str, str]:
    line = str(ctx.command_buffer or "")
    cursor = max(0, min(len(line), int(ctx.cursor_position or 0)))
    return line[:cursor], line[cursor:]


def _replace_at_cursor(
    prefix: str,
    suffix: str,
    pool_meta: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not suffix:
        return pool_meta
    adjusted: list[dict[str, Any]] = []
    for raw in pool_meta:
        item = dict(raw or {})
        accept_text = str(item.get("accept_text", "") or "")
        mode = str(item.get("accept_mode", "suffix_append") or "suffix_append")
        if mode == "replace_full":
            replacement = f"{accept_text}{suffix}"
        else:
            replacement = f"{prefix}{accept_text}{suffix}"
        item["accept_text"] = replacement
        item["display_text"] = replacement
        item["accept_mode"] = "replace_full"
        adjusted.append(item)
    return adjusted


async def predict_payload(ctx: Context, request: Request) -> dict[str, Any]:
    request_id = str(ctx.request_id or "").strip()[:128]
    prefix, line_suffix = _cursor_context(ctx)
    if not prefix.strip():
        return _empty_payload(request_id)

    config = deps.load_config()
    if not deps.autocomplete_enabled_from_config(config):
        return _empty_payload(request_id, bootstrap=deps.engine.get_bootstrap_status())

    provider = str(config.get("provider", "openai") or "openai").strip().lower()
    effective_allow_ai = bool(ctx.allow_ai and provider != "history_only")
    if effective_allow_ai:
        client_id = deps.get_client_id(request)
        allowed, used, limit = deps.check_and_track_llm_rate_limit(config, client_id)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail=f"LLM request rate limit exceeded ({used}/{limit} in 60s).",
            )

    trigger_source = str(ctx.trigger_source or "unknown").strip() or "unknown"
    # Keep embeddings out of keystroke/space requests. The pause timer is the
    # idle threshold; manual triggers may also opt into semantic retrieval.
    allow_semantic = trigger_source not in {"typing_auto", "space_auto"}
    req_context = deps.RequestContext(
        history_file=deps.get_history_file(ctx.shell),
        cwd=ctx.working_directory,
        buffer=prefix,
        shell=ctx.shell,
        cursor_position=len(prefix),
        allow_semantic=allow_semantic,
    )
    suggestions, pool, pool_meta, used_ai = await deps.engine.get_suggestions(
        config,
        req_context,
        allow_ai=effective_allow_ai,
    )
    adjusted_meta = _replace_at_cursor(prefix, line_suffix, pool_meta)
    if adjusted_meta is not pool_meta:
        pool = [str(item.get("accept_text", "") or "") for item in adjusted_meta]
        suggestions = pool[:3]
        while len(suggestions) < 3:
            suggestions.append("")
        pool = pool[:20] + [""] * max(0, 20 - len(pool))

    sanitized_buffer = deps.privacy_guard.sanitize_text(
        ctx.command_buffer,
        context="server_predict",
    )
    deps.logger.debug(
        "Req[%s:%s] allow_ai=%s semantic=%s used_ai=%s suggestions=%s buffer='%s' redactions=%d",
        trigger_source,
        request_id,
        effective_allow_ai,
        allow_semantic,
        used_ai,
        sum(1 for item in pool if item),
        deps.privacy_guard.sanitize_for_log(sanitized_buffer.text),
        sanitized_buffer.redaction_count,
    )
    ai_identity = (
        deps.engine.get_ai_identity(config)
        if used_ai
        else {"ai_agent": "", "ai_provider": "", "ai_model": ""}
    )
    return {
        "request_id": request_id,
        "suggestions": suggestions,
        "pool": pool,
        "pool_meta": adjusted_meta,
        "bootstrap": deps.engine.get_bootstrap_status(),
        "used_ai": used_ai,
        **ai_identity,
    }


def _line_safe(value: object) -> str:
    return (
        str(value or "")
        .replace("\r", " ")
        .replace("\n", " ")
        .replace(FIELD_SEPARATOR, " ")
    )


def prediction_lines(payload: dict[str, Any]) -> str:
    meta = payload.get("pool_meta", [])
    if not isinstance(meta, list):
        meta = []
    pool: list[str] = []
    display: list[str] = []
    modes: list[str] = []
    kinds: list[str] = []
    for raw in meta[:20]:
        if not isinstance(raw, dict):
            continue
        accept = _line_safe(raw.get("accept_text", ""))
        if not accept:
            continue
        pool.append(accept)
        display.append(_line_safe(raw.get("display_text", accept)))
        modes.append(_line_safe(raw.get("accept_mode", "suffix_append")))
        kinds.append(_line_safe(raw.get("kind", "normal")))
    lines = [
        PROTOCOL_VERSION,
        f"request_id={_line_safe(payload.get('request_id', ''))}",
        "ok=1",
        "error_code=",
        f"used_ai={'1' if payload.get('used_ai') else '0'}",
        f"ai_agent={_line_safe(payload.get('ai_agent', ''))}",
        f"ai_provider={_line_safe(payload.get('ai_provider', ''))}",
        f"ai_model={_line_safe(payload.get('ai_model', ''))}",
        f"pool={FIELD_SEPARATOR.join(pool)}",
        f"display={FIELD_SEPARATOR.join(display)}",
        f"modes={FIELD_SEPARATOR.join(modes)}",
        f"kinds={FIELD_SEPARATOR.join(kinds)}",
    ]
    return "\n".join(lines) + "\n"
