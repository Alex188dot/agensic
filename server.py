import os
import logging
import json
import uvicorn
import re
import shutil
from pathlib import Path
from fastapi import FastAPI
from pydantic import BaseModel
from litellm import completion

# ==========================================
# LOGGING
# ==========================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("ghostshell")

# ==========================================
# PROMPT ENGINEERING 
# ==========================================


class Settings:
    history_lines: int = 50
    max_commands_context: int = 40
    max_packages_context: int = 40

class RequestContext:
    def __init__(self, history_file: str, cwd: str, buffer: str, shell: str):
        self.history_file = history_file
        self.cwd = cwd
        self.buffer = buffer
        self.shell = shell

class SystemInventory:
    def __init__(self):
        self.commands: list[str] = []
        self.packages: list[str] = []
        self.package_sources: list[str] = []

# --- Start of User Provided Code ---

def _safe_tail(path: str, max_lines: int) -> list[str]:
    if not path:
        return []
    candidate = Path(path).expanduser()
    if not candidate.exists() or not candidate.is_file():
        return []
    try:
        lines = candidate.read_text(errors="ignore").splitlines()
    except OSError:
        return []
    return [line.strip() for line in lines[-max_lines:] if line.strip()]


def _list_working_dir(path: str, max_items: int = 80) -> list[str]:
    items: list[str] = []
    try:
        with os.scandir(path) as it:
            for entry in it:
                suffix = "/" if entry.is_dir() else ""
                items.append(entry.name + suffix)
                if len(items) >= max_items:
                    break
    except OSError:
        return []
    return sorted(items)


def build_prompt_context(
    request: RequestContext,
    inventory: SystemInventory,
    settings: Settings,
) -> str:
    history_file = request.history_file or ""
    history = _safe_tail(history_file, settings.history_lines)
    cwd_items = _list_working_dir(request.cwd)

    commands_context = inventory.commands[: settings.max_commands_context]
    packages_context = inventory.packages[: settings.max_packages_context]

    lines: list[str] = [
        f"OS shell: {request.shell}",
        f"Current directory: {request.cwd}",
        f"Current buffer: {request.buffer}",
        "",
        "Top executable commands from PATH:",
        ", ".join(commands_context) if commands_context else "(none)",
        "",
        "Detected installed packages across package managers:",
        ", ".join(packages_context) if packages_context else "(none)",
        f"Package sources: {', '.join(inventory.package_sources) if inventory.package_sources else '(none)'}",
        "",
        "Recent command history:",
        "\n".join(history[-20:]) if history else "(none)",
        "",
        "Current directory items:",
        ", ".join(cwd_items) if cwd_items else "(none)",
    ]
    return "\n".join(lines)


# ==========================================
# SERVER LOGIC
# ==========================================

CONFIG_DIR = os.path.expanduser("~/.ghostshell")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

app = FastAPI()

class Context(BaseModel):
    command_buffer: str
    cursor_position: int
    working_directory: str
    shell: str

def load_config():
    if not os.path.exists(CONFIG_FILE):
        return {}
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)

def get_history_file(shell: str) -> str:
    """Guess history file based on shell."""
    home = os.path.expanduser("~")
    if "zsh" in shell:
        return os.path.join(home, ".zsh_history")
    elif "bash" in shell:
        return os.path.join(home, ".bash_history")
    return ""

def get_simple_inventory() -> SystemInventory:
    """Quickly gather some system context without blocking."""
    inv = SystemInventory()
    
    # 1. Get some commands from PATH
    paths = os.environ.get("PATH", "").split(os.pathsep)
    cmds = set()
    for p in paths[:3]: # check top 3 paths only
        if os.path.exists(p):
            try:
                # Add first 10 files from each path
                for f in os.listdir(p)[:10]:
                    cmds.add(f)
            except: pass
    inv.commands = list(cmds)

    # 2. Check for common package managers
    if shutil.which("pip"): inv.package_sources.append("pip")
    if shutil.which("brew"): inv.package_sources.append("homebrew")
    if shutil.which("apt"): inv.package_sources.append("apt")
    if shutil.which("npm"): inv.package_sources.append("npm")

    return inv

@app.post("/predict")
def predict_completion(ctx: Context):
    
    # Log request to terminal
    logger.info(f"Prediction requested | Buffer: '{ctx.command_buffer}' | Directory: {ctx.working_directory}")

    # Load config
    config = load_config()
    model = config.get("model", "gpt-5-mini")
    provider = config.get("provider", "openai")
    api_key = config.get("api_key", None)
    base_url = config.get("base_url", None)

    # Prepare Context for Prompt Engineering
    settings = Settings()
    inventory = get_simple_inventory()
    req_context = RequestContext(
        history_file=get_history_file(ctx.shell),
        cwd=ctx.working_directory,
        buffer=ctx.command_buffer,
        shell=ctx.shell
    )

    # Generate the complex prompt
    context_str = build_prompt_context(req_context, inventory, settings)

    system_prompt = (
        "You are an advanced CLI terminal autocomplete engine. "
        "Analyze the context (history, files, installed tools) provided below. "
        "Complete the user's current buffer by providing FULL command suggestions, not just the next word. "
        "Provide practical and likely full commands based on the user's partial input. "
        "Output EXACTLY 3 suggestions, each separated by a pipe character (|). "
        "Each suggestion must be a complete, runnable command. "
        "Examples:\n"
        "- If the user types 'pip', suggest: 'pip install pandas | pip install numpy | pip install requests'\n"
        "- If the user types 'aiterminal -', suggest: 'aiterminal --help | aiterminal --shortcuts | aiterminal setup'\n"
        "If unsure, or if there are fewer than 3 relevant suggestions, return empty strings for the remaining ones (e.g., 'git checkout | git commit | '). "
        "Do not repeat the input buffer. Do not output markdown. "
        f"--- CONTEXT ---\n{context_str}"
    )

    # Provider specific adjustments
    if provider == "groq":
        if not model.startswith("groq/") and not model.startswith("groq/openai/"):
            model = f"groq/{model}"
    elif provider == "ollama":
        if not model.startswith("ollama/"):
            model = f"ollama/{model}"
        if not base_url:
            base_url = "http://localhost:11434"
    elif provider == "anthropic":
         if not model.startswith("claude"):
            model = "claude-3-5-sonnet-20241022"
    elif provider == "gemini":
        if not model.startswith("gemini/"):
            model = f"gemini/{model}"

    try:
        kwargs = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Buffer: {ctx.command_buffer}"}
            ],
            "temperature": 0.1,
        }

        if api_key:
            if provider == "groq": os.environ["GROQ_API_KEY"] = api_key
            elif provider == "openai": os.environ["OPENAI_API_KEY"] = api_key
            elif provider == "anthropic": os.environ["ANTHROPIC_API_KEY"] = api_key
            elif provider == "gemini": os.environ["GEMINI_API_KEY"] = api_key
            else: kwargs["api_key"] = api_key
        
        if base_url:
            kwargs["api_base"] = base_url

        response = completion(**kwargs)
        raw_output = response.choices[0].message.content.strip()

        # Split by | and clean up each suggestion
        raw_suggestions = raw_output.split("|")
        clean_suggestions = []
        for raw_sugg in raw_suggestions:
            s = raw_sugg.strip()
            # Clean up Markdown or Quotes
            s = re.sub(r"```.*?```", "", s, flags=re.DOTALL)
            s = s.replace("```", "").strip()
            if s.startswith('"') and s.endswith('"'): s = s[1:-1]
            elif s.startswith("'") and s.endswith("'"): s = s[1:-1]
            
            # Remove overlap if the model repeated the input
            if s.startswith(ctx.command_buffer):
                s = s[len(ctx.command_buffer):]
            clean_suggestions.append(s)

        # Pad with empty strings if less than 3
        while len(clean_suggestions) < 3:
            clean_suggestions.append("")

        logger.info(f"AI Response: {clean_suggestions}")

        return {"suggestions": clean_suggestions[:3]}

    except Exception as e:
        logger.error(f"Prediction failed: {str(e)}", exc_info=True)
        return {"suggestions": ["", "", ""]}

if __name__ == "__main__":
    # Completely silent startup
    uvicorn.run(app, host="127.0.0.1", port=22000, log_level="info")