import typer
import click
import csv
import json
import os
from contextlib import contextmanager
import hashlib
import shutil
import subprocess
import sys
import time
import tarfile
import tempfile
import requests
import questionary
import socket
import signal
import shlex
import uuid
import re
import select
from typing import Any
from pathlib import Path
try:
    import termios  # type: ignore
except Exception:
    termios = None
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from prompt_toolkit.application import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.key_bindings import merge_key_bindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth
from questionary import Separator, Question
from questionary.constants import DEFAULT_QUESTION_PREFIX
from questionary.prompts import common
from questionary.prompts.common import InquirerControl
from questionary.styles import merge_styles_default
from questionary import utils
from typer.core import TyperGroup
from agensic.version import __version__
from agensic.config.loader import (
    MAX_LLM_CALLS_PER_LINE,
    load_config_file,
    normalize_config_payload,
    save_config_file,
)
from agensic.config.auth import (
    AuthTokenCache,
    AUTH_FILE,
    build_auth_headers,
    load_auth_payload,
    rotate_auth_token,
)
from agensic.engine.provenance import build_local_proof_metadata, sign_proof_payload
from agensic.paths import APP_PATHS, LEGACY_ROOT_DIR, ensure_app_layout, migrate_legacy_layout
from agensic.utils import (
    atomic_write_json_private,
    atomic_write_text_private,
    enforce_private_file,
    ensure_private_dir,
    harden_private_tree,
)

class AgensicRootGroup(TyperGroup):
    _AUTH_HELP_ALIASES = ("auth rotate", "auth status")

    def list_commands(self, ctx):
        names = list(super().list_commands(ctx))
        out: list[str] = []
        inserted_auth = False
        for name in names:
            if name == "auth":
                continue
            out.append(name)
            if name == "doctor":
                out.extend(self._AUTH_HELP_ALIASES)
                inserted_auth = True
        if not inserted_auth and "auth" in names:
            out.extend(self._AUTH_HELP_ALIASES)
        return out

    def get_command(self, ctx, cmd_name):
        if cmd_name in self._AUTH_HELP_ALIASES:
            auth_group = super().get_command(ctx, "auth")
            if isinstance(auth_group, click.Group):
                sub_name = cmd_name.split(" ", 1)[1]
                sub_cmd = auth_group.get_command(ctx, sub_name)
                if sub_cmd is None:
                    return None
                return click.Command(
                    name=cmd_name,
                    callback=getattr(sub_cmd, "callback", None),
                    params=list(getattr(sub_cmd, "params", [])),
                    help=getattr(sub_cmd, "help", None),
                    short_help=getattr(sub_cmd, "short_help", None),
                    hidden=getattr(sub_cmd, "hidden", False),
                    deprecated=getattr(sub_cmd, "deprecated", False),
                )
            return None
        return super().get_command(ctx, cmd_name)


app = typer.Typer(add_completion=False, cls=AgensicRootGroup)
provenance_registry_app = typer.Typer(add_completion=False, help="Manage provenance agent registry")
ai_session_app = typer.Typer(add_completion=False, help="Manage AI session signing context")
auth_app = typer.Typer(add_completion=False, help="Manage local API auth token")
app.add_typer(provenance_registry_app, name="provenance-registry", hidden=True)
app.add_typer(ai_session_app, name="ai-session", hidden=True)
app.add_typer(ai_session_app, name="session", hidden=True)
app.add_typer(auth_app, name="auth", hidden=True)
console = Console()

CONFIG_DIR = APP_PATHS.config_dir
CONFIG_FILE = APP_PATHS.config_file
STATE_DIR = APP_PATHS.state_dir
CACHE_DIR = APP_PATHS.cache_dir
INSTALL_DIR = APP_PATHS.install_dir
USER_BIN_DIR = APP_PATHS.user_bin_dir
PID_FILE = APP_PATHS.pid_file
LEGACY_BRAND = "".join(("ghost", "shell"))
LEGACY_CLI_NAME = "".join(("ai", "terminal"))
LEGACY_CONFIG_DIR = LEGACY_ROOT_DIR
LEGACY_PID_FILE = os.path.join(LEGACY_CONFIG_DIR, "daemon.pid")
UNINSTALL_SENTINEL = os.path.join(tempfile.gettempdir(), f"agensic-shell-uninstalled-{os.getuid()}")
PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
SERVER_SCRIPT = os.path.join(PROJECT_ROOT, "server.py")
SHELL_CLIENT_SCRIPT = (
    APP_PATHS.shell_client_path
    if os.path.exists(APP_PATHS.shell_client_path)
    else os.path.join(PROJECT_ROOT, "shell_client.py")
)
PLIST_PATH = os.path.expanduser("~/Library/LaunchAgents/com.agensic.daemon.plist")
LEGACY_PLIST_PATH = os.path.expanduser(f"~/Library/LaunchAgents/com.{LEGACY_BRAND}.daemon.plist")
LOCKS_DIR = APP_PATHS.locks_dir
FIX_LOCK_FILE = os.path.join(LOCKS_DIR, "fix.lock")
REPAIR_DIR = APP_PATHS.repair_dir
REPAIR_LOG_FILE = APP_PATHS.repair_log_file
ZVEC_COMMANDS_PATH = APP_PATHS.zvec_commands_path
ZVEC_FEEDBACK_PATH = APP_PATHS.zvec_feedback_path
LAST_INDEXED_PATH = APP_PATHS.last_indexed_path
STATE_SQLITE_PATH = APP_PATHS.state_sqlite_path
EVENTS_DIR = APP_PATHS.events_dir
SNAPSHOTS_DIR = APP_PATHS.snapshots_dir
BIN_DIR = APP_PATHS.install_bin_dir
PROVENANCE_TUI_BIN = APP_PATHS.provenance_tui_bin
SERVER_LOG_FILE = APP_PATHS.server_log_file
PLUGIN_LOG_FILE = APP_PATHS.plugin_log_file
MOUSE_REPORTING_RESET_SEQ = "\x1b[?1000l\x1b[?1002l\x1b[?1003l\x1b[?1006l\x1b[?1015l"
CURSOR_SAVE_SEQ = "\x1b7"
CURSOR_RESTORE_SEQ = "\x1b8"
DEFAULT_TUI_MANIFEST_URL = (
    "https://github.com/Alex188dot/agensic/releases/latest/download/provenance_tui_manifest.json"
)
DEFAULT_SIGNING_AGENT = "unknown"
DEFAULT_SIGNING_MODEL = "unknown-model"
MAX_COMMAND_DURATION_MS = 86_400_000
DAEMON_BASE_URL = "http://127.0.0.1:22000"
_DAEMON_AUTH_CACHE = AuthTokenCache()
SHELL_RC_BLOCK_START = "# >>> agensic >>>"
SHELL_RC_BLOCK_END = "# <<< agensic <<<"
LEGACY_SHELL_RC_BLOCK_START = f"# >>> {LEGACY_BRAND} >>>"
LEGACY_SHELL_RC_BLOCK_END = f"# <<< {LEGACY_BRAND} <<<"


def _run_command_passthrough(args: list[str]) -> int:
    process = subprocess.Popen(args)
    try:
        return int(process.wait() or 0)
    except KeyboardInterrupt:
        try:
            process.wait(timeout=0.2)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        return int(process.returncode or 130)

class _BackSignal:
    pass


BACK_SIGNAL = _BackSignal()


def _setup_style() -> Style:
    return merge_styles_default(
        [
            Style([("instruction", "fg:#ff8c00 bold")]),
            Style([("instruction-key", "fg:#ff8c00 bold")]),
            Style([("pointer", "fg:#ff8c00 bold")]),
            Style([("highlighted", "bold")]),
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


def _build_select_question(
    message: str,
    choices: list[str],
    pointer: str = "👉",
    instruction: str | None = " ",
) -> Question:
    return questionary.select(
        message,
        choices=choices,
        pointer=pointer,
        instruction=instruction,
        style=_setup_style(),
    )


def _parse_bool_env(name: str) -> bool | None:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return None
    normalized = str(raw_value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"", "0", "false", "no", "off"}:
        return False
    return False


def _can_use_raw_setup_select() -> bool:
    if termios is None or not sys.stdin.isatty() or not sys.stdout.isatty():
        return False

    if _parse_bool_env("AGENSIC_DISABLE_RAW_SETUP_SELECT") is True:
        return False

    enabled_override = _parse_bool_env("AGENSIC_ENABLE_RAW_SETUP_SELECT")
    if enabled_override is not None:
        return enabled_override

    return True


@contextmanager
def _setup_select_terminal_mode(fd: int):
    if termios is None:
        raise RuntimeError("termios_unavailable")

    previous_attrs = termios.tcgetattr(fd)
    updated_attrs = termios.tcgetattr(fd)
    updated_attrs[3] &= ~(termios.ICANON | termios.ECHO)
    updated_attrs[6][termios.VMIN] = 1
    updated_attrs[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSAFLUSH, updated_attrs)
    try:
        yield previous_attrs
    finally:
        termios.tcsetattr(fd, termios.TCSAFLUSH, previous_attrs)


def _render_raw_setup_select(
    message: str,
    choices: list[str],
    selected_index: int,
    pointer: str,
    instruction: str | None,
    previous_line_count: int,
) -> int:
    def _terminal_columns() -> int:
        return max(1, shutil.get_terminal_size(fallback=(80, 24)).columns)

    def _truncate_line(line: str, columns: int) -> str:
        if columns <= 0:
            return ""
        if get_cwidth(line) <= columns:
            return line
        if columns <= 3:
            return "." * columns

        visible: list[str] = []
        width = 0
        for char in line:
            char_width = max(0, get_cwidth(char))
            if width + char_width > columns - 3:
                break
            visible.append(char)
            width += char_width
        return "".join(visible).rstrip() + "..."

    prompt_suffix = f" {instruction}" if instruction and instruction.strip() else ""
    terminal_columns = _terminal_columns()
    lines = [_truncate_line(f"? {message}{prompt_suffix}", terminal_columns)]
    for idx, choice in enumerate(choices):
        prefix = f"{pointer} " if idx == selected_index else "  "
        lines.append(_truncate_line(f"{prefix}{choice}", terminal_columns))
    visual_line_count = len(lines)
    block = "\n".join(lines)
    if previous_line_count > 0:
        sys.stdout.write(f"\r\033[{previous_line_count}A\033[J")
    sys.stdout.write(block)
    sys.stdout.write("\n")
    sys.stdout.flush()
    return visual_line_count


def _read_raw_setup_key(fd: int) -> str | None:
    chunk = os.read(fd, 1)
    if not chunk:
        return None
    if chunk in {b"\r", b"\n"}:
        return "enter"
    if chunk == b"\x03":
        raise KeyboardInterrupt
    if chunk == b"\x10":
        return "up"
    if chunk == b"\x0e":
        return "down"
    if chunk == b"\x1b":
        if select.select([fd], [], [], 0.05)[0]:
            second = os.read(fd, 1)
            if second == b"[" and select.select([fd], [], [], 0.05)[0]:
                third = os.read(fd, 1)
                if third == b"A":
                    return "up"
                if third == b"B":
                    return "down"
            return "escape"
        return "escape"
    try:
        value = chunk.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if len(value) == 1 and value.isalpha():
        return value.lower()
    return None


def _setup_select_raw(message: str, choices: list[str], pointer: str, instruction: str | None) -> Any:
    if not choices:
        return None

    fd = sys.stdin.fileno()
    selected_index = 0
    rendered_line_count = 0
    with _setup_select_terminal_mode(fd):
        sys.stdout.write("\033[?25l")
        sys.stdout.flush()
        try:
            while True:
                rendered_line_count = _render_raw_setup_select(
                    message=message,
                    choices=choices,
                    selected_index=selected_index,
                    pointer=pointer,
                    instruction=instruction,
                    previous_line_count=rendered_line_count,
                )
                key = _read_raw_setup_key(fd)
                if key == "down":
                    selected_index = (selected_index + 1) % len(choices)
                    continue
                if key == "up":
                    selected_index = (selected_index - 1) % len(choices)
                    continue
                if key == "enter":
                    return choices[selected_index]
                if key == "escape":
                    return BACK_SIGNAL
                if key and len(key) == 1 and key.isalpha():
                    for idx, choice in enumerate(choices):
                        if str(choice).strip().lower().startswith(key):
                            selected_index = idx
                            break
        finally:
            if rendered_line_count > 0:
                sys.stdout.write(f"\r\033[{rendered_line_count}A\033[J")
            sys.stdout.write("\033[?25h")
            sys.stdout.flush()


def _setup_select(message: str, choices: list[str], **kwargs) -> Any:
    pointer = str(kwargs.pop("pointer", "👉") or "👉")
    instruction = kwargs.pop("instruction", " ")
    if _can_use_raw_setup_select():
        try:
            return _setup_select_raw(
                message,
                choices,
                pointer=pointer,
                instruction=instruction,
            )
        except Exception:
            pass
    question = _build_select_question(
        message,
        choices,
        pointer=pointer,
        instruction=instruction,
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
    migrate_legacy_layout()
    ensure_app_layout()
    for path in (CONFIG_DIR, STATE_DIR, CACHE_DIR, INSTALL_DIR, BIN_DIR):
        ensure_private_dir(path)
        harden_private_tree(path)

def _load_config() -> dict:
    return load_config_file(CONFIG_FILE)

def _save_config(config: dict):
    save_config_file(config, CONFIG_FILE)


def _daemon_auth_headers() -> dict[str, str]:
    try:
        token = _DAEMON_AUTH_CACHE.get_token()
    except Exception:
        return {}
    return build_auth_headers(token)


def _daemon_request(method: str, path: str, timeout: float, **kwargs):
    if path.startswith("http://") or path.startswith("https://"):
        url = path
    else:
        url = f"{DAEMON_BASE_URL}{path}"
    supplied_headers = kwargs.pop("headers", None)
    merged_headers = _daemon_auth_headers()
    if isinstance(supplied_headers, dict):
        merged_headers.update({str(k): str(v) for k, v in supplied_headers.items()})
    return requests.request(method.upper(), url, headers=merged_headers, timeout=timeout, **kwargs)


def _print_daemon_auth_hint() -> None:
    console.print("[yellow]Daemon auth failed.[/yellow] Run `agensic setup`, reload your shell, and retry.")


def _default_shell_name() -> str:
    shell_path = str(os.environ.get("SHELL", "") or "").strip()
    shell_name = os.path.basename(shell_path).strip()
    return shell_name or "zsh"


def _decode_common_escapes(text: str) -> str:
    decoded = str(text or "")
    previous = decoded
    for _ in range(2):
        if "\\n" not in decoded and "\\r" not in decoded and "\\t" not in decoded:
            break
        decoded = decoded.replace("\\r\\n", "\n")
        decoded = decoded.replace("\\n", "\n")
        decoded = decoded.replace("\\r", "\n")
        decoded = decoded.replace("\\t", "\t")
        if decoded == previous:
            break
        previous = decoded
    return decoded


def _render_markdown_or_plain(text: str) -> None:
    rendered = _decode_common_escapes(text)
    try:
        console.print(Markdown(rendered))
    except Exception:
        console.print(rendered, highlight=False)


def _explain_command_or_exit(command_text: str) -> None:
    command = str(command_text or "").strip()
    if not command:
        console.print("[red]Missing command to explain.[/red]")
        raise typer.Exit(code=2)

    prompt = (
        "Explain this shell command for a developer. "
        "Describe what it does, what the main flags or segments mean, and any meaningful risks or side effects. "
        "Keep the answer concise and practical.\n\n"
        f"Command:\n{command}"
    )
    payload = {
        "prompt_text": prompt,
        "working_directory": os.getcwd(),
        "shell": _default_shell_name(),
        "terminal": str(os.environ.get("TERM", "") or "").strip() or None,
        "platform": sys.platform,
    }
    try:
        response = _daemon_request("POST", "/assist", timeout=15, json=payload)
    except Exception as exc:
        console.print(f"[red]Failed to reach daemon:[/red] {exc}")
        raise typer.Exit(code=1)

    if response.status_code != 200:
        if response.status_code == 401:
            _print_daemon_auth_hint()
        body = response.text.strip()
        if body:
            console.print(f"[red]Explain request failed ({response.status_code}):[/red] {body}")
        else:
            console.print(f"[red]Explain request failed ({response.status_code}).[/red]")
        raise typer.Exit(code=1)

    try:
        response_payload = response.json()
    except ValueError:
        console.print("[red]Invalid JSON response from explain endpoint.[/red]")
        raise typer.Exit(code=1)

    answer = str(response_payload.get("answer", "") or "").strip() if isinstance(response_payload, dict) else ""
    if not answer:
        console.print("[red]No explanation returned.[/red]")
        raise typer.Exit(code=1)
    _render_markdown_or_plain(answer)


def _file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _parse_semver_tuple(value: str) -> tuple[int, ...]:
    clean = str(value or "").strip()
    if not clean:
        return ()
    parts = clean.split(".")
    out: list[int] = []
    for part in parts:
        token = "".join(ch for ch in part if ch.isdigit())
        if token == "":
            out.append(0)
        else:
            out.append(int(token))
    return tuple(out)


def _version_lt(left: str, right: str) -> bool:
    a = list(_parse_semver_tuple(left))
    b = list(_parse_semver_tuple(right))
    max_len = max(len(a), len(b))
    while len(a) < max_len:
        a.append(0)
    while len(b) < max_len:
        b.append(0)
    return tuple(a) < tuple(b)


def _platform_tag() -> str:
    machine = (os.uname().machine if hasattr(os, "uname") else "").strip().lower()
    if sys.platform == "darwin" and machine in {"arm64", "aarch64"}:
        return "darwin-arm64"
    if sys.platform == "darwin" and machine in {"x86_64", "amd64"}:
        return "darwin-x64"
    if sys.platform.startswith("linux") and machine in {"x86_64", "amd64"}:
        return "linux-x64"
    if sys.platform.startswith("linux") and machine in {"arm64", "aarch64"}:
        return "linux-arm64"
    return f"{sys.platform}-{machine or 'unknown'}"


def _platform_rust_target() -> str:
    machine = (os.uname().machine if hasattr(os, "uname") else "").strip().lower()
    if sys.platform == "darwin" and machine in {"arm64", "aarch64"}:
        return "aarch64-apple-darwin"
    if sys.platform == "darwin" and machine in {"x86_64", "amd64"}:
        return "x86_64-apple-darwin"
    if sys.platform.startswith("linux") and machine in {"x86_64", "amd64"}:
        return "x86_64-unknown-linux-gnu"
    if sys.platform.startswith("linux") and machine in {"arm64", "aarch64"}:
        return "aarch64-unknown-linux-gnu"
    return ""


def _local_provenance_tui_candidates() -> list[str]:
    explicit = str(os.environ.get("AGENSIC_PROVENANCE_TUI_LOCAL_BIN", "") or "").strip()
    cwd = os.getcwd()
    target = _platform_rust_target()
    candidates = [
        explicit,
        PROVENANCE_TUI_BIN,
        os.path.join(cwd, "rust", "provenance_tui", "target", "release", "agensic-provenance-tui"),
        (
            os.path.join(
                cwd,
                "rust",
                "provenance_tui",
                "target",
                target,
                "release",
                "agensic-provenance-tui",
            )
            if target
            else ""
        ),
        os.path.join(PROJECT_ROOT, "rust", "provenance_tui", "target", "release", "agensic-provenance-tui"),
        (
            os.path.join(
                PROJECT_ROOT,
                "rust",
                "provenance_tui",
                "target",
                target,
                "release",
                "agensic-provenance-tui",
            )
            if target
            else ""
        ),
    ]
    out: list[str] = []
    seen: set[str] = set()
    for path in candidates:
        normalized = str(path or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out


def _resolve_local_provenance_tui_binary() -> str:
    for candidate in _local_provenance_tui_candidates():
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return ""


def _fetch_provenance_tui_manifest() -> dict:
    manifest_url = str(
        os.environ.get("AGENSIC_PROVENANCE_TUI_MANIFEST_URL", DEFAULT_TUI_MANIFEST_URL) or ""
    ).strip()
    if not manifest_url:
        raise RuntimeError("missing_manifest_url")
    response = requests.get(manifest_url, timeout=12)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("invalid_manifest_payload")
    min_cli_version = str(payload.get("min_cli_version", "") or "").strip()
    if min_cli_version and _version_lt(__version__, min_cli_version):
        raise RuntimeError(
            f"cli_too_old: current={__version__} required={min_cli_version}; "
            "please update/reinstall agensic"
        )
    return payload


def _default_export_dir() -> str:
    home = os.path.expanduser("~")
    downloads = os.path.join(home, "Downloads")
    if os.path.isdir(downloads):
        return downloads
    return home


def _default_export_path(export_format: str) -> str:
    clean = str(export_format or "").strip().lower() or "json"
    if clean not in {"json", "csv"}:
        clean = "json"
    filename = f"provenance_export_{int(time.time())}.{clean}"
    return os.path.join(_default_export_dir(), filename)


def _resolve_provenance_tui_platform_entry(manifest: dict) -> dict:
    platforms = manifest.get("platforms", {})
    if not isinstance(platforms, dict):
        raise RuntimeError("manifest_missing_platforms")
    tag = _platform_tag()
    entry = platforms.get(tag)
    if not isinstance(entry, dict):
        raise RuntimeError(f"platform_not_supported:{tag}")
    return entry


def _install_provenance_tui_binary(entry: dict) -> str:
    artifact_url = str(entry.get("url", "") or "").strip()
    artifact_sha = str(entry.get("artifact_sha256", "") or "").strip().lower()
    binary_sha = str(entry.get("binary_sha256", "") or "").strip().lower()
    binary_name = str(entry.get("binary", "agensic-provenance-tui") or "agensic-provenance-tui").strip()
    if not artifact_url:
        raise RuntimeError("manifest_missing_artifact_url")

    ensure_private_dir(BIN_DIR)
    if os.path.exists(PROVENANCE_TUI_BIN) and binary_sha:
        if _file_sha256(PROVENANCE_TUI_BIN).lower() == binary_sha:
            return PROVENANCE_TUI_BIN

    with tempfile.TemporaryDirectory(prefix="agensic-tui-") as tmp:
        artifact_path = os.path.join(tmp, "provenance_tui.tar.gz")
        with requests.get(artifact_url, timeout=30, stream=True) as response:
            response.raise_for_status()
            with open(artifact_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    f.write(chunk)

        if artifact_sha:
            got = _file_sha256(artifact_path).lower()
            if got != artifact_sha:
                raise RuntimeError("artifact_checksum_mismatch")

        with tarfile.open(artifact_path, mode="r:gz") as tar:
            members = [m for m in tar.getmembers() if m.isfile()]
            target = None
            for member in members:
                if os.path.basename(member.name) == binary_name:
                    target = member
                    break
            if target is None and members:
                target = members[0]
            if target is None:
                raise RuntimeError("artifact_missing_binary")
            source = tar.extractfile(target)
            if source is None:
                raise RuntimeError("artifact_extract_failed")
            with source as src, open(PROVENANCE_TUI_BIN, "wb") as out:
                shutil.copyfileobj(src, out)

    enforce_private_file(PROVENANCE_TUI_BIN, executable=True)
    if binary_sha:
        got_bin = _file_sha256(PROVENANCE_TUI_BIN).lower()
        if got_bin != binary_sha:
            raise RuntimeError("binary_checksum_mismatch")
    return PROVENANCE_TUI_BIN


def _ensure_provenance_tui_binary() -> str:
    local_bin = _resolve_local_provenance_tui_binary()
    if local_bin:
        return local_bin
    try:
        manifest = _fetch_provenance_tui_manifest()
        entry = _resolve_provenance_tui_platform_entry(manifest)
        return _install_provenance_tui_binary(entry)
    except Exception as manifest_exc:
        local_bin = _resolve_local_provenance_tui_binary()
        if local_bin:
            return local_bin
        raise RuntimeError(
            "provenance_tui_unavailable:"
            f"{manifest_exc}; "
            "for local dev build run: cargo build --manifest-path rust/provenance_tui/Cargo.toml --release"
        ) from manifest_exc


def _reset_terminal_mouse_reporting() -> None:
    """Best-effort guard to avoid leaked mouse escape reporting in parent shell."""
    try:
        if not sys.stdout.isatty():
            return
        sys.stdout.write(MOUSE_REPORTING_RESET_SEQ)
        sys.stdout.flush()
        if termios is not None and sys.stdin.isatty():
            termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
    except Exception:
        pass


def _export_provenance_rows_to_file(payload: dict, export_format: str, out_path: str) -> None:
    runs = payload.get("runs", []) if isinstance(payload, dict) else []
    rows = runs if isinstance(runs, list) else []
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    if export_format == "json":
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"runs": rows, "total": len(rows)}, f, ensure_ascii=True, indent=2)
        return
    if export_format != "csv":
        raise RuntimeError("unsupported_export_format")

    fieldnames = [
        "run_id",
        "ts",
        "command",
        "label",
        "confidence",
        "agent",
        "agent_name",
        "provider",
        "model",
        "raw_model",
        "normalized_model",
        "model_fingerprint",
        "evidence_tier",
        "agent_source",
        "registry_version",
        "registry_status",
        "source",
        "working_directory",
        "exit_code",
        "duration_ms",
        "shell_pid",
    ]
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            if not isinstance(row, dict):
                continue
            writer.writerow({name: row.get(name, "") for name in fieldnames})


def _fallback_export_provenance(
    limit: int,
    label: str,
    contains: str,
    since_ts: int,
    tier: str,
    agent: str,
    agent_name: str,
    provider: str,
    export_format: str,
    out_path: str,
) -> None:
    response = _daemon_request(
        "GET",
        "/provenance/runs",
        params={
            "limit": int(limit),
            "label": str(label or "").strip(),
            "command_contains": str(contains or "").strip(),
            "since_ts": int(since_ts or 0),
            "tier": str(tier or "").strip(),
            "agent": str(agent or "").strip(),
            "agent_name": str(agent_name or "").strip(),
            "provider": str(provider or "").strip(),
        },
        timeout=8,
    )
    if response.status_code != 200:
        body = response.text.strip()
        raise RuntimeError(f"export_request_failed:{response.status_code}:{body}")
    payload = response.json()
    _export_provenance_rows_to_file(payload, export_format=export_format, out_path=out_path)


def _run_provenance_tui(
    limit: int,
    label: str,
    contains: str,
    since_ts: int,
    tier: str,
    agent: str,
    agent_name: str,
    provider: str,
    export_format: str,
    out_path: str,
) -> bool:
    binary_path = _ensure_provenance_tui_binary()
    token = ""
    try:
        token = _DAEMON_AUTH_CACHE.get_token()
    except Exception:
        token = ""

    cmd = [
        binary_path,
        "--daemon-url",
        DAEMON_BASE_URL,
        "--limit",
        str(max(1, min(500, int(limit or 50)))),
    ]
    if token:
        # Keep auth tokens that begin with "-" from being parsed as a new flag.
        cmd.append(f"--auth-token={token}")
    if label:
        cmd.extend(["--label", label])
    if contains:
        cmd.extend(["--contains", contains])
    if since_ts > 0:
        cmd.extend(["--since-ts", str(int(since_ts))])
    if tier:
        cmd.extend(["--tier", tier])
    if agent:
        cmd.extend(["--agent", agent])
    if agent_name:
        cmd.extend(["--agent-name", agent_name])
    if provider:
        cmd.extend(["--provider", provider])
    if export_format:
        cmd.extend(["--export", export_format, "--out", out_path])

    _reset_terminal_mouse_reporting()
    try:
        result = subprocess.run(cmd, check=False)
        return int(result.returncode or 0) == 0
    finally:
        _reset_terminal_mouse_reporting()


def _run_sessions_tui(session_id: str = "", *, replay: bool = False) -> bool:
    binary_path = _ensure_provenance_tui_binary()
    token = ""
    try:
        token = _DAEMON_AUTH_CACHE.get_token()
    except Exception:
        token = ""

    cmd = [
        binary_path,
        "sessions",
        "--daemon-url",
        DAEMON_BASE_URL,
    ]
    if token:
        cmd.append(f"--auth-token={token}")
    if session_id:
        cmd.extend(["--session-id", str(session_id)])
    if replay:
        cmd.append("--replay")

    _reset_terminal_mouse_reporting()
    try:
        result = subprocess.run(cmd, check=False)
        return int(result.returncode or 0) == 0
    finally:
        _reset_terminal_mouse_reporting()


def _format_provenance_command_preview(command: object) -> str:
    text = str(command or "")
    text = text.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    return " ".join(part for part in text.split(" ") if part)


def _rotate_auth_token_or_exit(context: str) -> None:
    try:
        rotate_auth_token()
        _DAEMON_AUTH_CACHE.get_token(force_reload=True)
    except Exception as exc:
        console.print(f"[red]Failed to rotate local auth token ({context}):[/red] {exc}")
        raise typer.Exit(code=1)


def _repair_cli_enabled() -> bool:
    config = _load_config()
    return bool(config.get("repair_cli_enabled", True))


def _append_repair_log(event: str, details: dict | None = None) -> None:
    try:
        ensure_private_dir(REPAIR_DIR)
        payload = {
            "ts": int(time.time()),
            "event": str(event or "unknown"),
            "details": details or {},
        }
        with open(REPAIR_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, separators=(",", ":")) + "\n")
        enforce_private_file(REPAIR_LOG_FILE)
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
        "  agensic fix --safe    (recommended)\n"
        "  agensic fix --recover\n"
        "  agensic fix --factory-reset"
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
    ensure_private_dir(LOCKS_DIR)
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
        response = _daemon_request("POST", "/repair/export", timeout=20)
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
        response = _daemon_request(
            "POST",
            "/repair/import",
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
    ensure_private_dir(REPAIR_DIR)
    stamp = int(time.time())
    out_path = os.path.join(REPAIR_DIR, f"snapshot-{stamp}.json")
    atomic_write_json_private(out_path, snapshot, indent=2)
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
    _print_screen_heading("Manage Agensic command patterns")
    while True:
        config = _load_config()
        patterns = _get_disabled_patterns(config)
        action = _setup_select(
            "Pattern controls:",
            choices=[
                "Disable Agensic for a specific pattern",
                "Re-enable Agensic for a specific pattern",
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
                console.print(f"[green]✓ Disabled Agensic for '{normalized}'.[/green]")
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
        console.print(f"[green]✓ Re-enabled Agensic for '{selected}'.[/green]")

def _configure_provider(existing_config: dict, manage_runtime: bool = True) -> bool:
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

            _save_config(config)
            console.print("[green]✓ Configuration saved![/green]")
            console.print(f"Provider: {provider}, Model: {model}", style="dim", highlight=False)
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


def _is_startup_enabled() -> bool:
    return os.path.exists(PLIST_PATH)


def _disable_startup_impl() -> None:
    removed = False

    if os.path.exists(PLIST_PATH):
        subprocess.run(["launchctl", "unload", PLIST_PATH], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        if _remove_file_if_exists(PLIST_PATH):
            removed = True

    if os.path.exists(LEGACY_PLIST_PATH):
        subprocess.run(["launchctl", "unload", LEGACY_PLIST_PATH], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        if _remove_file_if_exists(LEGACY_PLIST_PATH):
            removed = True

    if removed:
        console.print("[green]✓ Removed daemon from startup.[/green]")
    else:
        console.print("[yellow]Daemon is not set to launch at startup.[/yellow]")


def _manage_daemon_launch() -> None:
    _print_screen_heading("Daemon launch")
    startup_enabled = _is_startup_enabled()

    if startup_enabled:
        console.print("Daemon is set to launch at startup (recommended).")
        action = _setup_select(
            "Choose one:",
            choices=[
                "keep as is (recommended)",
                "remove from startup (not recommended)",
            ],
        )
        if _is_back(action) or not action:
            return
        if action.startswith("remove from startup"):
            _disable_startup_impl()
        else:
            console.print("[green]✓ Kept daemon launch at startup.[/green]")
        return

    console.print("Daemon is not set to launch at startup.")
    action = _setup_select(
        "Choose one:",
        choices=[
            "launch at startup (recommended)",
            "keep as is (not recommended)",
        ],
    )
    if _is_back(action) or not action:
        return
    if action.startswith("launch at startup"):
        _enable_startup_impl(start_now=False)
    else:
        console.print("[yellow]Daemon startup setting unchanged.[/yellow]")


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
    try:
        response = _daemon_request(method.upper(), path, json=payload, timeout=20)
    except Exception as exc:
        console.print(f"[red]Failed to reach daemon:[/red] {exc}")
        return None

    if response.status_code != 200:
        if response.status_code == 401:
            console.print(
                "[red]Daemon authentication failed.[/red] Run `agensic setup`, reload your shell, and retry."
            )
            return None
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
            label.append(("class:potential-wrong-reason", f" ({reason})"))
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
            Style([("potential-wrong-reason", "fg:#fca5a5 bold")]),
            Style([("separator", "fg:#ff8c00 bold")]),
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
            choices.append(questionary.Separator("\n\n\nPotential wrong commands\n"))
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
            choices.append(questionary.Separator("\n\nCommands\n"))
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

def _read_pid_file(path: str = PID_FILE) -> int | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as f:
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


def _remove_file_if_exists(path: str) -> bool:
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def _remove_tree_if_exists(path: str) -> bool:
    if not os.path.exists(path):
        return False
    try:
        shutil.rmtree(path)
        return True
    except OSError:
        return False


def _clear_uninstall_sentinel() -> None:
    try:
        Path(UNINSTALL_SENTINEL).unlink(missing_ok=True)
    except OSError:
        pass


def _shell_rc_paths() -> list[Path]:
    return [Path.home() / ".zprofile", Path.home() / ".zshrc", Path.home() / ".bashrc"]


def _scrub_shell_rc_file(path: Path) -> bool:
    if not path.exists() or not path.is_file():
        return False

    try:
        original = path.read_text(encoding="utf-8")
    except OSError:
        return False

    alias_patterns = (
        re.compile(r"alias agensic='python3 .*\.agensic/cli\.py'"),
        re.compile(rf"alias {re.escape(LEGACY_CLI_NAME)}='python3 .*\.{re.escape(LEGACY_BRAND)}/cli\.py'"),
    )
    export_patterns = (
        re.compile(r'export PATH=".*\.agensic/bin:\$PATH"'),
        re.compile(rf'export PATH=".*\.{re.escape(LEGACY_BRAND)}/bin:\$PATH"'),
    )
    source_patterns = (
        re.compile(r"source .*\.agensic/agensic\.zsh"),
        re.compile(rf"source .*\.{re.escape(LEGACY_BRAND)}/{re.escape(LEGACY_BRAND)}\.zsh"),
    )
    block_starts = {SHELL_RC_BLOCK_START, LEGACY_SHELL_RC_BLOCK_START}
    block_ends = {SHELL_RC_BLOCK_END, LEGACY_SHELL_RC_BLOCK_END}

    cleaned_lines: list[str] = []
    in_block = False
    changed = False
    for line in original.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        if stripped in block_starts:
            in_block = True
            changed = True
            continue
        if in_block:
            changed = True
            if stripped in block_ends:
                in_block = False
            continue
        if any(pattern.search(stripped) for pattern in alias_patterns + export_patterns + source_patterns):
            changed = True
            continue
        cleaned_lines.append(line)

    if not changed:
        return False

    try:
        path.write_text("".join(cleaned_lines), encoding="utf-8")
    except OSError:
        return False
    return True


def _cleanup_legacy_daemon_artifacts() -> None:
    legacy_pid = _read_pid_file(LEGACY_PID_FILE)
    if os.path.exists(LEGACY_PLIST_PATH):
        subprocess.run(["launchctl", "unload", LEGACY_PLIST_PATH], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)

    if legacy_pid is not None:
        if _try_kill_pid(legacy_pid):
            _wait_for_port_close(timeout_seconds=1.5, interval_seconds=0.1)
        if _is_pid_alive(legacy_pid):
            _try_kill_pid(legacy_pid, signal.SIGKILL)
            _wait_for_port_close(timeout_seconds=1.0, interval_seconds=0.1)

    _remove_file_if_exists(LEGACY_PID_FILE)
    _remove_file_if_exists(LEGACY_PLIST_PATH)

def _fetch_daemon_status() -> dict | None:
    try:
        response = _daemon_request("GET", "/status", timeout=0.8)
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
                    "Daemon process exited before initialization completed. Check `agensic logs`.",
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
                        or "Detected corrupted vector storage. Run `agensic fix --safe`.",
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
                    status.update("[yellow]Starting Agensic...[/yellow]")
            else:
                if is_port_open():
                    status.update("[yellow]Waiting for daemon status...[/yellow]")
                else:
                    status.update("[yellow]Starting Agensic...[/yellow]")

            time.sleep(0.25)

@app.command()
def setup():
    ensure_config_dir()
    _clear_uninstall_sentinel()
    _rotate_auth_token_or_exit("setup")
    console.print(
        Panel.fit("[bold cyan]Agensic Configuration[/bold cyan] [bold #ff8c00](Esc = back)[/bold #ff8c00]")
    )
    while True:
        existing_config = _load_config()
        action = _setup_select(
            "Choose one:",
            choices=[
                "Choose AI provider",
                "Daemon launch",
                "Customize LLM budget",
                "Manage Agensic command patterns",
                "Add/Remove commands from store",
            ],
        )
        if _is_back(action) or not action:
            return
        if action == "Choose AI provider":
            completed = _configure_provider(existing_config)
            if completed:
                return
            continue
        if action == "Daemon launch":
            _manage_daemon_launch()
            continue
        if action == "Customize LLM budget":
            _configure_llm_budget(existing_config)
            continue
        if action == "Manage Agensic command patterns":
            _manage_pattern_controls(existing_config)
            continue
        _manage_command_store()

def _enable_startup_impl(start_now: bool) -> None:
    if sys.platform != "darwin":
        console.print("[red]Start on boot is currently only supported on macOS.[/red]")
        return
    _clear_uninstall_sentinel()
    _cleanup_legacy_daemon_artifacts()

    python_path = sys.executable
    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.agensic.daemon</string>
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
    <string>{SERVER_LOG_FILE}</string>
    <key>StandardErrorPath</key>
    <string>{SERVER_LOG_FILE}</string>
</dict>
</plist>
"""
    with open(PLIST_PATH, "w") as f:
        f.write(plist_content)

    if not start_now:
        console.print("[green]✔ Start on boot enabled[/green]")
        return

    was_running = is_port_open()
    if was_running:
        console.print("[yellow]Agensic is already running. Restarting under launchd...[/yellow]")
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
        console.print("[bold green]✔ Agensic started and set to start automatically! Open a new terminal to use it![/bold green]")
        return

    console.print(f"[red]✗ Startup failed before readiness:[/red] {error}")
    raise typer.Exit(code=1)


@app.command()
def enable_startup():
    """Create a macOS LaunchAgent to start on boot."""
    _enable_startup_impl(start_now=True)


def _run_first_install_onboarding() -> bool:
    ensure_config_dir()
    _clear_uninstall_sentinel()
    _rotate_auth_token_or_exit("setup")
    console.print(
        Panel.fit("[bold cyan]Agensic Setup[/bold cyan] [bold #ff8c00](Esc = back)[/bold #ff8c00]")
    )

    while True:
        existing_config = _load_config()
        completed = _configure_provider(existing_config, manage_runtime=False)
        if not completed:
            return False

        enable_boot = _setup_confirm("Enable start on boot (Recommended)?")
        if _is_back(enable_boot):
            continue

        if enable_boot:
            _enable_startup_impl(start_now=False)

        start()
        console.print("[bold green]Open a new terminal window and start using agensic[/bold green]")
        return True


@app.command("first-run")
def first_run():
    """Run the first-install onboarding flow."""
    completed = _run_first_install_onboarding()
    if not completed:
        raise typer.Exit(code=1)

@app.command()
def start():
    """Start the background AI daemon manually."""
    ensure_config_dir()
    _clear_uninstall_sentinel()
    _rotate_auth_token_or_exit("start")
    _cleanup_legacy_daemon_artifacts()
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

    console.print("[cyan]Starting Agensic Daemon...[/cyan]")
    log_path = SERVER_LOG_FILE
    with open(log_path, "w") as out:
        process = subprocess.Popen(
            [sys.executable, SERVER_SCRIPT],
            stdout=out,
            stderr=out,
            start_new_session=True
        )
    enforce_private_file(log_path)
    
    atomic_write_text_private(PID_FILE, str(process.pid))
    
    console.print(f"[green]✔ Started (PID: {process.pid})[/green]")
    console.print(f"[dim]Log file: {SERVER_LOG_FILE}[/dim]")
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
            response = _daemon_request("POST", "/shutdown", timeout=4)
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

    _cleanup_legacy_daemon_artifacts()

    if stopped_any or was_running:
        console.print("[red]✓ Stopped.[/red]")
    else:
        console.print("[yellow]Agensic was not running.[/yellow]")


@app.command()
def logs():
    """View server logs in real-time."""
    log_file = SERVER_LOG_FILE
    if not os.path.exists(log_file):
        console.print("[yellow]No logs found. Server may not be running.[/yellow]")
        return
    
    console.print(f"[cyan]Tailing {log_file}...[/cyan]")
    console.print("[dim]Press Ctrl+C to stop[/dim]\n")
    os.system(f"tail -f {log_file}")

@app.command()
def test():
    """Test the AI connection manually."""
    console.print("[bold]Testing connection to Agensic Daemon (Port 22000)...[/bold]")
    try:
        response = _daemon_request(
            "POST",
            "/predict",
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
        console.print("1. Check if server is running: agensic start")
        console.print("2. View logs: agensic logs")


@app.command()
def uninstall(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    keep_data: bool = typer.Option(False, "--keep-data", help="Keep local config/state/cache directories."),
):
    """Remove Agensic startup wiring and local install state."""
    if not yes:
        confirmed = typer.confirm(
            "Uninstall Agensic from this machine? This removes shell wiring and startup artifacts."
            if keep_data
            else "Uninstall Agensic from this machine and delete local state?"
        )
        if not confirmed:
            console.print("[yellow]Uninstall cancelled.[/yellow]")
            raise typer.Exit(code=1)

    stop()
    Path(UNINSTALL_SENTINEL).write_text("disabled\n", encoding="utf-8")

    removed: list[str] = []
    if _remove_file_if_exists(PLIST_PATH):
        removed.append(PLIST_PATH)
    if _remove_file_if_exists(LEGACY_PLIST_PATH):
        removed.append(LEGACY_PLIST_PATH)

    for rc_path in _shell_rc_paths():
        if _scrub_shell_rc_file(rc_path):
            removed.append(str(rc_path))

    for path in (INSTALL_DIR, LEGACY_CONFIG_DIR):
        if _remove_tree_if_exists(path):
            removed.append(path)
    for launcher_path in (
        APP_PATHS.launcher_path,
        APP_PATHS.session_start_launcher_path,
        APP_PATHS.session_status_launcher_path,
        APP_PATHS.session_stop_launcher_path,
    ):
        if _remove_file_if_exists(launcher_path):
            removed.append(launcher_path)

    if not keep_data:
        for path in (CONFIG_DIR, STATE_DIR, CACHE_DIR):
            if _remove_tree_if_exists(path):
                removed.append(path)

    if removed:
        console.print("[green]Removed:[/green]")
        for item in removed:
            console.print(f"  - {item}")
    else:
        console.print("[yellow]Nothing to remove.[/yellow]")
    console.print("[dim]Current shell plugin disabled. Open a new shell for a fully clean session.[/dim]")

@app.command()
def doctor():
    """Run diagnostics for Agensic suggestion pipeline."""
    console.print("[bold]Running Agensic diagnostics...[/bold]")

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
            console.print("[yellow]  fix:[/yellow] agensic fix --safe")
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
        if "_agensic_manual_trigger" not in (binding.stdout or ""):
            warnings.append("zsh_widget_not_bound")
            console.print("[yellow]⚠ Zsh binding:[/yellow] zsh_widget_not_bound")
        else:
            console.print("[green]✓ Zsh binding:[/green] Ctrl+Space mapped")
    except Exception:
        warnings.append("zsh_widget_not_bound")
        console.print("[yellow]⚠ Zsh binding:[/yellow] could not verify")

    plugin_log = PLUGIN_LOG_FILE
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


def _format_ts_display(ts_value: int) -> str:
    ts = int(ts_value or 0)
    if ts <= 0:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


@auth_app.command("rotate")
def auth_rotate():
    """Rotate local daemon auth token."""
    ensure_config_dir()
    _rotate_auth_token_or_exit("auth rotate")
    payload = load_auth_payload(AUTH_FILE) or {}
    last_rotated_at = int(payload.get("last_rotated_at", 0) or 0)
    console.print("[green]✔ Local auth token rotated.[/green]")
    console.print(f"[dim]path={AUTH_FILE}[/dim]")
    console.print(f"[dim]last_rotated_at={_format_ts_display(last_rotated_at)}[/dim]")


@auth_app.command("status")
def auth_status(
    as_json: bool = typer.Option(False, "--json", help="Print raw JSON payload"),
):
    """Show local daemon auth token metadata."""
    target = Path(AUTH_FILE).expanduser()
    exists = target.exists() and target.is_file()
    payload = load_auth_payload(AUTH_FILE) if exists else None
    created_at = int((payload or {}).get("created_at", 0) or 0)
    last_rotated_at = int((payload or {}).get("last_rotated_at", created_at) or created_at)
    try:
        mtime = int(target.stat().st_mtime) if exists else 0
    except Exception:
        mtime = 0

    status_payload = {
        "status": "ok",
        "path": str(target),
        "exists": bool(exists),
        "created_at": created_at,
        "created_at_display": _format_ts_display(created_at),
        "last_rotated_at": last_rotated_at,
        "last_rotated_at_display": _format_ts_display(last_rotated_at),
        "file_mtime": mtime,
        "file_mtime_display": _format_ts_display(mtime),
    }
    if as_json:
        console.print_json(data=status_payload)
        return

    style = "green" if exists and payload else "yellow"
    state = "present" if exists and payload else "missing_or_invalid"
    console.print(f"[{style}]auth status: {state}[/{style}]")
    console.print(f"path: {status_payload['path']}")
    console.print(f"created_at: {status_payload['created_at_display']}")
    console.print(f"last_rotated_at: {status_payload['last_rotated_at_display']}")
    console.print(f"file_mtime: {status_payload['file_mtime_display']}")


@app.command()
def provenance(
    limit: int = typer.Option(500, "--limit", min=1, max=500, help="Max rows to return"),
    label: str = typer.Option("", "--label", help="Filter by attribution label"),
    contains: str = typer.Option("", "--contains", help="Filter commands by substring"),
    since_ts: int = typer.Option(0, "--since-ts", help="Only rows with ts >= value"),
    tier: str = typer.Option("", "--tier", help="Filter by evidence tier"),
    agent: str = typer.Option("", "--agent", help="Filter by inferred agent"),
    agent_name: str = typer.Option("", "--agent-name", help="Filter by optional agent display name"),
    provider: str = typer.Option("", "--provider", help="Filter by provider"),
    tui: bool = typer.Option(False, "--tui", help="Open full-screen provenance TUI"),
    export: str = typer.Option("", "--export", help="When used with --tui, export current view to json or csv"),
    out: str = typer.Option("", "--out", help="Output file path for --export"),
    as_json: bool = typer.Option(False, "--json", help="Print raw JSON payload"),
):
    """Show command provenance attribution history."""
    export_format = str(export or "").strip().lower()
    out_path = str(out or "").strip()
    if export_format and export_format not in {"json", "csv"}:
        console.print("[red]Invalid --export value. Use json or csv.[/red]")
        raise typer.Exit(code=2)
    if export_format and not out_path:
        out_path = _default_export_path(export_format)

    if tui:
        try:
            ok = _run_provenance_tui(
                limit=limit,
                label=label,
                contains=contains,
                since_ts=since_ts,
                tier=tier,
                agent=agent,
                agent_name=agent_name,
                provider=provider,
                export_format=export_format,
                out_path=out_path,
            )
            if ok:
                if export_format:
                    console.print(f"[green]Exported provenance rows to:[/green] {out_path}")
                return
            console.print("[yellow]TUI exited with non-zero status, falling back.[/yellow]")
            if export_format:
                _fallback_export_provenance(
                    limit=limit,
                    label=label,
                    contains=contains,
                    since_ts=since_ts,
                    tier=tier,
                    agent=agent,
                    agent_name=agent_name,
                    provider=provider,
                    export_format=export_format,
                    out_path=out_path,
                )
                console.print(f"[green]Exported provenance rows to:[/green] {out_path}")
                return
        except Exception as exc:
            console.print(f"[yellow]TUI unavailable, falling back:[/yellow] {exc}")
            if export_format:
                try:
                    _fallback_export_provenance(
                        limit=limit,
                        label=label,
                        contains=contains,
                        since_ts=since_ts,
                        tier=tier,
                        agent=agent,
                        agent_name=agent_name,
                        provider=provider,
                        export_format=export_format,
                        out_path=out_path,
                    )
                    console.print(f"[green]Exported provenance rows to:[/green] {out_path}")
                    return
                except Exception as export_exc:
                    console.print(f"[red]Export failed:[/red] {export_exc}")
                    raise typer.Exit(code=1)

    params = {
        "limit": int(limit),
        "label": str(label or "").strip(),
        "command_contains": str(contains or "").strip(),
        "since_ts": int(since_ts or 0),
        "tier": str(tier or "").strip(),
        "agent": str(agent or "").strip(),
        "agent_name": str(agent_name or "").strip(),
        "provider": str(provider or "").strip(),
    }
    try:
        response = _daemon_request(
            "GET",
            "/provenance/runs",
            params=params,
            timeout=8,
        )
    except Exception as exc:
        console.print(f"[red]Failed to reach daemon:[/red] {exc}")
        raise typer.Exit(code=1)

    if response.status_code != 200:
        if response.status_code == 401:
            _print_daemon_auth_hint()
        body = response.text.strip()
        if body:
            console.print(f"[red]Provenance request failed ({response.status_code}):[/red] {body}")
        else:
            console.print(f"[red]Provenance request failed ({response.status_code}).[/red]")
        raise typer.Exit(code=1)

    try:
        payload = response.json()
    except ValueError:
        console.print("[red]Invalid JSON response from provenance endpoint.[/red]")
        raise typer.Exit(code=1)

    if as_json:
        console.print_json(data=payload)
        return

    runs = payload.get("runs", []) if isinstance(payload, dict) else []
    if not isinstance(runs, list) or not runs:
        console.print("[yellow]No provenance rows found.[/yellow]")
        return

    terminal_width = shutil.get_terminal_size(fallback=(160, 24)).columns
    compact = terminal_width < 160

    table = Table(title="Agensic Command Provenance")
    table.add_column("TS", style="dim")
    table.add_column("Label")
    if not compact:
        table.add_column("Confidence", justify="right")
        table.add_column("Tier")
    table.add_column("Agent")
    if not compact:
        table.add_column("Agent Name")
        table.add_column("Provider")
    table.add_column("Model")
    table.add_column("Command", overflow="fold")

    for row in runs:
        if not isinstance(row, dict):
            continue
        ts_value = int(row.get("ts", 0) or 0)
        ts_display = (
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts_value))
            if ts_value > 0
            else "-"
        )
        values = [
            ts_display,
            str(row.get("label", "") or ""),
        ]
        if not compact:
            values.extend(
                [
                    f"{float(row.get('confidence', 0.0) or 0.0):.2f}",
                    str(row.get("evidence_tier", "") or ""),
                ]
            )
        values.append(str(row.get("agent", "") or ""))
        if not compact:
            values.extend(
                [
                    str(row.get("agent_name", "") or ""),
                    str(row.get("provider", "") or ""),
                ]
            )
        values.extend(
            [
                str(row.get("model", "") or ""),
                _format_provenance_command_preview(row.get("command", "")),
            ]
        )
        table.add_row(*values)
    console.print(table)


@app.command()
def sessions(
    text: bool = typer.Option(False, "--text", help="Print sessions as text instead of opening the TUI"),
):
    """Browse tracked sessions."""
    from . import track as track_runtime

    if text:
        raise typer.Exit(code=track_runtime.print_sessions_text())
    try:
        ok = _run_sessions_tui()
        if ok:
            return
        console.print("[yellow]Sessions TUI exited with non-zero status, falling back.[/yellow]")
    except Exception as exc:
        console.print(f"[yellow]Sessions TUI unavailable, falling back:[/yellow] {exc}")
    raise typer.Exit(code=track_runtime.print_sessions_text())


def _shell_export_line(name: str, value: str) -> str:
    return f"export {name}={shlex.quote(str(value or ''))}"


AI_SESSION_ENV_KEYS = (
    "AGENSIC_AI_SESSION_ACTIVE",
    "AGENSIC_AI_SESSION_AGENT",
    "AGENSIC_AI_SESSION_MODEL",
    "AGENSIC_AI_SESSION_AGENT_NAME",
    "AGENSIC_AI_SESSION_ID",
    "AGENSIC_AI_SESSION_STARTED_TS",
    "AGENSIC_AI_SESSION_EXPIRES_TS",
    "AGENSIC_AI_SESSION_COUNTER",
    "AGENSIC_AI_SESSION_TIMER_PID",
    "AGENSIC_AI_SESSION_OWNER_SHELL_PID",
)


def _write_ai_session_state(values: dict[str, str]) -> None:
    ensure_app_layout()
    state_path = Path(APP_PATHS.ai_session_state_path)
    state_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lines = [f"{key}\t{str(values.get(key, '') or '')}" for key in AI_SESSION_ENV_KEYS]
    payload = "\n".join(lines) + "\n"
    tmp_path = state_path.with_suffix(f"{state_path.suffix}.tmp")
    tmp_path.write_text(payload, encoding="utf-8")
    os.chmod(tmp_path, 0o600)
    os.replace(tmp_path, state_path)


def _read_ai_session_state() -> dict[str, str]:
    state_path = Path(APP_PATHS.ai_session_state_path)
    if not state_path.exists():
        return {}
    values: dict[str, str] = {}
    try:
        for raw_line in state_path.read_text(encoding="utf-8").splitlines():
            if not raw_line or "\t" not in raw_line:
                continue
            key, value = raw_line.split("\t", 1)
            if key in AI_SESSION_ENV_KEYS:
                values[key] = value
    except Exception:
        return {}
    return values


def _clear_ai_session_state() -> None:
    try:
        Path(APP_PATHS.ai_session_state_path).unlink()
    except FileNotFoundError:
        return


def _current_ai_session_owner_shell_pid() -> str:
    raw = str(os.environ.get("AGENSIC_SHELL_PID", "") or "").strip()
    if raw.isdigit():
        return raw
    parent_pid = int(os.getppid() or 0)
    return str(parent_pid) if parent_pid > 0 else ""


def _ai_session_pid_is_alive(raw_pid: str) -> bool:
    clean = str(raw_pid or "").strip()
    if not clean.isdigit():
        return False
    try:
        os.kill(int(clean), 0)
    except Exception:
        return False
    return True


def _ai_session_owner_matches_current_shell(values: dict[str, str]) -> bool:
    owner_pid = str(values.get("AGENSIC_AI_SESSION_OWNER_SHELL_PID", "") or "").strip()
    if not owner_pid:
        return True
    return owner_pid == _current_ai_session_owner_shell_pid()


def _read_live_ai_session_state() -> dict[str, str]:
    values = _read_ai_session_state()
    owner_pid = str(values.get("AGENSIC_AI_SESSION_OWNER_SHELL_PID", "") or "").strip()
    if owner_pid and not _ai_session_pid_is_alive(owner_pid):
        _clear_ai_session_state()
        return {}
    return values


def _resolve_ai_session_values() -> dict[str, str]:
    values = {key: str(os.environ.get(key, "") or "") for key in AI_SESSION_ENV_KEYS}
    if str(values.get("AGENSIC_AI_SESSION_ACTIVE", "") or "").strip() == "1" and _ai_session_owner_matches_current_shell(values):
        return values
    persisted = _read_live_ai_session_state()
    return persisted if persisted and _ai_session_owner_matches_current_shell(persisted) else values


def _normalize_signing_identity(agent: str, model: str) -> tuple[str, str, bool]:
    clean_agent = str(agent or "").strip().lower()
    clean_model = str(model or "").strip()
    defaulted = False
    if not clean_agent:
        clean_agent = DEFAULT_SIGNING_AGENT
        defaulted = True
    if not clean_model:
        clean_model = DEFAULT_SIGNING_MODEL
        defaulted = True
    return clean_agent, clean_model, defaulted


def _warn_defaulted_identity(context: str, defaulted: bool) -> None:
    if not defaulted:
        return
    console.print(
        f"[yellow]Warning:[/yellow] {context} missing identity; "
        f"defaulting to agent={DEFAULT_SIGNING_AGENT} model={DEFAULT_SIGNING_MODEL}"
    )


@ai_session_app.command("start")
def ai_session_start(
    agent: str = typer.Option("", "--agent", help="Agent identifier (defaults to unknown)"),
    model: str = typer.Option("", "--model", help="Raw model identifier (defaults to unknown-model)"),
    agent_name: str = typer.Option("", "--agent-name", help="Optional user-facing agent name"),
    ttl_minutes: int = typer.Option(120, "--ttl-minutes", min=1, max=1440, help="Session expiration in minutes"),
):
    """Emit shell exports to start AI session signing."""
    clean_agent, clean_model, defaulted = _normalize_signing_identity(agent, model)
    _warn_defaulted_identity("ai-session start", defaulted)
    clean_agent_name = str(agent_name or "").strip()

    now_ts = int(time.time())
    expires_ts = now_ts + int(ttl_minutes) * 60
    session_id = uuid.uuid4().hex[:16]
    values = {
        "AGENSIC_AI_SESSION_ACTIVE": "1",
        "AGENSIC_AI_SESSION_AGENT": clean_agent,
        "AGENSIC_AI_SESSION_MODEL": clean_model,
        "AGENSIC_AI_SESSION_AGENT_NAME": clean_agent_name,
        "AGENSIC_AI_SESSION_ID": session_id,
        "AGENSIC_AI_SESSION_STARTED_TS": str(now_ts),
        "AGENSIC_AI_SESSION_EXPIRES_TS": str(expires_ts),
        "AGENSIC_AI_SESSION_COUNTER": "0",
        "AGENSIC_AI_SESSION_TIMER_PID": "",
        "AGENSIC_AI_SESSION_OWNER_SHELL_PID": _current_ai_session_owner_shell_pid(),
    }
    _write_ai_session_state(values)
    lines = [_shell_export_line(key, values.get(key, "")) for key in AI_SESSION_ENV_KEYS]
    console.print("\n".join(lines), highlight=False)


@ai_session_app.command("stop")
def ai_session_stop():
    """Emit shell unsets to stop AI session signing."""
    _clear_ai_session_state()
    lines = [f"unset {name}" for name in AI_SESSION_ENV_KEYS]
    console.print("\n".join(lines), highlight=False)


@ai_session_app.command("status")
def ai_session_status():
    """Show AI session signing status from current shell environment."""
    values = _resolve_ai_session_values()
    active = str(values.get("AGENSIC_AI_SESSION_ACTIVE", "") or "").strip() == "1"
    if not active:
        console.print("inactive")
        raise typer.Exit(code=0)

    now_ts = int(time.time())
    try:
        expires_ts = int(str(values.get("AGENSIC_AI_SESSION_EXPIRES_TS", "0") or "0"))
    except Exception:
        expires_ts = 0
    remaining = max(0, expires_ts - now_ts) if expires_ts > 0 else 0
    agent = str(values.get("AGENSIC_AI_SESSION_AGENT", "") or "").strip()
    model = str(values.get("AGENSIC_AI_SESSION_MODEL", "") or "").strip()
    session_id = str(values.get("AGENSIC_AI_SESSION_ID", "") or "").strip()
    name = str(values.get("AGENSIC_AI_SESSION_AGENT_NAME", "") or "").strip()
    state = "active" if remaining > 0 or expires_ts == 0 else "expired"
    if state == "expired":
        _clear_ai_session_state()
    console.print(
        f"{state} agent={agent} model={model} agent_name={name or '-'} session_id={session_id or '-'} remaining_seconds={remaining}"
    )


@app.command("ai-exec", context_settings={"allow_extra_args": True, "ignore_unknown_options": True}, hidden=True)
def ai_exec(
    ctx: typer.Context,
    agent: str = typer.Option("", "--agent", help="Agent identifier, for example codex"),
    model: str = typer.Option("", "--model", help="Raw model identifier, for example gpt-5.3"),
    agent_name: str = typer.Option("", "--agent-name", help="Optional user-facing agent name"),
    trace: str = typer.Option("", "--trace", help="Optional trace id"),
    source: str = typer.Option("unknown", "--source", help="Log source (runtime/history/unknown)"),
):
    """Run a command with deterministic AI_EXECUTED proof metadata."""
    args = list(ctx.args or [])
    if args and args[0] == "--":
        args = args[1:]
    if not args:
        console.print("[red]No command provided.[/red]")
        raise typer.Exit(code=2)

    clean_source = str(source or "unknown").strip().lower()
    if clean_source not in {"runtime", "history", "unknown"}:
        clean_source = "unknown"

    clean_agent, clean_model, defaulted = _normalize_signing_identity(agent, model)
    _warn_defaulted_identity("ai-exec", defaulted)
    trace_id = str(trace or "").strip() or uuid.uuid4().hex[:12]
    ts = int(time.time())
    signature = sign_proof_payload(
        "AI_EXECUTED",
        clean_agent,
        clean_model,
        trace_id,
        ts,
    )
    proof_metadata = build_local_proof_metadata()

    command_text = shlex.join(args)
    started = time.perf_counter()
    duration_ms = None
    try:
        exit_code = _run_command_passthrough(args)
        duration_ms = min(MAX_COMMAND_DURATION_MS, max(0, int((time.perf_counter() - started) * 1000.0)))
    except KeyboardInterrupt:
        exit_code = 130
        duration_ms = min(MAX_COMMAND_DURATION_MS, max(0, int((time.perf_counter() - started) * 1000.0)))
    except Exception as exc:
        console.print(f"[red]Command execution failed:[/red] {exc}")
        raise typer.Exit(code=1)

    payload = {
        "command": command_text,
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "source": clean_source,
        "working_directory": os.getcwd(),
        "shell_pid": os.getppid(),
        "provenance_last_action": "suggestion_accept",
        "provenance_accept_origin": "ai",
        "provenance_accept_mode": "replace_full",
        "provenance_suggestion_kind": "agent_wrapper",
        "provenance_manual_edit_after_accept": False,
        "provenance_ai_agent": clean_agent,
        "provenance_ai_provider": "",
        "provenance_ai_model": clean_model,
        "provenance_agent_name": str(agent_name or "").strip(),
        "provenance_agent_hint": clean_agent,
        "provenance_model_raw": clean_model,
        "provenance_wrapper_id": f"agensic_ai_exec:{trace_id}",
        "proof_label": "AI_EXECUTED",
        "proof_agent": clean_agent,
        "proof_model": clean_model,
        "proof_trace": trace_id,
        "proof_timestamp": ts,
        "proof_signature": signature,
        "proof_signer_scope": str(proof_metadata.get("proof_signer_scope", "") or ""),
        "proof_key_fingerprint": str(proof_metadata.get("proof_key_fingerprint", "") or ""),
        "proof_host_fingerprint": str(proof_metadata.get("proof_host_fingerprint", "") or ""),
    }
    try:
        response = _daemon_request(
            "POST",
            "/log_command",
            json=payload,
            timeout=8,
        )
        if response.status_code != 200:
            if response.status_code == 401:
                _print_daemon_auth_hint()
            body = response.text.strip()
            if body:
                console.print(f"[yellow]Warning: log_command failed ({response.status_code}):[/yellow] {body}")
            else:
                console.print(f"[yellow]Warning: log_command failed ({response.status_code}).[/yellow]")
    except Exception as exc:
        console.print(f"[yellow]Warning: could not log provenance:[/yellow] {exc}")

    raise typer.Exit(code=exit_code)


@app.command(hidden=True)
def wrap(
    agent: str = typer.Argument(..., help="Agent identifier"),
    model: str = typer.Option("gpt-5.3", "--model", help="Default model for the wrapper"),
    function_name: str = typer.Option("", "--name", help="Wrapper function name"),
):
    """Print a shell wrapper that routes executions through agensic ai-exec."""
    clean_agent = str(agent or "").strip()
    if not clean_agent:
        console.print("[red]Agent is required.[/red]")
        raise typer.Exit(code=2)
    wrapper_name = str(function_name or "").strip() or f"{clean_agent.replace('-', '_')}_run"
    snippet = (
        f"{wrapper_name}() {{\n"
        f"  agensic ai-exec --agent {shlex.quote(clean_agent)} --model {shlex.quote(str(model or '').strip())} -- \"$@\"\n"
        "}"
    )
    console.print(snippet)


@provenance_registry_app.command("list")
def provenance_registry_list(
    status: str = typer.Option("", "--status", help="Optional status filter: verified/community"),
    as_json: bool = typer.Option(False, "--json", help="Print raw JSON payload"),
):
    params = {"status": str(status or "").strip()}
    try:
        response = _daemon_request(
            "GET",
            "/provenance/registry/agents",
            params=params,
            timeout=8,
        )
    except Exception as exc:
        console.print(f"[red]Failed to reach daemon:[/red] {exc}")
        raise typer.Exit(code=1)

    if response.status_code != 200:
        if response.status_code == 401:
            _print_daemon_auth_hint()
        console.print(f"[red]Registry list failed ({response.status_code}).[/red]")
        raise typer.Exit(code=1)

    payload = response.json()
    if as_json:
        console.print_json(data=payload)
        return

    agents = payload.get("agents", []) if isinstance(payload, dict) else []
    if not isinstance(agents, list) or not agents:
        console.print("[yellow]No registry agents found.[/yellow]")
        return

    table = Table(title="Agensic Provenance Registry")
    table.add_column("Agent")
    table.add_column("Status")
    table.add_column("Executables")
    table.add_column("Aliases", overflow="fold")
    for row in agents:
        if not isinstance(row, dict):
            continue
        table.add_row(
            str(row.get("agent_id", "") or ""),
            str(row.get("status", "") or ""),
            ", ".join([str(x) for x in row.get("executables", []) if str(x)]),
            ", ".join([str(x) for x in row.get("aliases", []) if str(x)]),
        )
    console.print(table)


@provenance_registry_app.command("show-agent")
def provenance_registry_show_agent(
    agent_id: str = typer.Argument(..., help="Agent id"),
    as_json: bool = typer.Option(False, "--json", help="Print raw JSON payload"),
):
    try:
        response = _daemon_request(
            "GET",
            f"/provenance/registry/agents/{agent_id}",
            timeout=8,
        )
    except Exception as exc:
        console.print(f"[red]Failed to reach daemon:[/red] {exc}")
        raise typer.Exit(code=1)

    if response.status_code == 404:
        console.print(f"[yellow]Agent not found: {agent_id}[/yellow]")
        raise typer.Exit(code=1)
    if response.status_code != 200:
        if response.status_code == 401:
            _print_daemon_auth_hint()
        console.print(f"[red]Show-agent failed ({response.status_code}).[/red]")
        raise typer.Exit(code=1)

    payload = response.json()
    if as_json:
        console.print_json(data=payload)
        return
    summary = payload.get("summary", {}) if isinstance(payload, dict) else {}
    console.print_json(data=summary if isinstance(summary, dict) else {})


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
        from agensic.state import EventJournal, SnapshotManager, SQLiteStateStore
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
            "Try [bold]agensic fix --safe[/bold] instead."
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
        "Factory reset deletes Agensic data (history index, feedback, config). Continue?",
        default=False,
    )
    if _is_back(confirmed) or not confirmed:
        console.print("[yellow]Factory reset cancelled.[/yellow]")
        return 1

    _append_repair_log("fix_factory_reset_started", {})
    stop()
    for path in (CONFIG_DIR, STATE_DIR, CACHE_DIR, INSTALL_DIR):
        ok, err = _remove_path(path)
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
    factory_reset: bool = typer.Option(False, "--factory-reset", help="Fully wipe Agensic state."),
):
    """Repair Agensic storage state."""
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


@app.command("track", context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def track_command(
    ctx: typer.Context,
    agent: str = typer.Option("", "--agent", help="Override tracked agent identifier"),
    model: str = typer.Option("", "--model", help="Explicit tracked model identifier for any provider"),
    agent_name: str = typer.Option("", "--agent-name", help="Override tracked agent display name"),
    replay: bool = typer.Option(False, "--replay", help="Replay the decoded transcript when using 'track inspect'"),
    text: bool = typer.Option(False, "--text", help="Print text output instead of opening the session TUI for 'track inspect'"),
    tail: int = typer.Option(8, "--tail", min=1, max=100, help="Tail event count for 'track inspect'"),
):
    """Launch and supervise a tracked CLI session."""
    from . import track as track_runtime

    args = list(ctx.args or [])
    try:
        track_runtime.ensure_track_supported()
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)

    if not args:
        console.print("[red]No tracked app or command provided.[/red]")
        raise typer.Exit(code=2)

    if args[0] == "status" and len(args) == 1:
        raise typer.Exit(code=track_runtime.print_track_status())
    if args[0] == "stop":
        stop_args = args[1:]
        stop_all = False
        session_id = ""
        for raw_arg in stop_args:
            clean_arg = str(raw_arg or "").strip()
            if clean_arg == "--all":
                stop_all = True
                continue
            if clean_arg.startswith("-"):
                console.print(f"[red]Unknown track stop option:[/red] {clean_arg}")
                raise typer.Exit(code=2)
            if session_id:
                console.print("[red]Usage: agensic track stop [<session_id>] [--all][/red]")
                raise typer.Exit(code=2)
            session_id = clean_arg
        if stop_all and session_id:
            console.print("[red]Use either a session_id or --all for 'track stop', not both.[/red]")
            raise typer.Exit(code=2)
        raise typer.Exit(code=track_runtime.stop_track_sessions(session_id, stop_all=stop_all))
    if args[0] == "inspect" and len(args) <= 2:
        session_id = args[1] if len(args) == 2 else ""
        if text:
            raise typer.Exit(code=track_runtime.inspect_track_session(session_id, replay=replay, tail_events=tail))
        try:
            ok = _run_sessions_tui(session_id=session_id, replay=replay)
        except Exception as exc:
            console.print(f"[yellow]Sessions TUI unavailable, falling back:[/yellow] {exc}")
        else:
            if ok:
                raise typer.Exit(code=0)
            console.print("[yellow]Sessions TUI exited with non-zero status, falling back.[/yellow]")
        raise typer.Exit(code=track_runtime.inspect_track_session(session_id, replay=replay, tail_events=tail))

    try:
        launch = track_runtime.prepare_track_launch(
            args,
            agent_override=agent,
            model_override=model,
            agent_name_override=agent_name,
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2)

    raise typer.Exit(code=track_runtime.run_tracked_command(launch))


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        help="Show Agensic version and exit",
        is_eager=True,
    ),
    explain: str = typer.Option(
        "",
        "--explain",
        metavar="COMMAND",
        help="Explain a shell command and exit",
    ),
):
    """Agensic: AI-powered terminal autocomplete."""
    if version:
        console.print(f"Agensic {__version__}", highlight=False)
        raise typer.Exit()
    if explain:
        _explain_command_or_exit(explain)
        raise typer.Exit()
    _run_storage_preflight_if_enabled(ctx.invoked_subcommand)
    if ctx.invoked_subcommand is None:
        console.print("[bold cyan]Agensic[/bold cyan] - Use --help for commands.")

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
            title="[bold cyan]Agensic Shortcuts[/bold cyan]",
            subtitle="Use `agensic shortcuts`",
            expand=False,
        )
    )

if __name__ == "__main__":
    app()
