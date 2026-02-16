import os
import logging
import zvec
import transformers
from sentence_transformers import SentenceTransformer
from pathlib import Path
from typing import List, Tuple

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
        self.model_name = model_name
        self.dimensions = 384  # all-MiniLM-L6-v2 produces 384-dimensional vectors
        
        # Load the embedding model
        self.model = self._load_model()
        
        # Initialize or open the database
        self.collection = self._init_database()
        
        # Track commands we've already inserted to avoid duplicates
        self.inserted_commands = self._load_existing_commands()
    
    def _load_model(self) -> SentenceTransformer:
        """Load the sentence transformer model, downloading if necessary."""
        logger.info(f"Loading embedding model: {self.model_name}")
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
    
    def _init_database(self) -> zvec.Collection:
        """Initialize or open the zvec database."""
        if os.path.exists(self.db_path):
            logger.info(f"Opening existing database at {self.db_path}")
            collection = zvec.open(path=self.db_path)
        else:
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
            collection = zvec.create_and_open(path=self.db_path, schema=schema)
            
            # Create HNSW index for efficient similarity search
            try:
                index_param = zvec.HnswIndexParam(m=16, ef_construction=200)
                collection.create_index("embedding", index_param)
                logger.info("Created HNSW index for vector field")
            except Exception as e:
                logger.warning(f"Could not create index (may already exist): {e}")
        
        return collection
    
    def _load_existing_commands(self) -> set:
        """Load all existing commands from the database to avoid duplicates."""
        commands = set()
        try:
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
        
        logger.info(f"Initializing database from history: {history_file}")
        
        # Read all commands from history
        commands = []
        try:
            with open(history_path, 'rb') as f:
                content = f.read().decode('utf-8', errors='ignore')
                
            # Parse zsh history format (handles both simple and extended format)
            for line in content.splitlines():
                line = line.strip()
                if not line:
                    continue
                
                # Handle extended zsh history format: ": timestamp:duration;command"
                if line.startswith(':'):
                    parts = line.split(';', 1)
                    if len(parts) == 2:
                        line = parts[1].strip()
                
                # Skip duplicates and empty commands
                if line and line not in self.inserted_commands:
                    commands.append(line)
            
            logger.info(f"Found {len(commands)} unique commands in history")
            
            # Insert in batches
            self.insert_commands(commands)
            
        except Exception as e:
            logger.error(f"Error reading history file: {e}")
    
    def insert_commands(self, commands: List[str]):
        """
        Insert multiple commands into the database, avoiding duplicates.
        
        Args:
            commands: List of command strings to insert
        """
        # Filter out duplicates
        new_commands = [cmd for cmd in commands if cmd not in self.inserted_commands]
        
        if not new_commands:
            logger.debug("No new commands to insert")
            return
        
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
            for i in range(0, len(docs), batch_size):
                batch = docs[i:i+batch_size]
                self.collection.insert(batch)
                logger.debug(f"Inserted batch {i//batch_size + 1} ({len(batch)} commands)")
            
            # Update our tracking set
            self.inserted_commands.update(new_commands)
            
            logger.info(f"Successfully inserted {len(docs)} commands")
            
        except Exception as e:
            logger.error(f"Error inserting commands: {e}")
    
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
