import os
import logging
import json
import re
import shutil
import glob
from pathlib import Path
from litellm import acompletion
from learning import Learner

logger = logging.getLogger("ghostshell.engine")

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

class SuggestionEngine:
    def __init__(self):
        self.learner = Learner()
        self.inventory = self._get_simple_inventory()

    def _safe_tail(self, path: str, max_lines: int) -> list[str]:
        if not path: return []
        candidate = Path(path).expanduser()
        if not candidate.exists() or not candidate.is_file(): return []
        try:
            # Read minimal amount
            size = candidate.stat().st_size
            # Rough estimation: 100 bytes per line
            read_size = max_lines * 200
            with open(candidate, 'rb') as f:
                if size > read_size:
                    f.seek(-read_size, 2)
                lines = f.read().decode('utf-8', errors='ignore').splitlines()
            return [line.strip() for line in lines[-max_lines:] if line.strip()]
        except Exception:
            return []

    def _list_working_dir(self, path: str, max_items: int = 60) -> list[str]:
        items: list[str] = []
        try:
            with os.scandir(path) as it:
                for entry in it:
                    if entry.name.startswith("."): continue
                    suffix = "/" if entry.is_dir() else ""
                    items.append(entry.name + suffix)
                    if len(items) >= max_items: break
        except OSError:
            return []
        return sorted(items)

    def _get_simple_inventory(self) -> SystemInventory:
        inv = SystemInventory()
        # Scan PATH for common tools (cached in init roughly)
        paths = os.environ.get("PATH", "").split(os.pathsep)
        cmds = set()
        for p in paths[:2]: 
            if os.path.exists(p) and os.path.isdir(p):
                try:
                    # Just grab a handful to populate context, not exhaustively
                    for i, f in enumerate(os.listdir(p)):
                        if i > 20: break
                        cmds.add(f)
                except: pass
        inv.commands = list(cmds)
        
        # Check managers
        if shutil.which("pip"): inv.package_sources.append("pip")
        if shutil.which("npm"): inv.package_sources.append("npm")
        if shutil.which("cargo"): inv.package_sources.append("cargo")
        return inv

    def _get_deterministic_candidates(self, ctx: RequestContext) -> list[str]:
        """
        Generate candidates purely from history and local files (FAST).
        Used to 'prime' the suggestions or provide fallback.
        """
        candidates = []
        prefix = ctx.buffer.strip()
        if len(prefix) < 2:
            return []

        # 1. History match (Exact prefix)
        history = self._safe_tail(ctx.history_file, 1000) # Look deeper for deterministic
        seen = set()
        # Iterate backwards
        for cmd in reversed(history):
            if cmd.startswith(prefix) and cmd != prefix:
                if cmd not in seen:
                    candidates.append(cmd[len(prefix):]) # Return suffix
                    seen.add(cmd)
            if len(candidates) >= 2: break
        
        return candidates

    def build_prompt_context(self, request: RequestContext) -> str:
        history = self._safe_tail(request.history_file, Settings.history_lines)
        cwd_items = self._list_working_dir(request.cwd)
        
        lines: list[str] = [
            f"Shell: {request.shell}",
            f"CWD: {request.cwd}",
            f"Buffer: {request.buffer}",
            "",
            "Relevant Executables:",
            ", ".join(self.inventory.commands[:20]) if self.inventory.commands else "(none)",
            "",
            "Recent History:",
            "\n".join(history[-15:]) if history else "(none)",
            "",
            "Files in CWD:",
            ", ".join(cwd_items) if cwd_items else "(none)",
        ]
        return "\n".join(lines)

    async def get_suggestions(self, config: dict, ctx: RequestContext) -> list[str]:
        # 1. Deterministic Pass (Very fast)
        # We capture these but still ask LLM for creativity, unless deterministic is perfect.
        # deterministic = self._get_deterministic_candidates(ctx)
        
        # 2. LLM Pass
        model = config.get("model", "gpt-5-mini")
        provider = config.get("provider", "openai")
        api_key = config.get("api_key", None)
        base_url = config.get("base_url", None)
        
        context_str = self.build_prompt_context(ctx)
        
        system_prompt = (
            "You are a CLI autocomplete engine. "
            "Context provided below (History, CWD). "
            "Provide 3 completions for the user's buffer. "
            "JSON output keys: option_1, option_2, option_3. "
            "Values must be full command completions (suffixes or full replacements logic handled by client, but prefer full logical command). "
            "Do not output explanations."
            f"\n--- CONTEXT ---\n{context_str}"
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

        suggestions = ["", "", ""]

        try:
            kwargs = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Buffer: {ctx.buffer}"}
                ],
                "temperature": 0.3, # Lower temperature for stability
                "response_format": {"type": "json_object"},
            }

            if api_key:
                if provider == "groq": os.environ["GROQ_API_KEY"] = api_key
                elif provider == "openai": os.environ["OPENAI_API_KEY"] = api_key
                elif provider == "anthropic": os.environ["ANTHROPIC_API_KEY"] = api_key
                elif provider == "gemini": os.environ["GEMINI_API_KEY"] = api_key
                else: kwargs["api_key"] = api_key
            
            if base_url: kwargs["api_base"] = base_url

            try:
                response = await acompletion(**kwargs)
            except Exception as first_error:
                if "response_format" not in str(first_error).lower(): raise
                kwargs.pop("response_format", None)
                response = await acompletion(**kwargs)

            raw = (response.choices[0].message.content or "").strip()
            
            # Parsing logic
            parsed = None
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                # Fallback regex
                match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
                if match:
                    try: parsed = json.loads(match.group(0))
                    except: pass
            
            raw_sugg = []
            if isinstance(parsed, dict):
                raw_sugg = [parsed.get("option_1", ""), parsed.get("option_2", ""), parsed.get("option_3", "")]
            else:
                raw_sugg = raw.split("|")

            clean = []
            for s in raw_sugg:
                if not s: continue
                s = str(s).strip()
                s = s.replace("```", "").strip()
                # Remove quotes if wrapped
                if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
                    s = s[1:-1]
                # Remove buffer overlap if model included it
                if s.startswith(ctx.buffer):
                    s = s[len(ctx.buffer):]
                clean.append(s)
            
            suggestions = clean

        except Exception as e:
            logger.error(f"LLM Error: {e}")
            # Fallback to deterministic if LLM fails
            suggestions = self._get_deterministic_candidates(ctx)

        # Pad
        while len(suggestions) < 3: suggestions.append("")

        # 3. Rerank based on Learning (Local feedback)
        final_suggestions = self.learner.rerank(ctx.buffer, suggestions)
        
        return final_suggestions[:3]

    def log_feedback(self, buffer: str, accepted: str):
        self.learner.log_accept(buffer, accepted)
