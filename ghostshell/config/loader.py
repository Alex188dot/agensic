import json
import os
from pathlib import Path


CONFIG_DIR = os.path.expanduser("~/.ghostshell")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
DEFAULT_LLM_CALLS_PER_LINE = 4
MAX_LLM_CALLS_PER_LINE = 99
DEFAULT_LLM_REQUESTS_PER_MINUTE = 120
MIN_LLM_REQUESTS_PER_MINUTE = 10
MAX_LLM_REQUESTS_PER_MINUTE = 240
DEFAULT_TIMEOUT_SECONDS = 20.0
MIN_TIMEOUT_SECONDS = 1.0
MAX_TIMEOUT_SECONDS = 30.0


def _parse_int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_float(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp_int(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, int(value)))


def normalize_config_payload(payload: dict | None) -> dict:
    config = dict(payload or {})

    budget = _parse_int(
        config.get("llm_calls_per_line", DEFAULT_LLM_CALLS_PER_LINE),
        DEFAULT_LLM_CALLS_PER_LINE,
    )
    if budget < 0 or budget > MAX_LLM_CALLS_PER_LINE:
        budget = DEFAULT_LLM_CALLS_PER_LINE
    config["llm_calls_per_line"] = budget
    config["llm_budget_unlimited"] = bool(config.get("llm_budget_unlimited", False))

    rate_limit = _parse_int(
        config.get("llm_requests_per_minute", DEFAULT_LLM_REQUESTS_PER_MINUTE),
        DEFAULT_LLM_REQUESTS_PER_MINUTE,
    )
    config["llm_requests_per_minute"] = _clamp_int(
        rate_limit,
        MIN_LLM_REQUESTS_PER_MINUTE,
        MAX_LLM_REQUESTS_PER_MINUTE,
    )

    timeout = _parse_float(config.get("timeout", DEFAULT_TIMEOUT_SECONDS), DEFAULT_TIMEOUT_SECONDS)
    config["timeout"] = max(MIN_TIMEOUT_SECONDS, min(MAX_TIMEOUT_SECONDS, timeout))
    return config


def load_config_file(path: str | None = None) -> dict:
    target = Path(path or CONFIG_FILE).expanduser()
    if not target.exists() or not target.is_file():
        return normalize_config_payload({})
    try:
        with open(target, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if isinstance(payload, dict):
            return normalize_config_payload(payload)
    except Exception:
        return normalize_config_payload({})
    return normalize_config_payload({})


def save_config_file(config: dict, path: str | None = None) -> None:
    target = Path(path or CONFIG_FILE).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    normalized = normalize_config_payload(config)
    with open(target, "w", encoding="utf-8") as f:
        json.dump(normalized, f, indent=4)
