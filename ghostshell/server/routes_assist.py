from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from ghostshell.server import deps
from ghostshell.server.schemas import AssistContext, AssistResponse, Feedback, GenericStatusResponse

router = APIRouter()


@router.post("/assist", response_model=AssistResponse, response_model_exclude_unset=True)
async def resolve_assist(ctx: AssistContext, request: Request) -> AssistResponse:
    deps.enter_request_or_503()
    try:
        config = deps.load_config()
        provider = str(config.get("provider", "openai") or "openai").strip().lower()
        if provider == "history_only":
            return {
                "answer": "AI is disabled in current provider mode. Switch provider to use '##' assistant mode."
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
        answer = await deps.engine.get_general_assistant_reply(
            config,
            req_context,
            ctx.prompt_text,
        )
        return {"answer": answer}
    finally:
        deps.release_request_slot()


@router.post("/feedback", response_model=GenericStatusResponse, response_model_exclude_unset=True)
def log_feedback(fb: Feedback, background_tasks: BackgroundTasks) -> GenericStatusResponse:
    deps.enter_request_or_503()
    try:
        background_tasks.add_task(
            deps.run_background_task,
            deps.engine.log_feedback,
            fb.command_buffer,
            fb.accepted_suggestion,
            fb.accept_mode,
            fb.working_directory,
        )
        return {"status": "ok"}
    finally:
        deps.release_request_slot()
