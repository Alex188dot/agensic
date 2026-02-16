import os
import logging
import json
import uvicorn
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

CONFIG_DIR = os.path.expanduser("~/.ghostshell")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

app = FastAPI()
engine = SuggestionEngine()

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
        return {"suggestions": ["", "", ""]}

    config = load_config()
    
    req_context = RequestContext(
        history_file=get_history_file(ctx.shell),
        cwd=ctx.working_directory,
        buffer=ctx.command_buffer,
        shell=ctx.shell
    )

    suggestions = await engine.get_suggestions(config, req_context)
    
    logger.info(f"Req: '{ctx.command_buffer}' -> Sug: {suggestions}")
    return {"suggestions": suggestions}

@app.post("/feedback")
def log_feedback(fb: Feedback, background_tasks: BackgroundTasks):
    """
    Endpoint for the shell to report accepted suggestions.
    Processed in background to avoid latency.
    """
    background_tasks.add_task(engine.log_feedback, fb.command_buffer, fb.accepted_suggestion)
    return {"status": "ok"}

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=22000, log_level="warning")
