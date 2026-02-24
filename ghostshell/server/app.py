from contextlib import asynccontextmanager
import os
import signal

import uvicorn
from fastapi import FastAPI

from ghostshell.server import deps
from ghostshell.server.routes_admin import router as admin_router
from ghostshell.server.routes_assist import router as assist_router
from ghostshell.server.routes_command_store import router as command_store_router
from ghostshell.server.routes_intent import router as intent_router
from ghostshell.server.routes_predict import router as predict_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    deps.logger.info("Starting GhostShell server...")
    deps.reset_shutdown_state()
    deps.log_parallelism_settings_once()
    startup_history = deps.get_history_file(os.environ.get("SHELL", "zsh"))
    if startup_history:
        deps.engine.bootstrap_async(startup_history)
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
    deps.logger.info("Shutting down GhostShell server gracefully...")
    shutdown_reason = str(deps.shutdown_snapshot().get("reason", "") or "lifespan")
    deps.engine.close(join_timeout_seconds=20.0, shutdown_reason=shutdown_reason)


app = FastAPI(lifespan=lifespan)
app.include_router(predict_router)
app.include_router(intent_router)
app.include_router(assist_router)
app.include_router(command_store_router)
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
