from pydantic import BaseModel


class Context(BaseModel):
    command_buffer: str
    cursor_position: int
    working_directory: str
    shell: str
    allow_ai: bool = True
    trigger_source: str | None = None


class IntentContext(BaseModel):
    intent_text: str
    working_directory: str
    shell: str
    terminal: str | None = None
    platform: str | None = None


class AssistContext(BaseModel):
    prompt_text: str
    working_directory: str
    shell: str
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
