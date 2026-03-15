import os
from fastapi import APIRouter, BackgroundTasks
from agensic.server import deps
from agensic.server.schemas import (
    CommandStoreAddResponse,
    CommandStoreHistoryPayload,
    CommandStoreListResponse,
    LogCommandPayload,
    CommandStorePayload,
    CommandStoreRemovePayload,
    CommandStoreRemoveResponse,
    CommandStoreResyncResponse,
    LogCommandResponse,
)

router = APIRouter()
MAX_COMMAND_DURATION_MS = 86_400_000


def _verify_ai_executed_track_capability(data: LogCommandPayload) -> tuple[bool, str]:
    proof_label = str(data.proof_label or "").strip()
    if proof_label != "AI_EXECUTED":
        return (True, "")
    session_id = str(data.track_session_id or "").strip()
    wrapper_id = str(data.provenance_wrapper_id or "").strip()
    if not session_id:
        return (False, "track_session_id_missing")
    if wrapper_id != f"agensic_track:{session_id}":
        return (False, "track_wrapper_id_invalid")
    state_store = getattr(deps.engine, "state_store", None)
    if state_store is None:
        return (False, "track_state_store_unavailable")
    return state_store.verify_tracked_session_capability(
        session_id,
        str(data.track_session_capability or "").strip(),
    )


@router.post("/log_command", response_model=LogCommandResponse, response_model_exclude_unset=True)
def log_command(data: LogCommandPayload, background_tasks: BackgroundTasks) -> LogCommandResponse:
    deps.enter_request_or_503()
    try:
        command = str(data.command or "").strip()
        if not command:
            return {"status": "ignored", "reason": "empty_command"}

        raw_exit_code = data.exit_code
        exit_code = None
        if raw_exit_code is not None:
            try:
                exit_code = int(raw_exit_code)
            except (TypeError, ValueError):
                return {"status": "ignored", "reason": "invalid_exit_code"}
        raw_duration_ms = data.duration_ms
        duration_ms = None
        if raw_duration_ms is not None:
            try:
                duration_ms = min(MAX_COMMAND_DURATION_MS, max(0, int(raw_duration_ms)))
            except (TypeError, ValueError):
                return {"status": "ignored", "reason": "invalid_duration_ms"}

        source = str(data.source or "unknown").strip().lower()
        if source not in {"runtime", "history", "unknown"}:
            return {"status": "ignored", "reason": "invalid_source"}
        working_directory = str(data.working_directory or "").strip() or None

        config = deps.load_config()
        patterns = deps.disabled_patterns_from_config(config)
        if deps.command_matches_disabled_pattern(command, patterns):
            return {"status": "ignored", "reason": "disabled_pattern"}

        capability_ok, capability_reason = _verify_ai_executed_track_capability(data)
        if not capability_ok:
            return {"status": "ignored", "reason": capability_reason}

        provenance_payload = {
            "shell_pid": data.shell_pid,
            "provenance_last_action": data.provenance_last_action,
            "provenance_accept_origin": data.provenance_accept_origin,
            "provenance_accept_mode": data.provenance_accept_mode,
            "provenance_suggestion_kind": data.provenance_suggestion_kind,
            "provenance_manual_edit_after_accept": data.provenance_manual_edit_after_accept,
            "provenance_ai_agent": data.provenance_ai_agent,
            "provenance_ai_provider": data.provenance_ai_provider,
            "provenance_ai_model": data.provenance_ai_model,
            "provenance_agent_name": data.provenance_agent_name,
            "provenance_agent_hint": data.provenance_agent_hint,
            "provenance_model_raw": data.provenance_model_raw,
            "provenance_wrapper_id": data.provenance_wrapper_id,
            "proof_label": data.proof_label,
            "proof_agent": data.proof_agent,
            "proof_model": data.proof_model,
            "proof_trace": data.proof_trace,
            "proof_timestamp": data.proof_timestamp,
            "proof_signature": data.proof_signature,
            "proof_signer_scope": data.proof_signer_scope,
            "proof_key_fingerprint": data.proof_key_fingerprint,
            "proof_host_fingerprint": data.proof_host_fingerprint,
            "track_session_id": data.track_session_id,
            "track_root_pid": data.track_root_pid,
            "track_process_pid": data.track_process_pid,
            "track_parent_pid": data.track_parent_pid,
            "track_launch_mode": data.track_launch_mode,
            "track_violation_code": data.track_violation_code,
            "track_process_detached": data.track_process_detached,
            "track_process_session_escape": data.track_process_session_escape,
            "track_root_session_id": data.track_root_session_id,
            "track_process_session_id": data.track_process_session_id,
            "track_root_process_group_id": data.track_root_process_group_id,
            "track_process_group_id": data.track_process_group_id,
            "track_exit_code_unavailable": data.track_exit_code_unavailable,
            "track_capability_verified": capability_ok and str(data.proof_label or "").strip() == "AI_EXECUTED",
        }

        background_tasks.add_task(
            deps.run_background_task,
            deps.engine.log_executed_command,
            command,
            exit_code,
            duration_ms,
            source,
            working_directory,
            provenance_payload,
        )
        return {"status": "ok"}
    finally:
        deps.release_request_slot()


@router.get("/command_store/list", response_model=CommandStoreListResponse, response_model_exclude_unset=True)
def command_store_list(shell: str = "", include_all: bool = False) -> CommandStoreListResponse:
    deps.enter_request_or_503()
    try:
        target_shell = (shell or os.environ.get("SHELL", "zsh")).strip()
        history_file = deps.get_history_file(target_shell)
        vector_db = deps.engine._ensure_vector_db()
        payload = vector_db.list_command_store(history_file=history_file, include_all=include_all)
        return {
            "status": "ok",
            "history_file": history_file,
            **payload,
        }
    finally:
        deps.release_request_slot()


@router.post("/command_store/add", response_model=CommandStoreAddResponse, response_model_exclude_unset=True)
def command_store_add(data: CommandStorePayload) -> CommandStoreAddResponse:
    deps.enter_request_or_503()
    try:
        vector_db = deps.engine._ensure_vector_db()
        result = vector_db.add_manual_commands(data.commands or [])
        return {
            "status": "ok",
            **result,
        }
    finally:
        deps.release_request_slot()


@router.post(
    "/command_store/remove",
    response_model=CommandStoreRemoveResponse,
    response_model_exclude_unset=True,
)
def command_store_remove(data: CommandStoreRemovePayload) -> CommandStoreRemoveResponse:
    deps.enter_request_or_503()
    try:
        target_shell = (data.shell or os.environ.get("SHELL", "zsh")).strip()
        history_file = deps.get_history_file(target_shell)
        vector_db = deps.engine._ensure_vector_db()

        normalized_targets = deps.normalize_unique_commands(data.commands or [], vector_db)
        result = vector_db.remove_commands_exact(normalized_targets)

        history_removed_lines = 0
        warnings_list: list[str] = []
        if normalized_targets:
            history_removed_lines, history_warning = deps.rewrite_history_without_commands(
                history_file,
                set(normalized_targets),
            )
            if history_warning:
                warnings_list.append(history_warning)
            elif history_file and not vector_db.align_history_index_state_to_end(history_file):
                warnings_list.append("History index pointer could not be aligned after rewrite.")

        return {
            "status": "ok",
            "history_file": history_file,
            "history_removed_lines": history_removed_lines,
            "warnings": warnings_list,
            **result,
        }
    finally:
        deps.release_request_slot()


@router.post(
    "/command_store/resync_history",
    response_model=CommandStoreResyncResponse,
    response_model_exclude_unset=True,
)
def command_store_resync_history(data: CommandStoreHistoryPayload) -> CommandStoreResyncResponse:
    deps.enter_request_or_503()
    try:
        target_shell = (data.shell or os.environ.get("SHELL", "zsh")).strip()
        history_file = deps.get_history_file(target_shell)
        vector_db = deps.engine._ensure_vector_db()
        result = vector_db.resync_history(history_file)
        return {
            "status": str(result.get("status", "ok") or "ok"),
            "history_file": history_file,
            "parsed_entries": int(result.get("parsed_entries", 0) or 0),
            "unique_commands": int(result.get("unique_commands", 0) or 0),
            "delta_commands": int(result.get("delta_commands", 0) or 0),
            "imported_commands": int(result.get("imported_commands", 0) or 0),
            "reason": str(result.get("reason", "") or "") or None,
        }
    finally:
        deps.release_request_slot()
