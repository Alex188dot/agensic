import typer
import json
import os
import subprocess
import sys
import time
import requests # Added for testing
import questionary
from rich.console import Console
from rich.panel import Panel

app = typer.Typer()
console = Console()

CONFIG_DIR = os.path.expanduser("~/.termimind")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
PID_FILE = os.path.join(CONFIG_DIR, "daemon.pid")
LOG_FILE = os.path.join(CONFIG_DIR, "server.log")
SERVER_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server.py")

def ensure_config_dir():
    if not os.path.exists(CONFIG_DIR): os.makedirs(CONFIG_DIR)

@app.command()
def setup():
    ensure_config_dir()
    console.print(Panel.fit("[bold cyan]TermiMind Configuration[/bold cyan]"))

    provider = questionary.select(
        "Select Provider:",
        choices=["openai", "anthropic", "ollama", "azure", "other"],
        pointer="👉"
    ).ask()

    default_model = "gpt-4o-mini"
    if provider == "ollama": default_model = "qwen3:8b"
    
    model = questionary.text("Enter Model Name:", default=default_model).ask()

    api_key = ""
    if provider != "ollama":
        api_key = questionary.password("Enter API Key:").ask()
    
    base_url = ""
    if provider == "ollama":
        base_url = questionary.text("Enter Ollama URL:", default="http://localhost:11434").ask()

    config = {"provider": provider, "model": model, "api_key": api_key, "base_url": base_url}
    with open(CONFIG_FILE, "w") as f: json.dump(config, f, indent=4)
    console.print("[green]Saved![/green]")
    if questionary.confirm("Start now?").ask(): start()

@app.command()
def start():
    ensure_config_dir()
    if os.path.exists(PID_FILE):
        console.print("[yellow]Already running.[/yellow]")
        return
    
    console.print("[cyan]Starting...[/cyan]")
    with open(LOG_FILE, "w") as out:
        process = subprocess.Popen([sys.executable, SERVER_SCRIPT], stdout=out, stderr=out, start_new_session=True)
    with open(PID_FILE, "w") as f: f.write(str(process.pid))
    console.print(f"[green]Started (PID: {process.pid})[/green]")

@app.command()
def stop():
    if not os.path.exists(PID_FILE): return
    with open(PID_FILE, "r") as f: pid = int(f.read().strip())
    try: os.kill(pid, 15)
    except: pass
    os.remove(PID_FILE)
    console.print("[red]Stopped.[/red]")

@app.command()
def test():
    """Test the AI connection manually."""
    console.print("[bold]Testing connection to TermiMind Daemon (Port 22000)...[/bold]")
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
        if response.status_code == 200:
            console.print(f"[green]Success! AI Suggestion:[/green] '{response.json()['suggestion']}'")
        else:
            console.print(f"[red]Server Error:[/red] {response.text}")
    except Exception as e:
        console.print(f"[red]Connection Failed:[/red] {e}")
        console.print("Is the daemon running? Try 'aiterminal start'")

@app.command()
def logs():
    """Show the last 20 lines of logs."""
    if os.path.exists(LOG_FILE):
        os.system(f"tail -n 20 {LOG_FILE}")
    else:
        console.print("[yellow]No logs found.[/yellow]")

if __name__ == "__main__":
    app()