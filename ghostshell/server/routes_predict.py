from fastapi import APIRouter

from ghostshell.server import deps
from ghostshell.server.schemas import Context

router = APIRouter()


@router.post("/predict")
async def predict_completion(ctx: Context):
    if not ctx.command_buffer.strip():
        return {
            "suggestions": ["", "", ""],
            "pool": [],
            "pool_meta": [],
            "used_ai": False,
        }

    config = deps.load_config()
    req_context = deps.RequestContext(
        history_file=deps.get_history_file(ctx.shell),
        cwd=ctx.working_directory,
        buffer=ctx.command_buffer,
        shell=ctx.shell,
    )

    suggestions, pool, pool_meta, used_ai = await deps.engine.get_suggestions(
        config,
        req_context,
        allow_ai=ctx.allow_ai,
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
    deps.logger.info(
        "Req[%s] allow_ai=%s used_ai=%s suggestions=%s buffer='%s' redactions=%d",
        source,
        ctx.allow_ai,
        used_ai,
        display_pool_count,
        deps.privacy_guard.sanitize_for_log(sanitized_buffer.text),
        sanitized_buffer.redaction_count,
    )
    return {
        "suggestions": suggestions,
        "pool": pool,
        "pool_meta": pool_meta,
        "bootstrap": bootstrap,
        "used_ai": used_ai,
    }
