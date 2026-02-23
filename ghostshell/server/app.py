from contextlib import asynccontextmanager
import os

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
    startup_history = deps.get_history_file(os.environ.get("SHELL", "zsh"))
    if startup_history:
        deps.engine.bootstrap_async(startup_history)
    yield
    deps.logger.info("Shutting down GhostShell server gracefully...")
    deps.engine.close()


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
    uvicorn_server.run()


if __name__ == "__main__":
    run()
