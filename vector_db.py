import gc
import hashlib
import json
import logging
import math
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import transformers
import zvec
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

    SCORE_ALPHA = 0.35
    SCORE_BETA = 0.15

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
        self.inserted_commands = self._load_existing_commands(limit=1024)

    @staticmethod
    def normalize_command(command: str) -> str:
        return " ".join((command or "").strip().split())

    @staticmethod
    def extract_context_key(buffer_context: str) -> str:
        tokens = CommandVectorDB.normalize_command(buffer_context).lower().split()
        if not tokens:
            return ""
        return " ".join(tokens[:2])

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
    def blend_rank_score(
        rank: int,
        context_count: int,
        global_count: int,
        alpha: float = SCORE_ALPHA,
        beta: float = SCORE_BETA,
    ) -> float:
        base = 1.0 / float(rank + 1)
        return base + (alpha * math.log1p(max(context_count, 0))) + (
            beta * math.log1p(max(global_count, 0))
        )

    @staticmethod
    def rerank_suffixes_from_counts(
        candidates: List[str],
        global_counts: Dict[str, int],
        context_counts: Dict[str, int],
        alpha: float = SCORE_ALPHA,
        beta: float = SCORE_BETA,
    ) -> List[str]:
        scored = []
        for rank, suffix in enumerate(candidates):
            final_score = CommandVectorDB.blend_rank_score(
                rank=rank,
                context_count=context_counts.get(suffix, 0),
                global_count=global_counts.get(suffix, 0),
                alpha=alpha,
                beta=beta,
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

    def _command_schema_is_v2_collection(self, collection: zvec.Collection) -> bool:
        try:
            field_names = {field.name for field in collection.schema.fields}
            return "accept_count" in field_names and "last_accepted_at" in field_names
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

        if not self._command_schema_is_v2_collection(collection):
            logger.warning("Command database is not v2 schema; recreating fresh database")
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

    def _read_history_commands_from_offset(
        self, history_path: Path, start_offset: int
    ) -> Tuple[List[str], int]:
        with open(history_path, "rb") as f:
            started_mid_line = False
            if start_offset > 0:
                f.seek(start_offset - 1)
                started_mid_line = f.read(1) not in (b"\n", b"\r")
            f.seek(start_offset)
            chunk = f.read()
            end_offset = f.tell()

        if not chunk:
            return ([], end_offset)

        text = chunk.decode("utf-8", errors="ignore")
        lines = text.splitlines()
        if started_mid_line and lines:
            lines = lines[1:]

        batch_seen = set()
        commands: List[str] = []
        for raw_line in lines:
            cmd = self.normalize_command(self._parse_history_line(raw_line))
            if not cmd or cmd in batch_seen:
                continue
            batch_seen.add(cmd)
            commands.append(cmd)

        return (commands, end_offset)

    def initialize_from_history(self, history_file: str):
        history_path = Path(history_file).expanduser()
        if not history_path.exists():
            logger.warning(f"History file not found: {history_file}")
            return

        logger.info(f"Syncing database from history: {history_file}")

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

            commands, end_offset = self._read_history_commands_from_offset(history_path, start_offset)
            if not commands:
                self._save_index_state(
                    {
                        "history_file": history_key,
                        "inode": stat.st_ino,
                        "device": stat.st_dev,
                        "offset": stat.st_size,
                    }
                )
                return

            logger.info(f"Found {len(commands)} new commands in history")
            inserted = self.insert_commands(commands)
            if inserted == 0:
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
            seen.add(normalized)
            normalized_unique.append(normalized)

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
        self.insert_commands([normalized])

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

    def get_exact_prefix_matches(self, prefix: str, topk: int = 20) -> List[str]:
        if not prefix:
            return []

        candidates = self.search(prefix, topk=topk * 2)
        matches = [command for command, _ in candidates if command.startswith(prefix)]
        return matches[:topk]

    def rerank_candidates(self, buffer_context: str, candidates: List[str]) -> List[str]:
        if not candidates:
            return []

        context_key = self.extract_context_key(buffer_context)
        full_commands = [
            self.normalize_command(f"{buffer_context}{suffix}") for suffix in candidates
        ]
        command_ids = [self.command_doc_id(command) for command in full_commands]

        global_counts: Dict[str, int] = {}
        context_counts: Dict[str, int] = {}

        try:
            with self._io_lock:
                command_docs = self.collection.fetch(command_ids)
                for suffix, command_id in zip(candidates, command_ids):
                    doc = command_docs.get(command_id)
                    if doc is None:
                        global_counts[suffix] = 0
                    else:
                        global_counts[suffix] = int(doc.fields.get("accept_count", 0) or 0)

                if context_key:
                    stat_ids = [self.context_stat_doc_id(context_key, suffix) for suffix in candidates]
                    stat_docs = self.feedback_collection.fetch(stat_ids)
                    for suffix, stat_id in zip(candidates, stat_ids):
                        doc = stat_docs.get(stat_id)
                        if doc is None:
                            context_counts[suffix] = 0
                        else:
                            context_counts[suffix] = int(doc.fields.get("accept_count", 0) or 0)

            return self.rerank_suffixes_from_counts(
                candidates=candidates,
                global_counts=global_counts,
                context_counts=context_counts,
                alpha=self.SCORE_ALPHA,
                beta=self.SCORE_BETA,
            )
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
                    "last_accepted_at": accepted_at,
                },
                vectors={"embedding": embedding},
            )
        )
        self.inserted_commands.add(full_command)

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

    def record_feedback(self, buffer_context: str, accepted_suggestion: str):
        accepted_suggestion = accepted_suggestion or ""
        full_command = self.normalize_command(f"{buffer_context}{accepted_suggestion}")
        if not full_command:
            return

        context_key = self.extract_context_key(buffer_context)
        now_ts = int(time.time())

        try:
            with self._io_lock:
                self._increment_command_feedback(full_command, now_ts)
                self._increment_context_feedback(context_key, accepted_suggestion, now_ts)
        except Exception as exc:
            logger.warning(f"Failed to record feedback in zvec: {exc}")

    def close(self):
        try:
            with self._io_lock:
                self._is_closed = True
                self.collection = None
                self.feedback_collection = None
                self.inserted_commands.clear()

            self.model = None
            gc.collect()
            logger.info("Vector database closed successfully")
        except Exception as exc:
            logger.error(f"Error closing vector database: {exc}")
