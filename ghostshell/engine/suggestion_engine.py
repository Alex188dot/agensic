import os
import logging
import json
import re
import shutil
import threading
import time
import platform
import asyncio
from pathlib import Path
from litellm import acompletion
import requests
from ghostshell.config.loader import (
    DEFAULT_TIMEOUT_SECONDS,
    MAX_TIMEOUT_SECONDS,
    MIN_TIMEOUT_SECONDS,
)
from ghostshell.privacy.guard import PrivacyGuard, PrivacyGuardError
from .context import RequestContext, Settings, SystemInventory

logger = logging.getLogger("ghostshell.engine")

class SuggestionEngine:
    def __init__(self):
        self.inventory = self._get_simple_inventory()
        self.privacy_guard = PrivacyGuard(
            history_max_lines=Settings.llm_history_lines,
        )
        self.vector_db = None
        self._vector_db_lock = threading.Lock()
        self._vector_db_ready = threading.Event()
        self._bootstrap_lock = threading.Lock()
        self._bootstrap_thread = None
        self._bootstrap_history_file = ""
        self._bootstrap_completed_for = ""
        self._bootstrap_error = ""

    def _ensure_vector_db(self):
        if self.vector_db is not None:
            return self.vector_db

        with self._vector_db_lock:
            if self.vector_db is None:
                from ghostshell.vector_db import CommandVectorDB
                self.vector_db = CommandVectorDB()
                self._vector_db_ready.set()
        return self.vector_db

    def _bootstrap_worker(self, history_file: str):
        try:
            logger.info("Starting vector DB bootstrap in background")
            max_lock_retries = 20
            attempt = 0
            while True:
                try:
                    vector_db = self._ensure_vector_db()
                    break
                except Exception as lock_exc:
                    sanitized = self.privacy_guard.sanitize_for_log(str(lock_exc))
                    if "lock file" in sanitized.lower() and attempt < max_lock_retries:
                        attempt += 1
                        logger.warning(
                            "Vector DB lock busy; retrying bootstrap (%d/%d)",
                            attempt,
                            max_lock_retries,
                        )
                        time.sleep(0.25)
                        continue
                    raise
            if history_file:
                vector_db.initialize_from_history(history_file)
            logger.info("Background history sync complete")
        except Exception as e:
            sanitized = self.privacy_guard.sanitize_for_log(str(e))
            with self._bootstrap_lock:
                self._bootstrap_error = sanitized
            logger.error(
                "Background history sync failed: %s",
                sanitized,
            )
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

            self._bootstrap_error = ""
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
            bootstrap_error = self._bootstrap_error

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

        phase = "starting"
        model_download_in_progress = False
        model_download_needed = False
        error = ""
        if self.vector_db is not None and hasattr(self.vector_db, "get_init_status"):
            try:
                init_status = self.vector_db.get_init_status()
                phase = str(init_status.get("phase") or phase)
                model_download_in_progress = bool(
                    init_status.get("model_download_in_progress", False)
                )
                model_download_needed = bool(
                    init_status.get("model_download_needed", False)
                )
                error = str(init_status.get("error") or "")
            except Exception:
                pass
        else:
            try:
                from ghostshell.vector_db import get_runtime_init_status

                init_status = get_runtime_init_status()
                phase = str(init_status.get("phase") or phase)
                model_download_in_progress = bool(
                    init_status.get("model_download_in_progress", False)
                )
                model_download_needed = bool(
                    init_status.get("model_download_needed", False)
                )
                error = str(init_status.get("error") or "")
            except Exception:
                pass

        if bootstrap_error:
            phase = "error"
            error = bootstrap_error
        elif ready and phase != "error":
            phase = "ready"
            error = ""

        return {
            "running": running,
            "ready": ready,
            "history_file": history_file,
            "indexed_commands": indexed_commands,
            "phase": phase,
            "model_download_in_progress": model_download_in_progress,
            "model_download_needed": model_download_needed,
            "error": error,
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

    def _filter_blocked_full_commands(self, commands: list[str]) -> list[str]:
        filtered: list[str] = []
        for command in commands:
            normalized = (command or "").strip()
            if not normalized:
                continue
            if self._is_blocked_command(normalized):
                continue
            filtered.append(normalized)
        return filtered

    def _get_vector_candidates(self, ctx: RequestContext) -> list[dict[str, str]]:
        """
        Get command suggestions from the vector database.
        Returns structured candidates for suffix append or full replacement.
        """
        prefix = ctx.buffer.strip()
        if len(prefix) < 2:
            return []

        if ctx.history_file:
            self.bootstrap_async(ctx.history_file)

        if not self._vector_db_ready.is_set() or self.vector_db is None:
            return []

        try:
            matches = self.vector_db.get_prefix_or_semantic_matches(prefix, topk=100)
        except Exception as e:
            logger.error(
                "Vector DB lookup failed: %s",
                self.privacy_guard.sanitize_for_log(str(e)),
            )
            return []

        first_mode = matches[0].get("match_mode", "") if matches else ""
        if not matches or first_mode != "prefix":
            try:
                typo_candidate = self.vector_db.get_word_typo_candidate(ctx.buffer)
            except Exception as e:
                logger.warning(
                    "Word typo lookup failed: %s",
                    self.privacy_guard.sanitize_for_log(str(e)),
                )
                typo_candidate = None
            if typo_candidate is not None:
                corrected_prefix = (typo_candidate.get("corrected_prefix", "") or "").strip()
                if corrected_prefix and not self._is_blocked_command(corrected_prefix):
                    return [
                        {
                            "display_text": f" Did you mean: {corrected_prefix}", # extra space is needed otherwise it will be too close to the user command, DO NOT REMOVE IT
                            "accept_text": corrected_prefix,
                            "accept_mode": "replace_full",
                            "kind": "typo_recovery",
                        }
                    ]
            if not matches:
                return []

        candidates: list[dict[str, str]] = []
        if first_mode == "prefix":
            suffixes: list[str] = []
            for item in matches:
                cmd = item.get("command", "")
                if not cmd.startswith(prefix) or cmd == prefix:
                    continue
                suffixes.append(cmd[len(prefix):])
            if self.vector_db is not None and suffixes:
                suffixes = self.vector_db.rerank_candidates(ctx.buffer, suffixes)
            suffixes = self._filter_blocked_candidates(ctx.buffer, suffixes)
            for suffix in suffixes:
                candidates.append(
                    {
                        "display_text": suffix,
                        "accept_text": suffix,
                        "accept_mode": "suffix_append",
                        "kind": "normal",
                    }
                )
            return candidates

        semantic_mode = first_mode.startswith("semantic")
        full_commands = [item.get("command", "") for item in matches]
        full_commands = self._filter_blocked_full_commands(full_commands)
        for command in full_commands:
            if semantic_mode:
                display = f" Did you mean: {command}" # extra space is needed otherwise it will be too close to the user command, DO NOT REMOVE IT
                kind = "semantic_recovery"
            else:
                display = command
                kind = "normal"
            candidates.append(
                {
                    "display_text": display,
                    "accept_text": command,
                    "accept_mode": "replace_full",
                    "kind": kind,
                }
            )
        return candidates

    def _is_blocked_command(self, command: str) -> bool:
        if self.vector_db is not None:
            return self.vector_db.is_blocked_command(command)
        from ghostshell.vector_db import CommandVectorDB
        return CommandVectorDB.is_blocked_command(command)

    def _filter_blocked_candidates(self, buffer: str, candidates: list[str]) -> list[str]:
        if not candidates:
            return []

        from ghostshell.vector_db import CommandVectorDB

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

        sanitized_history, _ = self.privacy_guard.sanitize_history_lines(history)
        shell_value = self.privacy_guard.sanitize_text(request.shell, context="prompt_shell").text
        cwd_value = self.privacy_guard.sanitize_text(request.cwd, context="prompt_cwd").text
        buffer_value = self.privacy_guard.sanitize_text(request.buffer, context="prompt_buffer").text

        sanitized_items: list[str] = []
        for item in cwd_items:
            clean_item = self.privacy_guard.sanitize_text(item, context="prompt_cwd_item").text
            if clean_item:
                sanitized_items.append(clean_item)

        lines: list[str] = [
            f"Shell: {shell_value}",
            f"CWD: {cwd_value}",
            f"Buffer: {buffer_value}",
            "",
            "Relevant Executables:",
            ", ".join(self.inventory.commands[:20]) if self.inventory.commands else "(none)",
            "",
            "Recent History:",
            "\n".join(sanitized_history) if sanitized_history else "(none)",
            "",
            "Files in CWD:",
            ", ".join(sanitized_items) if sanitized_items else "(none)",
        ]
        context = "\n".join(lines)
        return self.privacy_guard.sanitize_text(context, context="prompt_context").text

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
        raw = {
            "os_name": platform.system() or "unknown",
            "os_version": platform.release() or "unknown",
            "shell": ctx.shell or "unknown",
            "terminal": (ctx.terminal or os.environ.get("TERM", "") or "unknown"),
            "cwd": ctx.cwd or "unknown",
            "platform": ctx.platform_name or platform.platform(),
        }
        sanitized: dict[str, str] = {}
        for key, value in raw.items():
            clean_value = self.privacy_guard.sanitize_text(str(value), context=f"env_{key}").text
            sanitized[key] = clean_value[:200]
        return sanitized

    @staticmethod
    def _parse_optional_dict(value: object, field_name: str) -> dict | None:
        if value is None:
            return None
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            try:
                parsed = json.loads(stripped)
            except Exception:
                logger.warning("Ignoring invalid JSON for %s", field_name)
                return None
            if isinstance(parsed, dict):
                return parsed
        logger.warning("Ignoring non-dict value for %s", field_name)
        return None

    @staticmethod
    def _parse_optional_float(value: object, field_name: str) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            logger.warning("Ignoring invalid numeric value for %s", field_name)
            return None

    @staticmethod
    def _strip_thinking_artifacts(raw: str) -> str:
        text = str(raw or "")
        closing_tag_matches = list(re.finditer(r"</think\s*>", text, flags=re.IGNORECASE))
        if closing_tag_matches:
            text = text[closing_tag_matches[-1].end():]
        text = re.sub(r"<think\b[^>]*>.*?</think\s*>", "", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"</?think\b[^>]*>", "", text, flags=re.IGNORECASE)
        return text.strip()

    def _build_llm_kwargs(
        self,
        config: dict,
        messages: list[dict],
        temperature: float,
        include_json_response_format: bool = False,
    ) -> dict:
        provider = str(config.get("provider", "openai") or "openai").strip().lower()
        model = str(config.get("model", "gpt-5-mini") or "gpt-5-mini").strip()
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
        elif provider == "lm_studio":
            if not model.startswith("lm_studio/"):
                model = f"lm_studio/{model}"
            if not base_url:
                base_url = "http://localhost:1234/v1"
        elif provider == "anthropic":
            if not model.startswith("claude"):
                model = "claude-3-5-sonnet-20241022"
        elif provider == "gemini":
            if not model.startswith("gemini/"):
                model = f"gemini/{model}"
        elif provider == "custom":
            # OpenAI-compatible custom endpoints typically use openai/<model>.
            if "/" not in model:
                model = f"openai/{model}"

        kwargs = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if include_json_response_format:
            kwargs["response_format"] = {"type": "json_object"}

        if api_key:
            if provider == "groq":
                os.environ["GROQ_API_KEY"] = str(api_key)
            elif provider == "openai":
                os.environ["OPENAI_API_KEY"] = str(api_key)
            elif provider == "anthropic":
                os.environ["ANTHROPIC_API_KEY"] = str(api_key)
            elif provider == "gemini":
                os.environ["GEMINI_API_KEY"] = str(api_key)
            else:
                kwargs["api_key"] = api_key

        if base_url:
            kwargs["api_base"] = str(base_url).strip()

        headers = self._parse_optional_dict(config.get("headers"), "headers")
        if headers:
            kwargs["headers"] = headers

        extra_body = self._parse_optional_dict(config.get("extra_body"), "extra_body")
        if extra_body:
            kwargs["extra_body"] = extra_body

        timeout = self._parse_optional_float(config.get("timeout"), "timeout")
        if timeout is None:
            timeout = DEFAULT_TIMEOUT_SECONDS
        timeout = max(MIN_TIMEOUT_SECONDS, min(MAX_TIMEOUT_SECONDS, float(timeout)))
        kwargs["timeout"] = timeout

        api_version = config.get("api_version", None)
        if api_version:
            kwargs["api_version"] = str(api_version).strip()

        if provider == "ollama":
            kwargs["think"] = False

        return kwargs

    @staticmethod
    def _is_provider(config: dict, provider_name: str) -> bool:
        return str(config.get("provider", "openai") or "openai").strip().lower() == provider_name

    def _build_lm_studio_rest_endpoint(self, config: dict) -> str:
        base_url = str(config.get("base_url", "") or "").strip()
        if not base_url:
            return "http://localhost:1234/api/v1/chat"

        normalized = base_url.rstrip("/")
        for suffix in ("/v1/chat/completions", "/v1/responses", "/api/v1/chat", "/api/v1", "/v1"):
            if normalized.endswith(suffix):
                normalized = normalized[: -len(suffix)]
                break
        return f"{normalized}/api/v1/chat"

    async def _privacy_checked_lm_studio_chat(
        self,
        config: dict,
        messages: list[dict],
        temperature: float,
        request_type: str,
    ) -> tuple[str, dict[str, object]]:
        sanitized_messages, redactions, flags = self._sanitize_messages_with_stats(messages)
        try:
            for msg in sanitized_messages:
                if isinstance(msg, dict):
                    content = msg.get("content")
                    if isinstance(content, str):
                        self.privacy_guard.assert_safe_or_raise(content)
        except Exception as exc:
            raise PrivacyGuardError(f"Sanitization failed for {request_type}") from exc

        system_prompt = ""
        text_inputs: list[str] = []
        for msg in sanitized_messages:
            if not isinstance(msg, dict):
                continue
            role = str(msg.get("role", "")).strip().lower()
            content = msg.get("content")
            if not isinstance(content, str):
                continue
            if role == "system":
                if system_prompt:
                    system_prompt = f"{system_prompt}\n{content}"
                else:
                    system_prompt = content
            elif role in {"user", "assistant"}:
                text_inputs.append(content)

        model = str(config.get("model", "local-model") or "local-model").strip()
        if model.startswith("lm_studio/"):
            model = model.split("/", 1)[1]

        payload: dict[str, object] = {
            "model": model,
            "input": "\n".join(text_inputs).strip() if text_inputs else "",
            "temperature": temperature,
            "stream": False,
            "reasoning": "off",
        }
        if system_prompt:
            payload["system_prompt"] = system_prompt

        headers = {"Content-Type": "application/json"}
        api_key = str(config.get("api_key", "") or "").strip()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        extra_headers = self._parse_optional_dict(config.get("headers"), "headers")
        if extra_headers:
            for key, value in extra_headers.items():
                headers[str(key)] = str(value)

        timeout = self._parse_optional_float(config.get("timeout"), "timeout")
        if timeout is None:
            timeout = DEFAULT_TIMEOUT_SECONDS
        request_timeout = max(MIN_TIMEOUT_SECONDS, min(MAX_TIMEOUT_SECONDS, float(timeout)))
        endpoint = self._build_lm_studio_rest_endpoint(config)

        def _do_request() -> requests.Response:
            return requests.post(endpoint, headers=headers, json=payload, timeout=request_timeout)

        response = await asyncio.to_thread(_do_request)
        try:
            response.raise_for_status()
        except requests.RequestException as exc:
            error_text = self.privacy_guard.sanitize_for_log(response.text[:400])
            raise Exception(f"LM Studio REST error: {error_text}") from exc

        try:
            data = response.json()
        except ValueError as exc:
            text = self.privacy_guard.sanitize_for_log(response.text[:400])
            raise Exception(f"LM Studio REST returned non-JSON response: {text}") from exc

        output = data.get("output", [])
        content_parts: list[str] = []
        if isinstance(output, list):
            for item in output:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "message" and isinstance(item.get("content"), str):
                    content_parts.append(item["content"])
        content = "\n".join([part for part in content_parts if part]).strip()

        # Fallbacks in case the server returns an OpenAI-like shape.
        if not content and isinstance(data.get("choices"), list) and data["choices"]:
            first = data["choices"][0]
            if isinstance(first, dict) and isinstance(first.get("message"), dict):
                maybe = first["message"].get("content")
                if isinstance(maybe, str):
                    content = maybe.strip()

        return content, {"redactions": redactions, "flags": flags}

    def _sanitize_messages_with_stats(self, messages: list[dict]) -> tuple[list[dict], int, list[str]]:
        sanitized_messages = self.privacy_guard.sanitize_messages(messages)
        total_redactions = 0
        flags: set[str] = set()
        for idx, msg in enumerate(messages or []):
            if not isinstance(msg, dict):
                continue
            clean_msg = sanitized_messages[idx] if idx < len(sanitized_messages) else dict(msg)
            content = msg.get("content")
            if isinstance(content, str):
                result = self.privacy_guard.sanitize_text(content, context="message")
                clean_msg["content"] = result.text
                total_redactions += result.redaction_count
                flags.update(result.flags)
            elif isinstance(content, list):
                clean_parts = []
                for part in content:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        part_copy = dict(part)
                        result = self.privacy_guard.sanitize_text(part_copy["text"], context="message")
                        part_copy["text"] = result.text
                        total_redactions += result.redaction_count
                        flags.update(result.flags)
                        clean_parts.append(part_copy)
                    else:
                        clean_parts.append(part)
                clean_msg["content"] = clean_parts
            if idx < len(sanitized_messages):
                sanitized_messages[idx] = clean_msg
        return (sanitized_messages, total_redactions, sorted(flags))

    async def _privacy_checked_acompletion(
        self,
        kwargs: dict,
        request_type: str,
    ) -> tuple[object, dict[str, object]]:
        safe_kwargs = dict(kwargs)
        try:
            messages = safe_kwargs.get("messages", [])
            sanitized_messages, redactions, flags = self._sanitize_messages_with_stats(messages)
            for msg in sanitized_messages:
                if isinstance(msg, dict):
                    content = msg.get("content")
                    if isinstance(content, str):
                        self.privacy_guard.assert_safe_or_raise(content)
                    elif isinstance(content, list):
                        for part in content:
                            if isinstance(part, dict) and isinstance(part.get("text"), str):
                                self.privacy_guard.assert_safe_or_raise(part["text"])
            safe_kwargs["messages"] = sanitized_messages
        except Exception as exc:
            raise PrivacyGuardError(f"Sanitization failed for {request_type}") from exc

        try:
            response = await acompletion(**safe_kwargs)
            return (response, {"redactions": redactions, "flags": flags})
        except Exception as first_error:
            if "response_format" not in str(first_error).lower() or "response_format" not in safe_kwargs:
                raise
            retry_kwargs = dict(safe_kwargs)
            retry_kwargs.pop("response_format", None)
            response = await acompletion(**retry_kwargs)
            return (response, {"redactions": redactions, "flags": flags})

    async def get_suggestions(
        self,
        config: dict,
        ctx: RequestContext,
        allow_ai: bool = True,
    ) -> tuple[list[str], list[str], list[dict[str, str]], bool]:
        """
        New paradigm:
        1. Get top 20 exact prefix matches from vector DB
        2. Return first 3 as suggestions + full pool of 20
        3. Only invoke AI if ALL 20 matches are exhausted (user typed something not in history)
        
        Returns:
            tuple: (top_3_suggestions, full_pool_of_20)
        """
        def _pad_pool(values: list[str], size: int = 20) -> list[str]:
            pool = values[:size]
            while len(pool) < size:
                pool.append("")
            return pool

        # Get vector-based candidates (up to 20)
        vector_candidates = self._get_vector_candidates(ctx)

        # If we have candidates from history, return the top 3 + full pool
        if vector_candidates:
            pool_meta = vector_candidates[:20]
            pool = [entry.get("accept_text", "") for entry in pool_meta]
            suggestions = pool[:3]

            # Pad to 3 if needed
            while len(suggestions) < 3:
                suggestions.append("")

            pool = _pad_pool(pool, size=20)
            logger.info(f"Vector DB returned {len(vector_candidates)} matches")
            return (suggestions, pool, pool_meta, False)

        # If no vector matches, this is a new/unknown command - invoke AI
        if not allow_ai:
            suggestions = ["", "", ""]
            pool = _pad_pool(suggestions, size=20)
            return (suggestions, pool, [], False)

        logger.info("No vector matches found, invoking AI for new command")

        context_str = self.build_prompt_context(ctx)
        buffer_for_prompt = self.privacy_guard.sanitize_text(ctx.buffer, context="prompt_buffer").text

        system_prompt = (
            "You are a CLI autocomplete engine. "
            "Context provided below (History, CWD). "
            "Provide 3 completions for the user's buffer. "
            "JSON output keys: option_1, option_2, option_3. "
            "Values must be full command completions (suffixes or full replacements logic handled by client, but prefer full logical command). "
            "Do not output explanations."
            f"\n--- CONTEXT ---\n{context_str}"
        )

        suggestions = ["", "", ""]
        privacy_blocked = False

        try:
            raw_model = str(config.get("model", "gpt-5-mini") or "gpt-5-mini")
            model_for_temp = raw_model.split("/")[-1]
            temperature = 1 if model_for_temp.startswith("gpt-5") else 0.3
            request_messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Buffer: {buffer_for_prompt}"},
            ]
            if self._is_provider(config, "lm_studio"):
                raw, privacy_meta = await self._privacy_checked_lm_studio_chat(
                    config,
                    request_messages,
                    temperature=temperature,
                    request_type="suggestions",
                )
            else:
                kwargs = self._build_llm_kwargs(
                    config,
                    request_messages,
                    temperature=temperature,
                    include_json_response_format=True,
                )
                response, privacy_meta = await self._privacy_checked_acompletion(
                    kwargs,
                    request_type="suggestions",
                )
                raw = (response.choices[0].message.content or "").strip()
            if self._is_provider(config, "ollama"):
                raw = self._strip_thinking_artifacts(raw)
            logger.info(
                "LLM request [suggestions] sanitized redactions=%s flags=%s",
                privacy_meta.get("redactions", 0),
                ",".join(privacy_meta.get("flags", [])),
            )
            
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

        except PrivacyGuardError as e:
            logger.warning(
                "LLM request [suggestions] blocked by privacy guard: %s",
                self.privacy_guard.sanitize_for_log(str(e)),
            )
            privacy_blocked = True
            suggestions = []
        except Exception as e:
            logger.error(
                "LLM Error: %s",
                self.privacy_guard.sanitize_for_log(str(e)),
            )
            suggestions = []

        # Pad to 3
        while len(suggestions) < 3:
            suggestions.append("")

        # For AI suggestions, pool is same as suggestions (no filtering needed)
        pool = _pad_pool(suggestions, size=20)
        pool_meta: list[dict[str, str]] = []
        for suffix in suggestions:
            if not suffix:
                continue
            pool_meta.append(
                {
                    "display_text": suffix,
                    "accept_text": suffix,
                    "accept_mode": "suffix_append",
                    "kind": "normal",
                }
            )

        return (suggestions[:3], pool, pool_meta, not privacy_blocked)

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
        safe_user_text = self.privacy_guard.sanitize_text(text, context="intent_user").text
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
            f"User request:\n{safe_user_text}"
        )

        result = {
            "status": "refusal",
            "primary_command": "",
            "explanation": "I can only help with terminal commands in '#' mode. Use '##' for general questions.",
            "alternatives": [],
            "copy_block": "",
        }

        try:
            request_messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
            if self._is_provider(config, "lm_studio"):
                raw, privacy_meta = await self._privacy_checked_lm_studio_chat(
                    config,
                    request_messages,
                    temperature=0.2,
                    request_type="intent",
                )
            else:
                kwargs = self._build_llm_kwargs(
                    config,
                    request_messages,
                    temperature=0.2,
                    include_json_response_format=True,
                )
                response, privacy_meta = await self._privacy_checked_acompletion(
                    kwargs,
                    request_type="intent",
                )
                raw = (response.choices[0].message.content or "").strip()
            if self._is_provider(config, "ollama"):
                raw = self._strip_thinking_artifacts(raw)
            logger.info(
                "LLM request [intent] sanitized redactions=%s flags=%s",
                privacy_meta.get("redactions", 0),
                ",".join(privacy_meta.get("flags", [])),
            )
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
        except PrivacyGuardError as e:
            logger.warning(
                "LLM request [intent] blocked by privacy guard: %s",
                self.privacy_guard.sanitize_for_log(str(e)),
            )
            return {
                "status": "error",
                "primary_command": "",
                "explanation": "Request blocked by privacy guard. Try a less sensitive prompt.",
                "alternatives": [],
                "copy_block": "",
            }
        except Exception as e:
            logger.error(
                "Intent LLM Error: %s",
                self.privacy_guard.sanitize_for_log(str(e)),
            )
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
        safe_text = self.privacy_guard.sanitize_text(text, context="assist_user").text
        user_prompt = (
            f"Environment:\n"
            f"- os_name: {env_ctx['os_name']}\n"
            f"- os_version: {env_ctx['os_version']}\n"
            f"- platform: {env_ctx['platform']}\n"
            f"- shell: {env_ctx['shell']}\n"
            f"- terminal: {env_ctx['terminal']}\n"
            f"- cwd: {env_ctx['cwd']}\n\n"
            f"User request:\n{safe_text}"
        )

        try:
            request_messages = [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": user_prompt},
            ]
            if self._is_provider(config, "lm_studio"):
                content, privacy_meta = await self._privacy_checked_lm_studio_chat(
                    config,
                    request_messages,
                    temperature=0.7,
                    request_type="assist",
                )
            else:
                kwargs = self._build_llm_kwargs(
                    config,
                    request_messages,
                    temperature=0.7,
                )
                response, privacy_meta = await self._privacy_checked_acompletion(
                    kwargs,
                    request_type="assist",
                )
                content = (response.choices[0].message.content or "").strip()
            if self._is_provider(config, "ollama"):
                content = self._strip_thinking_artifacts(content)
            logger.info(
                "LLM request [assist] sanitized redactions=%s flags=%s",
                privacy_meta.get("redactions", 0),
                ",".join(privacy_meta.get("flags", [])),
            )
            return content or "I couldn't generate a response."
        except PrivacyGuardError as e:
            logger.warning(
                "LLM request [assist] blocked by privacy guard: %s",
                self.privacy_guard.sanitize_for_log(str(e)),
            )
            return "Request blocked by privacy guard. Try a less sensitive prompt."
        except Exception as e:
            logger.error(
                "General assistant LLM Error: %s",
                self.privacy_guard.sanitize_for_log(str(e)),
            )
            return "I couldn't generate a response right now. Try again."

    def log_feedback(self, buffer: str, accepted: str, accept_mode: str = "suffix_append"):
        if not buffer:
            return
        mode = (accept_mode or "suffix_append").strip().lower()
        try:
            vector_db = self._ensure_vector_db()
            vector_db.record_feedback(buffer, accepted, mode)
            if mode == "replace_full":
                full_command = (accepted or "").replace("\n", " ").replace("\r", " ").strip()
            else:
                full_command = f"{buffer}{accepted}".replace("\n", " ").replace("\r", " ").strip()
            if full_command:
                sanitized = self.privacy_guard.sanitize_text(full_command, context="log_feedback")
                logger.info(
                    "Feedback recorded for: %s (redactions=%d)",
                    sanitized.text,
                    sanitized.redaction_count,
                )
        except Exception as e:
            logger.error(
                "Failed to log feedback to vector DB: %s",
                self.privacy_guard.sanitize_for_log(str(e)),
            )
    
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
            logger.error(
                "Failed to log command to vector DB: %s",
                self.privacy_guard.sanitize_for_log(str(e)),
            )
    
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
