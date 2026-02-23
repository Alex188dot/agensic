from dataclasses import dataclass, field


@dataclass
class GhostShellConfig:
    provider: str = "openai"
    model: str = "gpt-5-mini"
    api_key: str = ""
    base_url: str = ""
    llm_calls_per_line: int = 4
    llm_budget_unlimited: bool = False
    disabled_command_patterns: list[str] = field(default_factory=list)
    headers: dict = field(default_factory=dict)
    timeout: float | None = None
    llm_requests_per_minute: int = 120
    api_version: str = ""
    extra_body: dict = field(default_factory=dict)
