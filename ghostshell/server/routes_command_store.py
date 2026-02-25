import os

from fastapi import APIRouter, BackgroundTasks

from ghostshell.server import deps
from ghostshell.server.schemas import (
    CommandStoreAddResponse,
    CommandStoreListResponse,
    CommandStorePayload,
    CommandStoreRemovePayload,
    CommandStoreRemoveResponse,
    LogCommandResponse,
)

router = APIRouter()


@router.post("/log_command", response_model=LogCommandResponse, response_model_exclude_unset=True)
def log_command(data: dict, background_tasks: BackgroundTasks) -> LogCommandResponse:
    deps.enter_request_or_503()
    try:
        command = str(data.get("command", "") or "").strip()
        if not command:
            return {"status": "ignored", "reason": "empty_command"}

        raw_exit_code = data.get("exit_code", None)
        exit_code = None
        if raw_exit_code is not None:
            try:
                exit_code = int(raw_exit_code)
            except (TypeError, ValueError):
                return {"status": "ignored", "reason": "invalid_exit_code"}

        source = str(data.get("source", "unknown") or "unknown").strip().lower()
        if source not in {"runtime", "history", "unknown"}:
            return {"status": "ignored", "reason": "invalid_source"}
        working_directory = str(data.get("working_directory", "") or "").strip() or None

        config = deps.load_config()
        patterns = deps.disabled_patterns_from_config(config)
        if deps.command_matches_disabled_pattern(command, patterns):
            return {"status": "ignored", "reason": "disabled_pattern"}

        background_tasks.add_task(
            deps.run_background_task,
            deps.engine.log_executed_command,
            command,
            exit_code,
            source,
            working_directory,
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
