import typer
import json
import os
import subprocess
import sys
import requests
import questionary
from rich.console import Console
from rich.panel import Panel

app = typer.Typer()
console = Console()

CONFIG_DIR = os.path.expanduser("~/.termimind")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
PID_FILE = os.path.join(CONFIG_DIR, "daemon.pid")
SERVER_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server.py")
PLIST_PATH = os.path.expanduser("~/Library/LaunchAgents/com.termimind.daemon.plist")

def ensure_config_dir():
    if not os.path.exists(CONFIG_DIR): os.makedirs(CONFIG_DIR)

@app.command()
def setup():
    ensure_config_dir()
    console.print(Panel.fit("[bold cyan]TermiMind Configuration[/bold cyan]"))

    provider = questionary.select(
        "Select Provider:",
        choices=["openai", "groq", "ollama", "lm_studio", "gemini", "anthropic", "azure"],
        pointer="👉"
    ).ask()

    # Set proper default models for each provider
    default_model = "gpt-5-mini"
    if provider == "groq": 
        default_model = "openai/gpt-oss-20b"  # Don't include groq/ prefix here
    elif provider == "ollama": 
        default_model = "qwen3:8b"
    elif provider == "lm_studio": 
        default_model = "local-model"
    elif provider == "gemini": 
        default_model = "gemini-3-flash-preview"
    elif provider == "anthropic": 
        default_model = "claude-3-7-sonnet-20250219"
    elif provider == "azure": 
        default_model = "gpt-5-mini"
    
    model = questionary.text("Enter Model Name:", default=default_model).ask()

    api_key = ""
    if provider not in ["ollama", "lm_studio"]:
        api_key = questionary.password("Enter API Key:").ask()
    
    base_url = ""
    if provider in ["ollama", "lm_studio"]:
        default_url = "http://localhost:11434" if provider == "ollama" else "http://localhost:1234"
        base_url = questionary.text("Enter Base URL:", default=default_url).ask()

    config = {"provider": provider, "model": model, "api_key": api_key, "base_url": base_url}
    with open(CONFIG_FILE, "w") as f: json.dump(config, f, indent=4)
    console.print("[green]✓ Configuration saved![/green]")
    console.print(f"[dim]Provider: {provider}, Model: {model}[/dim]")
    
    if questionary.confirm("Enable start on boot (Recommended)?").ask():
        enable_startup()
    
    if questionary.confirm("Start daemon now?").ask():
        start()

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
    <string>com.termimind.daemon</string>
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
    
    # Load the service immediately
    os.system(f"launchctl unload {PLIST_PATH} 2>/dev/null")
    os.system(f"launchctl load {PLIST_PATH}")
    
    console.print(f"[bold green]✔ TermiMind set to start automatically![/bold green]")

@app.command()
def start():
    """Start the background AI daemon manually."""
    ensure_config_dir()
    if os.path.exists(PID_FILE):
        console.print("[yellow]Already running (or stale PID). Restarting...[/yellow]")
        stop()

    console.print("[cyan]Starting TermiMind Daemon...[/cyan]")
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

@app.command()
def stop():
    """Stop the daemon."""
    if os.path.exists(PID_FILE):
        with open(PID_FILE, "r") as f:
            try:
                pid = int(f.read().strip())
                os.kill(pid, 15)
            except:
                pass
        os.remove(PID_FILE)
    
    # Also unload launchd if it exists
    if os.path.exists(PLIST_PATH):
        os.system(f"launchctl unload {PLIST_PATH} 2>/dev/null")
    
    console.print("[red]✓ Stopped.[/red]")

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
        data = response.json()
        sugg = data.get("suggestion", "")
        error = data.get("error", "")
        
        if error:
            console.print(f"[red]✗ Server Error:[/red] {error}")
        
        console.print(f"[green]✓ Server Response:[/green] '{sugg}'")
        if sugg == "":
            console.print("[yellow]⚠ Received empty suggestion (Model might be unsure or filtered).[/yellow]")
        else:
            console.print(f"[cyan]Visual Preview:[/cyan] git comm[grey50]{sugg}[/grey50]")

    except Exception as e:
        console.print(f"[red]✗ Connection Failed:[/red] {e}")
        console.print("\n[yellow]Troubleshooting:[/yellow]")
        console.print("1. Check if server is running: aiterminal start")
        console.print("2. View logs: aiterminal logs")

if __name__ == "__main__":
    app()