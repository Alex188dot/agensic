import os
os.environ["TOKENIZERS_PARALLELISM"] = "false"
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")
os.environ.setdefault(
    "PYTHONWARNINGS",
    "ignore:resource_tracker:UserWarning",
)

import logging
import json
import shlex
import warnings
import tempfile
from pathlib import Path
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI, BackgroundTasks
from pydantic import BaseModel
from engine import SuggestionEngine, RequestContext
from privacy_guard import PrivacyGuard

# ==========================================
# SETUP
# ==========================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("ghostshell")

# Python 3.12 + torch/sentence-transformers can emit this at interpreter shutdown
# even after clean app teardown. Suppress the known noisy warning line.
warnings.filterwarnings(
    "ignore",
    message=r"resource_tracker: There appear to be \d+ leaked semaphore objects to clean up at shutdown",
    category=UserWarning,
)

CONFIG_DIR = os.path.expanduser("~/.ghostshell")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

engine = SuggestionEngine()
privacy_guard = PrivacyGuard()
uvicorn_server = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting GhostShell server...")
    startup_history = get_history_file(os.environ.get("SHELL", "zsh"))
    if startup_history:
        engine.bootstrap_async(startup_history)
    yield
    # Shutdown
    logger.info("Shutting down GhostShell server gracefully...")
    engine.close()

app = FastAPI(lifespan=lifespan)

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

def load_config():
    if not os.path.exists(CONFIG_FILE):
        return {}
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)

def get_history_file(shell: str) -> str:
    home = os.path.expanduser("~")
    if "zsh" in shell: return os.path.join(home, ".zsh_history")
    elif "bash" in shell: return os.path.join(home, ".bash_history")
    return ""

def _normalize_pattern_token(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parts = shlex.split(raw, posix=True)
    except Exception:
        parts = raw.split()
    if not parts:
        return ""
    return os.path.basename(parts[0]).strip().lower()

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

def _disabled_patterns_from_config(config: dict) -> list[str]:
    values = config.get("disabled_command_patterns", [])
    if not isinstance(values, list):
        return []
    clean: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _normalize_pattern_token(str(value))
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        clean.append(normalized)
    return clean

def _command_matches_disabled_pattern(command: str, patterns: list[str]) -> bool:
    exe = _extract_executable_token(command)
    if not exe:
        return False
    for pattern in patterns:
        if exe.startswith(pattern) or pattern.startswith(exe):
            return True
    return False

def _parse_history_line(raw_line: str) -> str:
    line = (raw_line or "").strip()
    if not line:
        return ""
    if line.startswith(":"):
        parts = line.split(";", 1)
        if len(parts) == 2:
            line = parts[1].strip()
    return line

def _normalize_unique_commands(commands: list[str], vector_db) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in commands:
        normalized = vector_db.normalize_command(str(raw or ""))
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out

def _rewrite_history_without_commands(history_file: str, commands_to_remove: set[str]) -> tuple[int, str]:
    if not history_file:
        return (0, "History file could not be detected for this shell.")

    history_path = Path(history_file).expanduser()
    if not history_path.exists() or not history_path.is_file():
        return (0, f"History file not found: {history_path}")

    removed_lines = 0
    tmp_path = None
    try:
        os.makedirs(str(history_path.parent), exist_ok=True)
        source_stat = history_path.stat()

        with open(history_path, "r", encoding="utf-8", errors="ignore") as src:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=str(history_path.parent),
                prefix=f"{history_path.name}.tmp.",
                delete=False,
            ) as dst:
                tmp_path = dst.name
                for line in src:
                    normalized = _parse_history_line(line)
                    if normalized and normalized in commands_to_remove:
                        removed_lines += 1
                        continue
                    dst.write(line)

        os.chmod(tmp_path, source_stat.st_mode)
        os.replace(tmp_path, history_path)
        return (removed_lines, "")
    except Exception as exc:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        return (removed_lines, f"Failed to rewrite history: {exc}")

@app.post("/predict")
async def predict_completion(ctx: Context):
    # Quick filter: empty buffer
    if not ctx.command_buffer.strip():
        return {"suggestions": ["", "", ""], "pool": [], "pool_meta": [], "used_ai": False}

    config = load_config()
    
    req_context = RequestContext(
        history_file=get_history_file(ctx.shell),
        cwd=ctx.working_directory,
        buffer=ctx.command_buffer,
        shell=ctx.shell,
    )

    suggestions, pool, pool_meta, used_ai = await engine.get_suggestions(
        config,
        req_context,
        allow_ai=ctx.allow_ai,
    )
    bootstrap = engine.get_bootstrap_status()
    
    source = (ctx.trigger_source or "unknown").strip() or "unknown"
    seen = set()
    display_pool_count = 0
    for item in pool:
        if not item or item in seen:
            continue
        seen.add(item)
        display_pool_count += 1
        if display_pool_count >= 20:
            break
    sanitized_buffer = privacy_guard.sanitize_text(ctx.command_buffer, context="server_predict")
    logger.info(
        "Req[%s] allow_ai=%s used_ai=%s suggestions=%s buffer='%s' redactions=%d",
        source,
        ctx.allow_ai,
        used_ai,
        display_pool_count,
        privacy_guard.sanitize_for_log(sanitized_buffer.text),
        sanitized_buffer.redaction_count,
    )
    return {
        "suggestions": suggestions,
        "pool": pool,
        "pool_meta": pool_meta,
        "bootstrap": bootstrap,
        "used_ai": used_ai,
    }

@app.post("/intent")
async def resolve_intent(ctx: IntentContext):
    config = load_config()
    req_context = RequestContext(
        history_file=get_history_file(ctx.shell),
        cwd=ctx.working_directory,
        buffer="",
        shell=ctx.shell,
        terminal=ctx.terminal,
        platform_name=ctx.platform,
    )
    result = await engine.get_intent_command(config, req_context, ctx.intent_text)
    return result

@app.post("/assist")
async def resolve_assist(ctx: AssistContext):
    config = load_config()
    req_context = RequestContext(
        history_file=get_history_file(ctx.shell),
        cwd=ctx.working_directory,
        buffer="",
        shell=ctx.shell,
        terminal=ctx.terminal,
        platform_name=ctx.platform,
    )
    answer = await engine.get_general_assistant_reply(config, req_context, ctx.prompt_text)
    return {"answer": answer}

@app.post("/feedback")
def log_feedback(fb: Feedback, background_tasks: BackgroundTasks):
    """
    Endpoint for the shell to report accepted suggestions.
    Processed in background to avoid latency.
    """
    background_tasks.add_task(
        engine.log_feedback,
        fb.command_buffer,
        fb.accepted_suggestion,
        fb.accept_mode,
    )
    return {"status": "ok"}

@app.post("/log_command")
def log_command(data: dict, background_tasks: BackgroundTasks):
    """
    Endpoint for logging executed commands to the vector database.
    """
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
    config = load_config()
    patterns = _disabled_patterns_from_config(config)
    if _command_matches_disabled_pattern(command, patterns):
        return {"status": "ignored", "reason": "disabled_pattern"}

    background_tasks.add_task(engine.log_executed_command, command, exit_code, source)
    return {"status": "ok"}

@app.get("/command_store/list")
def command_store_list(shell: str = ""):
    target_shell = (shell or os.environ.get("SHELL", "zsh")).strip()
    history_file = get_history_file(target_shell)
    vector_db = engine._ensure_vector_db()
    payload = vector_db.list_command_store(history_file=history_file)
    return {
        "status": "ok",
        "history_file": history_file,
        **payload,
    }

@app.post("/command_store/add")
def command_store_add(data: CommandStorePayload):
    vector_db = engine._ensure_vector_db()
    result = vector_db.add_manual_commands(data.commands or [])
    return {
        "status": "ok",
        **result,
    }

@app.post("/command_store/remove")
def command_store_remove(data: CommandStoreRemovePayload):
    target_shell = (data.shell or os.environ.get("SHELL", "zsh")).strip()
    history_file = get_history_file(target_shell)
    vector_db = engine._ensure_vector_db()

    normalized_targets = _normalize_unique_commands(data.commands or [], vector_db)
    result = vector_db.remove_commands_exact(normalized_targets)

    history_removed_lines = 0
    warnings_list: list[str] = []
    if normalized_targets:
        history_removed_lines, history_warning = _rewrite_history_without_commands(
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

@app.get("/status")
def daemon_status():
    bootstrap = engine.get_bootstrap_status()
    return {
        "status": "ok",
        "bootstrap": bootstrap,
    }

@app.post("/shutdown")
async def shutdown():
    """
    Trigger a graceful shutdown of the server.
    """
    global uvicorn_server
    logger.info("Shutdown request received.")
    if uvicorn_server is not None:
        uvicorn_server.should_exit = True
    else:
        logger.warning("Uvicorn server handle not available; shutdown deferred")
    return {"status": "shutting down"}

if __name__ == "__main__":
    config = uvicorn.Config(app, host="127.0.0.1", port=22000, log_level="warning")
    uvicorn_server = uvicorn.Server(config)
    uvicorn_server.run()
