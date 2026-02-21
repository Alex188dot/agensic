import gc
import hashlib
import json
import logging
import math
import os
import shlex
import shutil
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import transformers
import zvec
from rapidfuzz import fuzz
from sentence_transformers import SentenceTransformer

# Tell HuggingFace to avoid implicit network checks by default.
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"

transformers.logging.set_verbosity_error()
logging.getLogger("sentence_transformers").setLevel(logging.ERROR)

logger = logging.getLogger("ghostshell.vector_db")


class CommandVectorDB:
    """
    Vector database for storing and retrieving shell commands using semantic search.

    This implementation assumes a fresh v2 schema and does not run migrations.
    """

    SCORE_ALPHA = 1.10
    SCORE_BETA = 0.35
    SCORE_EXECUTE = 0.20
    SCORE_HISTORY = 0.10
    BLOCKED_EXECUTABLES = {"rm"}
    PREFIX_SCAN_LIMIT = 2000
    SEMANTIC_VECTOR_TOPN = 80
    EXEC_FUZZ_SCOPE_THRESHOLD = 84.0
    TYPO_EXEC_THRESHOLD = 90.0
    SEMANTIC_MIN_SCORE = 55.0

    def __init__(self, db_path: str = None, model_name: str = "all-MiniLM-L6-v2"):
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
        self.model_name = model_name
        self.dimensions = 384
        self._io_lock = threading.RLock()
        self._is_closed = False

        self.model = self._load_model()
        self.collection = self._init_command_collection(self.db_path)
        self.feedback_collection = self._init_feedback_collection(self.feedback_db_path)
        self.command_cache: set[str] = set()
        self.command_cache_by_exec: Dict[str, set[str]] = defaultdict(set)
        self._history_cache_warmed_for: set[str] = set()
        self.inserted_commands = self._load_existing_commands(limit=1024)
        self._register_commands(self.inserted_commands)

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
            self.command_cache.add(normalized)
            exec_key = self._prefix_exec_key(normalized)
            if exec_key:
                self.command_cache_by_exec[exec_key].add(normalized)

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

    def _warm_prefix_cache_from_history(self, history_path: Path):
        key = str(history_path)
        if key in self._history_cache_warmed_for:
            return
        if not history_path.exists() or not history_path.is_file():
            return

        commands: set[str] = set()
        try:
            with open(history_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    cmd = self.normalize_command(self._parse_history_line(line))
                    if cmd:
                        commands.add(cmd)
            self._register_commands(commands)
            self._history_cache_warmed_for.add(key)
            logger.info(f"Warmed lexical prefix cache with {len(commands)} commands from history")
        except Exception as exc:
            logger.warning(f"Could not warm lexical prefix cache from history: {exc}")

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
        if not tokens:
            return ""

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

            return token

        return ""

    @staticmethod
    def is_blocked_command(command: str) -> bool:
        tokens = CommandVectorDB.tokenize_command(command)
        executable = CommandVectorDB.extract_executable(tokens)
        if not executable:
            return False
        executable_name = os.path.basename(executable).strip().lower()
        return executable_name in CommandVectorDB.BLOCKED_EXECUTABLES

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
        context_count: int,
        accept_count: int,
        execute_count: int = 0,
        history_count: int = 0,
        alpha: float = SCORE_ALPHA,
        beta: float = SCORE_BETA,
        gamma: float = SCORE_EXECUTE,
        delta: float = SCORE_HISTORY,
    ) -> float:
        base = 1.0 / float(rank + 1)
        return base + (alpha * math.log1p(max(context_count, 0))) + (
            beta * math.log1p(max(accept_count, 0))
        ) + (gamma * math.log1p(max(execute_count, 0))) + (
            delta * math.log1p(max(history_count, 0))
        )

    @staticmethod
    def rerank_suffixes_from_counts(
        candidates: List[str],
        global_counts: Dict[str, int],
        context_counts: Dict[str, int],
        execute_counts: Optional[Dict[str, int]] = None,
        history_counts: Optional[Dict[str, int]] = None,
        alpha: float = SCORE_ALPHA,
        beta: float = SCORE_BETA,
        gamma: float = SCORE_EXECUTE,
        delta: float = SCORE_HISTORY,
    ) -> List[str]:
        execute_counts = execute_counts or {}
        history_counts = history_counts or {}
        scored = []
        for rank, suffix in enumerate(candidates):
            final_score = CommandVectorDB.blend_rank_score(
                rank=rank,
                context_count=context_counts.get(suffix, 0),
                accept_count=global_counts.get(suffix, 0),
                execute_count=execute_counts.get(suffix, 0),
                history_count=history_counts.get(suffix, 0),
                alpha=alpha,
                beta=beta,
                gamma=gamma,
                delta=delta,
            )
            scored.append((rank, suffix, final_score))

        scored.sort(key=lambda item: item[2], reverse=True)
        return [suffix for _, suffix, _ in scored]

    def _load_model(self) -> SentenceTransformer:
        logger.info(f"Loading embedding model: {self.model_name}")
        try:
            import torch

            torch.set_num_threads(1)
            torch.set_num_interop_threads(1)
        except Exception:
            pass

        try:
            model = SentenceTransformer(self.model_name, local_files_only=True)
            logger.info("Model loaded from local cache")
        except Exception:
            logger.info("Model not found locally. Downloading once...")
            os.environ["HF_HUB_OFFLINE"] = "0"
            os.environ["TRANSFORMERS_OFFLINE"] = "0"
            model = SentenceTransformer(self.model_name)
            os.environ["HF_HUB_OFFLINE"] = "1"
            os.environ["TRANSFORMERS_OFFLINE"] = "1"
            logger.info("Model downloaded and cached")

        return model

    def _is_corruption_error(self, error: Exception) -> bool:
        msg = str(error).lower()
        return any(
            token in msg
            for token in [
                "checksum",
                "corrupt",
                "invalid checksum",
                "vector indexer not found",
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
            with self._io_lock:
                query = zvec.VectorQuery("embedding", vector=[0.0] * self.dimensions)
                results = self.collection.query(query, topk=min(limit, 1024))
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
        history_path = Path(history_file).expanduser()
        if not history_path.exists():
            logger.warning(f"History file not found: {history_file}")
            return

        logger.info(f"Syncing database from history: {history_file}")
        self._warm_prefix_cache_from_history(history_path)

        try:
            stat = history_path.stat()
            state = self._load_index_state()
            history_key = str(history_path)
            saved_offset = state.get("offset")
            saved_offset_int = int(saved_offset) if isinstance(saved_offset, int) else 0

            if not state and self.inserted_commands:
                self._save_index_state(
                    {
                        "history_file": history_key,
                        "inode": stat.st_ino,
                        "device": stat.st_dev,
                        "offset": stat.st_size,
                    }
                )
                logger.info("Database already populated; seeded incremental history pointer")
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
                self._save_index_state(
                    {
                        "history_file": history_key,
                        "inode": stat.st_ino,
                        "device": stat.st_dev,
                        "offset": stat.st_size,
                    }
                )
                return

            total_occurrences = sum(command_counts.values())
            logger.info(
                f"Found {total_occurrences} new history entries across {len(command_counts)} unique commands"
            )
            inserted = self.upsert_history_commands(command_counts)
            if inserted < 0:
                logger.warning("History sync failed, keeping previous history pointer")
                return

            self._save_index_state(
                {
                    "history_file": history_key,
                    "inode": stat.st_ino,
                    "device": stat.st_dev,
                    "offset": end_offset,
                }
            )
        except Exception as exc:
            logger.error(f"Error reading history file: {exc}")

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

    def insert_command(self, command: str):
        normalized = self.normalize_command(command)
        if not normalized:
            return
        if self.is_blocked_command(normalized):
            logger.debug("Skipping blocked command execute_count update")
            return
        self._register_commands([normalized])
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
            n = int(count or 0)
            if n <= 0:
                continue
            normalized_counts[normalized] = normalized_counts.get(normalized, 0) + n

        if not normalized_counts:
            return 0

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
                if command:
                    matches.append((command, res.score))

            logger.debug(f"Found {len(matches)} matches for query: '{query}'")
            return matches
        except Exception as exc:
            logger.error(f"Error searching database: {exc}")
            return []

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
            rerank_score = (0.75 * command_score) + (0.25 * recall_rank_score)

            mode = "semantic_general"
            if not same_exec and exec_score >= self.TYPO_EXEC_THRESHOLD:
                mode = "semantic_typo"

            scored_entries.append(
                (
                    rerank_score,
                    -idx,
                    {
                        "command": command,
                        "match_mode": mode,
                        "typed_exec": typed_exec,
                        "candidate_exec": candidate_exec,
                    },
                )
            )

        if not scored_entries:
            return []

        scored_entries.sort(key=lambda item: (item[0], item[1]), reverse=True)
        best = scored_entries[0][2]
        return [
            {
                "command": best["command"],
                "match_mode": best["match_mode"],
                "typed_exec": best["typed_exec"],
                "candidate_exec": best["candidate_exec"],
            }
        ][:1]

    def rerank_candidates(self, buffer_context: str, candidates: List[str]) -> List[str]:
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
        context_keys = self.extract_context_keys(buffer_context)
        full_commands = [full_command for _, full_command in filtered_pairs]
        command_ids = [self.command_doc_id(command) for command in full_commands]

        global_counts: Dict[str, int] = {}
        context_counts: Dict[str, int] = {}
        execute_counts: Dict[str, int] = {}
        history_counts: Dict[str, int] = {}

        try:
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

                for suffix in candidates:
                    context_counts[suffix] = 0

                for context_key in context_keys:
                    stat_ids = [self.context_stat_doc_id(context_key, suffix) for suffix in candidates]
                    stat_docs = self.feedback_collection.fetch(stat_ids)
                    for suffix, stat_id in zip(candidates, stat_ids):
                        doc = stat_docs.get(stat_id)
                        if doc is not None:
                            context_counts[suffix] += int(doc.fields.get("accept_count", 0) or 0)

            reranked = self.rerank_suffixes_from_counts(
                candidates=candidates,
                global_counts=global_counts,
                context_counts=context_counts,
                execute_counts=execute_counts,
                history_counts=history_counts,
                alpha=self.SCORE_ALPHA,
                beta=self.SCORE_BETA,
                gamma=self.SCORE_EXECUTE,
                delta=self.SCORE_HISTORY,
            )
            if (
                any(context_counts.values())
                or any(global_counts.values())
                or any(execute_counts.values())
                or any(history_counts.values())
            ):
                preview = ", ".join(
                    f"{suffix.strip() or '<empty>'}(ctx={context_counts.get(suffix, 0)},acc={global_counts.get(suffix, 0)},exec={execute_counts.get(suffix, 0)},hist={history_counts.get(suffix, 0)})"
                    for suffix in reranked[:3]
                )
                logger.info(f"Feedback rerank for '{self.normalize_command(buffer_context)}': {preview}")
            return reranked
        except Exception as exc:
            logger.warning(f"Falling back to vector order; reranking failed: {exc}")
            return list(candidates)

    def _increment_command_feedback(self, full_command: str, accepted_at: int):
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

    def record_feedback(self, buffer_context: str, accepted_suggestion: str, accept_mode: str = "suffix_append"):
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

        # Store both 1-token and 2-token contexts derived from the finalized command.
        context_keys = self.extract_context_keys(full_command)
        now_ts = int(time.time())

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
                self._history_cache_warmed_for.clear()

            self.model = None
            gc.collect()
            logger.info("Vector database closed successfully")
        except Exception as exc:
            logger.error(f"Error closing vector database: {exc}")
