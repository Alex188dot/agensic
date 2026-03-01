from fastapi import APIRouter, HTTPException, Request
from ghostshell.server import deps
from ghostshell.server.schemas import Context, PredictResponse

router = APIRouter()


@router.post("/predict", response_model=PredictResponse, response_model_exclude_unset=True)
async def predict_completion(ctx: Context, request: Request) -> PredictResponse:
    deps.enter_request_or_503()
    try:
        if not ctx.command_buffer.strip():
            return {
                "suggestions": ["", "", ""],
                "pool": [],
                "pool_meta": [],
                "used_ai": False,
                "ai_agent": "",
                "ai_provider": "",
                "ai_model": "",
            }

        config = deps.load_config()
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
        req_context = deps.RequestContext(
            history_file=deps.get_history_file(ctx.shell),
            cwd=ctx.working_directory,
            buffer=ctx.command_buffer,
            shell=ctx.shell,
        )

        suggestions, pool, pool_meta, used_ai = await deps.engine.get_suggestions(
            config,
            req_context,
            allow_ai=effective_allow_ai,
        )
        bootstrap = deps.engine.get_bootstrap_status()

        source = (ctx.trigger_source or "unknown").strip() or "unknown"
        seen = set()
        display_pool_count = 0
        for item in pool:
            if not item or item in seen:
                continue
            seen.add(item)
            display_pool_count += 1
            if display_pool_count >= 20:
                break

        sanitized_buffer = deps.privacy_guard.sanitize_text(
            ctx.command_buffer,
            context="server_predict",
        )
        deps.logger.debug(
            "Req[%s] allow_ai=%s used_ai=%s suggestions=%s buffer='%s' redactions=%d",
            source,
            effective_allow_ai,
            used_ai,
            display_pool_count,
            deps.privacy_guard.sanitize_for_log(sanitized_buffer.text),
            sanitized_buffer.redaction_count,
        )
        ai_identity = deps.engine.get_ai_identity(config) if used_ai else {
            "ai_agent": "",
            "ai_provider": "",
            "ai_model": "",
        }
        return {
            "suggestions": suggestions,
            "pool": pool,
            "pool_meta": pool_meta,
            "bootstrap": bootstrap,
            "used_ai": used_ai,
            **ai_identity,
        }
    finally:
        deps.release_request_slot()
