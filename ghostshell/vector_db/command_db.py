import gc
import hashlib
import json
import logging
import math
import os
import shlex
import shutil
import subprocess
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import transformers
import zvec
from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein
from sentence_transformers import SentenceTransformer

# Tell HuggingFace to avoid implicit network checks by default.
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"

transformers.logging.set_verbosity_error()
logging.getLogger("sentence_transformers").setLevel(logging.ERROR)

logger = logging.getLogger("ghostshell.vector_db")

_RUNTIME_INIT_STATUS_LOCK = threading.Lock()
_RUNTIME_INIT_STATUS: Dict[str, object] = {
    "phase": "starting",
    "model_download_in_progress": False,
    "model_download_needed": False,
    "error": "",
}


def _update_runtime_init_status(**kwargs):
    with _RUNTIME_INIT_STATUS_LOCK:
        for key, value in kwargs.items():
            _RUNTIME_INIT_STATUS[key] = value


def get_runtime_init_status() -> Dict[str, object]:
    with _RUNTIME_INIT_STATUS_LOCK:
        return dict(_RUNTIME_INIT_STATUS)


class CommandVectorDB:
    """
    Vector database for storing and retrieving shell commands using semantic search.

    This implementation assumes a fresh v2 schema and does not run migrations.
    """

    SCORE_BASE_RANK = 0.12
    SCORE_ALPHA = 1.10
    SCORE_BETA = 0.35
    SCORE_MANUAL = 0.55
    SCORE_ASSIST = 0.18
    SCORE_ACCEPT = 0.20
    SCORE_HISTORY = 0.08
    SCORE_MANUAL_RECENCY = 0.30
    MANUAL_RECENCY_DECAY_HOURS = 48.0
    HISTORY_COUNT_CAP = 200
    MANUAL_SIGNAL_WINDOW_DAYS = 30
    ENABLE_REPO_CONFIDENCE_TIERS = True
    REPO_CONF_MEDIUM_MIN_ACCEPTS = 3
    REPO_CONF_MEDIUM_MIN_DISTINCT = 2
    REPO_CONF_HIGH_MIN_ACCEPTS = 6
    REPO_EXECUTE_CAP = 3
    HELP_DAMPENING_PENALTY = 0.25
    BLOCKED_EXECUTABLES = {
        "rm",
        "dd",
        "wipefs",
        "shred",
        "fdisk",
        "sfdisk",
        "cfdisk",
        "parted",
        "diskutil",
        "mkfs",
        "newfs",
        "mdadm",
        "zpool",
        "lvremove",
        "vgremove",
        "pvremove",
        "cryptsetup",
        "passwd",
        "chpasswd",
        "usermod",
        "userdel",
        "groupdel",
    }
    BLOCKED_EXECUTABLE_PREFIXES = {"mkfs.", "mkfs_", "newfs"}
    GIT_GLOBAL_OPTIONS_WITH_VALUE = {
        "-C",
        "-c",
        "--exec-path",
        "--git-dir",
        "--work-tree",
        "--namespace",
        "--super-prefix",
        "--config-env",
    }
    PREFIX_SCAN_LIMIT = 2000
    SEMANTIC_VECTOR_TOPN = 80
    EXEC_FUZZ_SCOPE_THRESHOLD = 84.0
    SEMANTIC_MIN_SCORE = 55.0
    WORD_TYPO_ROOT_CONTEXT = "__root__"

    def __init__(
        self,
        db_path: str = None,
        model_name: str = "all-MiniLM-L6-v2",
        state_store=None,
    ):
        _update_runtime_init_status(
            phase="starting",
            model_download_in_progress=False,
            model_download_needed=False,
            error="",
        )
        if db_path is None:
            db_path = os.path.expanduser("~/.ghostshell/zvec_commands")

        self.db_path = os.path.expanduser(db_path)
        self.feedback_db_path = os.path.join(
            os.path.dirname(self.db_path),
            "zvec_feedback_stats",
        )
        self.state_file = os.path.join(
            os.path.dirname(self.db_path),
            "last_indexed_line",
        )
        self.removed_commands_path = os.path.join(
            os.path.dirname(self.db_path),
            "removed_commands.json",
        )
        self.model_name = model_name
        self.state_store = state_store
        self.dimensions = 384
        self._io_lock = threading.RLock()
        self._is_closed = False
        self._status_lock = threading.Lock()
        self._repo_identity_cache: Dict[str, Tuple[float, str]] = {}
        self._init_phase = "starting"
        self._model_download_in_progress = False
        self._model_download_needed = False
        self._init_error = ""

        self.model = self._load_model()
        self._set_init_phase("initializing_db")
        self.collection = self._init_command_collection(self.db_path)
        self.feedback_collection = None
        if self.state_store is None:
            self.feedback_collection = self._init_feedback_collection(self.feedback_db_path)
        self.command_cache: set[str] = set()
        self.command_cache_by_exec: Dict[str, set[str]] = defaultdict(set)
        self.token_candidates_by_context: Dict[str, set[str]] = defaultdict(set)
        self.global_token_candidates: set[str] = set()
        self.removed_commands: set[str] = self._load_removed_commands()
        self.inserted_commands = self._load_existing_commands(limit=1024)
        self._register_commands(self.inserted_commands)

    def _set_init_phase(self, phase: str):
        with self._status_lock:
            self._init_phase = phase
        _update_runtime_init_status(phase=phase)

    def _set_init_error(self, error: str):
        clean_error = str(error or "").strip()
        with self._status_lock:
            self._init_phase = "error"
            self._init_error = clean_error
            self._model_download_in_progress = False
        _update_runtime_init_status(
            phase="error",
            error=clean_error,
            model_download_in_progress=False,
        )

    def get_init_status(self) -> Dict[str, object]:
        with self._status_lock:
            return {
                "phase": self._init_phase,
                "model_download_in_progress": bool(self._model_download_in_progress),
                "model_download_needed": bool(self._model_download_needed),
                "error": self._init_error,
            }

    def _load_removed_commands(self) -> set[str]:
        if self.state_store is not None:
            try:
                return set(self.state_store.list_removed_commands())
            except Exception as exc:
                logger.warning(f"Could not read removed commands from SQLite: {exc}")
                return set()
        if not os.path.exists(self.removed_commands_path):
            return set()
        try:
            with open(self.removed_commands_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            if not isinstance(payload, list):
                return set()
            clean: set[str] = set()
            for value in payload:
                normalized = self.normalize_command(str(value))
                if normalized:
                    clean.add(normalized)
            return clean
        except Exception as exc:
            logger.warning(f"Could not read removed commands file: {exc}")
            return set()

    def _save_removed_commands(self):
        if self.state_store is not None:
            return
        try:
            os.makedirs(os.path.dirname(self.removed_commands_path), exist_ok=True)
            tmp_path = f"{self.removed_commands_path}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(sorted(self.removed_commands), f, indent=2)
            os.replace(tmp_path, self.removed_commands_path)
        except Exception as exc:
            logger.warning(f"Could not save removed commands file: {exc}")

    def is_removed_command(self, command: str) -> bool:
        normalized = self.normalize_command(command)
        if not normalized:
            return False
        return normalized in self.removed_commands

    def mark_removed_commands(self, commands: List[str]) -> int:
        if not commands:
            return 0
        if self.state_store is not None:
            clean = [self.normalize_command(raw) for raw in commands]
            clean = [value for value in clean if value]
            added = int(self.state_store.mark_removed_commands(clean) or 0)
            self.removed_commands = self._load_removed_commands()
            return added
        added = 0
        with self._io_lock:
            for raw in commands:
                normalized = self.normalize_command(raw)
                if not normalized:
                    continue
                if normalized in self.removed_commands:
                    continue
                self.removed_commands.add(normalized)
                added += 1
            if added:
                self._save_removed_commands()
        return added

    def unmark_removed_commands(self, commands: List[str]) -> int:
        if not commands:
            return 0
        if self.state_store is not None:
            clean = [self.normalize_command(raw) for raw in commands]
            clean = [value for value in clean if value]
            removed = int(self.state_store.unmark_removed_commands(clean) or 0)
            self.removed_commands = self._load_removed_commands()
            return removed
        removed = 0
        with self._io_lock:
            for raw in commands:
                normalized = self.normalize_command(raw)
                if not normalized:
                    continue
                if normalized not in self.removed_commands:
                    continue
                self.removed_commands.remove(normalized)
                removed += 1
            if removed:
                self._save_removed_commands()
        return removed

    @staticmethod
    def normalize_command(command: str) -> str:
        return (command or "").strip()

    @staticmethod
    def _prefix_exec_key(command: str) -> str:
        tokens = CommandVectorDB.tokenize_command(command)
        executable = CommandVectorDB.extract_executable(tokens)
        if not executable:
            return ""
        return os.path.basename(executable).strip().lower()

    def _register_commands(self, commands: List[str] | set[str]):
        if not commands:
            return
        for raw in commands:
            normalized = self.normalize_command(raw)
            if not normalized:
                continue
            if self.is_blocked_command(normalized):
                continue
            if self.is_removed_command(normalized):
                continue
            self.command_cache.add(normalized)
            exec_key = self._prefix_exec_key(normalized)
            if exec_key:
                self.command_cache_by_exec[exec_key].add(normalized)
            self._register_command_tokens(normalized)

    def _unregister_commands(self, commands: List[str]):
        if not commands:
            return
        for raw in commands:
            normalized = self.normalize_command(raw)
            if not normalized:
                continue
            self.command_cache.discard(normalized)
            self.inserted_commands.discard(normalized)
            exec_key = self._prefix_exec_key(normalized)
            if exec_key in self.command_cache_by_exec:
                self.command_cache_by_exec[exec_key].discard(normalized)
                if not self.command_cache_by_exec[exec_key]:
                    self.command_cache_by_exec.pop(exec_key, None)

    def _rebuild_token_indexes(self):
        self.token_candidates_by_context.clear()
        self.global_token_candidates.clear()
        for command in self.command_cache:
            self._register_command_tokens(command)

    def _get_lexical_prefix_matches(self, prefix: str, topk: int) -> List[str]:
        normalized_prefix = self.normalize_command(prefix)
        if not normalized_prefix:
            return []

        exec_key = self._prefix_exec_key(normalized_prefix)
        source = self.command_cache
        if exec_key and exec_key in self.command_cache_by_exec:
            source = self.command_cache_by_exec[exec_key]

        # Bound scan cost on very large command sets.
        if len(source) > self.PREFIX_SCAN_LIMIT:
            base_candidates = sorted(source)[: self.PREFIX_SCAN_LIMIT]
        else:
            base_candidates = sorted(source)

        matches = [cmd for cmd in base_candidates if cmd.startswith(normalized_prefix)]
        return matches[:topk]

    @staticmethod
    def _normalize_for_fuzzy(text: str) -> str:
        normalized = os.path.basename((text or "").strip()).lower()
        for sep in ("-", "_", "/", "."):
            normalized = normalized.replace(sep, " ")
        return " ".join(normalized.split())

    @staticmethod
    def _normalize_word_token(token: str) -> str:
        return (token or "").strip().lower()

    @staticmethod
    def _is_shell_operator_token(token: str) -> bool:
        return (token or "").strip() in {
            "|",
            "||",
            "&",
            "&&",
            ";",
            "(",
            ")",
            "{",
            "}",
            "<",
            ">",
            "<<",
            ">>",
            "<<<",
            ">&",
            "<&",
            "2>",
            "1>",
            "2>>",
            "1>>",
            "&>",
            "2>&1",
        }

    @classmethod
    def _looks_path_like_token(cls, token: str) -> bool:
        value = (token or "").strip()
        if not value:
            return False
        if value in {".", ".."}:
            return True
        if value.startswith(("~/", "./", "../")):
            return True
        if "/" in value:
            return True
        if value.startswith("~") and len(value) > 1:
            return True
        if "." in value and not value.startswith("-"):
            return True
        return False

    @classmethod
    def _should_skip_word_typo_token(cls, token: str) -> bool:
        value = (token or "").strip()
        if not value:
            return True
        if cls._is_shell_operator_token(value):
            return True
        if cls._looks_path_like_token(value):
            return True
        if value.startswith(("$(", "${", "`")):
            return True
        return False

    @classmethod
    def _word_typo_context_keys(cls, prior_tokens: List[str]) -> List[str]:
        if not prior_tokens:
            return [cls.WORD_TYPO_ROOT_CONTEXT]
        keys: List[str] = []
        last = prior_tokens[-1]
        if last:
            keys.append(f"1:{last}")
        if len(prior_tokens) >= 2:
            two = f"{prior_tokens[-2]} {prior_tokens[-1]}".strip()
            if two:
                keys.insert(0, f"2:{two}")
        return keys

    def _register_command_tokens(self, command: str):
        tokens = self.tokenize_command(command)
        if not tokens:
            return

        prior_tokens: List[str] = []
        for raw in tokens:
            token = self._normalize_word_token(raw)
            if not token:
                continue
            if self._is_shell_operator_token(token):
                prior_tokens = []
                continue
            if self._should_skip_word_typo_token(token):
                prior_tokens.append(token)
                if len(prior_tokens) > 6:
                    prior_tokens = prior_tokens[-6:]
                continue

            for key in self._word_typo_context_keys(prior_tokens):
                self.token_candidates_by_context[key].add(token)
            self.global_token_candidates.add(token)
            prior_tokens.append(token)
            if len(prior_tokens) > 6:
                prior_tokens = prior_tokens[-6:]

    @classmethod
    def _word_typo_threshold(cls, token: str) -> float:
        compact = cls._normalize_for_fuzzy(token).replace(" ", "")
        length = len(compact) if compact else len((token or "").strip())
        if length <= 2:
            return 88.0
        if length == 3:
            return 66.0
        if length == 4:
            return 74.0
        if length <= 6:
            return 82.0
        return 80.0

    @classmethod
    def _word_typo_max_distance(cls, token: str) -> int:
        compact = cls._normalize_for_fuzzy(token).replace(" ", "")
        length = len(compact) if compact else len((token or "").strip())
        if length <= 2:
            return 1
        if length <= 4:
            return 2
        return 2

    def _get_contextual_token_candidates(self, prior_tokens: List[str]) -> set[str]:
        candidates: set[str] = set()
        for key in self._word_typo_context_keys(prior_tokens):
            candidates.update(self.token_candidates_by_context.get(key, set()))
        return candidates

    @classmethod
    def _best_word_typo_match(cls, token: str, candidates: set[str]) -> str:
        typed_word = cls._normalize_word_token(token)
        if not typed_word or not candidates:
            return ""

        typed_fuzzy = cls._normalize_for_fuzzy(typed_word)
        if not typed_fuzzy:
            return ""

        threshold = cls._word_typo_threshold(typed_word)
        best_token = ""
        best_score = 0.0

        for candidate in sorted(candidates):
            normalized_candidate = cls._normalize_word_token(candidate)
            if not normalized_candidate or normalized_candidate == typed_word:
                continue
            candidate_fuzzy = cls._normalize_for_fuzzy(normalized_candidate)
            if not candidate_fuzzy:
                continue

            if not typed_fuzzy or not candidate_fuzzy:
                continue

            if typed_fuzzy[0] != candidate_fuzzy[0]:
                continue

            length_delta = abs(len(candidate_fuzzy) - len(typed_fuzzy))
            if length_delta > 2:
                continue

            edit_distance = Levenshtein.distance(typed_fuzzy, candidate_fuzzy)
            if edit_distance > cls._word_typo_max_distance(typed_word):
                continue

            quick = float(fuzz.QRatio(typed_fuzzy, candidate_fuzzy))
            token_sorted = float(fuzz.token_sort_ratio(typed_fuzzy, candidate_fuzzy))
            score = (0.7 * quick) + (0.3 * token_sorted)

            if score > best_score:
                best_score = score
                best_token = normalized_candidate

        if best_score < threshold:
            return ""
        return best_token

    def get_word_typo_candidate(self, buffer: str) -> Dict[str, str] | None:
        normalized_buffer = self.normalize_command(buffer)
        if len(normalized_buffer) < 2:
            return None

        tokens = self.tokenize_command(normalized_buffer)
        if not tokens:
            return None

        corrected_tokens: List[str] = []
        prior_tokens: List[str] = []
        changed = False

        for raw in tokens:
            raw_token = (raw or "").strip()
            token = self._normalize_word_token(raw_token)
            if not raw_token or not token:
                continue

            if self._is_shell_operator_token(token):
                corrected_tokens.append(raw_token)
                prior_tokens = []
                continue

            if self._should_skip_word_typo_token(token):
                corrected_tokens.append(raw_token)
                prior_tokens.append(token)
                if len(prior_tokens) > 6:
                    prior_tokens = prior_tokens[-6:]
                continue

            contextual_candidates = self._get_contextual_token_candidates(prior_tokens)
            if token in contextual_candidates:
                corrected_tokens.append(raw_token)
                prior_tokens.append(token)
                if len(prior_tokens) > 6:
                    prior_tokens = prior_tokens[-6:]
                continue

            candidate_pool = contextual_candidates if contextual_candidates else self.global_token_candidates
            best_match = self._best_word_typo_match(token, candidate_pool)
            if best_match:
                corrected_tokens.append(best_match)
                prior_tokens.append(best_match)
                if len(prior_tokens) > 6:
                    prior_tokens = prior_tokens[-6:]
                if best_match != token:
                    changed = True
                continue

            corrected_tokens.append(raw_token)
            prior_tokens.append(token)
            if len(prior_tokens) > 6:
                prior_tokens = prior_tokens[-6:]

        if not changed:
            return None

        corrected_prefix = self.normalize_command(
            self.canonicalize_shell_spacing(" ".join(corrected_tokens))
        )
        if not corrected_prefix or corrected_prefix == normalized_buffer:
            return None
        if self.is_blocked_command(corrected_prefix):
            return None
        return {"corrected_prefix": corrected_prefix}

    @classmethod
    def _fuzzy_exec_score(cls, typed_exec: str, candidate_exec: str) -> float:
        typed = cls._normalize_for_fuzzy(typed_exec)
        candidate = cls._normalize_for_fuzzy(candidate_exec)
        if not typed or not candidate:
            return 0.0
        return float(fuzz.QRatio(typed, candidate))

    @classmethod
    def _fuzzy_command_score(cls, query: str, candidate: str) -> float:
        typed = cls._normalize_for_fuzzy(query)
        target = cls._normalize_for_fuzzy(candidate)
        if not typed or not target:
            return 0.0
        quick = float(fuzz.QRatio(typed, target))
        token_sorted = float(fuzz.token_sort_ratio(typed, target))
        # Blend fast direct similarity with token-order-insensitive match.
        return (0.7 * quick) + (0.3 * token_sorted)

    @classmethod
    def _is_whole_word_semantic_miss(
        cls,
        query: str,
        candidate: str,
        typed_exec: str,
        candidate_exec: str,
    ) -> bool:
        if typed_exec != candidate_exec:
            return False

        query_tokens = cls.tokenize_command(query)
        candidate_tokens = cls.tokenize_command(candidate)
        if not query_tokens or not candidate_tokens:
            return False

        typed_last = cls._normalize_word_token(query_tokens[-1])
        candidate_last = cls._normalize_word_token(candidate_tokens[-1])
        if not typed_last or not candidate_last:
            return False
        if cls._should_skip_word_typo_token(typed_last) or cls._should_skip_word_typo_token(candidate_last):
            return False

        typed_compact = cls._normalize_for_fuzzy(typed_last).replace(" ", "")
        candidate_compact = cls._normalize_for_fuzzy(candidate_last).replace(" ", "")
        if not typed_compact or not candidate_compact:
            return False
        if len(typed_compact) < 4:
            return False
        if typed_compact == candidate_compact:
            return False

        # Treat tiny single-edit differences as typos, not semantic misses.
        tiny_edit_typo = Levenshtein.distance(typed_compact, candidate_compact) <= 1
        return not tiny_edit_typo

    @staticmethod
    def tokenize_command(command: str) -> List[str]:
        normalized = CommandVectorDB.normalize_command(command)
        if not normalized:
            return []
        try:
            return shlex.split(normalized, posix=True)
        except Exception:
            return normalized.split()

    @staticmethod
    def extract_executable(tokens: List[str]) -> str:
        executable, _ = CommandVectorDB.extract_executable_with_index(tokens)
        return executable

    @staticmethod
    def extract_executable_with_index(tokens: List[str]) -> Tuple[str, int]:
        if not tokens:
            return ("", -1)

        i = 0
        n = len(tokens)
        while i < n:
            token = (tokens[i] or "").strip()
            if not token:
                i += 1
                continue

            if token in {"sudo", "command"}:
                i += 1
                continue

            if token in {"env", "/usr/bin/env"}:
                i += 1
                while i < n:
                    env_token = (tokens[i] or "").strip()
                    if not env_token:
                        i += 1
                        continue
                    if env_token.startswith("-"):
                        i += 1
                        continue
                    if "=" in env_token and not env_token.startswith("="):
                        i += 1
                        continue
                    break
                continue

            if token.startswith("-"):
                i += 1
                continue

            # Supports patterns like KEY=value command ...
            if "=" in token and not token.startswith("="):
                i += 1
                continue

            return (token, i)

        return ("", -1)

    @staticmethod
    def _token_has_short_flag(token: str, flag: str) -> bool:
        value = (token or "").strip().lower()
        short = (flag or "").strip().lower()
        if not value or not short:
            return False
        if value.startswith("--"):
            return False
        if value == f"-{short}":
            return True
        if value.startswith("-") and len(value) > 2:
            return short in value[1:]
        return False

    @classmethod
    def _history_clears_state(cls, args: List[str]) -> bool:
        for raw in args:
            token = (raw or "").strip().lower()
            if not token:
                continue
            if token == "--clear":
                return True
            if cls._token_has_short_flag(token, "c"):
                return True
        return False

    @classmethod
    def _extract_git_subcommand(cls, args: List[str]) -> Tuple[str, List[str]]:
        i = 0
        n = len(args)
        while i < n:
            token = (args[i] or "").strip()
            if not token:
                i += 1
                continue

            if token == "--":
                i += 1
                break

            if token in cls.GIT_GLOBAL_OPTIONS_WITH_VALUE:
                i += 2
                continue

            if token.startswith(("--exec-path=", "--git-dir=", "--work-tree=", "--namespace=", "--super-prefix=", "--config-env=")):
                i += 1
                continue

            # Handles compact forms like -Cpath or -ckey=value.
            if token.startswith("-C") and token != "-C":
                i += 1
                continue
            if token.startswith("-c") and token != "-c":
                i += 1
                continue

            if token.startswith("-"):
                i += 1
                continue

            subcommand = token.lower()
            remaining = []
            for value in args[i + 1:]:
                clean = (value or "").strip().lower()
                if clean:
                    remaining.append(clean)
            return (subcommand, remaining)

        return ("", [])

    @classmethod
    def _is_git_destructive_subcommand(cls, args: List[str]) -> bool:
        subcommand, remaining = cls._extract_git_subcommand(args)
        if not subcommand:
            return False

        if subcommand == "reset":
            return "--hard" in remaining

        if subcommand == "clean":
            for token in remaining:
                if token == "--force" or token.startswith("--force="):
                    return True
                if cls._token_has_short_flag(token, "f"):
                    return True

        return False

    @staticmethod
    def is_blocked_command(command: str) -> bool:
        tokens = CommandVectorDB.tokenize_command(command)
        executable, executable_index = CommandVectorDB.extract_executable_with_index(tokens)
        if not executable:
            return False
        executable_name = os.path.basename(executable).strip().lower()
        if executable_name in CommandVectorDB.BLOCKED_EXECUTABLES:
            return True
        if any(executable_name.startswith(prefix) for prefix in CommandVectorDB.BLOCKED_EXECUTABLE_PREFIXES):
            return True

        args = tokens[executable_index + 1:] if executable_index >= 0 else []
        if executable_name == "history" and CommandVectorDB._history_clears_state(args):
            return True
        if executable_name == "git" and CommandVectorDB._is_git_destructive_subcommand(args):
            return True
        return False

    @staticmethod
    def extract_context_key(buffer_context: str) -> str:
        tokens = CommandVectorDB.normalize_command(buffer_context).lower().split()
        if not tokens:
            return ""
        return " ".join(tokens[:2])

    @staticmethod
    def extract_context_keys(buffer_context: str) -> List[str]:
        tokens = CommandVectorDB.normalize_command(buffer_context).lower().split()
        if not tokens:
            return []
        keys = [tokens[0]]
        if len(tokens) > 1:
            keys.append(" ".join(tokens[:2]))
        return keys

    @staticmethod
    def _stable_key(prefix: str, payload: str) -> str:
        digest = hashlib.sha256((payload or "").encode("utf-8")).hexdigest()[:24]
        return f"{prefix}_{digest}"

    @staticmethod
    def _canonical_remote_url(remote: str) -> str:
        value = str(remote or "").strip().lower()
        if value.endswith(".git"):
            value = value[:-4]
        return value

    @staticmethod
    def _safe_git_output(cwd: str, args: List[str], timeout_sec: float = 0.12) -> str:
        if not cwd:
            return ""
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=cwd,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_sec,
            )
        except Exception:
            return ""
        if proc.returncode != 0:
            return ""
        return (proc.stdout or "").strip()

    def resolve_repo_key(self, working_directory: str) -> str:
        cwd = os.path.abspath(os.path.expanduser(str(working_directory or "").strip()))
        if not cwd:
            return ""
        now = time.monotonic()
        cached = self._repo_identity_cache.get(cwd)
        if cached is not None:
            ts, key = cached
            if (now - ts) <= 30.0:
                return key
        git_root = self._safe_git_output(cwd, ["rev-parse", "--show-toplevel"])
        if git_root:
            remote = self._canonical_remote_url(
                self._safe_git_output(cwd, ["config", "--get", "remote.origin.url"])
            )
            payload = f"{git_root.lower()}\x1f{remote}"
            key = self._stable_key("repo", payload)
            self._repo_identity_cache[cwd] = (now, key)
            return key

        fallback = self._stable_key("cwd", cwd.lower())
        self._repo_identity_cache[cwd] = (now, fallback)
        return fallback

    @staticmethod
    def extract_task_key(command: str) -> str:
        normalized = CommandVectorDB.normalize_command(command)
        tokens = normalized.split()
        if not tokens:
            return ""

        i = 0
        while i < len(tokens):
            token = tokens[i]
            if "=" in token and not token.startswith("="):
                i += 1
                continue
            break
        if i >= len(tokens):
            return ""

        executable = os.path.basename(tokens[i]).strip().lower()
        if not executable:
            return ""
        scripting_execs = {
            "python",
            "python3",
            "node",
            "ruby",
            "perl",
            "bash",
            "sh",
            "zsh",
            "pwsh",
        }
        if executable in scripting_execs:
            return executable

        subcmd = ""
        if i + 1 < len(tokens):
            candidate = tokens[i + 1].strip().lower()
            if candidate and not candidate.startswith("-") and not ("=" in candidate and not candidate.startswith("=")):
                subcmd = candidate
        if subcmd:
            return f"{executable} {subcmd}"
        return executable

    @classmethod
    def repo_confidence_tier(
        cls,
        total_repo_accepts: int,
        distinct_repo_suffixes: int,
        total_repo_execute_signal: int = 0,
    ) -> str:
        # Execute-derived signal contributes modestly to tier gating.
        effective_signal = int(total_repo_accepts or 0) + min(int(total_repo_execute_signal or 0), 2)
        if effective_signal >= cls.REPO_CONF_HIGH_MIN_ACCEPTS:
            return "HIGH"
        if (
            effective_signal >= cls.REPO_CONF_MEDIUM_MIN_ACCEPTS
            and distinct_repo_suffixes >= cls.REPO_CONF_MEDIUM_MIN_DISTINCT
        ):
            return "MEDIUM"
        return "LOW"

    @staticmethod
    def _has_help_intent(buffer_context: str) -> bool:
        low = CommandVectorDB.normalize_command(buffer_context).lower()
        if not low:
            return False
        return (" help" in f" {low}") or ("--help" in low) or ("--h" in low)

    @staticmethod
    def _is_help_like_command(command: str) -> bool:
        low = CommandVectorDB.normalize_command(command).lower()
        if not low:
            return False
        return low.endswith(" --help") or low.endswith(" help")

    @staticmethod
    def command_doc_id(command: str) -> str:
        normalized = CommandVectorDB.normalize_command(command)
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:60]
        return f"cmd_{digest}"

    @staticmethod
    def context_stat_doc_id(context_key: str, suggestion_suffix: str) -> str:
        payload = f"{context_key}\x1f{suggestion_suffix}"
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:60]
        return f"ctx_{digest}"

    @staticmethod
    def merge_buffer_and_suffix(buffer_context: str, suggestion_suffix: str) -> str:
        base = buffer_context or ""
        suffix = suggestion_suffix or ""
        if base and suffix and base[-1].isspace() and suffix[0].isspace():
            suffix = suffix.lstrip()
        return f"{base}{suffix}"

    @staticmethod
    def canonicalize_shell_spacing(command: str) -> str:
        """
        Collapse repeated separator whitespace outside quotes/escapes.
        Keep whitespace inside single/double quotes untouched.
        """
        s = command or ""
        out: List[str] = []
        in_single = False
        in_double = False
        escaped = False
        pending_space = False

        for ch in s:
            if escaped:
                if pending_space:
                    if out and out[-1] != " ":
                        out.append(" ")
                    pending_space = False
                out.append(ch)
                escaped = False
                continue

            if ch == "\\" and not in_single:
                if pending_space:
                    if out and out[-1] != " ":
                        out.append(" ")
                    pending_space = False
                out.append(ch)
                escaped = True
                continue

            if ch == "'" and not in_double:
                if pending_space:
                    if out and out[-1] != " ":
                        out.append(" ")
                    pending_space = False
                in_single = not in_single
                out.append(ch)
                continue

            if ch == '"' and not in_single:
                if pending_space:
                    if out and out[-1] != " ":
                        out.append(" ")
                    pending_space = False
                in_double = not in_double
                out.append(ch)
                continue

            if ch.isspace() and not in_single and not in_double:
                pending_space = True
                continue

            if pending_space:
                if out and out[-1] != " ":
                    out.append(" ")
                pending_space = False
            out.append(ch)

        return "".join(out).strip()

    @staticmethod
    def blend_rank_score(
        rank: int,
        repo_task_count: int,
        context_count: int,
        accept_count: int,
        execute_count: int = 0,
        history_count: int = 0,
        manual_count: int = 0,
        assist_count: int = 0,
        hours_since_last_manual: Optional[float] = None,
        base_weight: float = SCORE_BASE_RANK,
        alpha: float = SCORE_ALPHA,
        beta: float = SCORE_BETA,
        manual_weight: float = SCORE_MANUAL,
        assist_weight: float = SCORE_ASSIST,
        eta: float = SCORE_ACCEPT,
        delta: float = SCORE_HISTORY,
        manual_recency_weight: float = SCORE_MANUAL_RECENCY,
        manual_recency_decay_hours: float = MANUAL_RECENCY_DECAY_HOURS,
        history_cap: int = HISTORY_COUNT_CAP,
    ) -> float:
        # Kept for backward call compatibility; execute_count is intentionally unused.
        _ = execute_count
        safe_rank = max(int(rank), 0)
        base = float(base_weight) / float(safe_rank + 1)
        capped_history = min(max(int(history_count or 0), 0), max(int(history_cap or 0), 0))
        score = base + (alpha * math.log1p(max(repo_task_count, 0))) + (
            beta * math.log1p(max(context_count, 0))
        ) + (manual_weight * math.log1p(max(manual_count, 0))) + (
            assist_weight * math.log1p(max(assist_count, 0))
        ) + (eta * math.log1p(max(accept_count, 0))) + (
            delta * math.log1p(capped_history)
        )
        if hours_since_last_manual is not None:
            safe_hours = max(float(hours_since_last_manual), 0.0)
            decay_hours = max(float(manual_recency_decay_hours), 1.0)
            score += float(manual_recency_weight) * math.exp(-(safe_hours / decay_hours))
        return score

    @staticmethod
    def rerank_suffixes_from_counts(
        candidates: List[str],
        global_counts: Dict[str, int],
        context_counts: Dict[str, int],
        repo_task_counts: Optional[Dict[str, int]] = None,
        execute_counts: Optional[Dict[str, int]] = None,
        history_counts: Optional[Dict[str, int]] = None,
        manual_counts: Optional[Dict[str, int]] = None,
        assist_counts: Optional[Dict[str, int]] = None,
        last_manual_hours: Optional[Dict[str, float]] = None,
        base_weight: float = SCORE_BASE_RANK,
        alpha: float = SCORE_ALPHA,
        beta: float = SCORE_BETA,
        manual_weight: float = SCORE_MANUAL,
        assist_weight: float = SCORE_ASSIST,
        eta: float = SCORE_ACCEPT,
        delta: float = SCORE_HISTORY,
        manual_recency_weight: float = SCORE_MANUAL_RECENCY,
        manual_recency_decay_hours: float = MANUAL_RECENCY_DECAY_HOURS,
        history_cap: int = HISTORY_COUNT_CAP,
    ) -> List[str]:
        repo_task_counts = repo_task_counts or {}
        execute_counts = execute_counts or {}
        history_counts = history_counts or {}
        manual_counts = manual_counts or {}
        assist_counts = assist_counts or {}
        last_manual_hours = last_manual_hours or {}
        scored = []
        for rank, suffix in enumerate(candidates):
            final_score = CommandVectorDB.blend_rank_score(
                rank=rank,
                repo_task_count=repo_task_counts.get(suffix, 0),
                context_count=context_counts.get(suffix, 0),
                accept_count=global_counts.get(suffix, 0),
                execute_count=execute_counts.get(suffix, 0),
                history_count=history_counts.get(suffix, 0),
                manual_count=manual_counts.get(suffix, 0),
                assist_count=assist_counts.get(suffix, 0),
                hours_since_last_manual=last_manual_hours.get(suffix),
                base_weight=base_weight,
                alpha=alpha,
                beta=beta,
                manual_weight=manual_weight,
                assist_weight=assist_weight,
                eta=eta,
                delta=delta,
                manual_recency_weight=manual_recency_weight,
                manual_recency_decay_hours=manual_recency_decay_hours,
                history_cap=history_cap,
            )
            scored.append((rank, suffix, final_score))

        scored.sort(key=lambda item: item[2], reverse=True)
        return [suffix for _, suffix, _ in scored]

    @staticmethod
    def _apply_repo_tier_order(
        sorted_entries: List[Tuple[int, str, float]],
        repo_hit_suffixes: set[str],
        tier: str,
    ) -> Tuple[List[str], bool, bool, str]:
        if not sorted_entries:
            return ([], False, False, "none")

        repo_entries = [item for item in sorted_entries if item[1] in repo_hit_suffixes]
        non_repo_entries = [item for item in sorted_entries if item[1] not in repo_hit_suffixes]
        if not repo_entries:
            return ([suffix for _, suffix, _ in sorted_entries], False, False, "none")

        enforced_top1 = False
        enforced_top3 = False
        strategy = "none"
        ordered_entries = list(sorted_entries)

        if tier == "HIGH":
            prefix = (repo_entries + non_repo_entries)[:3]
            used = {id(item) for item in prefix}
            remaining = [item for item in sorted_entries if id(item) not in used]
            ordered_entries = prefix + remaining
            enforced_top3 = True
            strategy = "top3_repo_first"
        elif tier == "MEDIUM":
            best_repo = repo_entries[0]
            ordered_entries = [best_repo] + [item for item in sorted_entries if item is not best_repo]
            enforced_top1 = True
            strategy = "top1_repo_anchor"

        return ([suffix for _, suffix, _ in ordered_entries], enforced_top1, enforced_top3, strategy)

    def _load_model(self) -> SentenceTransformer:
        logger.info(f"Loading embedding model: {self.model_name}")
        self._set_init_phase("loading_model_local")
        try:
            import torch

            torch.set_num_threads(1)
            torch.set_num_interop_threads(1)
        except Exception:
            pass

        try:
            model = SentenceTransformer(self.model_name, local_files_only=True)
            logger.info("Model loaded from local cache")
            self._set_init_phase("initializing_db")
            return model
        except Exception as local_exc:
            logger.info("Model not found locally. Downloading once...")
            with self._status_lock:
                self._model_download_needed = True
                self._model_download_in_progress = True
                self._init_phase = "downloading_model"
            _update_runtime_init_status(
                phase="downloading_model",
                model_download_needed=True,
                model_download_in_progress=True,
            )
            try:
                os.environ["HF_HUB_OFFLINE"] = "0"
                os.environ["TRANSFORMERS_OFFLINE"] = "0"
                model = SentenceTransformer(self.model_name)
                logger.info("Model downloaded and cached")
                self._set_init_phase("initializing_db")
                return model
            except Exception as download_exc:
                self._set_init_error(
                    f"Embedding model initialization failed: {download_exc}"
                )
                raise download_exc from local_exc
            finally:
                with self._status_lock:
                    self._model_download_in_progress = False
                _update_runtime_init_status(model_download_in_progress=False)
                os.environ["HF_HUB_OFFLINE"] = "1"
                os.environ["TRANSFORMERS_OFFLINE"] = "1"

    def _is_corruption_error(self, error: Exception) -> bool:
        msg = str(error).lower()
        return any(
            token in msg
            for token in [
                "checksum",
                "corrupt",
                "invalid checksum",
                "vector indexer not found",
                "segment not found",
                "segment.cc",
                "failed to open index",
            ]
        )

    def _remove_path(self, path: str):
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        elif os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass

    def _quarantine_corrupted_path(self, path: str):
        self._remove_path(path)
        logger.error(f"Deleted corrupted database at {path}")

    def _create_command_collection(self, path: str) -> zvec.Collection:
        logger.info(f"Creating command database at {path}")
        os.makedirs(os.path.dirname(path), exist_ok=True)

        schema = zvec.CollectionSchema(
            name="shell_commands",
            fields=[
                zvec.FieldSchema("command", zvec.DataType.STRING),
                zvec.FieldSchema("timestamp", zvec.DataType.INT64),
                zvec.FieldSchema("accept_count", zvec.DataType.INT64, nullable=True),
                zvec.FieldSchema("history_count", zvec.DataType.INT64, nullable=True),
                zvec.FieldSchema("execute_count", zvec.DataType.INT64, nullable=True),
                zvec.FieldSchema("last_accepted_at", zvec.DataType.INT64, nullable=True),
            ],
            vectors=zvec.VectorSchema("embedding", zvec.DataType.VECTOR_FP32, self.dimensions),
        )
        return zvec.create_and_open(path=path, schema=schema)

    def _create_feedback_collection(self, path: str) -> zvec.Collection:
        logger.info(f"Creating feedback stats database at {path}")
        os.makedirs(os.path.dirname(path), exist_ok=True)

        schema = zvec.CollectionSchema(
            name="shell_feedback_stats",
            fields=[
                zvec.FieldSchema("context_key", zvec.DataType.STRING),
                zvec.FieldSchema("suggestion_suffix", zvec.DataType.STRING),
                zvec.FieldSchema("accept_count", zvec.DataType.INT64),
                zvec.FieldSchema("last_accepted_at", zvec.DataType.INT64),
            ],
            vectors=zvec.VectorSchema("embedding_dummy", zvec.DataType.VECTOR_FP32, 1),
        )
        return zvec.create_and_open(path=path, schema=schema)

    def _ensure_vector_index(self, collection: zvec.Collection, field_name: str):
        try:
            index_param = zvec.HnswIndexParam(m=16, ef_construction=200)
            collection.create_index(field_name, index_param)
            logger.info(f"HNSW index ready for vector field '{field_name}'")
        except Exception as exc:
            msg = str(exc).lower()
            if "exist" in msg or "already" in msg:
                logger.debug("HNSW index already exists")
            else:
                logger.warning(f"Could not ensure vector index on '{field_name}': {exc}")

    def _validate_collection_health(
        self, collection: zvec.Collection, vector_field: str, dimensions: int
    ) -> bool:
        try:
            query = zvec.VectorQuery(
                vector_field,
                vector=[0.0] * dimensions,
                param=zvec.HnswQueryParam(ef=8),
            )
            collection.query(query, topk=1)
            return True
        except Exception as exc:
            if self._is_corruption_error(exc):
                logger.error(f"Detected corrupted vector index during health check: {exc}")
                return False
            logger.warning(f"Vector DB health check failed with non-fatal error: {exc}")
            return True

    def _command_schema_is_v3_collection(self, collection: zvec.Collection) -> bool:
        try:
            field_names = {field.name for field in collection.schema.fields}
            return (
                "accept_count" in field_names
                and "history_count" in field_names
                and "execute_count" in field_names
                and "last_accepted_at" in field_names
            )
        except Exception:
            return False

    def _init_command_collection(self, path: str) -> zvec.Collection:
        os.makedirs(os.path.dirname(path), exist_ok=True)

        if os.path.exists(path):
            logger.info(f"Opening command database at {path}")
            try:
                collection = zvec.open(path=path)
            except Exception as exc:
                if self._is_corruption_error(exc):
                    self._quarantine_corrupted_path(path)
                    collection = self._create_command_collection(path)
                else:
                    raise
        else:
            collection = self._create_command_collection(path)

        if not self._command_schema_is_v3_collection(collection):
            logger.warning("Command database is not v3 schema; recreating fresh database")
            self._quarantine_corrupted_path(path)
            collection = self._create_command_collection(path)

        self._ensure_vector_index(collection, field_name="embedding")
        if not self._validate_collection_health(collection, "embedding", self.dimensions):
            self._quarantine_corrupted_path(path)
            collection = self._create_command_collection(path)
            self._ensure_vector_index(collection, field_name="embedding")
        return collection

    def _init_feedback_collection(self, path: str) -> Optional[zvec.Collection]:
        os.makedirs(os.path.dirname(path), exist_ok=True)

        if os.path.exists(path):
            logger.info(f"Opening feedback stats database at {path}")
            try:
                collection = zvec.open(path=path)
            except Exception as exc:
                if self._is_corruption_error(exc):
                    self._quarantine_corrupted_path(path)
                    collection = self._create_feedback_collection(path)
                else:
                    raise
        else:
            collection = self._create_feedback_collection(path)

        self._ensure_vector_index(collection, field_name="embedding_dummy")
        if not self._validate_collection_health(collection, "embedding_dummy", 1):
            self._quarantine_corrupted_path(path)
            collection = self._create_feedback_collection(path)
            self._ensure_vector_index(collection, field_name="embedding_dummy")
        return collection

    def _load_existing_commands(self, limit: int = 1024) -> set:
        commands = set()
        try:
            query_limit = int(limit or 0)
            if query_limit <= 0:
                with self._io_lock:
                    query_limit = int(getattr(self.collection.stats, "doc_count", 0) or 0)
            if query_limit <= 0:
                return commands
            with self._io_lock:
                # Prefer scalar-only query to enumerate docs for listing tasks.
                # Fallback to vector query for engines that require vectors.
                try:
                    results = self.collection.query(
                        vectors=None,
                        topk=query_limit,
                        output_fields=["command"],
                    )
                except Exception:
                    query = zvec.VectorQuery("embedding", vector=[0.0] * self.dimensions)
                    results = self.collection.query(query, topk=query_limit)
            for res in results:
                cmd = res.fields.get("command", "")
                if cmd:
                    commands.add(cmd)
            logger.info(f"Loaded {len(commands)} existing commands from database")
        except Exception as exc:
            logger.warning(f"Could not load existing commands: {exc}")
        return commands

    def _load_index_state(self) -> dict:
        if not os.path.exists(self.state_file):
            return {}
        try:
            with open(self.state_file, "r") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_index_state(self, state: dict):
        tmp_path = f"{self.state_file}.tmp"
        with open(tmp_path, "w") as f:
            json.dump(state, f)
        os.replace(tmp_path, self.state_file)

    def _parse_history_line(self, line: str) -> str:
        line = line.strip()
        if not line:
            return ""
        if line.startswith(":"):
            parts = line.split(";", 1)
            if len(parts) == 2:
                line = parts[1].strip()
        return line

    @staticmethod
    def count_history_commands(lines: List[str]) -> Dict[str, int]:
        command_counts: Dict[str, int] = {}
        for cmd in lines:
            if not cmd:
                continue
            command_counts[cmd] = command_counts.get(cmd, 0) + 1
        return command_counts

    def _read_history_commands_from_offset(
        self, history_path: Path, start_offset: int
    ) -> Tuple[Dict[str, int], int]:
        with open(history_path, "rb") as f:
            started_mid_line = False
            if start_offset > 0:
                f.seek(start_offset - 1)
                started_mid_line = f.read(1) not in (b"\n", b"\r")
            f.seek(start_offset)
            chunk = f.read()
            end_offset = f.tell()

        if not chunk:
            return ({}, end_offset)

        text = chunk.decode("utf-8", errors="ignore")
        lines = text.splitlines()
        if started_mid_line and lines:
            lines = lines[1:]

        parsed_commands: List[str] = []
        for raw_line in lines:
            cmd = self.normalize_command(self._parse_history_line(raw_line))
            if not cmd:
                continue
            parsed_commands.append(cmd)

        return (self.count_history_commands(parsed_commands), end_offset)

    def initialize_from_history(self, history_file: str):
        self._set_init_phase("syncing_history")
        history_path = Path(history_file).expanduser()
        if not history_path.exists():
            logger.warning(f"History file not found: {history_file}")
            self._set_init_phase("ready")
            return

        logger.info(f"Syncing database from history: {history_file}")

        try:
            stat = history_path.stat()
            history_key = str(history_path)
            state = (
                self.state_store.get_history_index_state(history_key)
                if self.state_store is not None
                else self._load_index_state()
            ) or {}
            saved_offset = state.get("offset")
            saved_offset_int = int(saved_offset) if isinstance(saved_offset, int) else 0

            if self.state_store is None and not state and self.inserted_commands:
                self._save_index_state(
                    {
                        "history_file": history_key,
                        "inode": stat.st_ino,
                        "device": stat.st_dev,
                        "offset": stat.st_size,
                    }
                )
                logger.info("Database already populated; seeded incremental history pointer")
                self._set_init_phase("ready")
                return

            start_offset = 0
            if (
                state.get("history_file") == history_key
                and state.get("inode") == stat.st_ino
                and state.get("device") == stat.st_dev
                and isinstance(saved_offset, int)
            ):
                start_offset = max(0, min(saved_offset_int, stat.st_size))
            elif stat.st_size < saved_offset_int:
                start_offset = 0

            if start_offset > 0:
                logger.info(f"Incremental history sync from byte offset {start_offset}")

            command_counts, end_offset = self._read_history_commands_from_offset(
                history_path, start_offset
            )
            if not command_counts:
                if self.state_store is not None:
                    self.state_store.set_history_index_state(
                        history_file=history_key,
                        inode=stat.st_ino,
                        device=stat.st_dev,
                        offset=stat.st_size,
                    )
                    if not self.inserted_commands:
                        self.insert_commands(self.state_store.list_all_commands(include_removed=False))
                else:
                    self._save_index_state(
                        {
                            "history_file": history_key,
                            "inode": stat.st_ino,
                            "device": stat.st_dev,
                            "offset": stat.st_size,
                        }
                    )
                self._set_init_phase("ready")
                return

            total_occurrences = sum(command_counts.values())
            logger.info(
                f"Found {total_occurrences} new history entries across {len(command_counts)} unique commands"
            )
            if self.state_store is not None:
                inserted = int(self.state_store.apply_history_counts(command_counts) or 0)
                self.insert_commands(list(command_counts.keys()))
            else:
                inserted = self.upsert_history_commands(command_counts)
            if inserted < 0:
                logger.warning("History sync failed, keeping previous history pointer")
                return

            if self.state_store is not None:
                self.state_store.set_history_index_state(
                    history_file=history_key,
                    inode=stat.st_ino,
                    device=stat.st_dev,
                    offset=end_offset,
                )
            else:
                self._save_index_state(
                    {
                        "history_file": history_key,
                        "inode": stat.st_ino,
                        "device": stat.st_dev,
                        "offset": end_offset,
                    }
                )
            self._set_init_phase("ready")
        except Exception as exc:
            logger.error(f"Error reading history file: {exc}")
            self._set_init_error(f"History sync failed: {exc}")

    def insert_commands(self, commands: List[str]) -> int:
        if self._is_closed:
            return 0

        normalized_unique = []
        seen = set()
        for command in commands:
            normalized = self.normalize_command(command)
            if not normalized or normalized in seen:
                continue
            if self.is_blocked_command(normalized):
                logger.debug("Skipping blocked command insert")
                continue
            if self.is_removed_command(normalized):
                logger.debug("Skipping removed command insert")
                continue
            seen.add(normalized)
            normalized_unique.append(normalized)

        self._register_commands(normalized_unique)

        if not normalized_unique:
            logger.debug("No new commands to insert")
            return 0

        command_ids = [self.command_doc_id(command) for command in normalized_unique]
        new_commands = []

        try:
            with self._io_lock:
                existing = self.collection.fetch(command_ids)
                for command, command_id in zip(normalized_unique, command_ids):
                    if command_id in existing:
                        continue
                    new_commands.append(command)

            if not new_commands:
                return 0

            logger.info(f"Inserting {len(new_commands)} new commands")
            embeddings = self.model.encode(new_commands, show_progress_bar=False)
            timestamp = int(time.time())
            docs = []
            for command, emb in zip(new_commands, embeddings):
                docs.append(
                    zvec.Doc(
                        id=self.command_doc_id(command),
                        fields={
                            "command": command,
                            "timestamp": timestamp,
                            "accept_count": 0,
                            "history_count": 0,
                            "execute_count": 0,
                            "last_accepted_at": timestamp,
                        },
                        vectors={"embedding": emb},
                    )
                )

            batch_size = 100
            with self._io_lock:
                for i in range(0, len(docs), batch_size):
                    self.collection.insert(docs[i : i + batch_size])

            self.inserted_commands.update(new_commands)
            logger.info(f"Successfully inserted {len(docs)} commands")
            return len(docs)
        except Exception as exc:
            logger.error(f"Error inserting commands: {exc}")
            return 0

    def insert_command(self, command: str, working_directory: str | None = None):
        normalized = self.normalize_command(command)
        if not normalized:
            return
        if self.is_blocked_command(normalized):
            logger.debug("Skipping blocked command execute_count update")
            return
        if self.is_removed_command(normalized):
            logger.debug("Skipping removed command execute_count update")
            return
        self._register_commands([normalized])
        if self.state_store is not None:
            try:
                repo_task_pair = None
                if working_directory:
                    repo_key = self.resolve_repo_key(working_directory)
                    task_key = self.extract_task_key(normalized)
                    if repo_key and task_key:
                        repo_task_pair = (repo_key, task_key, normalized)
                self.state_store.record_execute(
                    normalized,
                    delta=1,
                    repo_task_pair=repo_task_pair,
                )
            except Exception as exc:
                logger.error(f"Failed to record execute_count in SQLite: {exc}")
            self.insert_commands([normalized])
            return
        self._increment_execute_count(normalized)

    def upsert_history_commands(self, command_counts: Dict[str, int]) -> int:
        if self._is_closed:
            return 0

        normalized_counts: Dict[str, int] = {}
        for command, count in command_counts.items():
            normalized = self.normalize_command(command)
            if not normalized:
                continue
            if self.is_blocked_command(normalized):
                logger.debug("Skipping blocked command from history upsert")
                continue
            if self.is_removed_command(normalized):
                logger.debug("Skipping removed command from history upsert")
                continue
            n = int(count or 0)
            if n <= 0:
                continue
            normalized_counts[normalized] = normalized_counts.get(normalized, 0) + n

        if not normalized_counts:
            return 0

        if self.state_store is not None:
            try:
                inserted = int(self.state_store.apply_history_counts(normalized_counts) or 0)
                self.insert_commands(list(normalized_counts.keys()))
                return inserted
            except Exception as exc:
                logger.error(f"Error upserting history commands to SQLite: {exc}")
                return -1

        self._register_commands(normalized_counts.keys())
        commands = list(normalized_counts.keys())
        command_ids = [self.command_doc_id(command) for command in commands]
        timestamp = int(time.time())
        updated = 0
        new_commands: List[str] = []

        try:
            with self._io_lock:
                existing = self.collection.fetch(command_ids)
                for command, command_id in zip(commands, command_ids):
                    doc = existing.get(command_id)
                    if doc is None:
                        new_commands.append(command)
                        continue
                    prev = int(doc.fields.get("history_count", 0) or 0)
                    self.collection.update(
                        zvec.Doc(
                            id=command_id,
                            fields={"history_count": prev + normalized_counts[command]},
                        )
                    )
                    updated += 1

            if new_commands:
                embeddings = self.model.encode(new_commands, show_progress_bar=False)
                docs = []
                for command, emb in zip(new_commands, embeddings):
                    docs.append(
                        zvec.Doc(
                            id=self.command_doc_id(command),
                            fields={
                                "command": command,
                                "timestamp": timestamp,
                                "accept_count": 0,
                                "history_count": normalized_counts[command],
                                "execute_count": 0,
                                "last_accepted_at": timestamp,
                            },
                            vectors={"embedding": emb},
                        )
                    )

                batch_size = 100
                with self._io_lock:
                    for i in range(0, len(docs), batch_size):
                        self.collection.insert(docs[i : i + batch_size])
                self.inserted_commands.update(new_commands)

            return updated + len(new_commands)
        except Exception as exc:
            logger.error(f"Error upserting history commands: {exc}")
            return -1

    def _collect_history_command_counts(self, history_file: str) -> Dict[str, int]:
        history_path = Path(history_file).expanduser()
        if not history_path.exists() or not history_path.is_file():
            return {}

        counts: Dict[str, int] = {}
        try:
            with open(history_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    command = self.normalize_command(self._parse_history_line(line))
                    if not command:
                        continue
                    if self.is_blocked_command(command):
                        continue
                    if self.is_removed_command(command):
                        continue
                    counts[command] = counts.get(command, 0) + 1
        except Exception as exc:
            logger.warning(f"Could not read history for command store listing: {exc}")
            return {}
        return counts

    def _fetch_command_doc_fields(self, commands: List[str]) -> Dict[str, Dict[str, object]]:
        if not commands:
            return {}

        out: Dict[str, Dict[str, object]] = {}
        batch_size = 256
        for i in range(0, len(commands), batch_size):
            chunk = commands[i : i + batch_size]
            ids = [self.command_doc_id(command) for command in chunk]
            with self._io_lock:
                docs = self.collection.fetch(ids)
            for command, command_id in zip(chunk, ids):
                doc = docs.get(command_id)
                if doc is None:
                    continue
                out[command] = dict(doc.fields or {})
        return out

    @classmethod
    def _exec_tokens_look_like_typo(cls, candidate_exec: str, dominant_exec: str) -> bool:
        candidate = cls._normalize_word_token(candidate_exec)
        dominant = cls._normalize_word_token(dominant_exec)
        if not candidate or not dominant:
            return False
        if candidate == dominant:
            return False
        if len(candidate) < 4 or len(dominant) < 4:
            return False
        candidate_fuzzy = cls._normalize_for_fuzzy(candidate).replace(" ", "")
        dominant_fuzzy = cls._normalize_for_fuzzy(dominant).replace(" ", "")
        if not candidate_fuzzy or not dominant_fuzzy:
            return False
        if candidate_fuzzy[0] != dominant_fuzzy[0]:
            return False
        if abs(len(candidate_fuzzy) - len(dominant_fuzzy)) > 2:
            return False
        if Levenshtein.distance(candidate_fuzzy, dominant_fuzzy) > 2:
            return False
        score = float(fuzz.QRatio(candidate_fuzzy, dominant_fuzzy))
        return score >= 84.0

    @classmethod
    def _full_command_variant_looks_like_typo(cls, candidate: str, dominant: str) -> bool:
        candidate_tokens = cls.tokenize_command(candidate)
        dominant_tokens = cls.tokenize_command(dominant)
        if not candidate_tokens or not dominant_tokens:
            return False
        if len(candidate_tokens) != len(dominant_tokens):
            return False

        diffs = []
        for idx, (left, right) in enumerate(zip(candidate_tokens, dominant_tokens)):
            if cls._normalize_word_token(left) != cls._normalize_word_token(right):
                diffs.append(idx)
        if len(diffs) != 1:
            return False

        idx = diffs[0]
        left_raw = candidate_tokens[idx]
        right_raw = dominant_tokens[idx]
        left = cls._normalize_word_token(left_raw)
        right = cls._normalize_word_token(right_raw)
        if not left or not right:
            return False
        if cls._should_skip_word_typo_token(left) or cls._should_skip_word_typo_token(right):
            return False

        left_fuzzy = cls._normalize_for_fuzzy(left).replace(" ", "")
        right_fuzzy = cls._normalize_for_fuzzy(right).replace(" ", "")
        if not left_fuzzy or not right_fuzzy:
            return False
        if left_fuzzy[0] != right_fuzzy[0]:
            return False
        if abs(len(left_fuzzy) - len(right_fuzzy)) > 2:
            return False
        if Levenshtein.distance(left_fuzzy, right_fuzzy) > 2:
            return False

        command_score = float(
            fuzz.QRatio(
                cls._normalize_for_fuzzy(candidate),
                cls._normalize_for_fuzzy(dominant),
            )
        )
        return command_score >= 90.0

    def _detect_potential_wrong_commands(
        self, entries: List[Dict[str, object]]
    ) -> Dict[str, str]:
        if not entries:
            return {}

        by_exec: Dict[str, List[Dict[str, object]]] = defaultdict(list)
        exec_usage: Dict[str, int] = defaultdict(int)
        for entry in entries:
            command = str(entry.get("command", "") or "")
            if not command:
                continue
            exec_key = self._prefix_exec_key(command)
            if not exec_key:
                continue
            usage = int(entry.get("usage_score", 0) or 0)
            by_exec[exec_key].append(entry)
            exec_usage[exec_key] += usage

        suspicious: Dict[str, str] = {}

        # Executable-token typo detection: only very low-usage variants are flagged.
        ranked_execs = sorted(exec_usage.items(), key=lambda item: item[1], reverse=True)
        for candidate_exec, candidate_usage in ranked_execs:
            if candidate_usage <= 0 or candidate_usage > 2:
                continue
            for dominant_exec, dominant_usage in ranked_execs:
                if dominant_exec == candidate_exec:
                    continue
                if dominant_usage < 8:
                    continue
                if dominant_usage < candidate_usage * 4:
                    continue
                if not self._exec_tokens_look_like_typo(candidate_exec, dominant_exec):
                    continue
                for item in by_exec.get(candidate_exec, []):
                    command = str(item.get("command", "") or "")
                    if command and command not in suspicious:
                        suspicious[command] = f"looks like typo of '{dominant_exec}'"
                break

        # Full-command typo detection under same executable.
        for exec_key, items in by_exec.items():
            ordered = sorted(items, key=lambda item: int(item.get("usage_score", 0) or 0), reverse=True)
            dominant_candidates = [
                item for item in ordered if int(item.get("usage_score", 0) or 0) >= 5
            ]
            if not dominant_candidates:
                continue

            for item in ordered:
                command = str(item.get("command", "") or "")
                usage = int(item.get("usage_score", 0) or 0)
                if not command or usage > 2 or command in suspicious:
                    continue

                for dominant in dominant_candidates[:6]:
                    dominant_command = str(dominant.get("command", "") or "")
                    dominant_usage = int(dominant.get("usage_score", 0) or 0)
                    if not dominant_command or dominant_command == command:
                        continue
                    if dominant_usage < usage * 4:
                        continue
                    if not self._full_command_variant_looks_like_typo(command, dominant_command):
                        continue
                    suspicious[command] = f"looks similar to high-usage '{dominant_command}'"
                    break

        return suspicious

    def list_command_store(self, history_file: str = "", include_all: bool = False) -> Dict[str, object]:
        history_counts = self._collect_history_command_counts(history_file) if history_file else {}
        if self.state_store is not None:
            self.removed_commands = self._load_removed_commands()

        commands = set(history_counts.keys())
        with self._io_lock:
            commands.update(self.command_cache)
            commands.update(self.inserted_commands)
        if include_all:
            commands.update(self._load_existing_commands(limit=0))
        if self.state_store is not None:
            try:
                commands.update(self.state_store.list_all_commands(include_removed=True))
            except Exception as exc:
                logger.warning(f"Could not read commands from SQLite for listing: {exc}")

        filtered = sorted(
            command
            for command in commands
            if command
            and not self.is_blocked_command(command)
            and not self.is_removed_command(command)
        )
        fields = self._fetch_command_doc_fields(filtered) if self.state_store is None else {}
        sqlite_fields = {}
        if self.state_store is not None:
            try:
                sqlite_fields = self.state_store.get_command_stats(filtered)
            except Exception as exc:
                logger.warning(f"Could not read command stats from SQLite for listing: {exc}")
                sqlite_fields = {}

        entries: List[Dict[str, object]] = []
        for command in filtered:
            if self.state_store is not None:
                doc_fields = sqlite_fields.get(command, {})
            else:
                doc_fields = fields.get(command, {})
            accept_count = int(doc_fields.get("accept_count", 0) or 0)
            execute_count = int(doc_fields.get("execute_count", 0) or 0)
            stored_history = int(doc_fields.get("history_count", 0) or 0)
            parsed_history = int(history_counts.get(command, 0) or 0)
            history_count = max(stored_history, parsed_history)
            usage_score = accept_count + execute_count + history_count
            entries.append(
                {
                    "command": command,
                    "accept_count": accept_count,
                    "execute_count": execute_count,
                    "history_count": history_count,
                    "usage_score": usage_score,
                }
            )

        suspicious = self._detect_potential_wrong_commands(entries)

        potential_wrong: List[Dict[str, object]] = []
        commands_list: List[Dict[str, object]] = []
        for entry in entries:
            command = str(entry.get("command", "") or "")
            if not command:
                continue
            if command in suspicious:
                row = dict(entry)
                row["reason"] = suspicious[command]
                potential_wrong.append(row)
            else:
                commands_list.append(entry)

        potential_wrong.sort(
            key=lambda item: (
                int(item.get("usage_score", 0) or 0),
                str(item.get("command", "")),
            )
        )
        commands_list.sort(key=lambda item: str(item.get("command", "")))
        return {
            "potential_wrong": potential_wrong,
            "commands": commands_list,
            "total_commands": len(entries),
        }

    def add_manual_commands(self, commands: List[str]) -> Dict[str, int]:
        normalized_unique: List[str] = []
        seen: set[str] = set()
        for value in commands:
            normalized = self.normalize_command(str(value or ""))
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            normalized_unique.append(normalized)

        unblocked_removed = self.unmark_removed_commands(normalized_unique)
        eligible = [cmd for cmd in normalized_unique if not self.is_blocked_command(cmd)]
        if not eligible:
            return {
                "requested": len(commands),
                "normalized": len(normalized_unique),
                "inserted": 0,
                "already_present": 0,
                "unblocked_removed": unblocked_removed,
            }

        if self.state_store is not None:
            try:
                self.state_store.add_manual_commands(eligible)
            except Exception as exc:
                logger.warning(f"Could not persist manual commands in SQLite: {exc}")

        ids = [self.command_doc_id(command) for command in eligible]
        with self._io_lock:
            existing = self.collection.fetch(ids)
        existing_count = sum(1 for command_id in ids if command_id in existing)
        inserted = self.insert_commands(eligible)
        already_present = max(0, existing_count)
        return {
            "requested": len(commands),
            "normalized": len(normalized_unique),
            "inserted": inserted,
            "already_present": already_present,
            "unblocked_removed": unblocked_removed,
        }

    def remove_commands_exact(self, commands: List[str]) -> Dict[str, int]:
        normalized_unique: List[str] = []
        seen: set[str] = set()
        for value in commands:
            normalized = self.normalize_command(str(value or ""))
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            normalized_unique.append(normalized)

        guarded = self.mark_removed_commands(normalized_unique)
        removable = [cmd for cmd in normalized_unique if not self.is_blocked_command(cmd)]
        if not removable:
            return {
                "requested": len(commands),
                "normalized": len(normalized_unique),
                "vector_removed": 0,
                "guarded": guarded,
            }

        ids = [self.command_doc_id(command) for command in removable]
        existing_ids: List[str] = []
        with self._io_lock:
            existing = self.collection.fetch(ids)
            for command_id in ids:
                if command_id in existing:
                    existing_ids.append(command_id)
            if existing_ids:
                self.collection.delete(existing_ids)

        self._unregister_commands(removable)
        self._rebuild_token_indexes()

        return {
            "requested": len(commands),
            "normalized": len(normalized_unique),
            "vector_removed": len(existing_ids),
            "guarded": guarded,
        }

    def export_repair_snapshot(self, include_feedback: bool = True) -> Dict[str, object]:
        if self.state_store is not None:
            return self.state_store.export_payload()
        commands = sorted(self._load_existing_commands(limit=0))
        command_rows: List[Dict[str, object]] = []
        for command in commands:
            if not command:
                continue
            if self.is_blocked_command(command) or self.is_removed_command(command):
                continue
            command_id = self.command_doc_id(command)
            with self._io_lock:
                doc = self.collection.fetch([command_id]).get(command_id)
            if doc is None:
                continue
            fields = dict(doc.fields or {})
            command_rows.append(
                {
                    "command": command,
                    "accept_count": int(fields.get("accept_count", 0) or 0),
                    "execute_count": int(fields.get("execute_count", 0) or 0),
                    "history_count": int(fields.get("history_count", 0) or 0),
                    "last_accepted_at": int(fields.get("last_accepted_at", 0) or 0),
                }
            )

        feedback_rows: List[Dict[str, object]] = []
        if include_feedback and self.feedback_collection is not None:
            try:
                with self._io_lock:
                    doc_count = int(getattr(self.feedback_collection.stats, "doc_count", 0) or 0)
                    if doc_count > 0:
                        try:
                            results = self.feedback_collection.query(
                                vectors=None,
                                topk=doc_count,
                                output_fields=[
                                    "context_key",
                                    "suggestion_suffix",
                                    "accept_count",
                                    "last_accepted_at",
                                ],
                            )
                        except Exception:
                            query = zvec.VectorQuery("embedding_dummy", vector=[0.0])
                            results = self.feedback_collection.query(query, topk=doc_count)
                    else:
                        results = []
                for item in results:
                    fields = dict(item.fields or {})
                    context_key = str(fields.get("context_key", "") or "")
                    suggestion_suffix = str(fields.get("suggestion_suffix", "") or "")
                    if not context_key or not suggestion_suffix:
                        continue
                    feedback_rows.append(
                        {
                            "context_key": context_key,
                            "suggestion_suffix": suggestion_suffix,
                            "accept_count": int(fields.get("accept_count", 0) or 0),
                            "last_accepted_at": int(fields.get("last_accepted_at", 0) or 0),
                        }
                    )
            except Exception as exc:
                logger.warning(f"Failed to export feedback snapshot: {exc}")

        with self._io_lock:
            removed = sorted(self.removed_commands)

        return {
            "schema_version": 1,
            "commands": command_rows,
            "feedback": feedback_rows,
            "removed_commands": removed,
            "exported_at": int(time.time()),
        }

    def import_repair_snapshot(self, payload: Dict[str, object]) -> Dict[str, int]:
        if not isinstance(payload, dict):
            return {"commands_imported": 0, "feedback_imported": 0, "removed_imported": 0}

        if self.state_store is not None:
            result = self.state_store.import_payload(payload)
            try:
                commands = self.state_store.list_all_commands(include_removed=False)
                self.insert_commands(commands)
            except Exception as exc:
                logger.warning(f"Could not seed zvec cache from SQLite after import: {exc}")
            self.removed_commands = self._load_removed_commands()
            self._rebuild_token_indexes()
            return {
                "commands_imported": int(result.get("commands_imported", 0) or 0),
                "feedback_imported": int(result.get("feedback_imported", 0) or 0),
                "removed_imported": int(result.get("removed_imported", 0) or 0),
            }

        raw_commands = payload.get("commands", [])
        raw_feedback = payload.get("feedback", [])
        raw_removed = payload.get("removed_commands", [])

        command_rows = raw_commands if isinstance(raw_commands, list) else []
        feedback_rows = raw_feedback if isinstance(raw_feedback, list) else []
        removed_rows = raw_removed if isinstance(raw_removed, list) else []

        imported_commands = 0
        imported_feedback = 0

        command_ids: List[str] = []
        command_map: Dict[str, Dict[str, int | str]] = {}
        for row in command_rows:
            if not isinstance(row, dict):
                continue
            command = self.normalize_command(str(row.get("command", "") or ""))
            if not command:
                continue
            if self.is_blocked_command(command):
                continue
            if self.is_removed_command(command):
                continue
            command_map[command] = {
                "command": command,
                "accept_count": int(row.get("accept_count", 0) or 0),
                "execute_count": int(row.get("execute_count", 0) or 0),
                "history_count": int(row.get("history_count", 0) or 0),
                "last_accepted_at": int(row.get("last_accepted_at", 0) or 0),
            }
        commands = list(command_map.keys())
        command_ids = [self.command_doc_id(command) for command in commands]

        new_commands: List[str] = []
        now_ts = int(time.time())
        with self._io_lock:
            existing = self.collection.fetch(command_ids) if command_ids else {}
            for command, command_id in zip(commands, command_ids):
                row = command_map[command]
                existing_doc = existing.get(command_id)
                if existing_doc is None:
                    new_commands.append(command)
                    continue
                fields = dict(existing_doc.fields or {})
                accept = max(int(fields.get("accept_count", 0) or 0), int(row["accept_count"]))
                execute = max(int(fields.get("execute_count", 0) or 0), int(row["execute_count"]))
                history = max(int(fields.get("history_count", 0) or 0), int(row["history_count"]))
                last_accepted_at = max(
                    int(fields.get("last_accepted_at", 0) or 0), int(row["last_accepted_at"])
                )
                self.collection.update(
                    zvec.Doc(
                        id=command_id,
                        fields={
                            "accept_count": accept,
                            "execute_count": execute,
                            "history_count": history,
                            "last_accepted_at": last_accepted_at,
                        },
                    )
                )
                imported_commands += 1

        if new_commands:
            embeddings = self.model.encode(new_commands, show_progress_bar=False)
            docs = []
            for command, emb in zip(new_commands, embeddings):
                row = command_map.get(command, {})
                last_accepted_at = int(row.get("last_accepted_at", 0) or 0)
                timestamp = last_accepted_at if last_accepted_at > 0 else now_ts
                docs.append(
                    zvec.Doc(
                        id=self.command_doc_id(command),
                        fields={
                            "command": command,
                            "timestamp": timestamp,
                            "accept_count": int(row.get("accept_count", 0) or 0),
                            "history_count": int(row.get("history_count", 0) or 0),
                            "execute_count": int(row.get("execute_count", 0) or 0),
                            "last_accepted_at": timestamp,
                        },
                        vectors={"embedding": emb},
                    )
                )
            with self._io_lock:
                for i in range(0, len(docs), 100):
                    self.collection.insert(docs[i : i + 100])
            self.inserted_commands.update(new_commands)
            self._register_commands(new_commands)
            imported_commands += len(new_commands)

        if self.feedback_collection is not None:
            for row in feedback_rows:
                if not isinstance(row, dict):
                    continue
                context_key = str(row.get("context_key", "") or "")
                suggestion_suffix = str(row.get("suggestion_suffix", "") or "")
                if not context_key or not suggestion_suffix:
                    continue
                stat_id = self.context_stat_doc_id(context_key, suggestion_suffix)
                incoming_accept = int(row.get("accept_count", 0) or 0)
                incoming_last = int(row.get("last_accepted_at", 0) or 0)
                with self._io_lock:
                    existing_doc = self.feedback_collection.fetch([stat_id]).get(stat_id)
                    if existing_doc is None:
                        self.feedback_collection.insert(
                            zvec.Doc(
                                id=stat_id,
                                fields={
                                    "context_key": context_key,
                                    "suggestion_suffix": suggestion_suffix,
                                    "accept_count": incoming_accept,
                                    "last_accepted_at": incoming_last,
                                },
                                vectors={"embedding_dummy": [0.0]},
                            )
                        )
                    else:
                        fields = dict(existing_doc.fields or {})
                        merged_accept = max(
                            int(fields.get("accept_count", 0) or 0), incoming_accept
                        )
                        merged_last = max(
                            int(fields.get("last_accepted_at", 0) or 0), incoming_last
                        )
                        self.feedback_collection.update(
                            zvec.Doc(
                                id=stat_id,
                                fields={
                                    "accept_count": merged_accept,
                                    "last_accepted_at": merged_last,
                                },
                            )
                        )
                imported_feedback += 1

        removed_imported = self.mark_removed_commands([str(x or "") for x in removed_rows])
        return {
            "commands_imported": imported_commands,
            "feedback_imported": imported_feedback,
            "removed_imported": removed_imported,
        }

    def align_history_index_state_to_end(self, history_file: str) -> bool:
        history_path = Path(history_file).expanduser()
        if not history_path.exists() or not history_path.is_file():
            return False
        try:
            stat = history_path.stat()
            if self.state_store is not None:
                self.state_store.set_history_index_state(
                    history_file=str(history_path),
                    inode=stat.st_ino,
                    device=stat.st_dev,
                    offset=stat.st_size,
                )
            else:
                with self._io_lock:
                    self._save_index_state(
                        {
                            "history_file": str(history_path),
                            "inode": stat.st_ino,
                            "device": stat.st_dev,
                            "offset": stat.st_size,
                        }
                    )
            return True
        except Exception as exc:
            logger.warning(f"Could not align history index state: {exc}")
            return False

    def search(self, query: str, topk: int = 20) -> List[Tuple[str, float]]:
        if not query:
            return []

        topk = min(topk, 1024)

        try:
            with self._io_lock:
                query_vector = self.model.encode(query, show_progress_bar=False)
                query_obj = zvec.VectorQuery(
                    "embedding",
                    vector=query_vector,
                    param=zvec.HnswQueryParam(ef=100),
                )
                results = self.collection.query(query_obj, topk=topk)

            matches = []
            for res in results:
                command = res.fields.get("command", "")
                if command and not self.is_removed_command(command):
                    matches.append((command, res.score))

            logger.debug(f"Found {len(matches)} matches for query: '{query}'")
            return matches
        except Exception as exc:
            logger.error(f"Error searching database: {exc}")
            return []

    def search_commands_for_provenance(self, query: str, limit: int = 50) -> List[str]:
        normalized_query = self.normalize_command(query)
        if not normalized_query:
            return []

        row_limit = max(1, min(200, int(limit or 50)))
        semantic_topk = max(row_limit * 4, self.SEMANTIC_VECTOR_TOPN)
        ranked = self.search(normalized_query, topk=semantic_topk)

        out: List[str] = []
        seen: set[str] = set()
        for command, _ in ranked:
            normalized = self.normalize_command(command)
            if not normalized or normalized in seen:
                continue
            if self.is_blocked_command(normalized):
                continue
            if self.is_removed_command(normalized):
                continue
            seen.add(normalized)
            out.append(normalized)
            if len(out) >= row_limit:
                return out

        # Fallback for cases where vector recall is empty but lexical prefix still helps.
        for command in self._get_lexical_prefix_matches(normalized_query, topk=row_limit):
            normalized = self.normalize_command(command)
            if not normalized or normalized in seen:
                continue
            if self.is_blocked_command(normalized):
                continue
            if self.is_removed_command(normalized):
                continue
            seen.add(normalized)
            out.append(normalized)
            if len(out) >= row_limit:
                break
        return out

    def get_prefix_or_semantic_matches(self, prefix: str, topk: int = 100) -> List[Dict[str, str]]:
        if not prefix:
            return []

        normalized_prefix = self.normalize_command(prefix)
        if not normalized_prefix:
            return []

        lexical_matches = self._get_lexical_prefix_matches(normalized_prefix, topk=topk)
        typed_exec = self._prefix_exec_key(normalized_prefix)
        if lexical_matches:
            return [
                {
                    "command": command,
                    "match_mode": "prefix",
                    "typed_exec": typed_exec,
                    "candidate_exec": self._prefix_exec_key(command),
                }
                for command in lexical_matches[:topk]
            ]

        # Prefix miss: retrieve semantic candidates then re-rank with RapidFuzz.
        candidates = self.search(normalized_prefix, topk=self.SEMANTIC_VECTOR_TOPN)
        seen: set[str] = set()
        scored_entries: List[Tuple[float, int, Dict[str, str]]] = []
        for idx, (command, _) in enumerate(candidates):
            if command in seen:
                continue
            if self.is_blocked_command(command):
                continue
            if self.is_removed_command(command):
                continue
            seen.add(command)
            candidate_exec = self._prefix_exec_key(command)
            if not typed_exec or not candidate_exec:
                continue

            exec_score = self._fuzzy_exec_score(typed_exec, candidate_exec)
            same_exec = typed_exec == candidate_exec
            in_scope = same_exec or (exec_score >= self.EXEC_FUZZ_SCOPE_THRESHOLD)
            if not in_scope:
                continue

            command_score = self._fuzzy_command_score(normalized_prefix, command)
            if command_score < self.SEMANTIC_MIN_SCORE:
                continue
            recall_rank_score = ((self.SEMANTIC_VECTOR_TOPN - idx) / self.SEMANTIC_VECTOR_TOPN) * 100.0
            whole_word_miss = self._is_whole_word_semantic_miss(
                normalized_prefix,
                command,
                typed_exec,
                candidate_exec,
            )
            if whole_word_miss:
                # Semantic-first ranking for full-word misses like halt/terminate.
                rerank_score = (0.85 * recall_rank_score) + (0.15 * command_score)
                match_mode = "semantic_whole_word"
            else:
                rerank_score = (0.75 * command_score) + (0.25 * recall_rank_score)
                match_mode = "semantic_general"

            scored_entries.append(
                (
                    rerank_score,
                    -idx,
                    {
                        "command": command,
                        "match_mode": match_mode,
                        "typed_exec": typed_exec,
                        "candidate_exec": candidate_exec,
                    },
                )
            )

        if not scored_entries:
            return []

        scored_entries.sort(key=lambda item: (item[0], item[1]), reverse=True)
        top = [item[2] for item in scored_entries[:3]]
        return [
            {
                "command": item["command"],
                "match_mode": item["match_mode"],
                "typed_exec": item["typed_exec"],
                "candidate_exec": item["candidate_exec"],
            }
            for item in top
        ][:3]

    def rerank_candidates(
        self,
        buffer_context: str,
        candidates: List[str],
        working_directory: str | None = None,
    ) -> List[str]:
        if not candidates:
            return []

        filtered_pairs: List[Tuple[str, str]] = []
        for suffix in candidates:
            full_command = self.normalize_command(
                self.canonicalize_shell_spacing(
                    self.merge_buffer_and_suffix(buffer_context, suffix)
                )
            )
            if not full_command or self.is_blocked_command(full_command):
                continue
            filtered_pairs.append((suffix, full_command))

        if not filtered_pairs:
            return []

        candidates = [suffix for suffix, _ in filtered_pairs]
        full_command_by_suffix = {suffix: full_command for suffix, full_command in filtered_pairs}
        context_keys = self.extract_context_keys(buffer_context)
        full_commands = [full_command for _, full_command in filtered_pairs]
        command_ids = [self.command_doc_id(command) for command in full_commands]

        global_counts: Dict[str, int] = {}
        context_counts: Dict[str, int] = {}
        repo_accept_counts: Dict[str, int] = {}
        repo_execute_counts: Dict[str, int] = {}
        repo_task_counts: Dict[str, int] = {}
        execute_counts: Dict[str, int] = {}
        history_counts: Dict[str, int] = {}
        manual_30d_counts: Dict[str, int] = {}
        assist_30d_counts: Dict[str, int] = {}
        hours_since_last_manual: Dict[str, float] = {}
        repo_key = ""
        now_ts = int(time.time())
        manual_window_since = now_ts - (self.MANUAL_SIGNAL_WINDOW_DAYS * 24 * 3600)

        try:
            if self.state_store is not None:
                repo_key = self.resolve_repo_key(working_directory or "")
                stats = self.state_store.get_command_stats(full_commands)
                manual_counts_by_command: Dict[str, int] = {}
                assist_counts_by_command: Dict[str, int] = {}
                last_manual_ts_by_command: Dict[str, int] = {}
                if hasattr(self.state_store, "get_command_run_counts"):
                    raw_manual_counts = self.state_store.get_command_run_counts(
                        full_commands,
                        since_ts=manual_window_since,
                        labels=["HUMAN_TYPED"],
                    )
                    if isinstance(raw_manual_counts, dict):
                        manual_counts_by_command = raw_manual_counts
                    raw_assist_counts = self.state_store.get_command_run_counts(
                        full_commands,
                        since_ts=manual_window_since,
                        labels=["AI_SUGGESTED_HUMAN_RAN", "GS_SUGGESTED_HUMAN_RAN", "AI_EXECUTED"],
                    )
                    if isinstance(raw_assist_counts, dict):
                        assist_counts_by_command = raw_assist_counts
                if hasattr(self.state_store, "get_last_command_run_ts"):
                    raw_last_manual_ts = self.state_store.get_last_command_run_ts(
                        full_commands,
                        label="HUMAN_TYPED",
                    )
                    if isinstance(raw_last_manual_ts, dict):
                        last_manual_ts_by_command = raw_last_manual_ts
                for suffix, full_command in filtered_pairs:
                    row = stats.get(full_command, {})
                    global_counts[suffix] = int(row.get("accept_count", 0) or 0)
                    execute_counts[suffix] = int(row.get("execute_count", 0) or 0)
                    history_counts[suffix] = int(row.get("history_count", 0) or 0)
                    manual_30d_counts[suffix] = int(manual_counts_by_command.get(full_command, 0) or 0)
                    assist_30d_counts[suffix] = int(assist_counts_by_command.get(full_command, 0) or 0)
                    last_manual_ts = int(last_manual_ts_by_command.get(full_command, 0) or 0)
                    if last_manual_ts > 0:
                        hours_since_last_manual[suffix] = max(
                            0.0,
                            float(now_ts - last_manual_ts) / 3600.0,
                        )
                context_counts = self.state_store.get_feedback_counts(context_keys, candidates)
                for suffix in candidates:
                    context_counts[suffix] = int(context_counts.get(suffix, 0) or 0)
                    repo_accept_counts[suffix] = 0
                    repo_execute_counts[suffix] = 0
                    repo_task_counts[suffix] = 0
                    manual_30d_counts[suffix] = int(manual_30d_counts.get(suffix, 0) or 0)
                    assist_30d_counts[suffix] = int(assist_30d_counts.get(suffix, 0) or 0)
                if repo_key:
                    suffixes_by_task: Dict[str, List[str]] = defaultdict(list)
                    commands_by_task: Dict[str, List[str]] = defaultdict(list)
                    for suffix, full_command in filtered_pairs:
                        task_key = self.extract_task_key(full_command)
                        if task_key:
                            suffixes_by_task[task_key].append(suffix)
                            commands_by_task[task_key].append(full_command)
                            full_command_by_suffix[suffix] = full_command
                    for task_key, task_suffixes in suffixes_by_task.items():
                        accept_counts = self.state_store.get_repo_feedback_counts(
                            repo_key=repo_key,
                            task_key=task_key,
                            suffixes=task_suffixes,
                        )
                        execute_counts_for_commands = self.state_store.get_repo_execute_feedback_counts(
                            repo_key=repo_key,
                            task_key=task_key,
                            commands=commands_by_task.get(task_key, []),
                        )
                        for suffix in task_suffixes:
                            accept_v = int(accept_counts.get(suffix, 0) or 0)
                            full_command = full_command_by_suffix.get(suffix, "")
                            execute_v = int(execute_counts_for_commands.get(full_command, 0) or 0)
                            repo_accept_counts[suffix] = accept_v
                            repo_execute_counts[suffix] = execute_v
                            repo_task_counts[suffix] = accept_v + min(execute_v, self.REPO_EXECUTE_CAP)
            else:
                with self._io_lock:
                    command_docs = self.collection.fetch(command_ids)
                    for suffix, command_id in zip(candidates, command_ids):
                        doc = command_docs.get(command_id)
                        if doc is None:
                            global_counts[suffix] = 0
                            execute_counts[suffix] = 0
                            history_counts[suffix] = 0
                        else:
                            global_counts[suffix] = int(doc.fields.get("accept_count", 0) or 0)
                            execute_counts[suffix] = int(doc.fields.get("execute_count", 0) or 0)
                            history_counts[suffix] = int(doc.fields.get("history_count", 0) or 0)
                        manual_30d_counts[suffix] = 0
                        assist_30d_counts[suffix] = 0

                    for suffix in candidates:
                        context_counts[suffix] = 0
                        repo_accept_counts[suffix] = 0
                        repo_execute_counts[suffix] = 0
                        repo_task_counts[suffix] = 0

                    for context_key in context_keys:
                        stat_ids = [self.context_stat_doc_id(context_key, suffix) for suffix in candidates]
                        stat_docs = self.feedback_collection.fetch(stat_ids)
                        for suffix, stat_id in zip(candidates, stat_ids):
                            doc = stat_docs.get(stat_id)
                            if doc is not None:
                                context_counts[suffix] += int(doc.fields.get("accept_count", 0) or 0)

            help_intent = self._has_help_intent(buffer_context)
            ranked_entries: List[Tuple[int, str, float]] = []
            for rank, suffix in enumerate(candidates):
                score = self.blend_rank_score(
                    rank=rank,
                    repo_task_count=repo_task_counts.get(suffix, 0),
                    context_count=context_counts.get(suffix, 0),
                    accept_count=global_counts.get(suffix, 0),
                    execute_count=execute_counts.get(suffix, 0),
                    history_count=history_counts.get(suffix, 0),
                    manual_count=manual_30d_counts.get(suffix, 0),
                    assist_count=assist_30d_counts.get(suffix, 0),
                    hours_since_last_manual=hours_since_last_manual.get(suffix),
                    base_weight=self.SCORE_BASE_RANK,
                    alpha=self.SCORE_ALPHA,
                    beta=self.SCORE_BETA,
                    manual_weight=self.SCORE_MANUAL,
                    assist_weight=self.SCORE_ASSIST,
                    eta=self.SCORE_ACCEPT,
                    delta=self.SCORE_HISTORY,
                    manual_recency_weight=self.SCORE_MANUAL_RECENCY,
                    manual_recency_decay_hours=self.MANUAL_RECENCY_DECAY_HOURS,
                    history_cap=self.HISTORY_COUNT_CAP,
                )
                full_command = full_command_by_suffix.get(suffix, "")
                if not help_intent and self._is_help_like_command(full_command):
                    score -= self.HELP_DAMPENING_PENALTY
                ranked_entries.append((rank, suffix, score))
            ranked_entries.sort(key=lambda item: (item[2], -item[0]), reverse=True)

            total_repo_accepts = sum(int(repo_accept_counts.get(suffix, 0) or 0) for suffix in candidates)
            total_repo_execute_signal = sum(
                min(int(repo_execute_counts.get(suffix, 0) or 0), self.REPO_EXECUTE_CAP)
                for suffix in candidates
            )
            distinct_repo_suffixes = sum(1 for suffix in candidates if int(repo_task_counts.get(suffix, 0) or 0) > 0)
            tier = self.repo_confidence_tier(
                total_repo_accepts,
                distinct_repo_suffixes,
                total_repo_execute_signal,
            )
            repo_hit_suffixes = {suffix for suffix in candidates if int(repo_task_counts.get(suffix, 0) or 0) > 0}
            if not self.ENABLE_REPO_CONFIDENCE_TIERS:
                tier = "LOW"
            reranked, enforced_top1, enforced_top3, tier_strategy = self._apply_repo_tier_order(
                ranked_entries,
                repo_hit_suffixes,
                tier,
            )
            if (
                any(repo_task_counts.values())
                or any(context_counts.values())
                or any(global_counts.values())
                or any(execute_counts.values())
                or any(history_counts.values())
                or any(manual_30d_counts.values())
                or any(assist_30d_counts.values())
            ):
                top3 = reranked[:3]
                repo_top3 = sum(1 for suffix in top3 if suffix in repo_hit_suffixes)
                preview = ", ".join(
                    (
                        f"{suffix.strip() or '<empty>'}(repo={repo_task_counts.get(suffix, 0)},"
                        f"repo_acc={repo_accept_counts.get(suffix, 0)},repo_exec={repo_execute_counts.get(suffix, 0)},"
                        f"ctx={context_counts.get(suffix, 0)},acc={global_counts.get(suffix, 0)},"
                        f"exec={execute_counts.get(suffix, 0)},hist={history_counts.get(suffix, 0)},"
                        f"man30={manual_30d_counts.get(suffix, 0)},assist30={assist_30d_counts.get(suffix, 0)},"
                        f"hmanual={('-' if hours_since_last_manual.get(suffix) is None else f'{hours_since_last_manual.get(suffix, 0.0):.1f}')})"
                    )
                    for suffix in reranked[:3]
                )
                logger.debug(
                    "Feedback rerank for '%s': tier=%s strategy=%s repo_hits=%d repo_accepts=%d distinct_repo=%d top3_repo=%d enforce_top1=%s enforce_top3=%s help_intent=%s %s",
                    self.normalize_command(buffer_context),
                    tier,
                    tier_strategy,
                    len(repo_hit_suffixes),
                    total_repo_accepts,
                    distinct_repo_suffixes,
                    repo_top3,
                    enforced_top1,
                    enforced_top3,
                    help_intent,
                    preview,
                )
            return reranked
        except Exception as exc:
            logger.warning(f"Falling back to vector order; reranking failed: {exc}")
            return list(candidates)

    def _increment_command_feedback(self, full_command: str, accepted_at: int):
        if self.is_removed_command(full_command):
            return
        command_id = self.command_doc_id(full_command)
        existing = self.collection.fetch([command_id]).get(command_id)
        if existing is not None:
            prev = int(existing.fields.get("accept_count", 0) or 0)
            self.collection.update(
                zvec.Doc(
                    id=command_id,
                    fields={
                        "accept_count": prev + 1,
                        "last_accepted_at": accepted_at,
                    },
                )
            )
            return

        embedding = self.model.encode([full_command], show_progress_bar=False)[0]
        self.collection.insert(
            zvec.Doc(
                id=command_id,
                fields={
                    "command": full_command,
                    "timestamp": accepted_at,
                    "accept_count": 1,
                    "history_count": 0,
                    "execute_count": 0,
                    "last_accepted_at": accepted_at,
                },
                vectors={"embedding": embedding},
            )
        )
        self.inserted_commands.add(full_command)
        self._register_commands([full_command])

    def _increment_execute_count(self, full_command: str):
        if self.is_removed_command(full_command):
            return
        command_id = self.command_doc_id(full_command)
        now_ts = int(time.time())
        try:
            with self._io_lock:
                existing = self.collection.fetch([command_id]).get(command_id)
                if existing is not None:
                    prev = int(existing.fields.get("execute_count", 0) or 0)
                    self.collection.update(
                        zvec.Doc(
                            id=command_id,
                            fields={"execute_count": prev + 1},
                        )
                    )
                    return

            embedding = self.model.encode([full_command], show_progress_bar=False)[0]
            self.collection.insert(
                zvec.Doc(
                    id=command_id,
                    fields={
                        "command": full_command,
                        "timestamp": now_ts,
                        "accept_count": 0,
                        "history_count": 0,
                        "execute_count": 1,
                        "last_accepted_at": now_ts,
                    },
                    vectors={"embedding": embedding},
                )
            )
            self.inserted_commands.add(full_command)
            self._register_commands([full_command])
        except Exception as exc:
            logger.error(f"Failed to increment execute_count for '{full_command}': {exc}")

    def _increment_context_feedback(
        self, context_key: str, suggestion_suffix: str, accepted_at: int
    ):
        if not context_key or not suggestion_suffix:
            return

        stat_id = self.context_stat_doc_id(context_key, suggestion_suffix)
        existing = self.feedback_collection.fetch([stat_id]).get(stat_id)

        if existing is not None:
            prev = int(existing.fields.get("accept_count", 0) or 0)
            self.feedback_collection.update(
                zvec.Doc(
                    id=stat_id,
                    fields={
                        "accept_count": prev + 1,
                        "last_accepted_at": accepted_at,
                    },
                )
            )
            return

        self.feedback_collection.insert(
            zvec.Doc(
                id=stat_id,
                fields={
                    "context_key": context_key,
                    "suggestion_suffix": suggestion_suffix,
                    "accept_count": 1,
                    "last_accepted_at": accepted_at,
                },
                vectors={"embedding_dummy": [0.0]},
            )
        )

    def record_feedback(
        self,
        buffer_context: str,
        accepted_suggestion: str,
        accept_mode: str = "suffix_append",
        working_directory: str | None = None,
    ):
        accepted_suggestion = accepted_suggestion or ""
        mode = (accept_mode or "suffix_append").strip().lower()
        if mode == "replace_full":
            full_command = self.normalize_command(
                self.canonicalize_shell_spacing(accepted_suggestion)
            )
            context_payload = full_command
        else:
            full_command = self.normalize_command(
                self.canonicalize_shell_spacing(
                    self.merge_buffer_and_suffix(buffer_context, accepted_suggestion)
                )
            )
            context_payload = accepted_suggestion
        if not full_command:
            return
        if self.is_blocked_command(full_command):
            logger.debug("Skipping blocked command feedback record")
            return
        if self.is_removed_command(full_command):
            logger.debug("Skipping removed command feedback record")
            return

        # Store both 1-token and 2-token contexts derived from the finalized command.
        context_keys = self.extract_context_keys(full_command)
        task_key = self.extract_task_key(full_command)
        repo_key = self.resolve_repo_key(working_directory or "") if working_directory else ""
        now_ts = int(time.time())

        if self.state_store is not None:
            try:
                pairs = [(context_key, context_payload) for context_key in context_keys if context_key]
                repo_task_pairs: List[Tuple[str, str, str]] = []
                if repo_key and task_key and context_payload:
                    repo_task_pairs.append((repo_key, task_key, context_payload))
                self.state_store.record_feedback(
                    full_command,
                    pairs,
                    repo_task_pairs=repo_task_pairs,
                    ts=now_ts,
                )
                self.insert_commands([full_command])
                return
            except Exception as exc:
                logger.warning(f"Failed to record feedback in SQLite: {exc}")
                return

        try:
            with self._io_lock:
                self._increment_command_feedback(full_command, now_ts)
                for context_key in context_keys:
                    self._increment_context_feedback(context_key, context_payload, now_ts)
        except Exception as exc:
            logger.warning(f"Failed to record feedback in zvec: {exc}")

    def close(self):
        try:
            with self._io_lock:
                self._is_closed = True
                self.collection = None
                self.feedback_collection = None
                self.inserted_commands.clear()
                self.command_cache.clear()
                self.command_cache_by_exec.clear()
                self.token_candidates_by_context.clear()
                self.global_token_candidates.clear()
                self.removed_commands.clear()

            self.model = None
            gc.collect()
            logger.info("Vector database closed successfully")
        except Exception as exc:
            logger.error(f"Error closing vector database: {exc}")
