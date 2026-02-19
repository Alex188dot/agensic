import os
import logging
import json
import re
import shutil
import threading
import platform
from pathlib import Path
from litellm import acompletion

logger = logging.getLogger("ghostshell.engine")

class Settings:
    history_lines: int = 50
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

class SuggestionEngine:
    def __init__(self):
        self.inventory = self._get_simple_inventory()
        self.vector_db = None
        self._vector_db_lock = threading.Lock()
        self._vector_db_ready = threading.Event()
        self._bootstrap_lock = threading.Lock()
        self._bootstrap_thread = None
        self._bootstrap_history_file = ""
        self._bootstrap_completed_for = ""

    def _ensure_vector_db(self):
        if self.vector_db is not None:
            return self.vector_db

        with self._vector_db_lock:
            if self.vector_db is None:
                from vector_db import CommandVectorDB
                self.vector_db = CommandVectorDB()
                self._vector_db_ready.set()
        return self.vector_db

    def _bootstrap_worker(self, history_file: str):
        try:
            logger.info("Starting vector DB bootstrap in background")
            vector_db = self._ensure_vector_db()
            if history_file:
                vector_db.initialize_from_history(history_file)
            logger.info("Background history sync complete")
        except Exception as e:
            logger.error(f"Background history sync failed: {e}")
        finally:
            with self._bootstrap_lock:
                self._bootstrap_completed_for = history_file

    def bootstrap_async(self, history_file: str):
        history_file = (history_file or "").strip()
        if not history_file:
            return

        history_file = os.path.expanduser(history_file)
        with self._bootstrap_lock:
            if (
                self._bootstrap_completed_for == history_file
                and self._vector_db_ready.is_set()
            ):
                return

            if (
                self._bootstrap_thread
                and self._bootstrap_thread.is_alive()
                and self._bootstrap_history_file == history_file
            ):
                return

            self._bootstrap_history_file = history_file
            self._bootstrap_thread = threading.Thread(
                target=self._bootstrap_worker,
                args=(history_file,),
                daemon=True,
                name="ghostshell-history-index",
            )
            self._bootstrap_thread.start()

    def get_bootstrap_status(self) -> dict:
        with self._bootstrap_lock:
            thread = self._bootstrap_thread
            history_file = self._bootstrap_history_file
            completed_for = self._bootstrap_completed_for

        running = bool(thread and thread.is_alive())
        ready = bool(
            self._vector_db_ready.is_set()
            and history_file
            and completed_for == history_file
            and not running
        )

        indexed_commands = 0
        if self.vector_db is not None and hasattr(self.vector_db, "inserted_commands"):
            try:
                indexed_commands = len(self.vector_db.inserted_commands)
            except Exception:
                indexed_commands = 0

        return {
            "running": running,
            "ready": ready,
            "history_file": history_file,
            "indexed_commands": indexed_commands,
        }

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

    def _get_vector_candidates(self, ctx: RequestContext) -> list[str]:
        """
        Get command suggestions from the vector database.
        Returns exact prefix matches using semantic similarity.
        """
        prefix = ctx.buffer.strip()
        if len(prefix) < 2:
            return []

        if ctx.history_file:
            self.bootstrap_async(ctx.history_file)

        if not self._vector_db_ready.is_set() or self.vector_db is None:
            return []

        try:
            matches = self.vector_db.get_exact_prefix_matches(prefix, topk=20)
        except Exception as e:
            logger.error(f"Vector DB lookup failed: {e}")
            return []
        
        # Return only the suffix part (what comes after the prefix)
        candidates = []
        for cmd in matches:
            if cmd.startswith(prefix) and cmd != prefix:
                suffix = cmd[len(prefix):]
                candidates.append(suffix)
        
        return candidates

    def _is_blocked_command(self, command: str) -> bool:
        if self.vector_db is not None:
            return self.vector_db.is_blocked_command(command)
        from vector_db import CommandVectorDB
        return CommandVectorDB.is_blocked_command(command)

    def _filter_blocked_candidates(self, buffer: str, candidates: list[str]) -> list[str]:
        if not candidates:
            return []

        from vector_db import CommandVectorDB

        filtered: list[str] = []
        for suffix in candidates:
            if not suffix:
                continue
            standalone_command = CommandVectorDB.normalize_command(
                CommandVectorDB.canonicalize_shell_spacing(suffix)
            )
            full_command = CommandVectorDB.normalize_command(
                CommandVectorDB.canonicalize_shell_spacing(
                    CommandVectorDB.merge_buffer_and_suffix(buffer, suffix)
                )
            )
            if not full_command and not standalone_command:
                continue
            if standalone_command and self._is_blocked_command(standalone_command):
                continue
            if full_command and self._is_blocked_command(full_command):
                continue
            filtered.append(suffix)
        return filtered

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

    @staticmethod
    def _parse_json_payload(raw: str) -> dict | None:
        parsed = None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group(0))
                except Exception:
                    parsed = None
        return parsed if isinstance(parsed, dict) else None

    @staticmethod
    def _sanitize_single_line(value: str) -> str:
        cleaned = str(value or "").replace("```", "").replace("\r", " ").replace("\n", " ").strip()
        while "  " in cleaned:
            cleaned = cleaned.replace("  ", " ")
        return cleaned

    def _collect_env_context(self, ctx: RequestContext) -> dict[str, str]:
        return {
            "os_name": platform.system() or "unknown",
            "os_version": platform.release() or "unknown",
            "shell": ctx.shell or "unknown",
            "terminal": (ctx.terminal or os.environ.get("TERM", "") or "unknown"),
            "cwd": ctx.cwd or "unknown",
            "platform": ctx.platform_name or platform.platform(),
        }

    async def get_suggestions(
        self,
        config: dict,
        ctx: RequestContext,
        allow_ai: bool = True,
    ) -> tuple[list[str], list[str], bool]:
        """
        New paradigm:
        1. Get top 20 exact prefix matches from vector DB
        2. Return first 3 as suggestions + full pool of 20
        3. Only invoke AI if ALL 20 matches are exhausted (user typed something not in history)
        
        Returns:
            tuple: (top_3_suggestions, full_pool_of_20)
        """
        # Get vector-based candidates (up to 20)
        vector_candidates = self._get_vector_candidates(ctx)
        
        # If we have candidates from history, return the top 3 + full pool
        if vector_candidates:
            reranked = vector_candidates
            if self.vector_db is not None:
                reranked = self.vector_db.rerank_candidates(ctx.buffer, vector_candidates)
            reranked = self._filter_blocked_candidates(ctx.buffer, reranked)
            suggestions = reranked[:3]
            pool = reranked[:20]  # Full pool for filtering
            
            # Pad to 3 if needed
            while len(suggestions) < 3:
                suggestions.append("")
            
            # Pad pool to 20
            while len(pool) < 20:
                pool.append("")
            
            logger.info(f"Vector DB returned {len(vector_candidates)} matches")
            return (suggestions, pool, False)

        # If no vector matches, this is a new/unknown command - invoke AI
        if not allow_ai:
            suggestions = ["", "", ""]
            pool = suggestions[:]
            while len(pool) < 20:
                pool.append("")
            return (suggestions, pool, False)

        logger.info("No vector matches found, invoking AI for new command")
        
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
            model_for_temp = model.split("/")[-1]
            temperature = 1 if model_for_temp.startswith("gpt-5") else 0.3
            kwargs = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Buffer: {ctx.buffer}"}
                ],
                "temperature": temperature,
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

            suggestions = self._filter_blocked_candidates(ctx.buffer, clean)

        except Exception as e:
            logger.error(f"LLM Error: {e}")
            suggestions = []

        # Pad to 3
        while len(suggestions) < 3:
            suggestions.append("")

        # For AI suggestions, pool is same as suggestions (no filtering needed)
        pool = suggestions[:]
        while len(pool) < 20:
            pool.append("")

        return (suggestions[:3], pool, True)

    async def get_intent_command(self, config: dict, ctx: RequestContext, intent_text: str) -> dict:
        text = (intent_text or "").strip()
        if not text:
            return {
                "status": "empty",
                "primary_command": "",
                "explanation": "Please add a terminal-related request after '#'.",
                "alternatives": [],
                "copy_block": "",
            }

        env_ctx = self._collect_env_context(ctx)
        system_prompt = (
            "You are a command-line intent translator. "
            "Answer ONLY terminal-command related requests. "
            "If the user asks for non-terminal topics, refuse briefly and suggest using '##'. "
            "Prefer safe, non-destructive commands unless destructive behavior is explicitly requested. "
            "Return valid JSON with keys: status, primary_command, explanation, alternatives. "
            "status must be one of: ok, refusal. "
            "primary_command must be a single copy-ready shell command when status=ok, otherwise empty. "
            "explanation must be brief (max 2 sentences). "
            "alternatives must be an array of up to 2 single-line commands."
        )
        user_prompt = (
            f"Environment:\n"
            f"- os_name: {env_ctx['os_name']}\n"
            f"- os_version: {env_ctx['os_version']}\n"
            f"- platform: {env_ctx['platform']}\n"
            f"- shell: {env_ctx['shell']}\n"
            f"- terminal: {env_ctx['terminal']}\n"
            f"- cwd: {env_ctx['cwd']}\n\n"
            f"User request:\n{text}"
        )

        model = config.get("model", "gpt-5-mini")
        provider = config.get("provider", "openai")
        api_key = config.get("api_key", None)
        base_url = config.get("base_url", None)
        result = {
            "status": "refusal",
            "primary_command": "",
            "explanation": "I can only help with terminal commands in '#' mode. Use '##' for general questions.",
            "alternatives": [],
            "copy_block": "",
        }

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

        try:
            kwargs = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
            }
            if api_key:
                if provider == "groq":
                    os.environ["GROQ_API_KEY"] = api_key
                elif provider == "openai":
                    os.environ["OPENAI_API_KEY"] = api_key
                elif provider == "anthropic":
                    os.environ["ANTHROPIC_API_KEY"] = api_key
                elif provider == "gemini":
                    os.environ["GEMINI_API_KEY"] = api_key
                else:
                    kwargs["api_key"] = api_key
            if base_url:
                kwargs["api_base"] = base_url

            try:
                response = await acompletion(**kwargs)
            except Exception as first_error:
                if "response_format" not in str(first_error).lower():
                    raise
                kwargs.pop("response_format", None)
                response = await acompletion(**kwargs)

            raw = (response.choices[0].message.content or "").strip()
            parsed = self._parse_json_payload(raw)
            if not parsed:
                return result

            status = str(parsed.get("status", "refusal")).strip().lower()
            primary = self._sanitize_single_line(parsed.get("primary_command", ""))
            explanation = self._sanitize_single_line(parsed.get("explanation", ""))
            alternatives = parsed.get("alternatives", [])
            if not isinstance(alternatives, list):
                alternatives = []

            safe_alternatives: list[str] = []
            for alt in alternatives:
                clean_alt = self._sanitize_single_line(alt)
                if not clean_alt:
                    continue
                if self._is_blocked_command(clean_alt):
                    continue
                if clean_alt not in safe_alternatives:
                    safe_alternatives.append(clean_alt)
                if len(safe_alternatives) >= 2:
                    break

            if primary and self._is_blocked_command(primary):
                status = "refusal"
                primary = ""
                if not explanation:
                    explanation = "I won't suggest unsafe destructive commands in '#' mode."

            if status != "ok" or not primary:
                return {
                    "status": "refusal",
                    "primary_command": "",
                    "explanation": explanation or "I can only help with terminal commands in '#' mode. Use '##' for general questions.",
                    "alternatives": [],
                    "copy_block": "",
                }

            return {
                "status": "ok",
                "primary_command": primary,
                "explanation": explanation or "Here is a command you can run.",
                "alternatives": safe_alternatives,
                "copy_block": primary,
            }
        except Exception as e:
            logger.error(f"Intent LLM Error: {e}")
            return {
                "status": "error",
                "primary_command": "",
                "explanation": "I couldn't generate a command right now. Try again.",
                "alternatives": [],
                "copy_block": "",
            }

    async def get_general_assistant_reply(self, config: dict, ctx: RequestContext, prompt_text: str) -> str:
        text = (prompt_text or "").strip()
        if not text:
            return "Please add a question after '##'."

        env_ctx = self._collect_env_context(ctx)
        user_prompt = (
            f"Environment:\n"
            f"- os_name: {env_ctx['os_name']}\n"
            f"- os_version: {env_ctx['os_version']}\n"
            f"- platform: {env_ctx['platform']}\n"
            f"- shell: {env_ctx['shell']}\n"
            f"- terminal: {env_ctx['terminal']}\n"
            f"- cwd: {env_ctx['cwd']}\n\n"
            f"User request:\n{text}"
        )

        model = config.get("model", "gpt-5-mini")
        provider = config.get("provider", "openai")
        api_key = config.get("api_key", None)
        base_url = config.get("base_url", None)

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

        try:
            kwargs = {
                "model": model,
                "messages": [
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.7,
            }
            if api_key:
                if provider == "groq":
                    os.environ["GROQ_API_KEY"] = api_key
                elif provider == "openai":
                    os.environ["OPENAI_API_KEY"] = api_key
                elif provider == "anthropic":
                    os.environ["ANTHROPIC_API_KEY"] = api_key
                elif provider == "gemini":
                    os.environ["GEMINI_API_KEY"] = api_key
                else:
                    kwargs["api_key"] = api_key
            if base_url:
                kwargs["api_base"] = base_url

            response = await acompletion(**kwargs)
            content = (response.choices[0].message.content or "").strip()
            return content or "I couldn't generate a response."
        except Exception as e:
            logger.error(f"General assistant LLM Error: {e}")
            return "I couldn't generate a response right now. Try again."

    def log_feedback(self, buffer: str, accepted: str):
        if not buffer:
            return
        try:
            vector_db = self._ensure_vector_db()
            vector_db.record_feedback(buffer, accepted)
            full_command = f"{buffer}{accepted}".replace("\n", " ").replace("\r", " ").strip()
            if full_command:
                logger.info(f"Feedback recorded for: {full_command}")
        except Exception as e:
            logger.error(f"Failed to log feedback to vector DB: {e}")
    
    def log_executed_command(self, command: str, exit_code: int | None = None, source: str = "unknown"):
        """
        Log a command that was executed by the user.
        This adds it to the vector database for future suggestions.
        """
        normalized_source = (source or "unknown").strip().lower()
        normalized_command = (command or "").strip()
        if not normalized_command:
            return

        if normalized_source == "runtime" and exit_code != 0:
            logger.debug("Skipping runtime command with non-zero exit code")
            return

        try:
            vector_db = self._ensure_vector_db()
            if vector_db.is_blocked_command(normalized_command):
                logger.debug("Skipping blocked command from runtime logging")
                return
            vector_db.insert_command(normalized_command)
        except Exception as e:
            logger.error(f"Failed to log command to vector DB: {e}")
    
    def close(self):
        """Clean up resources."""
        with self._bootstrap_lock:
            thread = self._bootstrap_thread

        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=20)
            if thread.is_alive():
                logger.warning("History bootstrap thread did not finish before shutdown")

        if self.vector_db is not None:
            self.vector_db.close()
            self.vector_db = None
            self._vector_db_ready.clear()
