from fastapi import APIRouter

from ghostshell.server import deps
from ghostshell.server.schemas import GenericStatusResponse, StatusResponse

router = APIRouter()


@router.get("/status", response_model=StatusResponse, response_model_exclude_unset=True)
def daemon_status() -> StatusResponse:
    bootstrap = deps.engine.get_bootstrap_status()
    return {
        "status": "ok",
        "bootstrap": bootstrap,
    }


@router.post("/shutdown", response_model=GenericStatusResponse, response_model_exclude_unset=True)
async def shutdown() -> GenericStatusResponse:
    deps.logger.info("Shutdown request received.")
    if deps.uvicorn_server is not None:
        deps.uvicorn_server.should_exit = True
    else:
        deps.logger.warning("Uvicorn server handle not available; shutdown deferred")
    return {"status": "shutting down"}
