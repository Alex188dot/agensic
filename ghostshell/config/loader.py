import json
import os
from pathlib import Path


CONFIG_DIR = os.path.expanduser("~/.ghostshell")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")


def load_config_file(path: str | None = None) -> dict:
    target = Path(path or CONFIG_FILE).expanduser()
    if not target.exists() or not target.is_file():
        return {}
    try:
        with open(target, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if isinstance(payload, dict):
            return payload
    except Exception:
        return {}
    return {}


def save_config_file(config: dict, path: str | None = None) -> None:
    target = Path(path or CONFIG_FILE).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4)
