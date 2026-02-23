from fastapi import APIRouter

from ghostshell.server import deps
from ghostshell.server.schemas import IntentContext

router = APIRouter()


@router.post("/intent")
async def resolve_intent(ctx: IntentContext):
    config = deps.load_config()
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
