# Windows Support Plan

This document tracks everything required to bring Agensic to Windows.  
Each phase has a checklist — mark items with `[x]` as they are completed.

---

## Prerequisite: Upgrade zvec to v0.3.1

The current dependency is `zvec>=0.1.0`. Version 0.3.0+ introduced **native Windows builds**
(MSVC 2022 tested). Without this upgrade, the vector database layer cannot run on Windows at all.

- [ ] Bump `zvec>=0.3.1` in `pyproject.toml`
- [ ] Bump `zvec>=0.3.1` in `requirements.txt`
- [ ] Verify `zvec` import works on a Windows Python 3.10+ environment
- [ ] Run existing unit tests on macOS/Linux with the upgraded zvec to confirm no regressions

---

## Phase 1 — Core Python Runtime (no UI, no shell integration)

Goal: `agensic doctor`, `agensic setup`, and the daemon server start on Windows
without crashing, even if some features are gated behind platform checks.

### 1.1 Path Layout

`agensic/paths.py` already has a `win32` branch, but several fields are still Unix-specific.

- [ ] Fix `runtime_python_path`: change `.venv/bin/python` → `.venv/Scripts/python.exe` on Windows
- [ ] Fix `launcher_path` / `session_*_launcher_path`: replace bare `agensic` with `agensic.exe`
- [ ] Fix `tuis_bin`: append `.exe` suffix on Windows
- [ ] Fix `primary_shell_integration`: add `powershell` option for Windows
- [ ] Review `ensure_app_layout()`: `mode=0o700` is a no-op on Windows — use `os.makedirs(path, exist_ok=True)` and rely on per-file ACLs
- [ ] Add Windows-specific shell integration path: `agensic_profile.ps1`

### 1.2 File Permissions & Security

`agensic/utils/fs.py`, `agensic/config/auth.py`, `agensic/engine/provenance.py`

- [ ] Wrap every `os.chmod()` / `os.fchmod()` call in a `if os.name != 'nt':` guard
  (already partially done via `_chmod_best_effort`, but some direct calls remain)
- [ ] `PRIVATE_DIR_MODE` / `PRIVATE_FILE_MODE` — document that these are no-ops on NTFS;
  rely on per-directory ACLs via `icacls` or Python `os.chmod` limited support
- [ ] `cli.py` line 36: `os.chmod(tmp_path, 0o600)` → guard with platform check
- [ ] `agensic/engine/provenance.py` lines 137/156: same pattern
- [ ] `agensic/utils/history.py` line 50: same pattern

### 1.3 Platform Detection Helpers

Repeated across `app.py`, `track.py`, `paths.py`, `shell.py`.

- [ ] Create `agensic/utils/platform.py` with:
  - `is_windows() -> bool`
  - `is_macos() -> bool`
  - `is_linux() -> bool`
  - `platform_tag() -> str` (consolidate from `app.py._platform_tag` + `track.py._platform_rust_target`)
  - `python_executable() -> str` (`.venv/Scripts/python.exe` vs `.venv/bin/python`)
  - `binary_suffix() -> str` (`.exe` on Windows, `` elsewhere)
- [ ] Replace all `sys.platform == "win32"` / `sys.platform.startswith("linux")` / etc.
  scattered across the codebase with calls to this module

### 1.4 shlex Usage (posix=True)

`shlex.split(value, posix=True)` is used in ~15 places. On Windows, shell syntax
differs (backslash paths, different quoting rules).

- [ ] Audit all `shlex.split(..., posix=True)` calls
- [ ] Create a helper `shell_split(value: str) -> list[str]` in `agensic/utils/shell.py`
  that uses `posix=True` on Unix and `posix=False` on Windows
- [ ] Replace direct `shlex.split` calls with the helper
- [ ] Handle Windows backslash paths in command tokenization

### 1.5 os.uname() and Machine Detection

- [ ] `app.py._platform_tag()` and `track.py._platform_rust_target()` use `os.uname()`
  which does not exist on Windows
- [ ] Replace with `platform.machine()` (already imported in `engine/context.py`)
- [ ] Add Windows platform tags: `windows-x64`, `windows-arm64`
- [ ] Add Windows Rust targets: `x86_64-pc-windows-msvc`, `aarch64-pc-windows-msvc`

### 1.6 Process Management

`agensic/cli/app.py`, `agensic/cli/track.py`

- [ ] `os.kill(pid, signal.SIGTERM)` / `os.kill(pid, signal.SIGKILL)` — these **do work** on
  Windows (Python maps them to `TerminateProcess`), so no code change needed ✓
- [ ] `os.killpg(root_pid, signal.SIGTERM)` → not available on Windows;
  use `psutil.Process(root_pid).terminate()` (recursively terminates children) or
  `subprocess.run(["taskkill", "/PID", str(root_pid), "/T"])`
- [ ] `os.getsid()` / `os.getpgid()` → not available on Windows (already guarded in
  `_safe_getsid`/`_safe_getpgid` with try/except, but verify `AttributeError` is caught)
- [ ] `_is_pid_alive()`: `os.kill(pid, 0)` **does work** on Windows — raises `OSError` if
  process doesn't exist. Current implementation should be fine, but verify on Windows ✓
- [ ] `_find_listening_pids()`: `lsof` doesn't exist on Windows;
  use `psutil.net_connections()` or `netstat` as fallback
- [ ] `UNINSTALL_SENTINEL`: uses `os.getuid()` which doesn't exist on Windows;
  use `os.getlogin()` or `getpass.getuser()` as fallback
- [ ] `os.fork()` — used in test code and possibly `track.py`; does **not exist** on Windows
  at all. Must replace with `subprocess` multiprocessing approach
- [ ] `subprocess.Popen(preexec_fn=...)` — used for process group setup on Unix;
  raises `AttributeError` on Windows. Must replace with
  `creationflags=subprocess.CREATE_NEW_PROCESS_GROUP` on Windows
- [ ] `fcntl.flock()` / `fcntl.lockf()` — used for file locking in `track.py`;
  not available on Windows. Replace with `msvcrt.locking()` or `portalocker` package

### 1.7 Signals

- [ ] `signal.SIGTERM` and `signal.SIGKILL` — **are defined on Windows** (values 15 and 9)
  and `os.kill()` with them works (maps to `TerminateProcess`). No code change needed ✓
- [ ] `signal.SIGWINCH` → not available on Windows; disable terminal resize handling or
  use `threading`-based approach
- [ ] `signal.SIGUSR1` / `SIGUSR2` if used → not available on Windows
- [ ] `agensic/server/app.py` line 92: `signal.signal(signal.SIGTERM, ...)` → works on Windows
  but the handler behavior differs; verify graceful shutdown works via `CTRL_C_EVENT`
- [ ] `agensic/cli/track.py` line 4295: `signal.getsignal/set(signal.SIGWINCH)` → guard
  with `hasattr(signal, 'SIGWINCH')` for Windows

### 1.8 select / poll

- [ ] `agensic/cli/track.py` uses `select.select()` for PTY I/O — Windows `select()`
  only works with sockets, not file descriptors
- [ ] For Phase 1 (daemon-only), track sessions are gated out on Windows, so this is not blocking
- [ ] For Phase 3 (full track support), need `msvcrt.kbhit()` or `asyncio`-based approach

### 1.9 Environment Variables

- [ ] `HOME` vs `USERPROFILE` — `os.path.expanduser("~")` works on both platforms, but any
  direct `os.environ["HOME"]` reads will fail on Windows (uses `USERPROFILE` instead)
- [ ] `PATH` separator: `os.pathsep` is `:` on Unix, `;` on Windows — any code splitting
  on `:` explicitly will break. Audit all `os.environ.get("PATH", "").split(":")` → use `os.pathsep`
- [ ] `COMSPEC` — Windows equivalent of `SHELL`; already partially handled in `shell.py` ✓

### 1.10 shlex.join() Output

`shlex.join()` is used in several places and produces **Unix-style quoting** (single quotes)
which is wrong for `cmd.exe` and PowerShell.

- [ ] Audit all `shlex.join()` calls
- [ ] Create a helper `shell_join(tokens: list[str]) -> str` in `agensic/utils/shell.py`
  that uses `shlex.join()` on Unix and `subprocess.list2cmdline()` on Windows
- [ ] Replace direct `shlex.join` calls with the helper

### 1.11 Self-Update Flow

- [ ] `_run_release_installer()` in `app.py` calls `["bash", "install.sh"]` — completely
  broken on Windows where bash may not be available
- [ ] Create a Python-native installer path that doesn't require bash
- [ ] Or: detect Windows and use `install.ps1` instead

### 1.12 GPU Cache

- [ ] `_clear_gpu_cache()` in `vector_db/command_db.py` checks `torch.mps.is_available()`
  — MPS is macOS-only and doesn't exist on Windows. The try/except already guards this,
  but add a `hasattr(torch, 'mps')` check for clarity

---

## Phase 2 — Daemon Lifecycle on Windows

Goal: Daemon starts automatically at login and persists across reboots.

> **Note:** This phase should be completed before Phase 3 (Shell Integration) because
> shell integration depends on the daemon running reliably.

### 2.1 Windows Service

macOS uses `launchd` (plist), Linux uses `systemd`. Windows needs a Windows Service.

- [ ] Create `agensic/windows_service.py` using `pywin32` (`win32serviceutil`)
- [ ] Register service with `sc create AgensicDaemon binPath= ...`
- [ ] Or: use Windows Task Scheduler with "Run at logon" trigger (simpler, no admin needed)
- [ ] `_is_startup_enabled()` → check Task Scheduler or Windows Service status
- [ ] `_enable_startup_impl()` → register Task Scheduler task or install service
- [ ] `_disable_startup_impl()` → remove Task Scheduler task or uninstall service
- [ ] `_cleanup_legacy_daemon_artifacts()` → skip launchctl/systemd on Windows

### 2.2 Daemon Process Management

- [ ] `_try_kill_pid()` → `os.kill()` with SIGTERM works on Windows (maps to `TerminateProcess`)
  but `signal.SIGKILL` is also defined and works. Verify behavior ✓
- [ ] PID file location: already in `APP_PATHS.pid_file` (AppData on Windows) ✓
- [ ] Log file location: already in `APP_PATHS.server_log_file` ✓

---

## Phase 3 — Shell Integration (PowerShell + WSL Bash)

Goal: Users get autocomplete suggestions inside PowerShell and optionally WSL bash.

> **Depends on:** Phase 1 + Phase 2 (daemon must be running)

### 3.1 PowerShell Integration Script

- [ ] Create `agensic.ps1` — PowerShell profile module equivalent to `agensic.bash` / `agensic.zsh`
- [ ] Implement `Set-PSReadLineKeyHandler` for inline suggestion display
- [ ] Implement `Invoke-Expression`-based suggestion acceptance
- [ ] Add `agensic` PowerShell module manifest (`.psd1`) for clean `Import-Module` experience
- [ ] Handle PowerShell history file path (`(Get-PSReadLineOption).HistorySavePath`)
- [ ] Handle PowerShell prompt routing to daemon

### 3.2 Shared Shell Helpers for Windows

`shell/agensic_shared.sh` is bash-only.

- [ ] Create `shell/agensic_shared.ps1` with Windows equivalents for:
  - File modification time checking
  - Shell integration state management
  - Command execution hooks
- [ ] Or: consolidate into a Python-based shell client that works cross-platform
  (the `shell_client.py` approach already partially does this)

### 3.3 Shell RC / Profile Patching

`install.sh` patches `.bashrc` / `.zshrc`. Windows needs different profile paths.

- [ ] Detect PowerShell profile path: `$HOME\Documents\PowerShell\Microsoft.PowerShell_profile.ps1`
  or `$HOME\Documents\WindowsPowerShell\Microsoft.PowerShell_profile.ps1`
- [ ] Add idempotent profile block (like `# >>> agensic >>>` markers)
- [ ] Add `agensic` to `$env:PATH` or install to a directory already on PATH
- [ ] Support both PowerShell 5.1 (Windows PowerShell) and PowerShell 7+ (pwsh)

### 3.4 Windows PATH Management

- [ ] Install `agensic.exe` launcher to `%LOCALAPPDATA%\Agensic\Bin\`
- [ ] Add this directory to user PATH via `[Environment]::SetEnvironmentVariable("PATH", ...)`
- [ ] Or: install to a directory already on PATH (e.g., `%LOCALAPPDATA%\Programs\`)

---

## Phase 4 — Full Track / `agensic run` Support

Goal: `agensic run <agent>` works on Windows with session tracking, transcripts, and checkpoints.

This is the **hardest phase** because it relies on PTY/process-group APIs that are fundamentally
different on Windows.

### 4.1 PTY / Console Emulation

- [ ] `import pty` / `os.openpty()` — not available on Windows
- [ ] Evaluate options:
  - **ConPTY** (Windows Pseudo Console, available since Windows 10 1809):
    Use `pywinpty` Python package as a drop-in replacement for `pty.openpty()`
  - **winpty**: Older, less maintained alternative
  - **subprocess without PTY**: Lose transcript fidelity but simpler
- [ ] Add `pywinpty` as a Windows-only dependency (`pip install pywinpty; sys.platform == 'win32'`)
- [ ] Create `agensic/utils/pty_compat.py`:
  - `openpty()` → `pty.openpty()` on Unix, `winpty.PTY.open()` on Windows
  - Abstract the master/slave fd interface
- [ ] Update `track.py` to use the compatibility layer

### 4.2 fcntl / termios / tty

- [ ] `import fcntl` — not available on Windows
- [ ] `import termios` — not available on Windows (already guarded in `app.py` with try/except)
- [ ] `import tty` — not available on Windows
- [ ] Create platform-aware terminal control:
  - Raw mode: use `msvcrt.getch()` / `msvcrt.kbhit()` on Windows
  - Terminal attributes: not directly available; use `pywinpty` or Windows Console API
- [ ] Guard `track.py` line 3: `import fcntl` with `if os.name != 'nt':`
- [ ] Guard `track.py` line 17: `import termios` with `if os.name != 'nt':`
- [ ] Guard `track.py` line 16: `import tty` with `if os.name != 'nt':`

### 4.3 Session Tracking on Windows

- [ ] Remove `RuntimeError` in `ensure_track_supported()` for Windows when ConPTY is available
- [ ] `os.killpg()` for stopping sessions → Windows process tree termination via
  `psutil.Process().children()` + terminate
- [ ] Process group / session ID detection → use `psutil` instead of `os.getsid/getpgid`
- [ ] Transcript recording: `select.select()` on PTY fd → use `winpty` read with timeout
  or `asyncio` event loop

### 4.4 Checkpoint / Git Operations

- [ ] `git` commands should work on Windows (Git for Windows provides `git.exe`)
- [ ] Verify `_run_git_capture()` works with Git for Windows in PATH
- [ ] `_git_binary_diff_against_head()` — verify `git apply --binary` works on Windows
- [ ] Test `_capture_repo_snapshot()` on Windows paths with backslashes

---



---

## Phase 5 — Rust TUI Sidecar (agensic-tuis)

> **Depends on:** Phase 1 (platform tags must be defined)

Goal: `agensic-tuis.exe` builds and runs on Windows.

### 5.1 Cross-Compilation

- [ ] Add `x86_64-pc-windows-msvc` and `aarch64-pc-windows-msvc` targets to CI
- [ ] `crossterm` (used by the TUI) already supports Windows — no code changes needed ✓
- [ ] `libc` crate: verify it compiles for Windows (it does, but may need minor adjustments)
- [ ] Test `ratatui` rendering on Windows Terminal, cmd.exe, PowerShell
- [ ] `vt100` crate: verify Windows compatibility

### 5.2 Build & Release Pipeline

- [ ] Add `windows-x64` and `windows-arm64` entries to `tuis_manifest.json`
- [ ] Upload `agensic-tuis.exe` to GitHub Releases
- [ ] Update `PUBLISHED_TUIS_PLATFORMS` in `app.py` to include `windows-x64`
- [ ] Update `install.sh` Python sidecar download logic to handle `.zip` instead of `.tar.gz` on Windows
- [ ] Create `install.ps1` that downloads and installs the TUI sidecar

---

## Phase 6 — Installer

Goal: One-command install on Windows, equivalent to `curl ... | bash install.sh`.

### 6.1 PowerShell Installer

- [ ] Create `install.ps1` — equivalent of `install.sh`:
  - Create AppData directories
  - Copy shell integration assets (`.ps1` files)
  - Set up Python venv (`.venv\Scripts\python.exe`)
  - Install `agensic` package via pip
  - Create launcher scripts (`.exe` wrappers or `.bat`/`.ps1` files)
  - Patch PowerShell profile
  - Add to PATH
- [ ] Create `bootstrap.ps1` — equivalent of `bootstrap.sh`:
  - Clone repo
  - Run `install.ps1`
- [ ] Update `README.md` with Windows install instructions

### 6.2 Python Venv on Windows

- [ ] `install.sh` creates venv at `$INSTALL_DIR/.venv` — on Windows the Python binary is
  at `.venv/Scripts/python.exe` not `.venv/bin/python`
- [ ] Launcher scripts must use `Scripts/python.exe` path
- [ ] `uv` and `pip` commands are the same, but paths differ

### 6.3 PyTorch Installation on Windows

- [ ] On Windows, `torch` defaults to CUDA builds which are very large
- [ ] Add Windows CPU-only torch install option (like Linux `+cpu` variant)
- [ ] CUDA support: auto-detect NVIDIA GPU and install appropriate torch build

---

## Phase 7 — Testing & CI

Goal: Full test suite passes on Windows; CI runs on `windows-latest`.

### 7.1 CI Pipeline

- [ ] Add `windows-latest` to CI matrix in `.github/workflows/ci.yml`
- [ ] Add TUIs build job for Windows (`cargo build` on `windows-latest`)
- [ ] Install `pywinpty` and `pywin32` as dev dependencies on Windows
- [ ] Handle `zsh` not being available on Windows runners (skip zsh tests)

### 7.2 Unit Test Fixes

- [ ] Fix hardcoded `/tmp/` paths in tests → use `tempfile.gettempdir()`
- [ ] Fix hardcoded Unix user/group IDs → use `os.getuid()` with fallback
- [ ] Skip `pty`/`termios`/`fcntl` tests on Windows (`@unittest.skipIf(os.name == 'nt', ...)`)
- [ ] Fix `test_shell_utils.py`: `/usr/bin/bash` → use `shutil.which('bash')`
- [ ] Fix `test_command_blocking_policy.py`: `/dev/zero`, `/dev/disk0` → Windows equivalents
- [ ] Fix `test_paths.py`: verify Windows AppData paths resolve correctly
- [ ] Fix `test_cli_track.py`: skip PTY/process-group tests on Windows

### 7.3 Integration Tests

- [ ] `test_agensic_bash_adapter.py` → needs WSL or skip on Windows
- [ ] `test_agensic_bash_sessions.py` → needs WSL or skip on Windows
- [ ] Add PowerShell integration tests for the new `.ps1` scripts

---

## Phase 8 — Polish & Edge Cases

Goal: Production-quality Windows experience.

### 8.1 Windows Terminal Quirks

- [ ] ANSI escape sequences: Windows 10+ supports VT sequences natively, but must
  enable virtual terminal processing via `SetConsoleMode`
- [ ] `MOUSE_REPORTING_RESET_SEQ`, `ALT_SCREEN_ENTER_SEQ`, etc. — verify on Windows Terminal
- [ ] Console encoding: Windows may default to UTF-16 or CP-1252; enforce UTF-8
  (`chcp 65001` or `PYTHONUTF8=1`)
- [ ] Line endings: `\r\n` vs `\n` — ensure transcript files handle both

### 8.2 Windows Defender / SmartScreen

- [ ] Unsigned `.exe` files may trigger SmartScreen warnings
- [ ] Consider code signing the TUI sidecar binary
- [ ] Add Agensic directory to Defender exclusions in docs (Python venv + torch can trigger false positives)

### 8.3 Windows-Specific Documentation

- [ ] Add Windows section to README
- [ ] Document WSL vs native Windows usage
- [ ] Document known limitations (e.g., track without ConPTY on older Windows)
- [ ] Document PowerShell profile setup

### 8.4 WSL (Windows Subsystem for Linux) Support

- [ ] Verify Agensic works inside WSL2 out of the box (Linux paths, bash, etc.)
- [ ] Document that WSL2 is a "just works" option for users who don't need native Windows
- [ ] Handle detection: `sys.platform == 'linux'` inside WSL — add `AGENSIC_WSL=1` env var
  for any WSL-specific workarounds

---

## Summary of Dependencies to Add

| Package | Purpose | Platform |
|---------|---------|----------|
| `zvec>=0.3.1` | Vector DB with Windows builds | All |
| `pywinpty` | PTY emulation for `agensic run` | Windows only |
| `pywin32` | Windows Service / Task Scheduler | Windows only |
| `portalocker` | Cross-platform file locking (alternative to `fcntl.flock`) | All |
| `psutil` | Cross-platform process management (already a dependency) | All |

## Files Requiring Significant Changes

| File | Change Scope |
|------|-------------|
| `agensic/paths.py` | Windows path fixes, `.exe` suffixes, PowerShell integration path |
| `agensic/utils/fs.py` | Guard `chmod`/`fchmod` on Windows |
| `agensic/utils/shell.py` | `shlex` compat, PowerShell detection |
| `agensic/cli/app.py` | Platform detection, signals, process mgmt, TUI binary, install logic |
| `agensic/cli/track.py` | PTY compat, signals, fcntl/termios guards, process groups |
| `agensic/server/deps.py` | History file paths on Windows, platform checks |
| `agensic/server/app.py` | Signal handler guards |
| `agensic/vector_db/command_db.py` | `shlex` compat, `torch` GPU cache on Windows |
| `install.sh` | Add Windows-specific Python sidecar download |
| `.github/workflows/ci.yml` | Add `windows-latest` |
| `pyproject.toml` | Bump zvec, add Windows-only deps |
| `requirements.txt` | Bump zvec |

## New Files to Create

| File | Purpose |
|------|---------|
| `agensic/utils/platform.py` | Cross-platform detection helpers |
| `agensic/utils/pty_compat.py` | PTY abstraction (Unix pty / Windows winpty) |
| `agensic.ps1` | PowerShell shell integration |
| `shell/agensic_shared.ps1` | PowerShell helper functions |
| `install.ps1` | Windows installer script |
| `bootstrap.ps1` | Windows bootstrap script |
| `agensic/windows_service.py` | Windows Service / Task Scheduler integration |
