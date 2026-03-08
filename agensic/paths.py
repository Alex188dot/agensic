import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


APP_NAME = "agensic"
APP_DISPLAY_NAME = "Agensic"
LEGACY_ROOT_DIR = str(Path.home() / f".{APP_NAME}")


def _expand(path: str) -> str:
    return str(Path(path).expanduser())


def _env_path(name: str, default: str) -> str:
    raw = str(os.environ.get(name, "") or "").strip()
    return _expand(raw or default)


def _home_child(*parts: str) -> str:
    return str(Path.home().joinpath(*parts))


@dataclass(frozen=True)
class AppPaths:
    config_dir: str
    state_dir: str
    cache_dir: str
    install_dir: str
    install_bin_dir: str
    user_bin_dir: str
    config_file: str
    auth_file: str
    pid_file: str
    state_sqlite_path: str
    events_dir: str
    snapshots_dir: str
    locks_dir: str
    repair_dir: str
    repair_log_file: str
    zvec_commands_path: str
    zvec_feedback_path: str
    last_indexed_path: str
    server_log_file: str
    plugin_log_file: str
    shell_integration_path: str
    shell_client_path: str
    runtime_python_path: str
    launcher_path: str
    provenance_tui_bin: str
    provenance_private_key_path: str
    provenance_public_key_path: str
    agent_registry_remote_cache_path: str
    agent_registry_remote_meta_path: str
    agent_registry_local_override_path: str


def get_app_paths() -> AppPaths:
    if sys.platform == "win32":
        config_base = _env_path("APPDATA", _home_child("AppData", "Roaming"))
        local_base = _env_path("LOCALAPPDATA", _home_child("AppData", "Local"))
        config_dir = os.path.join(config_base, APP_DISPLAY_NAME)
        state_dir = os.path.join(local_base, APP_DISPLAY_NAME, "State")
        cache_dir = os.path.join(local_base, APP_DISPLAY_NAME, "Cache")
        install_dir = os.path.join(local_base, APP_DISPLAY_NAME, "Install")
        user_bin_dir = os.path.join(local_base, APP_DISPLAY_NAME, "Bin")
    else:
        config_dir = os.path.join(
            _env_path("XDG_CONFIG_HOME", _home_child(".config")),
            APP_NAME,
        )
        state_dir = os.path.join(
            _env_path("XDG_STATE_HOME", _home_child(".local", "state")),
            APP_NAME,
        )
        cache_dir = os.path.join(
            _env_path("XDG_CACHE_HOME", _home_child(".cache")),
            APP_NAME,
        )
        install_dir = os.path.join(state_dir, "install")
        user_bin_dir = _env_path("XDG_BIN_HOME", _home_child(".local", "bin"))

    install_bin_dir = os.path.join(install_dir, "bin")
    return AppPaths(
        config_dir=config_dir,
        state_dir=state_dir,
        cache_dir=cache_dir,
        install_dir=install_dir,
        install_bin_dir=install_bin_dir,
        user_bin_dir=user_bin_dir,
        config_file=os.path.join(config_dir, "config.json"),
        auth_file=os.path.join(config_dir, "auth.json"),
        pid_file=os.path.join(state_dir, "daemon.pid"),
        state_sqlite_path=os.path.join(state_dir, "state.sqlite"),
        events_dir=os.path.join(state_dir, "events"),
        snapshots_dir=os.path.join(state_dir, "snapshots"),
        locks_dir=os.path.join(state_dir, "locks"),
        repair_dir=os.path.join(state_dir, "repair"),
        repair_log_file=os.path.join(state_dir, "repair", "repair.log"),
        zvec_commands_path=os.path.join(cache_dir, "zvec_commands"),
        zvec_feedback_path=os.path.join(cache_dir, "zvec_feedback_stats"),
        last_indexed_path=os.path.join(cache_dir, "last_indexed_line"),
        server_log_file=os.path.join(state_dir, "server.log"),
        plugin_log_file=os.path.join(state_dir, "plugin.log"),
        shell_integration_path=os.path.join(install_dir, "agensic.zsh"),
        shell_client_path=os.path.join(install_dir, "shell_client.py"),
        runtime_python_path=os.path.join(install_dir, ".venv", "bin", "python"),
        launcher_path=os.path.join(user_bin_dir, APP_NAME),
        provenance_tui_bin=os.path.join(install_bin_dir, "agensic-provenance-tui"),
        provenance_private_key_path=os.path.join(config_dir, "provenance_ed25519.pem"),
        provenance_public_key_path=os.path.join(config_dir, "provenance_ed25519.pub.pem"),
        agent_registry_remote_cache_path=os.path.join(cache_dir, "agent_registry.remote.json"),
        agent_registry_remote_meta_path=os.path.join(cache_dir, "agent_registry.remote.meta.json"),
        agent_registry_local_override_path=os.path.join(config_dir, "agent_registry.local.json"),
    )


APP_PATHS = get_app_paths()


def ensure_app_layout() -> None:
    for path in (
        APP_PATHS.config_dir,
        APP_PATHS.state_dir,
        APP_PATHS.cache_dir,
        APP_PATHS.install_dir,
        APP_PATHS.install_bin_dir,
        APP_PATHS.user_bin_dir,
    ):
        os.makedirs(path, mode=0o700, exist_ok=True)


def migrate_legacy_layout() -> None:
    legacy_root = Path(LEGACY_ROOT_DIR)
    if not legacy_root.exists() or not legacy_root.is_dir():
        return

    ensure_app_layout()

    file_migrations = (
        (legacy_root / "config.json", Path(APP_PATHS.config_file)),
        (legacy_root / "auth.json", Path(APP_PATHS.auth_file)),
        (legacy_root / "daemon.pid", Path(APP_PATHS.pid_file)),
        (legacy_root / "state.sqlite", Path(APP_PATHS.state_sqlite_path)),
        (legacy_root / "server.log", Path(APP_PATHS.server_log_file)),
        (legacy_root / "plugin.log", Path(APP_PATHS.plugin_log_file)),
        (legacy_root / "provenance_ed25519.pem", Path(APP_PATHS.provenance_private_key_path)),
        (legacy_root / "provenance_ed25519.pub.pem", Path(APP_PATHS.provenance_public_key_path)),
        (legacy_root / "agent_registry.remote.json", Path(APP_PATHS.agent_registry_remote_cache_path)),
        (legacy_root / "agent_registry.remote.meta.json", Path(APP_PATHS.agent_registry_remote_meta_path)),
        (legacy_root / "agent_registry.local.json", Path(APP_PATHS.agent_registry_local_override_path)),
        (legacy_root / "last_indexed_line", Path(APP_PATHS.last_indexed_path)),
        (legacy_root / "agensic.zsh", Path(APP_PATHS.shell_integration_path)),
        (legacy_root / "shell_client.py", Path(APP_PATHS.shell_client_path)),
    )
    for source, dest in file_migrations:
        if not source.exists() or dest.exists():
            continue
        os.makedirs(dest.parent, mode=0o700, exist_ok=True)
        shutil.copy2(source, dest)

    dir_migrations = (
        (legacy_root / "events", Path(APP_PATHS.events_dir)),
        (legacy_root / "snapshots", Path(APP_PATHS.snapshots_dir)),
        (legacy_root / "locks", Path(APP_PATHS.locks_dir)),
        (legacy_root / "repair", Path(APP_PATHS.repair_dir)),
        (legacy_root / "zvec_commands", Path(APP_PATHS.zvec_commands_path)),
        (legacy_root / "zvec_feedback_stats", Path(APP_PATHS.zvec_feedback_path)),
        (legacy_root / ".venv", Path(APP_PATHS.install_dir) / ".venv"),
        (legacy_root / "bin", Path(APP_PATHS.install_bin_dir)),
    )
    for source, dest in dir_migrations:
        if not source.exists() or dest.exists():
            continue
        os.makedirs(dest.parent, mode=0o700, exist_ok=True)
        shutil.copytree(source, dest, dirs_exist_ok=True)
