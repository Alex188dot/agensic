from fastapi import APIRouter, HTTPException
from ghostshell.server import deps
from ghostshell.server.schemas import (
    GenericStatusResponse,
    RepairExportResponse,
    RepairImportPayload,
    RepairImportResponse,
    RepairRecoverResponse,
    StatusResponse,
)

router = APIRouter()


@router.get("/status", response_model=StatusResponse, response_model_exclude_unset=True)
def daemon_status() -> StatusResponse:
    bootstrap = deps.engine.get_bootstrap_status()
    shutdown = deps.shutdown_snapshot()
    return {
        "status": "ok",
        "bootstrap": bootstrap,
        "shutdown": shutdown,
    }


@router.post("/shutdown", response_model=GenericStatusResponse, response_model_exclude_unset=True)
async def shutdown() -> GenericStatusResponse:
    deps.logger.info("Shutdown request received.")
    deps.begin_shutdown("api_shutdown")
    if deps.uvicorn_server is not None:
        deps.uvicorn_server.should_exit = True
    else:
        deps.logger.warning("Uvicorn server handle not available; shutdown deferred")
    return {"status": "shutting down"}


@router.post("/repair/export", response_model=RepairExportResponse, response_model_exclude_unset=True)
def repair_export() -> RepairExportResponse:
    try:
        snapshot = deps.engine.export_repair_snapshot()
    except Exception as exc:
        deps.logger.error("Repair export failed: %s", exc)
        raise HTTPException(status_code=503, detail="repair_export_failed")
    return {"status": "ok", "snapshot": snapshot if isinstance(snapshot, dict) else {}}


@router.post("/repair/import", response_model=RepairImportResponse, response_model_exclude_unset=True)
def repair_import(payload: RepairImportPayload) -> RepairImportResponse:
    try:
        result = deps.engine.import_repair_snapshot(payload.snapshot)
    except Exception as exc:
        deps.logger.error("Repair import failed: %s", exc)
        raise HTTPException(status_code=503, detail="repair_import_failed")
    if not isinstance(result, dict):
        result = {}
    return {
        "status": "ok",
        "commands_imported": int(result.get("commands_imported", 0) or 0),
        "feedback_imported": int(result.get("feedback_imported", 0) or 0),
        "removed_imported": int(result.get("removed_imported", 0) or 0),
        "provenance_imported": int(result.get("provenance_imported", 0) or 0),
    }


@router.post("/repair/recover", response_model=RepairRecoverResponse, response_model_exclude_unset=True)
def repair_recover() -> RepairRecoverResponse:
    result = deps.engine.recover_state_from_snapshot()
    if not isinstance(result, dict):
        raise HTTPException(status_code=503, detail="repair_recover_failed")
    if not bool(result.get("ok", False)):
        reason = str(result.get("reason", "") or result.get("restore_error", "") or "repair_recover_failed")
        raise HTTPException(status_code=503, detail=reason)
    replay = result.get("replay", {}) if isinstance(result.get("replay"), dict) else {}
    return {
        "status": "ok",
        "restored": bool(result.get("restored", False)),
        "replay_total": int(replay.get("total", 0) or 0),
        "replay_applied": int(replay.get("applied", 0) or 0),
        "replay_skipped": int(replay.get("skipped", 0) or 0),
        "reason": "",
    }
