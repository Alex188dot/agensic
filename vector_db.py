import os
import logging
import json
import gc
import threading
import zvec
import transformers
from sentence_transformers import SentenceTransformer
from pathlib import Path
from typing import List, Tuple
from datetime import datetime, timezone

# --- 1. SILENCE WARNINGS (Must be before other imports) ---
# Tell HuggingFace to NEVER check the internet
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
# Stop the "unauthenticated" warning
os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"

# Suppress the "BertModel LOAD REPORT" and other logs
transformers.logging.set_verbosity_error()
logging.getLogger("sentence_transformers").setLevel(logging.ERROR)

logger = logging.getLogger("ghostshell.vector_db")

class CommandVectorDB:
    """
    Vector database for storing and retrieving shell commands using semantic similarity.
    Uses zvec for storage and sentence-transformers for embeddings.
    """
    
    def __init__(self, db_path: str = None, model_name: str = 'all-MiniLM-L6-v2'):
        """
        Initialize the vector database.
        
        Args:
            db_path: Path to the zvec database directory
            model_name: Name of the sentence-transformers model to use
        """
        if db_path is None:
            db_path = os.path.expanduser("~/.ghostshell/zvec_commands")
        
        self.db_path = db_path
        self.state_file = os.path.join(os.path.dirname(self.db_path), "last_indexed_line")
        self.model_name = model_name
        self.dimensions = 384  # all-MiniLM-L6-v2 produces 384-dimensional vectors
        self._io_lock = threading.RLock()
        self._is_closed = False
        
        # Load the embedding model
        self.model = self._load_model()
        
        # Initialize or open the database
        self.collection = self._init_database()
        
        # Track commands we've already inserted to avoid duplicates
        self.inserted_commands = self._load_existing_commands()
    
    def _load_model(self) -> SentenceTransformer:
        """Load the sentence transformer model, downloading if necessary."""
        logger.info(f"Loading embedding model: {self.model_name}")

        # Keep CPU thread usage controlled for stable behavior in daemon mode.
        try:
            import torch
            torch.set_num_threads(1)
            torch.set_num_interop_threads(1)
        except Exception:
            pass

        try:
            # Try loading from local cache ONLY
            model = SentenceTransformer(self.model_name, local_files_only=True)
            logger.info("Model loaded from local cache")
        except Exception:
            # If the model isn't downloaded yet, temporarily allow online access
            logger.info("Model not found locally. Downloading once...")
            os.environ["HF_HUB_OFFLINE"] = "0"
            os.environ["TRANSFORMERS_OFFLINE"] = "0"
            model = SentenceTransformer(self.model_name)
            # Re-enable offline mode for the rest of the session
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

    def _quarantine_corrupted_db(self):
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        backup_path = f"{self.db_path}.corrupt.{timestamp}"
        os.rename(self.db_path, backup_path)
        logger.error(f"Moved corrupted database to {backup_path}")

    def _create_new_database(self) -> zvec.Collection:
        logger.info(f"Creating new database at {self.db_path}")
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

        schema = zvec.CollectionSchema(
            name="shell_commands",
            fields=[
                zvec.FieldSchema("command", zvec.DataType.STRING),
                zvec.FieldSchema("timestamp", zvec.DataType.INT64)
            ],
            vectors=zvec.VectorSchema(
                "embedding",
                zvec.DataType.VECTOR_FP32,
                self.dimensions
            ),
        )
        return zvec.create_and_open(path=self.db_path, schema=schema)

    def _ensure_vector_index(self):
        try:
            index_param = zvec.HnswIndexParam(m=16, ef_construction=200)
            self.collection.create_index("embedding", index_param)
            logger.info("HNSW index ready for vector field 'embedding'")
        except Exception as e:
            msg = str(e).lower()
            if "exist" in msg or "already" in msg:
                logger.debug("HNSW index already exists")
            else:
                logger.warning(f"Could not ensure vector index: {e}")

    def _validate_collection_health(self) -> bool:
        """
        Validate that the embedding index can actually be opened and queried.
        """
        try:
            query = zvec.VectorQuery(
                "embedding",
                vector=[0.0] * self.dimensions,
                param=zvec.HnswQueryParam(ef=8),
            )
            self.collection.query(query, topk=1)
            return True
        except Exception as e:
            if self._is_corruption_error(e):
                logger.error(f"Detected corrupted vector index during health check: {e}")
                return False
            logger.warning(f"Vector DB health check failed with non-fatal error: {e}")
            return True

    def _recreate_database_due_to_corruption(self) -> zvec.Collection:
        try:
            if hasattr(self, "collection") and self.collection and hasattr(self.collection, "close"):
                self.collection.close()
        except Exception:
            pass

        if os.path.exists(self.db_path):
            self._quarantine_corrupted_db()

        if os.path.exists(self.state_file):
            try:
                os.remove(self.state_file)
            except OSError:
                pass

        collection = self._create_new_database()
        self.collection = collection
        self._ensure_vector_index()
        return collection

    def _init_database(self) -> zvec.Collection:
        """Initialize or open the zvec database with recovery for corrupted stores."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

        if os.path.exists(self.db_path):
            logger.info(f"Opening existing database at {self.db_path}")
            try:
                collection = zvec.open(path=self.db_path)
            except Exception as e:
                if self._is_corruption_error(e):
                    self._quarantine_corrupted_db()
                    collection = self._create_new_database()
                else:
                    raise
        else:
            collection = self._create_new_database()

        self.collection = collection
        self._ensure_vector_index()
        if not self._validate_collection_health():
            collection = self._recreate_database_due_to_corruption()
        return collection
    
    def _load_existing_commands(self) -> set:
        """Load all existing commands from the database to avoid duplicates."""
        commands = set()
        try:
            with self._io_lock:
                # Query with max topk of 1024 (zvec limitation)
                # For larger databases, we'll rely on the insert deduplication
                query = zvec.VectorQuery("embedding", vector=[0.0] * self.dimensions)
                results = self.collection.query(query, topk=1024)
            for res in results:
                cmd = res.fields.get('command', '')
                if cmd:
                    commands.add(cmd)
            logger.info(f"Loaded {len(commands)} existing commands from database")
        except Exception as e:
            logger.warning(f"Could not load existing commands: {e}")
        
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
        if line.startswith(':'):
            parts = line.split(';', 1)
            if len(parts) == 2:
                line = parts[1].strip()
        return line
    
    def initialize_from_history(self, history_file: str):
        """
        Initialize the database with commands from .zsh_history.
        Only runs once on first initialization.
        
        Args:
            history_file: Path to the shell history file (e.g., ~/.zsh_history)
        """
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

            # Existing DB with no pointer file: seed incremental state and skip full rescan.
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

            with open(history_path, 'rb') as f:
                started_mid_line = False
                if start_offset > 0:
                    f.seek(start_offset - 1)
                    started_mid_line = f.read(1) not in (b"\n", b"\r")
                f.seek(start_offset)
                chunk = f.read()
                end_offset = f.tell()

            if not chunk:
                self._save_index_state(
                    {
                        "history_file": history_key,
                        "inode": stat.st_ino,
                        "device": stat.st_dev,
                        "offset": stat.st_size,
                    }
                )
                return

            text = chunk.decode('utf-8', errors='ignore')
            lines = text.splitlines()
            if started_mid_line and lines:
                # We started in the middle of a line; drop partial first row.
                lines = lines[1:]

            batch_seen = set()
            commands = []
            for raw_line in lines:
                cmd = self._parse_history_line(raw_line)
                if not cmd or cmd in batch_seen or cmd in self.inserted_commands:
                    continue
                batch_seen.add(cmd)
                commands.append(cmd)

            logger.info(f"Found {len(commands)} new commands in history")
            if commands:
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
        except Exception as e:
            logger.error(f"Error reading history file: {e}")
    
    def insert_commands(self, commands: List[str]) -> int:
        """
        Insert multiple commands into the database, avoiding duplicates.
        
        Args:
            commands: List of command strings to insert
        """
        if self._is_closed:
            return 0

        batch_seen = set()
        new_commands = []
        for cmd in commands:
            if not cmd or cmd in batch_seen or cmd in self.inserted_commands:
                continue
            batch_seen.add(cmd)
            new_commands.append(cmd)
        
        if not new_commands:
            logger.debug("No new commands to insert")
            return 0
        
        logger.info(f"Inserting {len(new_commands)} new commands")
        
        try:
            # Generate embeddings for all commands
            embeddings = self.model.encode(new_commands, show_progress_bar=False)
            
            # Create documents
            import time
            timestamp = int(time.time())
            docs = [
                zvec.Doc(
                    id=f"cmd_{hash(cmd) % (10**10)}_{i}",  # Simple hash-based ID
                    fields={"command": cmd, "timestamp": timestamp},
                    vectors={"embedding": emb}
                )
                for i, (cmd, emb) in enumerate(zip(new_commands, embeddings))
            ]
            
            # Insert in batches of 100 to avoid "Too many docs" error
            batch_size = 100
            with self._io_lock:
                for i in range(0, len(docs), batch_size):
                    batch = docs[i:i+batch_size]
                    self.collection.insert(batch)
                    logger.debug(f"Inserted batch {i//batch_size + 1} ({len(batch)} commands)")
            
            # Update our tracking set
            self.inserted_commands.update(new_commands)
            
            logger.info(f"Successfully inserted {len(docs)} commands")
            return len(docs)
            
        except Exception as e:
            logger.error(f"Error inserting commands: {e}")
            return 0
    
    def insert_command(self, command: str):
        """
        Insert a single command into the database.
        
        Args:
            command: Command string to insert
        """
        if not command or command in self.inserted_commands:
            return
        
        self.insert_commands([command])
    
    def search(self, query: str, topk: int = 20) -> List[Tuple[str, float]]:
        """
        Search for similar commands using semantic similarity.
        
        Args:
            query: The query string (partial command)
            topk: Number of results to return (max 1024)
            
        Returns:
            List of tuples (command, score) sorted by relevance
        """
        if not query:
            return []
        
        # Limit topk to zvec's maximum
        topk = min(topk, 1024)
        
        try:
            with self._io_lock:
                # Generate embedding for query
                query_vector = self.model.encode(query, show_progress_bar=False)

                # Search the database - use 'param' not 'params'
                query_obj = zvec.VectorQuery(
                    "embedding",
                    vector=query_vector,
                    param=zvec.HnswQueryParam(ef=100)  # Search parameter for HNSW
                )
                results = self.collection.query(query_obj, topk=topk)
            
            # Extract commands and scores
            matches = []
            for res in results:
                cmd = res.fields.get('command', '')
                if cmd:
                    matches.append((cmd, res.score))
            
            logger.debug(f"Found {len(matches)} matches for query: '{query}'")
            return matches
            
        except Exception as e:
            logger.error(f"Error searching database: {e}")
            return []
    
    def get_exact_prefix_matches(self, prefix: str, topk: int = 20) -> List[str]:
        """
        Get commands that start with the exact prefix.
        This is a fast filter that can be applied before or instead of vector search.
        
        Args:
            prefix: The prefix to match
            topk: Maximum number of results
            
        Returns:
            List of matching commands
        """
        if not prefix:
            return []
        
        # First do a vector search to get candidates
        candidates = self.search(prefix, topk=topk * 2)
        
        # Filter for exact prefix matches
        matches = [cmd for cmd, score in candidates if cmd.startswith(prefix)]
        
        return matches[:topk]

    def close(self):
        """
        Close the database and clean up resources.
        """
        try:
            with self._io_lock:
                self._is_closed = True
                if hasattr(self, 'collection') and self.collection:
                    if hasattr(self.collection, 'close'):
                        self.collection.close()
                    logger.info("Vector database closed successfully")
                self.collection = None
                self.inserted_commands.clear()

            self.model = None
            gc.collect()

            try:
                import multiprocessing as mp
                for child in mp.active_children():
                    child.join(timeout=1)
            except Exception:
                pass
        except Exception as e:
            logger.error(f"Error closing vector database: {e}")
