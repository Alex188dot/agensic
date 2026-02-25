import typer
import json
import os
import shutil
import subprocess
import sys
import time
import requests
import questionary
import socket
import signal
import shlex
from typing import Any
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from prompt_toolkit.application import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.key_bindings import merge_key_bindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.styles import Style
from questionary import Separator, Question
from questionary.constants import DEFAULT_QUESTION_PREFIX
from questionary.prompts import common
from questionary.prompts.common import InquirerControl
from questionary.styles import merge_styles_default
from questionary import utils
from ghostshell.version import __version__
from ghostshell.config.loader import (
    DEFAULT_LLM_CALLS_PER_LINE,
    MAX_LLM_CALLS_PER_LINE,
    load_config_file,
    normalize_config_payload,
    save_config_file,
)

app = typer.Typer(add_completion=False)
console = Console()

CONFIG_DIR = os.path.expanduser("~/.ghostshell")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
PID_FILE = os.path.join(CONFIG_DIR, "daemon.pid")
PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
SERVER_SCRIPT = os.path.join(PROJECT_ROOT, "server.py")
SHELL_CLIENT_SCRIPT = os.path.join(PROJECT_ROOT, "shell_client.py")
PLIST_PATH = os.path.expanduser("~/Library/LaunchAgents/com.ghostshell.daemon.plist")
LOCKS_DIR = os.path.join(CONFIG_DIR, "locks")
FIX_LOCK_FILE = os.path.join(LOCKS_DIR, "fix.lock")
REPAIR_DIR = os.path.join(CONFIG_DIR, "repair")
REPAIR_LOG_FILE = os.path.join(REPAIR_DIR, "repair.log")
ZVEC_COMMANDS_PATH = os.path.join(CONFIG_DIR, "zvec_commands")
ZVEC_FEEDBACK_PATH = os.path.join(CONFIG_DIR, "zvec_feedback_stats")
LAST_INDEXED_PATH = os.path.join(CONFIG_DIR, "last_indexed_line")
STATE_SQLITE_PATH = os.path.join(CONFIG_DIR, "state.sqlite")
EVENTS_DIR = os.path.join(CONFIG_DIR, "events")
SNAPSHOTS_DIR = os.path.join(CONFIG_DIR, "snapshots")

class _BackSignal:
    pass


BACK_SIGNAL = _BackSignal()


def _setup_style() -> Style:
    return merge_styles_default(
        [
            Style([("instruction", "fg:#ff8c00 bold")]),
            Style([("instruction-key", "fg:#ff8c00 bold")]),
        ]
    )


def _print_screen_heading(title: str) -> None:
    console.print(f"[bold cyan]{title}[/bold cyan]")


def _attach_escape_back(question: Question) -> Question:
    extra_bindings = KeyBindings()

    @extra_bindings.add(Keys.Escape, eager=True)
    def _escape(event):
        event.app.exit(result=BACK_SIGNAL)

    question.application.key_bindings = merge_key_bindings(
        [question.application.key_bindings, extra_bindings]
    )
    return question


def _setup_select(message: str, choices: list[str], **kwargs) -> Any:
    question = questionary.select(
        message,
        choices=choices,
        pointer="👉",
        instruction=" ",
        style=_setup_style(),
        **kwargs,
    )
    return _attach_escape_back(question).ask()


def _setup_text(message: str, default: str = "", show_back_instruction: bool = False, **kwargs) -> Any:
    instruction = "Esc = back" if show_back_instruction else None
    question = questionary.text(
        message,
        default=default,
        instruction=instruction,
        style=_setup_style(),
        **kwargs,
    )
    return _attach_escape_back(question).ask()


def _setup_confirm(message: str, default: bool = True, **kwargs) -> Any:
    question = questionary.confirm(
        message,
        default=default,
        style=_setup_style(),
        **kwargs,
    )
    return _attach_escape_back(question).ask()


def _setup_password(message: str, **kwargs) -> Any:
    question = questionary.password(
        message,
        style=_setup_style(),
        **kwargs,
    )
    return _attach_escape_back(question).ask()


def _is_back(value: Any) -> bool:
    return value is BACK_SIGNAL

def ensure_config_dir():
    if not os.path.exists(CONFIG_DIR): os.makedirs(CONFIG_DIR)

def _load_config() -> dict:
    return load_config_file(CONFIG_FILE)

def _save_config(config: dict):
    save_config_file(config, CONFIG_FILE)


def _repair_cli_enabled() -> bool:
    config = _load_config()
    return bool(config.get("repair_cli_enabled", True))


def _append_repair_log(event: str, details: dict | None = None) -> None:
    try:
        os.makedirs(REPAIR_DIR, exist_ok=True)
        payload = {
            "ts": int(time.time()),
            "event": str(event or "unknown"),
            "details": details or {},
        }
        with open(REPAIR_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, separators=(",", ":")) + "\n")
    except Exception:
        pass


def _extract_storage_health(payload: dict | None) -> tuple[str, str, str]:
    if not isinstance(payload, dict):
        return ("unknown", "", "")
    bootstrap = payload.get("bootstrap", {})
    if not isinstance(bootstrap, dict):
        return ("unknown", "", "")
    state = str(bootstrap.get("storage_state", "unknown") or "unknown").strip().lower()
    code = str(bootstrap.get("storage_error_code", "") or "").strip()
    detail = str(bootstrap.get("storage_error_detail", "") or "").strip()
    if not state:
        state = "unknown"
    return (state, code, detail)


def _print_storage_corruption_banner(payload: dict | None) -> None:
    state, code, detail = _extract_storage_health(payload)
    if state != "corrupt":
        return
    text = detail or "Detected inconsistent semantic index files."
    if len(text) > 180:
        text = text[:180].rstrip() + "..."
    body = (
        "[bold red]Command semantic index appears corrupted.[/bold red]\n"
        f"[red]Reason:[/red] {text}\n"
        f"[red]Code:[/red] {code or 'vector_db_corrupt'}\n\n"
        "[red]Available fixes:[/red]\n"
        "  aiterminal fix --safe    (recommended)\n"
        "  aiterminal fix --recover\n"
        "  aiterminal fix --factory-reset"
    )
    console.print(Panel.fit(body, border_style="red", title="Storage Health"))
    _append_repair_log(
        "corruption_banner_printed",
        {"code": code or "vector_db_corrupt", "detail": text},
    )


def _run_storage_preflight_if_enabled(invoked_subcommand: str | None) -> None:
    if not _repair_cli_enabled():
        return
    if "--help" in sys.argv or "-h" in sys.argv:
        return
    if invoked_subcommand in {"fix"}:
        return
    payload = _fetch_daemon_status()
    _print_storage_corruption_banner(payload)


def _remove_path(path: str) -> tuple[bool, str]:
    target = os.path.expanduser(path)
    if not os.path.exists(target):
        return (True, "")
    try:
        if os.path.isdir(target):
            shutil.rmtree(target)
        else:
            os.remove(target)
        return (True, "")
    except Exception as exc:
        return (False, str(exc))


def _acquire_fix_lock() -> tuple[int | None, str]:
    os.makedirs(LOCKS_DIR, exist_ok=True)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(FIX_LOCK_FILE, flags, 0o600)
        os.write(fd, str(os.getpid()).encode("utf-8"))
        return (fd, "")
    except FileExistsError:
        return (None, "Another repair operation is already running.")
    except Exception as exc:
        return (None, str(exc))


def _release_fix_lock(fd: int | None) -> None:
    if fd is not None:
        try:
            os.close(fd)
        except Exception:
            pass
    try:
        if os.path.exists(FIX_LOCK_FILE):
            os.remove(FIX_LOCK_FILE)
    except Exception:
        pass


def _repair_export_snapshot() -> tuple[dict | None, str]:
    try:
        response = requests.post("http://127.0.0.1:22000/repair/export", timeout=20)
    except Exception as exc:
        return (None, str(exc))
    if response.status_code != 200:
        return (None, f"http_{response.status_code}")
    try:
        payload = response.json()
    except Exception:
        return (None, "bad_json")
    if not isinstance(payload, dict):
        return (None, "bad_shape")
    status = str(payload.get("status", "") or "").strip().lower()
    if status and status != "ok":
        return (None, f"status_{status}")
    snapshot = payload.get("snapshot", {})
    if not isinstance(snapshot, dict):
        return (None, "bad_snapshot")
    return (snapshot, "")


def _repair_import_snapshot(snapshot: dict) -> tuple[dict | None, str]:
    try:
        response = requests.post(
            "http://127.0.0.1:22000/repair/import",
            json={"snapshot": snapshot},
            timeout=35,
        )
    except Exception as exc:
        return (None, str(exc))
    if response.status_code != 200:
        return (None, f"http_{response.status_code}")
    try:
        payload = response.json()
    except Exception:
        return (None, "bad_json")
    if not isinstance(payload, dict):
        return (None, "bad_shape")
    status = str(payload.get("status", "") or "").strip().lower()
    if status and status != "ok":
        return (None, f"status_{status}")
    return (payload, "")


def _write_snapshot_artifact(snapshot: dict) -> str:
    os.makedirs(REPAIR_DIR, exist_ok=True)
    stamp = int(time.time())
    out_path = os.path.join(REPAIR_DIR, f"snapshot-{stamp}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2)
    return out_path

def _normalize_command_pattern(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    try:
        tokens = shlex.split(value, posix=True)
    except Exception:
        tokens = value.split()
    if not tokens:
        return ""
    token = os.path.basename(tokens[0]).strip().lower()
    return token

def _extract_executable_token(command: str) -> str:
    raw = str(command or "").strip()
    if not raw:
        return ""
    try:
        tokens = shlex.split(raw, posix=True)
    except Exception:
        tokens = raw.split()
    if not tokens:
        return ""

    i = 0
    n = len(tokens)
    while i < n:
        token = (tokens[i] or "").strip()
        if not token:
            i += 1
            continue
        if token in {"sudo", "command"}:
            i += 1
            continue
        if token in {"env", "/usr/bin/env"}:
            i += 1
            while i < n:
                env_token = (tokens[i] or "").strip()
                if not env_token or env_token.startswith("-") or ("=" in env_token and not env_token.startswith("=")):
                    i += 1
                    continue
                break
            continue
        if token.startswith("-"):
            i += 1
            continue
        if "=" in token and not token.startswith("="):
            i += 1
            continue
        return os.path.basename(token).strip().lower()
    return ""

def _command_matches_disabled_patterns(command: str, patterns: list[str]) -> bool:
    exe = _extract_executable_token(command)
    if not exe:
        return False
    for pattern in patterns:
        if exe.startswith(pattern) or pattern.startswith(exe):
            return True
    return False

def _sanitize_disabled_patterns(values) -> list[str]:
    if not isinstance(values, list):
        return []
    clean: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _normalize_command_pattern(str(value))
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        clean.append(normalized)
    return clean

def _get_disabled_patterns(config: dict) -> list[str]:
    return _sanitize_disabled_patterns(config.get("disabled_command_patterns", []))

def _with_disabled_patterns(config: dict, patterns: list[str]) -> dict:
    updated = dict(config or {})
    updated["disabled_command_patterns"] = _sanitize_disabled_patterns(patterns)
    return updated


def _get_llm_calls_per_line(config: dict) -> int:
    normalized = normalize_config_payload(config)
    return int(normalized["llm_calls_per_line"])


def _is_llm_budget_unlimited(config: dict) -> bool:
    normalized = normalize_config_payload(config)
    return bool(normalized["llm_budget_unlimited"])


def _with_llm_calls_per_line(config: dict, value: int) -> dict:
    updated = normalize_config_payload(config)
    updated["llm_calls_per_line"] = max(0, min(MAX_LLM_CALLS_PER_LINE, int(value)))
    updated["llm_budget_unlimited"] = False
    return updated


def _with_llm_budget_unlimited(config: dict, enabled: bool) -> dict:
    updated = normalize_config_payload(config)
    updated["llm_budget_unlimited"] = bool(enabled)
    return updated

def _disable_pattern_in_config(config: dict, raw_pattern: str) -> tuple[dict, str, bool]:
    normalized = _normalize_command_pattern(raw_pattern)
    if not normalized:
        return (dict(config or {}), "", False)

    patterns = _get_disabled_patterns(config)
    changed = normalized not in patterns
    if changed:
        patterns.append(normalized)
    return (_with_disabled_patterns(config, patterns), normalized, changed)

def _enable_pattern_in_config(config: dict, raw_pattern: str) -> tuple[dict, bool]:
    normalized = _normalize_command_pattern(raw_pattern)
    patterns = _get_disabled_patterns(config)
    filtered = [pattern for pattern in patterns if pattern != normalized]
    changed = len(filtered) != len(patterns)
    return (_with_disabled_patterns(config, filtered), changed)

_PROVIDER_SETUP_CHOICES: list[tuple[str, str]] = [
    ("anthropic", "Anthropic"),
    ("sagemaker", "AWS Sagemaker"),
    ("azure", "Azure"),
    ("custom", "Custom model"),
    ("dashscope", "DashScope (Qwen)"),
    ("deepseek", "DeepSeek"),
    ("gemini", "Gemini"),
    ("groq", "Groq"),
    ("lm_studio", "LM Studio"),
    ("minimax", "MiniMax"),
    ("mistral", "Mistral"),
    ("moonshot", "Moonshot"),
    ("openai", "OpenAI"),
    ("openrouter", "OpenRouter"),
    ("ollama", "Ollama"),
    ("xiaomi_mimo", "Xiaomi MiMo"),
    ("zai", "Z.AI (Zhipu AI)"),
    ("history_only", "use without AI (will just use your history)"),
]

_PROVIDER_DEFAULT_MODELS: dict[str, str] = {
    "openai": "gpt-5-mini",
    "groq": "openai/gpt-oss-20b",
    "ollama": "sam860/LFM2:1.2b",
    "lm_studio": "qwen/qwen3-4b",
    "custom": "your-custom-model-name",
    "gemini": "gemini-3-flash-preview",
    "anthropic": "claude-3-7-sonnet-20250219",
    "azure": "gpt-5-mini",
    "dashscope": "dashscope/qwen-turbo",
    "minimax": "minimax/MiniMax-M2.1",
    "deepseek": "deepseek/deepseek-chat",
    "moonshot": "moonshot/moonshot-v1-8k",
    "mistral": "mistral/mistral-small-latest",
    "openrouter": "openrouter/openai/gpt-4o-mini",
    "xiaomi_mimo": "xiaomi_mimo/mimo-v2-flash",
    "zai": "zai/glm-4.7",
    "sagemaker": "sagemaker/<your-endpoint-name>",
    "history_only": "history-only",
}

_PROVIDER_DEFAULT_BASE_URLS: dict[str, str] = {
    "ollama": "http://localhost:11434",
    "lm_studio": "http://localhost:1234/v1",
    "custom": "https://api.openai.com/v1",
    "dashscope": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    "minimax": "https://api.minimax.io/anthropic/v1/messages",
    "moonshot": "https://api.moonshot.ai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}

_PROVIDER_OPTIONAL_API_KEY_PROMPT: set[str] = {"ollama", "lm_studio", "sagemaker"}


def _provider_labels_to_id() -> dict[str, str]:
    return {label: provider_id for provider_id, label in _PROVIDER_SETUP_CHOICES}


def _provider_id_to_label() -> dict[str, str]:
    return {provider_id: label for provider_id, label in _PROVIDER_SETUP_CHOICES}


def _default_model_for_provider(provider: str) -> str:
    normalized = str(provider or "").strip().lower()
    return _PROVIDER_DEFAULT_MODELS.get(normalized, "gpt-5-mini")

def _manage_pattern_controls(existing_config: dict):
    _print_screen_heading("Manage GhostShell command patterns")
    while True:
        config = _load_config()
        patterns = _get_disabled_patterns(config)
        action = _setup_select(
            "Pattern controls:",
            choices=[
                "Disable GhostShell for a specific pattern",
                "Re-enable GhostShell for a specific pattern",
            ],
        )
        if _is_back(action) or not action:
            return

        if action.startswith("Disable"):
            raw_pattern = _setup_text(
                "Enter pattern (command family) to disable, e.g. docker:"
            )
            if _is_back(raw_pattern):
                continue
            updated, normalized, changed = _disable_pattern_in_config(config, raw_pattern)
            if not normalized:
                console.print("[yellow]No valid pattern provided. Nothing changed.[/yellow]")
                continue
            _save_config(updated)
            if changed:
                console.print(f"[green]✓ Disabled GhostShell for '{normalized}'.[/green]")
            else:
                console.print(f"[yellow]Pattern '{normalized}' was already disabled.[/yellow]")
            continue

        if not patterns:
            console.print("[yellow]No disabled patterns found. Nothing to re-enable.[/yellow]")
            continue

        selected = _setup_select(
            "Select a pattern to re-enable:",
            choices=patterns,
        )
        if _is_back(selected) or not selected:
            continue
        updated, changed = _enable_pattern_in_config(config, selected)
        if not changed:
            console.print("[yellow]Pattern not found. Nothing changed.[/yellow]")
            continue
        _save_config(updated)
        console.print(f"[green]✓ Re-enabled GhostShell for '{selected}'.[/green]")

def _configure_provider(existing_config: dict) -> bool:
    _print_screen_heading("Choose AI provider")
    config = dict(existing_config or {})
    provider = ""
    model = ""
    api_key = ""
    base_url = ""
    headers_raw = ""
    timeout_raw = ""
    api_version = ""
    extra_body_raw = ""
    step = 0

    while True:
        if step == 0:
            selected_provider = _setup_select(
                "Select Provider:",
                choices=[label for _, label in _PROVIDER_SETUP_CHOICES],
            )
            if _is_back(selected_provider) or not selected_provider:
                return False
            provider = _provider_labels_to_id().get(str(selected_provider), str(selected_provider))
            if provider == "history_only":
                model = _default_model_for_provider(provider)
                api_key = ""
                base_url = ""
                headers_raw = ""
                timeout_raw = ""
                api_version = ""
                extra_body_raw = ""
                step = 5
                continue
            step = 1
            continue

        if step == 1:
            current_provider = str(config.get("provider", "") or "").strip().lower()
            if current_provider == provider and str(config.get("model", "") or "").strip():
                default_model = str(config.get("model"))
            else:
                default_model = _default_model_for_provider(provider)
            model_value = _setup_text(
                "Enter Model Name:",
                default=default_model,
            )
            if _is_back(model_value):
                step = 0
                continue
            if not model_value:
                continue
            model = model_value
            step = 2
            continue

        if step == 2:
            if provider in _PROVIDER_OPTIONAL_API_KEY_PROMPT:
                provider_label = _provider_id_to_label().get(provider, provider)
                wants_api_key = _setup_confirm(f"Set an API key for {provider_label}?", default=False)
                if _is_back(wants_api_key):
                    step = 1
                    continue
                if wants_api_key:
                    value = _setup_password("Enter API Key:")
                    if _is_back(value):
                        step = 1
                        continue
                    api_key = value or ""
                else:
                    api_key = ""
            else:
                value = _setup_password("Enter API Key:")
                if _is_back(value):
                    step = 1
                    continue
                api_key = value or ""
            step = 3
            continue

        if step == 3:
            default_url = _PROVIDER_DEFAULT_BASE_URLS.get(provider, "")
            if default_url:
                value = _setup_text("Enter Base URL:", default=default_url)
                if _is_back(value):
                    step = 2
                    continue
                base_url = value or ""
            else:
                base_url = ""
            step = 4
            continue

        if step == 4:
            if provider == "custom":
                value = _setup_text(
                    "Optional headers as JSON (e.g. {\"X-API-Key\": \"...\"}):",
                    default=headers_raw,
                )
                if _is_back(value):
                    step = 3
                    continue
                headers_raw = value or ""

                value = _setup_text("Optional timeout in seconds:", default=timeout_raw)
                if _is_back(value):
                    step = 3
                    continue
                timeout_raw = value or ""

                value = _setup_text("Optional API version:", default=api_version)
                if _is_back(value):
                    step = 3
                    continue
                api_version = value or ""

                value = _setup_text("Optional extra body as JSON:", default=extra_body_raw)
                if _is_back(value):
                    step = 3
                    continue
                extra_body_raw = value or ""
            step = 5
            continue

        if step == 5:
            config["provider"] = provider
            config["model"] = model
            config["api_key"] = api_key
            config["base_url"] = base_url
            if "disabled_command_patterns" in existing_config:
                config["disabled_command_patterns"] = _get_disabled_patterns(existing_config)

            if provider == "custom":
                if headers_raw.strip():
                    try:
                        parsed_headers = json.loads(headers_raw)
                        if isinstance(parsed_headers, dict):
                            config["headers"] = parsed_headers
                        else:
                            console.print("[yellow]Ignoring headers: JSON must be an object.[/yellow]")
                    except json.JSONDecodeError:
                        console.print("[yellow]Ignoring headers: invalid JSON.[/yellow]")

                if timeout_raw.strip():
                    try:
                        timeout_value = float(timeout_raw)
                        if timeout_value > 0:
                            config["timeout"] = timeout_value
                        else:
                            console.print("[yellow]Ignoring timeout: must be > 0.[/yellow]")
                    except ValueError:
                        console.print("[yellow]Ignoring timeout: invalid number.[/yellow]")

                if api_version.strip():
                    config["api_version"] = api_version.strip()

                if extra_body_raw.strip():
                    try:
                        parsed_body = json.loads(extra_body_raw)
                        if isinstance(parsed_body, dict):
                            config["extra_body"] = parsed_body
                        else:
                            console.print("[yellow]Ignoring extra_body: JSON must be an object.[/yellow]")
                    except json.JSONDecodeError:
                        console.print("[yellow]Ignoring extra_body: invalid JSON.[/yellow]")
            else:
                config.pop("headers", None)
                config.pop("timeout", None)
                config.pop("api_version", None)
                config.pop("extra_body", None)

            if provider == "history_only":
                config["api_key"] = ""
                config["base_url"] = ""
                config["model"] = "history-only"
                config["llm_calls_per_line"] = 0
                config["llm_budget_unlimited"] = False

            _save_config(config)
            console.print("[green]✓ Configuration saved![/green]")
            console.print(f"Provider: {provider}, Model: {model}", style="dim", highlight=False)
            step = 6
            continue

        if step == 6:
            enable_boot = _setup_confirm("Enable start on boot (Recommended)?")
            if _is_back(enable_boot):
                step = 5
                continue
            if enable_boot:
                enable_startup()
            else:
                start()
            return True

def _ensure_command_store_backend_ready() -> bool:
    if not is_port_open():
        should_start = _setup_confirm("Command store needs the daemon running. Start daemon now?")
        if _is_back(should_start) or not should_start:
            console.print("[yellow]Command store cancelled.[/yellow]")
            return False
        try:
            start()
            return True
        except typer.Exit:
            console.print("[red]Failed to start daemon for command store.[/red]")
            return False

    payload = _fetch_daemon_status()
    bootstrap = payload.get("bootstrap", {}) if isinstance(payload, dict) else {}
    if isinstance(bootstrap, dict) and bootstrap.get("ready"):
        return True

    console.print("[yellow]Daemon is running but command index is not ready yet. Waiting...[/yellow]")
    ready, _, error = _wait_for_bootstrap_ready()
    if ready:
        return True

    console.print(f"[red]Command store unavailable:[/red] {error}")
    return False


def _configure_llm_budget(existing_config: dict):
    config = normalize_config_payload(existing_config)
    current = _get_llm_calls_per_line(config)
    unlimited = _is_llm_budget_unlimited(config)
    _print_screen_heading("Customize LLM budget")
    console.print("Budget range: 0-99 calls per command line")
    console.print("0 = no LLM calls")
    console.print("Use 'No budget limit' for unlimited calls")
    console.print("[dim]This limit resets when you submit or clear the command line.[/dim]")

    set_unlimited = _setup_confirm(
        "Enable 'No budget limit'?",
        default=unlimited,
    )
    if _is_back(set_unlimited):
        return
    if set_unlimited:
        config = _with_llm_budget_unlimited(config, True)
        _save_config(config)
        console.print("[green]✓ LLM budget saved.[/green] Unlimited calls per command line")
        return

    while True:
        raw_value = _setup_text(
            "How many LLM calls are allowed per line? (0-99)",
            default=str(current),
        )
        if _is_back(raw_value):
            return
        try:
            parsed = int(str(raw_value).strip())
            if parsed < 0 or parsed > MAX_LLM_CALLS_PER_LINE:
                raise ValueError
        except ValueError:
            console.print("[yellow]Please enter an integer from 0 to 99.[/yellow]")
            continue
        config = _with_llm_calls_per_line(config, parsed)
        _save_config(config)
        console.print(
            f"[green]✓ LLM budget saved.[/green] {parsed} calls per command line "
            f"({'disabled' if parsed == 0 else 'enabled'})"
        )
        return

def _command_store_request(method: str, path: str, payload: dict | None = None) -> dict | None:
    url = f"http://127.0.0.1:22000{path}"
    try:
        response = requests.request(method.upper(), url, json=payload, timeout=20)
    except Exception as exc:
        console.print(f"[red]Failed to reach daemon:[/red] {exc}")
        return None

    if response.status_code != 200:
        body = response.text.strip()
        if body:
            console.print(f"[red]Command store request failed ({response.status_code}):[/red] {body}")
        else:
            console.print(f"[red]Command store request failed ({response.status_code}).[/red]")
        return None

    try:
        data = response.json()
    except ValueError:
        console.print("[red]Invalid response from command store endpoint.[/red]")
        return None
    if not isinstance(data, dict):
        console.print("[red]Unexpected response format from command store endpoint.[/red]")
        return None
    return data

def _manage_command_store_add():
    _print_screen_heading("Add commands")
    raw = _setup_text(
        "Add commands (comma-separated; spaces are ok):"
    )
    if _is_back(raw):
        return
    raw = raw or ""
    parts = [part.strip() for part in raw.split(",")]
    commands = [part for part in parts if part]
    if not commands:
        console.print("[yellow]No commands provided. Nothing changed.[/yellow]")
        return

    payload = _command_store_request("POST", "/command_store/add", {"commands": commands})
    if not payload:
        return
    inserted = int(payload.get("inserted", 0) or 0)
    already_present = int(payload.get("already_present", 0) or 0)
    unblocked_removed = int(payload.get("unblocked_removed", 0) or 0)

    console.print(
        f"[green]✓ Added commands[/green] inserted={inserted}, already_present={already_present}, "
        f"unblocked_from_removed={unblocked_removed}"
    )

def _percentile(sorted_values: list[int], q: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    q = max(0.0, min(1.0, q))
    pos = q * (len(sorted_values) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = pos - lo
    return float(sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac)

def _format_command_store_choice(
    item: dict,
    show_reason: bool = False,
    low_cutoff: float | None = None,
    high_cutoff: float | None = None,
):
    command = str(item.get("command", "") or "").strip()
    usage = int(item.get("usage_score", 0) or 0)
    usage_style = "class:text"
    if low_cutoff is not None and usage <= low_cutoff:
        usage_style = "class:usage-low"
    elif high_cutoff is not None and usage >= high_cutoff:
        usage_style = "class:usage-high"
    label: list[tuple[str, str]] = [
        ("class:text", f"{command} [usage_score:"),
        (usage_style, str(usage)),
        ("class:text", "]"),
    ]
    if show_reason:
        reason = str(item.get("reason", "") or "").strip()
        if reason:
            label.append(("class:text", f" ({reason})"))
    return label

def _checkbox_without_invert(
    message: str,
    choices: list,
    pointer: str = "👉",
    instruction: str | None = None,
):
    # Custom checkbox prompt that keeps "a" (toggle all) but removes "i" (invert).
    merged_style = merge_styles_default(
        [
            Style([("bottom-toolbar", "noreverse")]),
            Style([("instruction-key", "fg:#ff8c00 bold")]),
            Style([("usage-high", "fg:#22c55e bold")]),
            Style([("usage-low", "fg:#ef4444 bold")]),
        ]
    )
    ic = InquirerControl(
        choices,
        default=None,
        pointer=pointer,
        initial_choice=None,
        show_description=True,
    )

    def _message_tokens(msg: str) -> list[tuple[str, str]]:
        # Map simple inline tags used by callers to prompt_toolkit style classes.
        tokens: list[tuple[str, str]] = [("class:question", " ")]
        i = 0
        active_style = "class:question"
        while i < len(msg):
            if msg.startswith("[green]", i):
                active_style = "class:usage-high"
                i += len("[green]")
                continue
            if msg.startswith("[/green]", i):
                active_style = "class:question"
                i += len("[/green]")
                continue
            if msg.startswith("[red]", i):
                active_style = "class:usage-low"
                i += len("[red]")
                continue
            if msg.startswith("[/red]", i):
                active_style = "class:question"
                i += len("[/red]")
                continue
            tokens.append((active_style, msg[i]))
            i += 1
        return tokens

    def get_prompt_tokens():
        tokens = [("class:qmark", DEFAULT_QUESTION_PREFIX)]
        tokens.extend(_message_tokens(message))
        if ic.is_answered:
            nbr_selected = len(ic.selected_options)
            if nbr_selected == 0:
                tokens.append(("class:answer", "done"))
            elif nbr_selected == 1:
                selected_title = ic.get_selected_values()[0].title
                if isinstance(selected_title, list):
                    tokens.append(("class:answer", "".join([token[1] for token in selected_title])))
                else:
                    tokens.append(("class:answer", f"[{selected_title}]"))
            else:
                tokens.append(("class:answer", f"done ({nbr_selected} selections)"))
        else:
            if instruction:
                tokens.append(("class:instruction", instruction))
            else:
                tokens.extend(
                    [
                        ("class:instruction", "(Use "),
                        ("class:instruction-key", "arrow keys"),
                        ("class:instruction", " to move, "),
                        ("class:instruction-key", "<space>"),
                        ("class:instruction", " to select, "),
                        ("class:instruction-key", "letter keys"),
                        ("class:instruction", " to jump, "),
                        ("class:instruction-key", "<Ctrl-A>"),
                        ("class:instruction", " to select all)"),
                    ]
                )
        return tokens

    def get_selected_values():
        return [c.value for c in ic.get_selected_values()]

    def perform_validation(_selected_values):
        ic.error_message = None
        return True

    layout = common.create_inquirer_layout(ic, get_prompt_tokens)
    bindings = KeyBindings()

    @bindings.add(Keys.ControlQ, eager=True)
    @bindings.add(Keys.ControlC, eager=True)
    def _abort(event):
        event.app.exit(exception=KeyboardInterrupt, style="class:aborting")

    @bindings.add(" ", eager=True)
    def _toggle(_event):
        pointed_choice = ic.get_pointed_at().value
        if pointed_choice in ic.selected_options:
            ic.selected_options.remove(pointed_choice)
        else:
            ic.selected_options.append(pointed_choice)
        perform_validation(get_selected_values())

    @bindings.add(Keys.ControlA, eager=True)
    def _toggle_all(_event):
        all_selected = True
        for c in ic.choices:
            if not isinstance(c, Separator) and c.value not in ic.selected_options and not c.disabled:
                ic.selected_options.append(c.value)
                all_selected = False
        if all_selected:
            ic.selected_options = []
        perform_validation(get_selected_values())

    def _move_cursor_down(_event):
        ic.select_next()
        while not ic.is_selection_valid():
            ic.select_next()

    def _move_cursor_up(_event):
        ic.select_previous()
        while not ic.is_selection_valid():
            ic.select_previous()

    bindings.add(Keys.Down, eager=True)(_move_cursor_down)
    bindings.add(Keys.Up, eager=True)(_move_cursor_up)
    bindings.add(Keys.ControlN, eager=True)(_move_cursor_down)
    bindings.add(Keys.ControlP, eager=True)(_move_cursor_up)

    @bindings.add(Keys.ControlM, eager=True)
    def _submit(event):
        selected_values = get_selected_values()
        ic.submission_attempted = True
        if perform_validation(selected_values):
            ic.is_answered = True
            event.app.exit(result=selected_values)

    @bindings.add(Keys.Escape, eager=True)
    def _back(event):
        event.app.exit(result=BACK_SIGNAL)

    @bindings.add(Keys.Any, eager=True)
    def _jump_to_first_by_letter(event):
        # Letter key navigation: jump to first command that starts with typed letter.
        key = str(getattr(event, "data", "") or "")
        if len(key) != 1 or not key.isalpha():
            return
        prefix = key.lower()
        for idx, choice in enumerate(ic.choices):
            if isinstance(choice, Separator) or getattr(choice, "disabled", False):
                continue
            value = str(getattr(choice, "value", "") or "").strip().lower()
            if value.startswith(prefix):
                ic.pointed_at = idx
                return

    return Question(
        Application(
            layout=layout,
            key_bindings=bindings,
            style=merged_style,
            **utils.used_kwargs({}, Application.__init__),
        )
    )

def _manage_command_store_remove():
    _print_screen_heading("Remove commands")
    while True:
        payload = _command_store_request("GET", "/command_store/list?include_all=true")
        if not payload:
            return

        potential_wrong = payload.get("potential_wrong", [])
        commands = payload.get("commands", [])
        if not isinstance(potential_wrong, list):
            potential_wrong = []
        if not isinstance(commands, list):
            commands = []

        usage_values: list[int] = []
        for item in potential_wrong + commands:
            if not isinstance(item, dict):
                continue
            usage_values.append(int(item.get("usage_score", 0) or 0))
        usage_values.sort()
        low_cutoff = _percentile(usage_values, 0.10) if usage_values else None
        high_cutoff = _percentile(usage_values, 0.90) if usage_values else None

        choices: list = []
        seen: set[str] = set()

        if potential_wrong:
            choices.append(questionary.Separator("Potential wrong commands"))
            for item in potential_wrong:
                if not isinstance(item, dict):
                    continue
                command = str(item.get("command", "") or "").strip()
                if not command or command in seen:
                    continue
                seen.add(command)
                choices.append(
                    questionary.Choice(
                        title=_format_command_store_choice(
                            item,
                            show_reason=True,
                            low_cutoff=low_cutoff,
                            high_cutoff=high_cutoff,
                        ),
                        value=command,
                    )
                )

        regular_choices = []
        for item in commands:
            if not isinstance(item, dict):
                continue
            command = str(item.get("command", "") or "").strip()
            if not command or command in seen:
                continue
            seen.add(command)
            regular_choices.append(
                questionary.Choice(
                    title=_format_command_store_choice(
                        item,
                        show_reason=False,
                        low_cutoff=low_cutoff,
                        high_cutoff=high_cutoff,
                    ),
                    value=command,
                )
            )

        if regular_choices:
            choices.append(questionary.Separator("Commands"))
            choices.extend(regular_choices)

        if not choices:
            console.print("[yellow]No commands available in command store.[/yellow]")
            return

        selected = _checkbox_without_invert(
            "Select commands to remove ([green]top 10%[/green] in green, [red]bottom 10%[/red] in red):",
            choices=choices,
            pointer="👉",
        ).ask()
        if _is_back(selected):
            return
        if not selected:
            console.print("[yellow]No commands selected. Nothing changed.[/yellow]")
            return

        count = len(selected)
        prompt = (
            "Are you sure you want to delete this command from your history and command store?"
            if count == 1
            else "Are you sure you want to delete these commands from your history and command store?"
        )
        confirmed = _setup_confirm(prompt)
        if _is_back(confirmed):
            continue
        if not confirmed:
            console.print("[yellow]Deletion cancelled.[/yellow]")
            return

        shell_name = os.environ.get("SHELL", "zsh")
        result = _command_store_request(
            "POST",
            "/command_store/remove",
            {"commands": selected, "shell": shell_name},
        )
        if not result:
            return

        vector_removed = int(result.get("vector_removed", 0) or 0)
        guarded = int(result.get("guarded", 0) or 0)
        history_removed = int(result.get("history_removed_lines", 0) or 0)
        console.print(
            f"[green]✓ Removed commands[/green] vector_removed={vector_removed}, "
            f"history_lines_removed={history_removed}, guarded={guarded}"
        )

        warnings_list = result.get("warnings", [])
        if isinstance(warnings_list, list):
            for warning in warnings_list:
                message = str(warning or "").strip()
                if message:
                    console.print(f"[yellow]{message}[/yellow]")
        return

def _manage_command_store():
    if not _ensure_command_store_backend_ready():
        return

    _print_screen_heading("Manage command store")
    while True:
        action = _setup_select(
            "Manage command store:",
            choices=[
                "Add commands",
                "Remove commands",
            ],
        )
        if _is_back(action) or not action:
            return
        if action == "Add commands":
            _manage_command_store_add()
            continue
        _manage_command_store_remove()

def is_port_open(host: str = "127.0.0.1", port: int = 22000) -> bool:
    """Return True if something is already listening on host:port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex((host, port)) == 0

def _read_pid_file() -> int | None:
    if not os.path.exists(PID_FILE):
        return None
    try:
        with open(PID_FILE, "r") as f:
            return int(f.read().strip())
    except Exception:
        return None

def _find_listening_pids(port: int = 22000) -> list[int]:
    try:
        result = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return []

    pids: list[int] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.isdigit():
            pids.append(int(line))
    return pids

def _try_kill_pid(pid: int, sig: int = signal.SIGTERM) -> bool:
    try:
        os.kill(pid, sig)
        return True
    except ProcessLookupError:
        return True
    except Exception:
        return False

def _is_pid_alive(pid: int | None) -> bool:
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False

def _wait_for_port_close(timeout_seconds: float = 10.0, interval_seconds: float = 0.2) -> bool:
    import time
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if not is_port_open():
            return True
        time.sleep(interval_seconds)
    return not is_port_open()

def _fetch_daemon_status() -> dict | None:
    try:
        response = requests.get("http://127.0.0.1:22000/status", timeout=0.8)
        if response.status_code != 200:
            return None
        return response.json()
    except Exception:
        return None

def _wait_for_bootstrap_ready(started_pid: int | None = None) -> tuple[bool, int, str]:
    import time

    last_indexed = 0

    with console.status("[yellow]Warming up command index...[/yellow]", spinner="dots") as status:
        while True:
            if started_pid is not None and not _is_pid_alive(started_pid):
                return (
                    False,
                    last_indexed,
                    "Daemon process exited before initialization completed. Check `aiterminal logs`.",
                )

            payload = _fetch_daemon_status()
            if payload and isinstance(payload.get("bootstrap"), dict):
                bootstrap = payload["bootstrap"]
                last_indexed = int(bootstrap.get("indexed_commands", 0) or 0)
                phase = str(bootstrap.get("phase") or "starting")
                error = str(bootstrap.get("error") or "").strip()
                storage_state = str(bootstrap.get("storage_state", "unknown") or "unknown")
                storage_error = str(bootstrap.get("storage_error_detail", "") or "").strip()
                if storage_state == "corrupt":
                    return (
                        False,
                        last_indexed,
                        storage_error
                        or "Detected corrupted vector storage. Run `aiterminal fix --safe`.",
                    )
                if bootstrap.get("ready"):
                    return (True, last_indexed, "")
                if phase == "error":
                    return (False, last_indexed, error or "Backend initialization failed.")

                if phase == "downloading_model":
                    status.update("[yellow]Downloading embedding model from Hugging Face...[/yellow]")
                elif phase == "syncing_history":
                    status.update("[yellow]Warming up command index...[/yellow]")
                elif phase == "loading_model_local":
                    status.update("[yellow]Loading embedding model from local cache...[/yellow]")
                elif phase == "initializing_db":
                    status.update("[yellow]Initializing vector database...[/yellow]")
                else:
                    status.update("[yellow]Starting GhostShell...[/yellow]")
            else:
                if is_port_open():
                    status.update("[yellow]Waiting for daemon status...[/yellow]")
                else:
                    status.update("[yellow]Starting GhostShell...[/yellow]")

            time.sleep(0.25)

@app.command()
def setup():
    ensure_config_dir()
    console.print(
        Panel.fit("[bold cyan]GhostShell Configuration[/bold cyan] [bold #ff8c00](Esc = back)[/bold #ff8c00]")
    )
    while True:
        existing_config = _load_config()
        action = _setup_select(
            "Choose one:",
            choices=[
                "Choose AI provider",
                "Customize LLM budget",
                "Manage GhostShell command patterns",
                "Manage command store (add/remove commands)",
            ],
        )
        if _is_back(action) or not action:
            return
        if action == "Choose AI provider":
            completed = _configure_provider(existing_config)
            if completed:
                return
            continue
        if action == "Customize LLM budget":
            _configure_llm_budget(existing_config)
            continue
        if action == "Manage GhostShell command patterns":
            _manage_pattern_controls(existing_config)
            continue
        _manage_command_store()

@app.command()
def enable_startup():
    """Create a macOS LaunchAgent to start on boot."""
    if sys.platform != "darwin":
        console.print("[red]Start on boot is currently only supported on macOS.[/red]")
        return

    python_path = sys.executable
    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.ghostshell.daemon</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python_path}</string>
        <string>{SERVER_SCRIPT}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{CONFIG_DIR}/server.log</string>
    <key>StandardErrorPath</key>
    <string>{CONFIG_DIR}/server.log</string>
</dict>
</plist>
"""
    with open(PLIST_PATH, "w") as f:
        f.write(plist_content)

    was_running = is_port_open()
    if was_running:
        console.print("[yellow]GhostShell is already running. Restarting under launchd...[/yellow]")
        stop()

    # Load the service immediately.
    subprocess.run(["launchctl", "unload", PLIST_PATH], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    load_res = subprocess.run(["launchctl", "load", PLIST_PATH], capture_output=True, text=True, check=False)
    if load_res.returncode != 0:
        err_text = (load_res.stderr or load_res.stdout or "launchctl load failed").strip()
        console.print(f"[red]✗ Failed to enable start on boot:[/red] {err_text}")
        raise typer.Exit(code=1)

    ready, indexed, error = _wait_for_bootstrap_ready()
    if ready:
        console.print("[green]✔ Command index ready[/green]")
        console.print("[bold green]✔ GhostShell started and set to start automatically![/bold green]")
        return

    console.print(f"[red]✗ Startup failed before readiness:[/red] {error}")
    raise typer.Exit(code=1)

@app.command()
def start():
    """Start the background AI daemon manually."""
    ensure_config_dir()
    if is_port_open():
        payload = _fetch_daemon_status()
        bootstrap = payload.get("bootstrap", {}) if isinstance(payload, dict) else {}
        if isinstance(bootstrap, dict) and bootstrap.get("ready"):
            indexed = int(bootstrap.get("indexed_commands", 0) or 0)
            console.print("[yellow]Daemon already running on port 22000.[/yellow]")
            console.print(f"[green]✔ Command index ready[/green]")
            return
        console.print("[yellow]Daemon already running; waiting for readiness...[/yellow]")
        ready, indexed, error = _wait_for_bootstrap_ready()
        if ready:
            console.print(f"[green]✔ Command index ready[/green]")
            return
        console.print(f"[red]✗ Startup failed before readiness:[/red] {error}")
        raise typer.Exit(code=1)
        return

    if os.path.exists(PID_FILE):
        console.print("[yellow]Already running (or stale PID). Restarting...[/yellow]")
        stop()

    console.print("[cyan]Starting GhostShell Daemon...[/cyan]")
    with open(os.path.join(CONFIG_DIR, "server.log"), "w") as out:
        process = subprocess.Popen(
            [sys.executable, SERVER_SCRIPT],
            stdout=out,
            stderr=out,
            start_new_session=True
        )
    
    with open(PID_FILE, "w") as f:
        f.write(str(process.pid))
    
    console.print(f"[green]✔ Started (PID: {process.pid})[/green]")
    console.print(f"[dim]Log file: {CONFIG_DIR}/server.log[/dim]")
    ready, indexed, error = _wait_for_bootstrap_ready(started_pid=process.pid)
    if ready:
        console.print(f"[green]✔ Command index ready[/green]")
    else:
        console.print(f"[red]✗ Startup failed before readiness:[/red] {error}")
        raise typer.Exit(code=1)

@app.command()
def stop():
    """Stop the daemon."""
    # Capture if it's running before we start killing things
    was_running = is_port_open() or os.path.exists(PID_FILE)
    graceful_stopped = False
    
    # Try graceful shutdown first
    if is_port_open():
        try:
            console.print("[cyan]Requesting graceful shutdown...[/cyan]")
            response = requests.post("http://127.0.0.1:22000/shutdown", timeout=4)
            if response.status_code == 200:
                console.print("[green]✔ Shutdown request accepted.[/green]")
                graceful_stopped = _wait_for_port_close(timeout_seconds=12.0, interval_seconds=0.2)
                if graceful_stopped:
                    console.print("[green]✔ Server exited cleanly.[/green]")
                else:
                    console.print("[yellow]Graceful shutdown timed out, applying fallback stop.[/yellow]")
        except Exception:
            pass

    # Unload launchd to prevent KeepAlive respawning.
    if os.path.exists(PLIST_PATH):
        res = os.system(f"launchctl unload {PLIST_PATH} 2>/dev/null")
        if res == 0:
            was_running = True

    stopped_any = graceful_stopped

    # Fallback hard stop only if the daemon is still present.
    if is_port_open() or _read_pid_file() is not None:
        pid = _read_pid_file()
        if pid is not None and _try_kill_pid(pid):
            stopped_any = True

        # Also stop any process currently listening on the daemon port.
        for listener_pid in _find_listening_pids():
            if _try_kill_pid(listener_pid):
                stopped_any = True

        if is_port_open():
            _wait_for_port_close(timeout_seconds=2.0, interval_seconds=0.1)

    if os.path.exists(PID_FILE):
        try:
            os.remove(PID_FILE)
        except OSError:
            # Non-fatal: stale/permission-protected pid files should not crash setup/stop.
            pass

    if stopped_any or was_running:
        console.print("[red]✓ Stopped.[/red]")
    else:
        console.print("[yellow]GhostShell was not running.[/yellow]")


@app.command()
def logs():
    """View server logs in real-time."""
    log_file = os.path.join(CONFIG_DIR, "server.log")
    if not os.path.exists(log_file):
        console.print("[yellow]No logs found. Server may not be running.[/yellow]")
        return
    
    console.print(f"[cyan]Tailing {log_file}...[/cyan]")
    console.print("[dim]Press Ctrl+C to stop[/dim]\n")
    os.system(f"tail -f {log_file}")

@app.command()
def test():
    """Test the AI connection manually."""
    console.print("[bold]Testing connection to GhostShell Daemon (Port 22000)...[/bold]")
    try:
        response = requests.post(
            "http://127.0.0.1:22000/predict",
            json={
                "command_buffer": "git comm",
                "cursor_position": 8,
                "working_directory": "/tmp",
                "shell": "zsh"
            },
            timeout=5
        )
        data = response.json()
        suggestions = data.get("suggestions", [])
        sugg = suggestions[0] if suggestions else ""
        error = data.get("error", "")
        
        if error:
            console.print(f"[red]✗ Server Error:[/red] {error}")
        
        console.print(f"[green]✓ Server Response:[/green] {suggestions}")
        if sugg == "":
            console.print("[yellow]⚠ Received empty suggestion (Model might be unsure or filtered).[/yellow]")
        else:
            console.print(f"[cyan]Visual Preview:[/cyan] git comm[grey50]{sugg}[/grey50]")

    except Exception as e:
        console.print(f"[red]✗ Connection Failed:[/red] {e}")
        console.print("\n[yellow]Troubleshooting:[/yellow]")
        console.print("1. Check if server is running: aiterminal start")
        console.print("2. View logs: aiterminal logs")

@app.command()
def doctor():
    """Run diagnostics for GhostShell suggestion pipeline."""
    console.print("[bold]Running GhostShell diagnostics...[/bold]")

    issues: list[str] = []
    warnings: list[str] = []
    suggestion_preview = ""

    payload = _fetch_daemon_status()
    if not payload or not isinstance(payload, dict):
        issues.append("daemon_unreachable")
        console.print("[red]✗ Daemon status:[/red] unreachable")
    else:
        console.print("[green]✓ Daemon status:[/green] reachable")
        bootstrap = payload.get("bootstrap", {}) if isinstance(payload.get("bootstrap"), dict) else {}
        if not bootstrap.get("ready"):
            issues.append("bootstrap_not_ready")
            phase = str(bootstrap.get("phase") or "unknown")
            console.print(f"[red]✗ Bootstrap:[/red] not ready (phase={phase})")
        else:
            indexed = int(bootstrap.get("indexed_commands", 0) or 0)
            console.print(f"[green]✓ Bootstrap:[/green] ready")

        storage_state, storage_code, storage_detail = _extract_storage_health(payload)
        sqlite_state = str(bootstrap.get("sqlite_state", "unknown") or "unknown")
        journal_state = str(bootstrap.get("journal_state", "unavailable") or "unavailable")
        snapshot_state = str(bootstrap.get("snapshot_state", "missing") or "missing")
        auto_recover = str(bootstrap.get("auto_recover_result", "skipped") or "skipped")
        if storage_state == "corrupt":
            issues.append(storage_code or "vector_db_corrupt")
            console.print("[red]✗ Storage health:[/red] corrupt")
            if storage_detail:
                console.print(f"[yellow]  detail:[/yellow] {storage_detail}")
            console.print("[yellow]  fix:[/yellow] aiterminal fix --safe")
        elif storage_state == "healthy":
            console.print("[green]✓ Storage health:[/green] healthy")
        elif storage_state == "repaired":
            console.print("[green]✓ Storage health:[/green] repaired")
        else:
            console.print("[yellow]⚠ Storage health:[/yellow] unknown")
        console.print(
            f"[dim]State backend: sqlite={sqlite_state}, journal={journal_state}, "
            f"snapshot={snapshot_state}, auto_recover={auto_recover}[/dim]"
        )

    if os.path.exists(SHELL_CLIENT_SCRIPT):
        sample_payload = {
            "command_buffer": "git st",
            "cursor_position": 6,
            "working_directory": os.path.expanduser("~"),
            "shell": "zsh",
            "allow_ai": False,
            "trigger_source": "doctor",
        }
        try:
            run = subprocess.run(
                [sys.executable, SHELL_CLIENT_SCRIPT, "--timeout", "2.5"],
                input=json.dumps(sample_payload),
                text=True,
                capture_output=True,
                check=False,
            )
            output = (run.stdout or "").strip()
            if not output:
                issues.append("predict_error")
                console.print("[red]✗ Predict probe:[/red] empty helper response")
            else:
                parsed = json.loads(output)
                if not parsed.get("ok"):
                    error_code = str(parsed.get("error_code", "predict_error") or "predict_error")
                    issues.append(error_code)
                    console.print(f"[red]✗ Predict probe:[/red] {error_code}")
                else:
                    pool = parsed.get("pool", [])
                    if not isinstance(pool, list):
                        issues.append("predict_error")
                        console.print("[red]✗ Predict probe:[/red] invalid response shape")
                    elif not pool:
                        warnings.append("empty_pool")
                        console.print("[yellow]⚠ Predict probe:[/yellow] empty_pool")
                    else:
                        suggestion_preview = str(pool[0] or "")
                        console.print("[green]✓ Predict probe:[/green] returned suggestions")
        except subprocess.TimeoutExpired:
            issues.append("predict_timeout")
            console.print("[red]✗ Predict probe:[/red] predict_timeout")
        except Exception:
            issues.append("predict_error")
            console.print("[red]✗ Predict probe:[/red] predict_error")
    else:
        issues.append("helper_missing")
        console.print(f"[red]✗ Helper:[/red] missing at {SHELL_CLIENT_SCRIPT}")

    try:
        binding = subprocess.run(
            ["zsh", "-ic", "bindkey '^@'"],
            capture_output=True,
            text=True,
            check=False,
            timeout=3,
        )
        if "_ghostshell_manual_trigger" not in (binding.stdout or ""):
            warnings.append("zsh_widget_not_bound")
            console.print("[yellow]⚠ Zsh binding:[/yellow] zsh_widget_not_bound")
        else:
            console.print("[green]✓ Zsh binding:[/green] Ctrl+Space mapped")
    except Exception:
        warnings.append("zsh_widget_not_bound")
        console.print("[yellow]⚠ Zsh binding:[/yellow] could not verify")

    plugin_log = os.path.join(CONFIG_DIR, "plugin.log")
    if os.path.exists(plugin_log):
        console.print(f"[dim]Plugin log: {plugin_log}[/dim]")

    if suggestion_preview:
        console.print(f"[dim]Suggestion preview: {suggestion_preview}[/dim]")

    unique_issues = list(dict.fromkeys(issues))
    unique_warnings = list(dict.fromkeys(warnings))

    if unique_issues:
        console.print(f"[red]Doctor result:[/red] FAILED ({', '.join(unique_issues)})")
        raise typer.Exit(code=1)

    if unique_warnings:
        console.print(f"[yellow]Doctor result:[/yellow] WARN ({', '.join(unique_warnings)})")
        return

    console.print("[green]Doctor result:[/green] OK")


def _run_fix_safe() -> int:
    started = time.time()
    _append_repair_log("fix_safe_started", {})
    stop()

    failed_paths: list[str] = []
    for path in [
        ZVEC_COMMANDS_PATH,
        ZVEC_FEEDBACK_PATH,
        LAST_INDEXED_PATH,
        f"{LAST_INDEXED_PATH}.tmp",
    ]:
        ok, err = _remove_path(path)
        if not ok:
            failed_paths.append(f"{path}: {err}")
    if failed_paths:
        for item in failed_paths:
            console.print(f"[red]Failed to remove:[/red] {item}")
        _append_repair_log("fix_safe_remove_failed", {"failed_paths": failed_paths})
        raise typer.Exit(code=1)

    try:
        start()
    except typer.Exit as exc:
        _append_repair_log("fix_safe_restart_failed", {"exit_code": int(exc.exit_code or 1)})
        raise

    elapsed = round(time.time() - started, 2)
    console.print("[green]Safe repair complete.[/green]")
    console.print(f"[dim]SQLite state preserved at: {STATE_SQLITE_PATH}[/dim]")
    console.print(f"[dim]Elapsed: {elapsed}s[/dim]")
    _append_repair_log("fix_safe_finished", {"partial": False, "elapsed_s": elapsed})
    return 0


def _run_fix_recover() -> int:
    started = time.time()
    _append_repair_log("fix_recover_started", {})
    stop()

    try:
        from ghostshell.state import EventJournal, SnapshotManager, SQLiteStateStore
    except Exception as exc:
        console.print(f"[red]Recover failed: state backend import error:[/red] {exc}")
        _append_repair_log("fix_recover_failed", {"error": str(exc)})
        return 1

    journal = EventJournal(EVENTS_DIR)
    snapshots = SnapshotManager(STATE_SQLITE_PATH, SNAPSHOTS_DIR)
    restored, row, reason = snapshots.restore_latest()
    if not restored:
        console.print(
            "[red]Recover failed:[/red] no usable snapshot found. "
            "Try [bold]aiterminal fix --safe[/bold] instead."
        )
        _append_repair_log("fix_recover_failed", {"error": reason or "no_snapshot"})
        return 1

    try:
        store = SQLiteStateStore(STATE_SQLITE_PATH, journal=journal)
        snapshot_ts = int((row or {}).get("snapshot_ts", 0) or 0)
        replay = journal.replay(
            lambda event: store.apply_event(event, append_to_journal=False),
            since_ts=max(0, snapshot_ts),
        )
    except Exception as exc:
        console.print(f"[red]Recover failed while replaying journal:[/red] {exc}")
        _append_repair_log("fix_recover_failed", {"error": str(exc)})
        return 1

    failed_paths: list[str] = []
    for path in [
        ZVEC_COMMANDS_PATH,
        ZVEC_FEEDBACK_PATH,
        LAST_INDEXED_PATH,
        f"{LAST_INDEXED_PATH}.tmp",
    ]:
        ok, err = _remove_path(path)
        if not ok:
            failed_paths.append(f"{path}: {err}")
    if failed_paths:
        for item in failed_paths:
            console.print(f"[red]Failed to remove:[/red] {item}")
        _append_repair_log("fix_recover_remove_failed", {"failed_paths": failed_paths})
        return 1

    try:
        start()
    except typer.Exit as exc:
        _append_repair_log("fix_recover_restart_failed", {"exit_code": int(exc.exit_code or 1)})
        raise

    elapsed = round(time.time() - started, 2)
    console.print("[green]Recovery complete.[/green]")
    console.print(
        f"[dim]Journal replay: total={int(replay.get('total', 0) or 0)}, "
        f"applied={int(replay.get('applied', 0) or 0)}, "
        f"skipped={int(replay.get('skipped', 0) or 0)}[/dim]"
    )
    console.print(f"[dim]Elapsed: {elapsed}s[/dim]")
    _append_repair_log(
        "fix_recover_finished",
        {
            "elapsed_s": elapsed,
            "replay_total": int(replay.get("total", 0) or 0),
            "replay_applied": int(replay.get("applied", 0) or 0),
            "replay_skipped": int(replay.get("skipped", 0) or 0),
        },
    )
    return 0


def _run_fix_factory_reset() -> int:
    confirmed = _setup_confirm(
        "Factory reset deletes GhostShell data (history index, feedback, config). Continue?",
        default=False,
    )
    if _is_back(confirmed) or not confirmed:
        console.print("[yellow]Factory reset cancelled.[/yellow]")
        return 1

    _append_repair_log("fix_factory_reset_started", {})
    stop()
    ok, err = _remove_path(CONFIG_DIR)
    if not ok:
        console.print(f"[red]Factory reset failed:[/red] {err}")
        _append_repair_log("fix_factory_reset_failed", {"error": err})
        return 1
    ensure_config_dir()
    _save_config(normalize_config_payload({}))
    start()
    console.print("[green]Factory reset complete.[/green]")
    _append_repair_log("fix_factory_reset_finished", {})
    return 0


@app.command()
def fix(
    safe: bool = typer.Option(False, "--safe", help="Rebuild vector index and preserve metadata when possible."),
    recover: bool = typer.Option(False, "--recover", help="Restore SQLite from latest snapshot, replay journal, then rebuild vector cache."),
    factory_reset: bool = typer.Option(False, "--factory-reset", help="Fully wipe GhostShell state."),
):
    """Repair GhostShell storage state."""
    selected = [flag for flag in (safe, recover, factory_reset) if flag]
    if len(selected) > 1:
        console.print("[red]Choose exactly one mode:[/red] --safe, --recover, or --factory-reset")
        raise typer.Exit(code=1)
    if not selected:
        safe = True

    lock_fd, lock_err = _acquire_fix_lock()
    if lock_fd is None:
        console.print(f"[red]Could not start repair:[/red] {lock_err}")
        raise typer.Exit(code=1)

    try:
        if recover:
            code = _run_fix_recover()
            raise typer.Exit(code=code)
        if factory_reset:
            code = _run_fix_factory_reset()
            raise typer.Exit(code=code)
        code = _run_fix_safe()
        raise typer.Exit(code=code)
    finally:
        _release_fix_lock(lock_fd)

@app.command("shortcuts")
def shortcuts_command():
    """Show keyboard shortcuts."""
    show_shortcuts()

@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        help="Show GhostShell version and exit",
        is_eager=True,
    ),
):
    """GhostShell: AI-powered terminal autocomplete."""
    if version:
        console.print(f"GhostShell {__version__}", highlight=False)
        raise typer.Exit()
    _run_storage_preflight_if_enabled(ctx.invoked_subcommand)
    if ctx.invoked_subcommand is None:
        console.print("[bold cyan]GhostShell[/bold cyan] - Use --help for commands.")

def show_shortcuts():
    """Display the shortcuts help panel."""
    rows = [
        ("Accept inline suggestion", "Tab", "Accept full suggestion (native completion in path/script contexts)"),
        ("Trigger suggestion", "Ctrl+Space", "Manual trigger"),
        ("Partial accept (word)", "Option+Right", "Accept next word"),
        ("Cycle suggestions", "Ctrl+N / Ctrl+P", "Next / previous"),
    ]
    separator = "-" * 60
    lines = []
    for action, primary, notes in rows:
        lines.append(f"[bold green]{action}[/bold green]")
        lines.append(f"Primary : {primary}")
        lines.append(f"Notes   : {notes}")
        lines.append(separator)

    shortcuts_text = "\n".join(lines[:-1])
    console.print(
        Panel(
            shortcuts_text,
            title="[bold cyan]GhostShell Shortcuts[/bold cyan]",
            subtitle="Use `aiterminal shortcuts`",
            expand=False,
        )
    )

if __name__ == "__main__":
    app()
