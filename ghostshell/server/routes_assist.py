from fastapi import APIRouter, BackgroundTasks

from ghostshell.server import deps
from ghostshell.server.schemas import AssistContext, AssistResponse, Feedback, GenericStatusResponse

router = APIRouter()


@router.post("/assist", response_model=AssistResponse, response_model_exclude_unset=True)
async def resolve_assist(ctx: AssistContext) -> AssistResponse:
    config = deps.load_config()
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


@router.post("/feedback", response_model=GenericStatusResponse, response_model_exclude_unset=True)
def log_feedback(fb: Feedback, background_tasks: BackgroundTasks) -> GenericStatusResponse:
    background_tasks.add_task(
        deps.engine.log_feedback,
        fb.command_buffer,
        fb.accepted_suggestion,
        fb.accept_mode,
    )
    return {"status": "ok"}
