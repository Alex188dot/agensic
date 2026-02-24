from typing import Any

from pydantic import BaseModel, Field


class Context(BaseModel):
    command_buffer: str = Field(max_length=4096)
    cursor_position: int
    working_directory: str = Field(max_length=2048)
    shell: str = Field(max_length=128)
    allow_ai: bool = True
    trigger_source: str | None = None


class IntentContext(BaseModel):
    intent_text: str = Field(max_length=2000)
    working_directory: str = Field(max_length=2048)
    shell: str = Field(max_length=128)
    terminal: str | None = None
    platform: str | None = None


class AssistContext(BaseModel):
    prompt_text: str = Field(max_length=4000)
    working_directory: str = Field(max_length=2048)
    shell: str = Field(max_length=128)
    terminal: str | None = None
    platform: str | None = None


class Feedback(BaseModel):
    command_buffer: str
    accepted_suggestion: str
    accept_mode: str = "suffix_append"


class CommandStorePayload(BaseModel):
    commands: list[str]


class CommandStoreRemovePayload(BaseModel):
    commands: list[str]
    shell: str | None = None


class BootstrapStatus(BaseModel):
    running: bool = False
    ready: bool = False
    history_file: str | None = None
    indexed_commands: int = 0
    phase: str = "starting"
    model_download_in_progress: bool = False
    model_download_needed: bool = False
    error: str = ""
    storage_state: str = "unknown"
    storage_error_code: str = ""
    storage_error_detail: str = ""
    state_backend: str = "sqlite"
    sqlite_state: str = "unknown"
    journal_state: str = "unavailable"
    snapshot_state: str = "missing"
    auto_recover_attempted: bool = False
    auto_recover_result: str = "skipped"


class ShutdownStatus(BaseModel):
    shutting_down: bool = False
    reason: str = ""
    active_requests: int = 0
    active_background_jobs: int = 0
    active_jobs_total: int = 0


class PredictResponse(BaseModel):
    suggestions: list[str]
    pool: list[str]
    pool_meta: list[dict[str, Any]]
    bootstrap: BootstrapStatus | None = None
    used_ai: bool


class IntentResponse(BaseModel):
    status: str = "refusal"
    primary_command: str = ""
    explanation: str = ""
    alternatives: list[str] = Field(default_factory=list)
    copy_block: str = ""


class AssistResponse(BaseModel):
    answer: str


class GenericStatusResponse(BaseModel):
    status: str


class RepairExportResponse(BaseModel):
    status: str = "ok"
    snapshot: dict[str, Any] = Field(default_factory=dict)


class RepairImportPayload(BaseModel):
    snapshot: dict[str, Any] = Field(default_factory=dict)


class RepairImportResponse(BaseModel):
    status: str = "ok"
    commands_imported: int = 0
    feedback_imported: int = 0
    removed_imported: int = 0


class RepairRecoverResponse(BaseModel):
    status: str = "ok"
    restored: bool = False
    replay_total: int = 0
    replay_applied: int = 0
    replay_skipped: int = 0
    reason: str = ""


class LogCommandResponse(BaseModel):
    status: str
    reason: str | None = None


class CommandStoreEntry(BaseModel):
    command: str
    accept_count: int = 0
    execute_count: int = 0
    history_count: int = 0
    usage_score: int = 0
    reason: str | None = None


class CommandStoreListResponse(BaseModel):
    status: str
    history_file: str
    potential_wrong: list[CommandStoreEntry] = Field(default_factory=list)
    commands: list[CommandStoreEntry] = Field(default_factory=list)
    total_commands: int = 0


class CommandStoreAddResponse(BaseModel):
    status: str
    requested: int = 0
    normalized: int = 0
    inserted: int = 0
    already_present: int = 0
    unblocked_removed: int = 0


class CommandStoreRemoveResponse(BaseModel):
    status: str
    history_file: str
    history_removed_lines: int = 0
    warnings: list[str] = Field(default_factory=list)
    requested: int = 0
    normalized: int = 0
    vector_removed: int = 0
    guarded: int = 0


class StatusResponse(BaseModel):
    status: str
    bootstrap: BootstrapStatus
    shutdown: ShutdownStatus | None = None
