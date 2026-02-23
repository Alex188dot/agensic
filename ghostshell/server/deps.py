import json
import logging
import os
import warnings

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

from ghostshell.engine import RequestContext, SuggestionEngine
from ghostshell.privacy import PrivacyGuard
from ghostshell.utils.history import rewrite_history_without_commands
from ghostshell.utils.shell import command_matches_pattern, sanitize_patterns

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("ghostshell")

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


def set_uvicorn_server(server):
    global uvicorn_server
    uvicorn_server = server


def load_config() -> dict:
    if not os.path.exists(CONFIG_FILE):
        return {}
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def get_history_file(shell: str) -> str:
    home = os.path.expanduser("~")
    if "zsh" in shell:
        return os.path.join(home, ".zsh_history")
    if "bash" in shell:
        return os.path.join(home, ".bash_history")
    return ""


def disabled_patterns_from_config(config: dict) -> list[str]:
    values = config.get("disabled_command_patterns", [])
    return sanitize_patterns(values)


def command_matches_disabled_pattern(command: str, patterns: list[str]) -> bool:
    return command_matches_pattern(command, patterns)


def normalize_unique_commands(commands: list[str], vector_db) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in commands:
        normalized = vector_db.normalize_command(str(raw or ""))
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out
