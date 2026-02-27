from fastapi import APIRouter, HTTPException, Request
from ghostshell.server import deps
from ghostshell.server.schemas import IntentContext, IntentResponse

router = APIRouter()


@router.post("/intent", response_model=IntentResponse, response_model_exclude_unset=True)
async def resolve_intent(ctx: IntentContext, request: Request) -> IntentResponse:
    deps.enter_request_or_503()
    try:
        config = deps.load_config()
        provider = str(config.get("provider", "openai") or "openai").strip().lower()
        if provider == "history_only":
            return {
                "status": "refusal",
                "primary_command": "",
                "explanation": "AI is disabled in current provider mode. Switch provider to use '#' intent mode.",
                "alternatives": [],
                "copy_block": "",
            }
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
            buffer="",
            shell=ctx.shell,
            terminal=ctx.terminal,
            platform_name=ctx.platform,
        )
        result = await deps.engine.get_intent_command(config, req_context, ctx.intent_text)
        return result
    finally:
        deps.release_request_slot()
