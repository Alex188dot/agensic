import typer
import json
import os
import subprocess
import sys
import requests
import questionary
import socket
import signal
from rich.console import Console
from rich.panel import Panel

app = typer.Typer(add_completion=False)
console = Console()

CONFIG_DIR = os.path.expanduser("~/.ghostshell")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
PID_FILE = os.path.join(CONFIG_DIR, "daemon.pid")
SERVER_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server.py")
PLIST_PATH = os.path.expanduser("~/Library/LaunchAgents/com.ghostshell.daemon.plist")

def ensure_config_dir():
    if not os.path.exists(CONFIG_DIR): os.makedirs(CONFIG_DIR)

def is_port_open(host: str = "127.0.0.1", port: int = 22000) -> bool:
    """Return True if something is already listening on host:port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex((host, port)) == 0

def _read_pid_file() -> int | None:
    if not os.path.exists(PID_FILE):
        return None
    try:
        with open(PID_FILE, "r") as f:
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

@app.command()
def setup():
    ensure_config_dir()
    console.print(Panel.fit("[bold cyan]GhostShell Configuration[/bold cyan]"))

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
    
    start_enabled = False
    if questionary.confirm("Enable start on boot (Recommended)?").ask():
        enable_startup()
        start_enabled = True

    # If launchd was enabled we already load/start it in enable_startup().
    if not start_enabled and questionary.confirm("Start daemon now?").ask():
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
    <string>com.ghostshell.daemon</string>
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

    was_running = is_port_open()
    if was_running:
        console.print("[yellow]GhostShell is already running. Restarting under launchd...[/yellow]")
        stop()

    # Load the service immediately.
    os.system(f"launchctl unload {PLIST_PATH} 2>/dev/null")
    os.system(f"launchctl load {PLIST_PATH}")
    
    console.print(f"[bold green]✔ GhostShell started and set to start automatically![/bold green]")

@app.command()
def start():
    """Start the background AI daemon manually."""
    ensure_config_dir()
    if is_port_open():
        console.print("[yellow]Daemon already running on port 22000.[/yellow]")
        return

    if os.path.exists(PID_FILE):
        console.print("[yellow]Already running (or stale PID). Restarting...[/yellow]")
        stop()

    console.print("[cyan]Starting GhostShell Daemon...[/cyan]")
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

    # Interactive countdown for DB initialization
    import time
    try:
        with console.status("[yellow]Waiting for DB initialization...[/yellow]", spinner="dots") as status:
            for i in range(10, 0, -1):
                status.update(f"[yellow]Waiting for DB initialization... {i}s[/yellow]")
                time.sleep(1)
        console.print("[green]✔ DB Ready![/green]")
    except KeyboardInterrupt:
        pass

@app.command()
def stop():
    """Stop the daemon."""
    # Unload launchd first to prevent KeepAlive respawning while stopping.
    if os.path.exists(PLIST_PATH):
        os.system(f"launchctl unload {PLIST_PATH} 2>/dev/null")

    stopped_any = False

    pid = _read_pid_file()
    if pid is not None and _try_kill_pid(pid):
        stopped_any = True

    # Also stop any process currently listening on the daemon port.
    for listener_pid in _find_listening_pids():
        if _try_kill_pid(listener_pid):
            stopped_any = True

    if os.path.exists(PID_FILE):
        try:
            os.remove(PID_FILE)
        except OSError:
            # Non-fatal: stale/permission-protected pid files should not crash setup/stop.
            pass

    if stopped_any:
        console.print("[red]✓ Stopped.[/red]")
    else:
        console.print("[yellow]GhostShell was not running.[/yellow]")

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
    console.print("[bold]Testing connection to GhostShell Daemon (Port 22000)...[/bold]")
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
        console.print("1. Check if server is running: aiterminal start")
        console.print("2. View logs: aiterminal logs")

@app.command("shortcuts")
def shortcuts_command():
    """Show keyboard shortcuts."""
    show_shortcuts()

@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    shortcuts: bool = typer.Option(False, "--shortcuts", help="Show keyboard shortcuts help")
):
    """GhostShell: AI-powered terminal autocomplete."""
    if shortcuts:
        show_shortcuts()
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        console.print("[bold cyan]GhostShell[/bold cyan] - Use --help for commands.")

def show_shortcuts():
    """Display the shortcuts help panel."""
    rows = [
        ("Accept inline suggestion", "Tab", "-", "Accept full suggestion"),
        ("Trigger suggestion", "Ctrl+Space", "-", "Manual trigger"),
        ("Partial accept (word)", "Option+Right", "-", "Accept next word"),
        ("Cycle suggestions", "Ctrl+N / Ctrl+P", "-", "Next / previous"),
    ]
    separator = "-" * 60
    lines = []
    for action, primary, fallback, notes in rows:
        lines.append(f"[bold green]{action}[/bold green]")
        lines.append(f"Primary : {primary}")
        lines.append(f"Fallback: {fallback}")
        lines.append(f"Notes   : {notes}")
        lines.append(separator)

    shortcuts_text = "\n".join(lines[:-1])
    console.print(
        Panel(
            shortcuts_text,
            title="[bold cyan]GhostShell Shortcuts[/bold cyan]",
            subtitle="Use `aiterminal shortcuts` or `aiterminal --shortcuts`",
            expand=False,
        )
    )

if __name__ == "__main__":
    app()
