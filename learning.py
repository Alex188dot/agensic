import os
import json
import logging
from collections import defaultdict

logger = logging.getLogger("ghostshell.learning")

DATA_FILE = os.path.expanduser("~/.ghostshell/learned_data.json")

class Learner:
    def __init__(self):
        self.data = self._load_data()

    def _load_data(self):
        if not os.path.exists(DATA_FILE):
            return {"weights": {}}
        try:
            with open(DATA_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {"weights": {}}

    def _save_data(self):
        try:
            with open(DATA_FILE, "w") as f:
                json.dump(self.data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save learning data: {e}")

    def log_accept(self, buffer_context: str, accepted_suggestion: str):
        """
        Boost the score of the accepted suggestion for this specific buffer prefix.
        We simplify the buffer context to the first word or last token to generalize.
        """
        # Simple strategy: Key is the first word of the command (e.g., 'git', 'docker')
        # This helps rerank common flags.
        parts = buffer_context.split()
        if not parts:
            return

        key = parts[0] 
        # Also store specific context if length > 1
        sub_key = f"{key}_{parts[-1]}" if len(parts) > 1 else key

        if "weights" not in self.data:
            self.data["weights"] = {}

        # Increment weight for this suggestion under this key
        cmd_weights = self.data["weights"].get(key, {})
        current_weight = cmd_weights.get(accepted_suggestion, 0)
        cmd_weights[accepted_suggestion] = current_weight + 1
        self.data["weights"][key] = cmd_weights
        
        # Save periodically or immediately
        self._save_data()

    def rerank(self, buffer_context: str, candidates: list[str]) -> list[str]:
        """
        Reorder candidates based on historical accept rates.
        """
        if not candidates or "weights" not in self.data:
            return candidates

        parts = buffer_context.split()
        if not parts:
            return candidates
        
        key = parts[0]
        cmd_weights = self.data["weights"].get(key, {})

        # Sort candidates: higher weight first.
        # We use a stable sort so original LLM order is preserved for ties.
        def get_score(cand):
            # Exact match bonus
            return cmd_weights.get(cand, 0)

        # Python sort is stable (Timsort)
        candidates.sort(key=get_score, reverse=True)
        return candidates