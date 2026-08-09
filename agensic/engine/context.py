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
        cursor_position: int | None = None,
        allow_semantic: bool = True,
    ):
        self.history_file = history_file
        self.cwd = cwd
        self.buffer = buffer
        self.shell = shell
        self.terminal = terminal or os.environ.get("TERM", "")
        self.platform_name = platform_name or platform.system()
        self.cursor_position = len(buffer) if cursor_position is None else int(cursor_position)
        self.allow_semantic = bool(allow_semantic)


class SystemInventory:
    def __init__(self):
        self.commands: list[str] = []
        self.packages: list[str] = []
        self.package_sources: list[str] = []
