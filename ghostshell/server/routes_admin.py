from fastapi import APIRouter

from ghostshell.server import deps

router = APIRouter()


@router.get("/status")
def daemon_status():
    bootstrap = deps.engine.get_bootstrap_status()
    return {
        "status": "ok",
        "bootstrap": bootstrap,
    }


@router.post("/shutdown")
async def shutdown():
    deps.logger.info("Shutdown request received.")
    if deps.uvicorn_server is not None:
        deps.uvicorn_server.should_exit = True
    else:
        deps.logger.warning("Uvicorn server handle not available; shutdown deferred")
    return {"status": "shutting down"}
