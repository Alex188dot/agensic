import json
import os
from pathlib import Path

from agensic.paths import APP_PATHS, ensure_app_layout, migrate_legacy_layout
from agensic.utils import atomic_write_json_private, enforce_private_file, ensure_private_dir


CONFIG_DIR = APP_PATHS.config_dir
CONFIG_FILE = APP_PATHS.config_file
DEFAULT_LLM_CALLS_PER_LINE = 4
MAX_LLM_CALLS_PER_LINE = 99
DEFAULT_LLM_REQUESTS_PER_MINUTE = 120
MIN_LLM_REQUESTS_PER_MINUTE = 10
MAX_LLM_REQUESTS_PER_MINUTE = 240
DEFAULT_TIMEOUT_SECONDS = 20.0
MIN_TIMEOUT_SECONDS = 1.0
MAX_TIMEOUT_SECONDS = 30.0
DEFAULT_PROVENANCE_REGISTRY_URL = "https://registry.agensic.ai/v1/agents.json"
DEFAULT_PROVENANCE_REGISTRY_REFRESH_HOURS = 24
MIN_PROVENANCE_REGISTRY_REFRESH_HOURS = 1
MAX_PROVENANCE_REGISTRY_REFRESH_HOURS = 168


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

    registry_url = str(config.get("provenance_registry_url", DEFAULT_PROVENANCE_REGISTRY_URL) or "").strip()
    config["provenance_registry_url"] = registry_url or DEFAULT_PROVENANCE_REGISTRY_URL
    config["provenance_registry_pubkey"] = str(config.get("provenance_registry_pubkey", "") or "").strip()
    refresh_hours = _parse_int(
        config.get("provenance_registry_refresh_hours", DEFAULT_PROVENANCE_REGISTRY_REFRESH_HOURS),
        DEFAULT_PROVENANCE_REGISTRY_REFRESH_HOURS,
    )
    config["provenance_registry_refresh_hours"] = _clamp_int(
        refresh_hours,
        MIN_PROVENANCE_REGISTRY_REFRESH_HOURS,
        MAX_PROVENANCE_REGISTRY_REFRESH_HOURS,
    )
    config["include_ai_executed_in_suggestions"] = bool(
        config.get("include_ai_executed_in_suggestions", False)
    )
    return config


def load_config_file(path: str | None = None) -> dict:
    target = Path(path or CONFIG_FILE).expanduser()
    migrate_legacy_layout()
    ensure_app_layout()
    ensure_private_dir(target.parent)
    enforce_private_file(target)
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
    migrate_legacy_layout()
    ensure_app_layout()
    ensure_private_dir(target.parent)
    normalized = normalize_config_payload(config)
    atomic_write_json_private(target, normalized, indent=4)
