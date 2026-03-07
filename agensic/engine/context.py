import os
import platform


class Settings:
    history_lines: int = 50
    llm_history_lines: int = 12
    max_commands_context: int = 40
    max_packages_context: int = 40


class RequestContext:
    def __init__(
        self,
        history_file: str,
        cwd: str,
        buffer: str,
        shell: str,
        terminal: str | None = None,
        platform_name: str | None = None,
    ):
        self.history_file = history_file
        self.cwd = cwd
        self.buffer = buffer
        self.shell = shell
        self.terminal = terminal or os.environ.get("TERM", "")
        self.platform_name = platform_name or platform.system()


class SystemInventory:
    def __init__(self):
        self.commands: list[str] = []
        self.packages: list[str] = []
        self.package_sources: list[str] = []
