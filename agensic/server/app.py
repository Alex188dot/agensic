from contextlib import asynccontextmanager
import os
import signal

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from agensic.server import deps
from agensic.server.routes_admin import router as admin_router
from agensic.server.routes_assist import router as assist_router
from agensic.server.routes_command_store import router as command_store_router
from agensic.server.routes_intent import router as intent_router
from agensic.server.routes_predict import router as predict_router
from agensic.server.routes_provenance import router as provenance_router
from agensic.server.routes_sessions import router as sessions_router
from agensic.utils.shell import current_shell_name


@asynccontextmanager
async def lifespan(app: FastAPI):
    deps.logger.info("Starting Agensic server...")
    deps.reset_shutdown_state()
    deps.rotate_local_auth_token()
    deps.log_parallelism_settings_once()
    startup_history = deps.get_history_file(current_shell_name())
    if startup_history:
        deps.engine.bootstrap_async(startup_history)
    try:
        summary = deps.engine.get_provenance_registry_summary()
        deps.logger.info(
            "Provenance registry loaded version=%s source=%s agents=%s",
            str(summary.get("version", "") or ""),
            str(summary.get("source", "") or ""),
            int(summary.get("agent_count", 0) or 0),
        )
    except Exception as exc:
        deps.logger.warning("Failed to load provenance registry summary: %s", str(exc))
    yield
    deps.begin_shutdown("lifespan")
    drained = deps.wait_for_active_jobs_to_drain(timeout_seconds=5.0, poll_interval_seconds=0.05)
    if not drained:
        snapshot = deps.shutdown_snapshot()
        deps.logger.warning(
            "forced shutdown with active_jobs=%d active_requests=%d active_background_jobs=%d",
            int(snapshot.get("active_jobs_total", 0) or 0),
            int(snapshot.get("active_requests", 0) or 0),
            int(snapshot.get("active_background_jobs", 0) or 0),
        )
    deps.logger.info("Shutting down Agensic server gracefully...")
    shutdown_reason = str(deps.shutdown_snapshot().get("reason", "") or "lifespan")
    deps.engine.close(join_timeout_seconds=20.0, shutdown_reason=shutdown_reason)


app = FastAPI(lifespan=lifespan)


@app.middleware("http")
async def enforce_local_auth(request: Request, call_next):
    if not deps.request_has_valid_auth(request):
        deps.logger.warning(
            "local auth rejected method=%s path=%s client=%s reason=%s",
            str(request.method or ""),
            str(request.url.path or ""),
            deps.get_client_id(request),
            deps.auth_failure_reason(request),
        )
        return JSONResponse(status_code=401, content={"detail": "unauthorized"})
    return await call_next(request)


app.include_router(predict_router)
app.include_router(intent_router)
app.include_router(assist_router)
app.include_router(command_store_router)
app.include_router(provenance_router)
app.include_router(sessions_router)
app.include_router(admin_router)


def run():
    config = uvicorn.Config(app, host="127.0.0.1", port=22000, log_level="warning")
    uvicorn_server = uvicorn.Server(config)
    deps.set_uvicorn_server(uvicorn_server)

    def _handle_sigterm(signum, frame) -> None:  # noqa: ARG001
        deps.begin_shutdown("sigterm")
        if deps.uvicorn_server is not None:
            deps.uvicorn_server.should_exit = True

    try:
        signal.signal(signal.SIGTERM, _handle_sigterm)
    except Exception as exc:
        deps.logger.warning("Failed to register SIGTERM handler: %s", str(exc))

    uvicorn_server.run()


if __name__ == "__main__":
    run()
