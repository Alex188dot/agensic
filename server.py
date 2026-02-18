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
import warnings
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI, BackgroundTasks
from pydantic import BaseModel
from engine import SuggestionEngine, RequestContext

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

class Feedback(BaseModel):
    command_buffer: str
    accepted_suggestion: str

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

@app.post("/predict")
async def predict_completion(ctx: Context):
    # Quick filter: empty buffer
    if not ctx.command_buffer.strip():
        return {"suggestions": ["", "", ""], "pool": []}

    config = load_config()
    
    req_context = RequestContext(
        history_file=get_history_file(ctx.shell),
        cwd=ctx.working_directory,
        buffer=ctx.command_buffer,
        shell=ctx.shell
    )

    suggestions, pool = await engine.get_suggestions(config, req_context)
    bootstrap = engine.get_bootstrap_status()
    
    logger.info(f"Req: '{ctx.command_buffer}' -> Sug: {suggestions}")
    return {"suggestions": suggestions, "pool": pool, "bootstrap": bootstrap}

@app.post("/feedback")
def log_feedback(fb: Feedback, background_tasks: BackgroundTasks):
    """
    Endpoint for the shell to report accepted suggestions.
    Processed in background to avoid latency.
    """
    background_tasks.add_task(engine.log_feedback, fb.command_buffer, fb.accepted_suggestion)
    return {"status": "ok"}

@app.post("/log_command")
def log_command(data: dict, background_tasks: BackgroundTasks):
    """
    Endpoint for logging executed commands to the vector database.
    """
    command = data.get("command", "")
    if command:
        background_tasks.add_task(engine.log_executed_command, command)
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
