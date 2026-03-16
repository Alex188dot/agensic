from fastapi import APIRouter, HTTPException

from agensic.cli import track as track_runtime
from agensic.server import deps
from agensic.server.schemas import (
    GenericStatusResponse,
    SessionDetailResponse,
    SessionEventsResponse,
    SessionLaunchPayload,
    SessionLaunchResponse,
    SessionRenamePayload,
    SessionTimeTravelForkPayload,
    SessionTimeTravelForkResponse,
    SessionTimeTravelPreviewPayload,
    SessionTimeTravelPreviewResponse,
    SessionSummariesResponse,
)

router = APIRouter()


@router.get(
    "/sessions",
    response_model=SessionSummariesResponse,
    response_model_exclude_unset=True,
)
def list_sessions(
    limit: int = 50,
    before_started_at: int = 0,
    before_session_id: str = "",
    status: str = "",
) -> SessionSummariesResponse:
    deps.enter_request_or_503()
    try:
        track_runtime.reconcile_tracked_sessions()
        sessions = deps.engine.list_session_summaries(
            limit=limit,
            before_started_at=before_started_at,
            before_session_id=before_session_id,
            status=status,
        )
        total_matching = deps.engine.count_session_summaries(status=status)
        return {
            "status": "ok",
            "sessions": sessions,
            "total": len(sessions),
            "total_matching": int(total_matching or 0),
        }
    finally:
        deps.release_request_slot()


@router.get(
    "/sessions/{session_id}",
    response_model=SessionDetailResponse,
    response_model_exclude_unset=True,
)
def get_session(session_id: str) -> SessionDetailResponse:
    deps.enter_request_or_503()
    try:
        track_runtime.reconcile_tracked_sessions()
        session = deps.engine.get_session_summary(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="session_not_found")
        return {
            "status": "ok",
            "session": session,
        }
    finally:
        deps.release_request_slot()


@router.get(
    "/sessions/{session_id}/events",
    response_model=SessionEventsResponse,
    response_model_exclude_unset=True,
)
def get_session_events(session_id: str) -> SessionEventsResponse:
    deps.enter_request_or_503()
    try:
        track_runtime.reconcile_tracked_sessions()
        session = deps.engine.get_session_summary(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="session_not_found")
        event_stream_path = str(session.get("event_stream_path", "") or "").strip()
        events = track_runtime._load_session_events(event_stream_path) if event_stream_path else []
        normalized_events: list[dict[str, object]] = []
        for event in events:
            payload = dict(event.get("payload", {}) or {})
            data = payload.get("data")
            if isinstance(data, (bytes, bytearray)):
                payload["data"] = data.decode("utf-8", errors="replace")
            normalized_events.append(
                {
                    "session_id": str(event.get("session_id", "") or ""),
                    "seq": int(event.get("seq", 0) or 0),
                    "ts_wall": float(event.get("ts_wall", 0.0) or 0.0),
                    "ts_monotonic_ms": int(event.get("ts_monotonic_ms", 0) or 0),
                    "type": str(event.get("type", "") or ""),
                    "payload": payload,
                }
            )
        return {
            "status": "ok",
            "session_id": session_id,
            "events": normalized_events,
            "total": len(normalized_events),
        }
    finally:
        deps.release_request_slot()


@router.patch(
    "/sessions/{session_id}",
    response_model=SessionDetailResponse,
    response_model_exclude_unset=True,
)
def rename_session(session_id: str, payload: SessionRenamePayload) -> SessionDetailResponse:
    deps.enter_request_or_503()
    try:
        track_runtime.reconcile_tracked_sessions()
        session = deps.engine.rename_session(session_id, payload.session_name)
        if session is None:
            raise HTTPException(status_code=404, detail="session_not_found")
        return {
            "status": "ok",
            "session": session,
        }
    finally:
        deps.release_request_slot()


@router.delete(
    "/sessions/{session_id}",
    response_model=GenericStatusResponse,
    response_model_exclude_unset=True,
)
def delete_session(session_id: str) -> GenericStatusResponse:
    deps.enter_request_or_503()
    try:
        track_runtime.reconcile_tracked_sessions()
        session = deps.engine.get_session_summary(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="session_not_found")
        deleted = track_runtime.delete_track_session_artifacts(session_id, state=session)
        if not deleted:
            raise HTTPException(status_code=404, detail="session_not_found")
        return {"status": "ok"}
    finally:
        deps.release_request_slot()


@router.post(
    "/sessions/{session_id}/time-travel/preview",
    response_model=SessionTimeTravelPreviewResponse,
    response_model_exclude_unset=True,
)
def preview_time_travel(
    session_id: str,
    payload: SessionTimeTravelPreviewPayload,
) -> SessionTimeTravelPreviewResponse:
    deps.enter_request_or_503()
    try:
        track_runtime.reconcile_tracked_sessions()
        result = track_runtime.preview_time_travel(session_id, payload.target_seq)
        if str(result.get("status", "") or "") != "ok":
            raise HTTPException(status_code=404, detail=str(result.get("reason", "") or "time_travel_preview_failed"))
        return result
    finally:
        deps.release_request_slot()


@router.post(
    "/sessions/{session_id}/time-travel/fork",
    response_model=SessionTimeTravelForkResponse,
    response_model_exclude_unset=True,
)
def fork_time_travel(
    session_id: str,
    payload: SessionTimeTravelForkPayload,
) -> SessionTimeTravelForkResponse:
    deps.enter_request_or_503()
    try:
        track_runtime.reconcile_tracked_sessions()
        result = track_runtime.fork_time_travel(session_id, payload.target_seq, branch_name=payload.branch_name)
        if str(result.get("status", "") or "") != "ok":
            raise HTTPException(status_code=409, detail=str(result.get("reason", "") or "time_travel_fork_failed"))
        return result
    finally:
        deps.release_request_slot()


@router.post(
    "/sessions/launch",
    response_model=SessionLaunchResponse,
    response_model_exclude_unset=True,
)
def launch_session(payload: SessionLaunchPayload) -> SessionLaunchResponse:
    deps.enter_request_or_503()
    try:
        track_runtime.reconcile_tracked_sessions()
        try:
            launch = track_runtime.build_launch_from_session(
                payload.source_session_id,
                working_directory=payload.working_directory,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        result = track_runtime.launch_tracked_command_async(
            launch,
            session_name=payload.session_name,
            replay_metadata=dict(payload.replay_metadata or {}),
        )
        if str(result.get("status", "") or "") != "ok":
            raise HTTPException(status_code=409, detail=str(result.get("reason", "") or "session_launch_failed"))
        return result
    finally:
        deps.release_request_slot()
