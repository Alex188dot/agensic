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
