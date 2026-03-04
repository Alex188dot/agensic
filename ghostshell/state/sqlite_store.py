import json
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Dict, Iterable, List, Optional, Tuple
from .journal import EventJournal
from .snapshot import SnapshotManager


class SQLiteStateStore:
    def __init__(self, db_path: str, journal: Optional[EventJournal] = None):
        self.db_path = os.path.expanduser(db_path)
        self.journal = journal
        self._lock = threading.RLock()
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.init_schema()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path, timeout=5, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA busy_timeout=5000;")
            conn.execute("PRAGMA foreign_keys=ON;")
            yield conn
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self._lock, self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS commands (
                    command TEXT PRIMARY KEY,
                    accept_count INTEGER NOT NULL DEFAULT 0,
                    execute_count INTEGER NOT NULL DEFAULT 0,
                    history_count INTEGER NOT NULL DEFAULT 0,
                    last_accepted_at INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS feedback_context (
                    context_key TEXT NOT NULL,
                    suggestion_suffix TEXT NOT NULL,
                    accept_count INTEGER NOT NULL DEFAULT 0,
                    last_accepted_at INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (context_key, suggestion_suffix)
                );

                CREATE TABLE IF NOT EXISTS repo_feedback_context (
                    repo_key TEXT NOT NULL,
                    task_key TEXT NOT NULL,
                    suggestion_suffix TEXT NOT NULL,
                    accept_count INTEGER NOT NULL DEFAULT 0,
                    last_accepted_at INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (repo_key, task_key, suggestion_suffix)
                );

                CREATE TABLE IF NOT EXISTS repo_feedback_execute_context (
                    repo_key TEXT NOT NULL,
                    task_key TEXT NOT NULL,
                    suggestion_suffix TEXT NOT NULL,
                    execute_count INTEGER NOT NULL DEFAULT 0,
                    last_executed_at INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (repo_key, task_key, suggestion_suffix)
                );

                CREATE TABLE IF NOT EXISTS removed_commands (
                    command TEXT PRIMARY KEY,
                    removed_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS command_runs (
                    run_id TEXT PRIMARY KEY,
                    ts INTEGER NOT NULL,
                    command TEXT NOT NULL,
                    label TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 0,
                    agent TEXT NOT NULL DEFAULT '',
                    agent_name TEXT NOT NULL DEFAULT '',
                    provider TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '',
                    raw_model TEXT NOT NULL DEFAULT '',
                    normalized_model TEXT NOT NULL DEFAULT '',
                    model_fingerprint TEXT NOT NULL DEFAULT '',
                    evidence_tier TEXT NOT NULL DEFAULT '',
                    agent_source TEXT NOT NULL DEFAULT '',
                    registry_version TEXT NOT NULL DEFAULT '',
                    registry_status TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT 'unknown',
                    working_directory TEXT NOT NULL DEFAULT '',
                    exit_code INTEGER,
                    duration_ms INTEGER,
                    shell_pid INTEGER,
                    evidence_json TEXT NOT NULL DEFAULT '[]',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at INTEGER NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_command_runs_ts ON command_runs(ts DESC);
                CREATE INDEX IF NOT EXISTS idx_command_runs_label_ts ON command_runs(label, ts DESC);
                CREATE INDEX IF NOT EXISTS idx_command_runs_command_ts ON command_runs(command, ts DESC);

                CREATE TABLE IF NOT EXISTS history_index_state (
                    history_file TEXT PRIMARY KEY,
                    inode INTEGER NOT NULL,
                    device INTEGER NOT NULL,
                    offset INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS applied_events (
                    event_id TEXT PRIMARY KEY,
                    applied_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            self._ensure_command_runs_columns(conn)
            conn.commit()

    @staticmethod
    def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {str(row["name"] or "").strip() for row in rows if str(row["name"] or "").strip()}

    def _ensure_command_runs_columns(self, conn: sqlite3.Connection) -> None:
        existing = self._table_columns(conn, "command_runs")
        required_columns = {
            "agent_name": "TEXT NOT NULL DEFAULT ''",
            "raw_model": "TEXT NOT NULL DEFAULT ''",
            "normalized_model": "TEXT NOT NULL DEFAULT ''",
            "evidence_tier": "TEXT NOT NULL DEFAULT ''",
            "agent_source": "TEXT NOT NULL DEFAULT ''",
            "registry_version": "TEXT NOT NULL DEFAULT ''",
            "registry_status": "TEXT NOT NULL DEFAULT ''",
            "duration_ms": "INTEGER",
        }
        for name, ddl in required_columns.items():
            if name in existing:
                continue
            conn.execute(f"ALTER TABLE command_runs ADD COLUMN {name} {ddl}")
            existing.add(name)

    def integrity_check(self) -> Tuple[bool, str]:
        try:
            with self._lock, self._conn() as conn:
                row = conn.execute("PRAGMA integrity_check;").fetchone()
            msg = str(row[0] if row else "").strip().lower()
            if msg == "ok":
                return (True, "")
            return (False, msg or "integrity_check_failed")
        except Exception as exc:
            return (False, str(exc))

    def access_mode(self) -> str:
        try:
            with self._lock, self._conn() as conn:
                conn.execute("BEGIN IMMEDIATE;")
                conn.rollback()
            return "writable"
        except sqlite3.OperationalError as exc:
            low = str(exc).lower()
            if "readonly" in low or "read-only" in low:
                return "read_only"
            return "error"
        except Exception:
            return "error"

    @staticmethod
    def _chunk(values: List[str], size: int = 250) -> Iterable[List[str]]:
        for i in range(0, len(values), size):
            yield values[i : i + size]

    @staticmethod
    def _clean_command(value: str) -> str:
        return str(value or "").strip()

    @staticmethod
    def _json_dumps(value: object, fallback: str) -> str:
        try:
            return json.dumps(value, separators=(",", ":"))
        except Exception:
            return fallback

    def _build_event(self, event_type: str, payload: Dict[str, object]) -> Dict[str, object]:
        out = dict(payload or {})
        out["type"] = str(event_type or "")
        out["event_id"] = str(out.get("event_id") or uuid.uuid4())
        out["ts"] = int(out.get("ts", 0) or 0) or int(time.time())
        return out

    def _ensure_command_row(self, conn: sqlite3.Connection, command: str, now_ts: int) -> None:
        conn.execute(
            """
            INSERT INTO commands(command, accept_count, execute_count, history_count, last_accepted_at, created_at, updated_at)
            VALUES(?, 0, 0, 0, 0, ?, ?)
            ON CONFLICT(command) DO NOTHING
            """,
            (command, now_ts, now_ts),
        )

    def _event_applied(self, conn: sqlite3.Connection, event_id: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM applied_events WHERE event_id = ? LIMIT 1",
            (event_id,),
        ).fetchone()
        return bool(row)

    def _mark_event_applied(self, conn: sqlite3.Connection, event_id: str, ts: int) -> None:
        conn.execute(
            """
            INSERT INTO applied_events(event_id, applied_at)
            VALUES(?, ?)
            ON CONFLICT(event_id) DO NOTHING
            """,
            (event_id, int(ts or time.time())),
        )

    def _apply_event_tx(self, conn: sqlite3.Connection, event: Dict[str, object]) -> bool:
        event_id = str(event.get("event_id", "") or "").strip()
        if not event_id:
            return False
        if self._event_applied(conn, event_id):
            return False

        etype = str(event.get("type", "") or "")
        ts = int(event.get("ts", 0) or 0) or int(time.time())
        command = self._clean_command(str(event.get("command", "") or ""))

        if etype == "command_execute":
            if command:
                delta = max(1, int(event.get("delta", 1) or 1))
                self._ensure_command_row(conn, command, ts)
                conn.execute(
                    """
                    UPDATE commands
                    SET execute_count = execute_count + ?, updated_at = ?
                    WHERE command = ?
                    """,
                    (delta, ts, command),
                )
        elif etype == "feedback_accept":
            scope = str(event.get("scope", "command") or "command")
            if scope == "command":
                if command:
                    delta = max(1, int(event.get("delta", 1) or 1))
                    self._ensure_command_row(conn, command, ts)
                    conn.execute(
                        """
                        UPDATE commands
                        SET accept_count = accept_count + ?,
                            last_accepted_at = MAX(last_accepted_at, ?),
                            updated_at = ?
                        WHERE command = ?
                        """,
                        (delta, ts, ts, command),
                    )
            elif scope == "context":
                context_key = str(event.get("context_key", "") or "").strip()
                suffix = str(event.get("suggestion_suffix", "") or "")
                if context_key and suffix:
                    delta = max(1, int(event.get("delta", 1) or 1))
                    conn.execute(
                        """
                        INSERT INTO feedback_context(context_key, suggestion_suffix, accept_count, last_accepted_at)
                        VALUES(?, ?, ?, ?)
                        ON CONFLICT(context_key, suggestion_suffix)
                        DO UPDATE SET
                            accept_count = feedback_context.accept_count + excluded.accept_count,
                            last_accepted_at = MAX(feedback_context.last_accepted_at, excluded.last_accepted_at)
                        """,
                        (context_key, suffix, delta, ts),
                    )
            elif scope == "repo_context":
                repo_key = str(event.get("repo_key", "") or "").strip()
                task_key = str(event.get("task_key", "") or "").strip()
                suffix = str(event.get("suggestion_suffix", "") or "")
                if repo_key and task_key and suffix:
                    delta = max(1, int(event.get("delta", 1) or 1))
                    conn.execute(
                        """
                        INSERT INTO repo_feedback_context(repo_key, task_key, suggestion_suffix, accept_count, last_accepted_at)
                        VALUES(?, ?, ?, ?, ?)
                        ON CONFLICT(repo_key, task_key, suggestion_suffix)
                        DO UPDATE SET
                            accept_count = repo_feedback_context.accept_count + excluded.accept_count,
                            last_accepted_at = MAX(repo_feedback_context.last_accepted_at, excluded.last_accepted_at)
                        """,
                        (repo_key, task_key, suffix, delta, ts),
                    )
            elif scope == "repo_context_execute":
                repo_key = str(event.get("repo_key", "") or "").strip()
                task_key = str(event.get("task_key", "") or "").strip()
                suffix = str(event.get("suggestion_suffix", "") or "")
                if repo_key and task_key and suffix:
                    delta = max(1, int(event.get("delta", 1) or 1))
                    conn.execute(
                        """
                        INSERT INTO repo_feedback_execute_context(repo_key, task_key, suggestion_suffix, execute_count, last_executed_at)
                        VALUES(?, ?, ?, ?, ?)
                        ON CONFLICT(repo_key, task_key, suggestion_suffix)
                        DO UPDATE SET
                            execute_count = repo_feedback_execute_context.execute_count + excluded.execute_count,
                            last_executed_at = MAX(repo_feedback_execute_context.last_executed_at, excluded.last_executed_at)
                        """,
                        (repo_key, task_key, suffix, delta, ts),
                    )
        elif etype == "history_upsert":
            if command:
                delta = max(1, int(event.get("delta", 1) or 1))
                self._ensure_command_row(conn, command, ts)
                conn.execute(
                    """
                    UPDATE commands
                    SET history_count = history_count + ?, updated_at = ?
                    WHERE command = ?
                    """,
                    (delta, ts, command),
                )
        elif etype == "command_removed":
            if command:
                conn.execute(
                    """
                    INSERT INTO removed_commands(command, removed_at)
                    VALUES(?, ?)
                    ON CONFLICT(command) DO UPDATE SET removed_at = excluded.removed_at
                    """,
                    (command, ts),
                )
        elif etype == "command_unremoved":
            if command:
                conn.execute("DELETE FROM removed_commands WHERE command = ?", (command,))
        elif etype == "manual_add":
            if command:
                self._ensure_command_row(conn, command, ts)
        elif etype == "command_provenance":
            run_id = str(event.get("run_id", "") or "").strip()
            label = str(event.get("label", "") or "").strip()
            if run_id and command and label:
                confidence = float(event.get("confidence", 0.0) or 0.0)
                agent = str(event.get("agent", "") or "").strip().lower()
                agent_name = str(event.get("agent_name", "") or "").strip()
                provider = str(event.get("provider", "") or "").strip().lower()
                model = str(event.get("model", "") or "").strip()
                raw_model = str(event.get("raw_model", "") or "").strip()
                normalized_model = str(event.get("normalized_model", "") or "").strip().lower()
                fingerprint = str(event.get("model_fingerprint", "") or "").strip()
                evidence_tier = str(event.get("evidence_tier", "") or "").strip().lower()
                agent_source = str(event.get("agent_source", "") or "").strip().lower()
                registry_version = str(event.get("registry_version", "") or "").strip()
                registry_status = str(event.get("registry_status", "") or "").strip().lower()
                source = str(event.get("source", "unknown") or "unknown").strip().lower()
                working_directory = str(event.get("working_directory", "") or "").strip()
                exit_code = event.get("exit_code", None)
                duration_ms = event.get("duration_ms", None)
                shell_pid = event.get("shell_pid", None)
                evidence_json = self._json_dumps(event.get("evidence", []), "[]")
                payload_json = self._json_dumps(event.get("payload", {}), "{}")
                conn.execute(
                    """
                    INSERT INTO command_runs(
                        run_id, ts, command, label, confidence, agent, agent_name, provider, model,
                        raw_model, normalized_model, model_fingerprint, evidence_tier, agent_source,
                        registry_version, registry_status, source, working_directory, exit_code,
                        duration_ms, shell_pid, evidence_json, payload_json, created_at
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(run_id) DO NOTHING
                    """,
                    (
                        run_id,
                        ts,
                        command,
                        label,
                        confidence,
                        agent,
                        agent_name,
                        provider,
                        model,
                        raw_model,
                        normalized_model,
                        fingerprint,
                        evidence_tier,
                        agent_source,
                        registry_version,
                        registry_status,
                        source,
                        working_directory,
                        (int(exit_code) if exit_code is not None else None),
                        (max(0, int(duration_ms)) if duration_ms is not None else None),
                        (int(shell_pid) if shell_pid is not None else None),
                        evidence_json,
                        payload_json,
                        ts,
                    ),
                )
        else:
            return False

        self._mark_event_applied(conn, event_id, ts)
        return True

    def apply_events(self, events: List[Dict[str, object]], append_to_journal: bool = True) -> int:
        normalized = [self._build_event(str(e.get("type", "") or ""), e) for e in events if isinstance(e, dict)]
        if not normalized:
            return 0
        if append_to_journal and self.journal is not None:
            for event in normalized:
                self.journal.append(event)
        changed = 0
        with self._lock, self._conn() as conn:
            conn.execute("BEGIN")
            try:
                for event in normalized:
                    if self._apply_event_tx(conn, event):
                        changed += 1
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return changed

    def apply_event(self, event: Dict[str, object], append_to_journal: bool = True) -> bool:
        return self.apply_events([event], append_to_journal=append_to_journal) > 0

    def list_removed_commands(self) -> set[str]:
        with self._lock, self._conn() as conn:
            rows = conn.execute("SELECT command FROM removed_commands").fetchall()
        return {self._clean_command(str(row["command"] or "")) for row in rows if row["command"]}

    def is_removed_command(self, command: str) -> bool:
        normalized = self._clean_command(command)
        if not normalized:
            return False
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM removed_commands WHERE command = ? LIMIT 1",
                (normalized,),
            ).fetchone()
        return bool(row)

    def mark_removed_commands(self, commands: List[str]) -> int:
        ts = int(time.time())
        events = []
        for command in commands:
            normalized = self._clean_command(command)
            if not normalized:
                continue
            events.append({"type": "command_removed", "command": normalized, "ts": ts})
        return self.apply_events(events, append_to_journal=True)

    def unmark_removed_commands(self, commands: List[str]) -> int:
        ts = int(time.time())
        events = []
        for command in commands:
            normalized = self._clean_command(command)
            if not normalized:
                continue
            events.append({"type": "command_unremoved", "command": normalized, "ts": ts})
        return self.apply_events(events, append_to_journal=True)

    def add_manual_commands(self, commands: List[str]) -> int:
        ts = int(time.time())
        events = []
        for command in commands:
            normalized = self._clean_command(command)
            if not normalized:
                continue
            events.append({"type": "manual_add", "command": normalized, "ts": ts})
        return self.apply_events(events, append_to_journal=True)

    def record_execute(
        self,
        command: str,
        delta: int = 1,
        ts: Optional[int] = None,
        repo_task_pair: Optional[Tuple[str, str, str]] = None,
    ) -> bool:
        normalized = self._clean_command(command)
        if not normalized:
            return False
        now_ts = int(ts or time.time())
        events: List[Dict[str, object]] = [
            {
                "type": "command_execute",
                "command": normalized,
                "delta": max(1, int(delta or 1)),
                "ts": now_ts,
            }
        ]
        if repo_task_pair is not None:
            rk = str(repo_task_pair[0] or "").strip()
            tk = str(repo_task_pair[1] or "").strip()
            sf = str(repo_task_pair[2] or "")
            if rk and tk and sf:
                events.append(
                    {
                        "type": "feedback_accept",
                        "scope": "repo_context_execute",
                        "repo_key": rk,
                        "task_key": tk,
                        "suggestion_suffix": sf,
                        "delta": max(1, int(delta or 1)),
                        "ts": now_ts,
                    }
                )
        return self.apply_events(events, append_to_journal=True) > 0

    def record_feedback(
        self,
        command: str,
        context_pairs: List[Tuple[str, str]],
        repo_task_pairs: Optional[List[Tuple[str, str, str]]] = None,
        ts: Optional[int] = None,
    ) -> int:
        normalized = self._clean_command(command)
        if not normalized:
            return 0
        now_ts = int(ts or time.time())
        events: List[Dict[str, object]] = [
            {
                "type": "feedback_accept",
                "scope": "command",
                "command": normalized,
                "delta": 1,
                "ts": now_ts,
            }
        ]
        for context_key, suffix in context_pairs:
            ck = str(context_key or "").strip()
            sf = str(suffix or "")
            if not ck or not sf:
                continue
            events.append(
                {
                    "type": "feedback_accept",
                    "scope": "context",
                    "context_key": ck,
                    "suggestion_suffix": sf,
                    "delta": 1,
                    "ts": now_ts,
                }
            )
        for repo_key, task_key, suffix in (repo_task_pairs or []):
            rk = str(repo_key or "").strip()
            tk = str(task_key or "").strip()
            sf = str(suffix or "")
            if not rk or not tk or not sf:
                continue
            events.append(
                {
                    "type": "feedback_accept",
                    "scope": "repo_context",
                    "repo_key": rk,
                    "task_key": tk,
                    "suggestion_suffix": sf,
                    "delta": 1,
                    "ts": now_ts,
                }
            )
        return self.apply_events(events, append_to_journal=True)

    def record_command_provenance(
        self,
        command: str,
        label: str,
        confidence: float,
        agent: str = "",
        agent_name: str = "",
        provider: str = "",
        model: str = "",
        raw_model: str = "",
        normalized_model: str = "",
        model_fingerprint: str = "",
        evidence_tier: str = "",
        agent_source: str = "",
        registry_version: str = "",
        registry_status: str = "",
        source: str = "unknown",
        working_directory: str = "",
        exit_code: Optional[int] = None,
        duration_ms: Optional[int] = None,
        shell_pid: Optional[int] = None,
        evidence: Optional[List[str]] = None,
        payload: Optional[Dict[str, object]] = None,
        ts: Optional[int] = None,
        run_id: Optional[str] = None,
    ) -> bool:
        normalized = self._clean_command(command)
        clean_label = str(label or "").strip()
        if not normalized or not clean_label:
            return False
        now_ts = int(ts or time.time())
        event = {
            "type": "command_provenance",
            "run_id": str(run_id or uuid.uuid4()),
            "command": normalized,
            "label": clean_label,
            "confidence": float(confidence or 0.0),
            "agent": str(agent or "").strip().lower(),
            "agent_name": str(agent_name or "").strip(),
            "provider": str(provider or "").strip().lower(),
            "model": str(model or "").strip(),
            "raw_model": str(raw_model or "").strip(),
            "normalized_model": str(normalized_model or "").strip().lower(),
            "model_fingerprint": str(model_fingerprint or "").strip(),
            "evidence_tier": str(evidence_tier or "").strip().lower(),
            "agent_source": str(agent_source or "").strip().lower(),
            "registry_version": str(registry_version or "").strip(),
            "registry_status": str(registry_status or "").strip().lower(),
            "source": str(source or "unknown").strip().lower(),
            "working_directory": str(working_directory or "").strip(),
            "exit_code": (int(exit_code) if exit_code is not None else None),
            "duration_ms": (max(0, int(duration_ms)) if duration_ms is not None else None),
            "shell_pid": (int(shell_pid) if shell_pid is not None else None),
            "evidence": list(evidence or []),
            "payload": dict(payload or {}),
            "ts": now_ts,
        }
        return self.apply_event(event, append_to_journal=True)

    def apply_history_counts(self, command_counts: Dict[str, int], ts: Optional[int] = None) -> int:
        now_ts = int(ts or time.time())
        events: List[Dict[str, object]] = []
        for command, count in (command_counts or {}).items():
            normalized = self._clean_command(command)
            delta = int(count or 0)
            if not normalized or delta <= 0:
                continue
            events.append(
                {
                    "type": "history_upsert",
                    "command": normalized,
                    "delta": delta,
                    "ts": now_ts,
                }
            )
        return self.apply_events(events, append_to_journal=True)

    def get_command_stats(self, commands: List[str]) -> Dict[str, Dict[str, int]]:
        clean = []
        seen = set()
        for command in commands:
            normalized = self._clean_command(command)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            clean.append(normalized)
        out: Dict[str, Dict[str, int]] = {}
        if not clean:
            return out
        with self._lock, self._conn() as conn:
            for chunk in self._chunk(clean):
                placeholders = ",".join("?" for _ in chunk)
                rows = conn.execute(
                    f"""
                    SELECT command, accept_count, execute_count, history_count, last_accepted_at
                    FROM commands
                    WHERE command IN ({placeholders})
                    """,
                    tuple(chunk),
                ).fetchall()
                for row in rows:
                    key = str(row["command"] or "")
                    out[key] = {
                        "accept_count": int(row["accept_count"] or 0),
                        "execute_count": int(row["execute_count"] or 0),
                        "history_count": int(row["history_count"] or 0),
                        "last_accepted_at": int(row["last_accepted_at"] or 0),
                    }
        return out

    def get_feedback_counts(self, context_keys: List[str], suffixes: List[str]) -> Dict[str, int]:
        keys = [str(x or "").strip() for x in context_keys if str(x or "").strip()]
        suff = [str(x or "") for x in suffixes if str(x or "") != ""]
        if not keys or not suff:
            return {suffix: 0 for suffix in suff}
        out = {suffix: 0 for suffix in suff}
        with self._lock, self._conn() as conn:
            key_placeholders = ",".join("?" for _ in keys)
            suffix_placeholders = ",".join("?" for _ in suff)
            rows = conn.execute(
                f"""
                SELECT suggestion_suffix, SUM(accept_count) AS total
                FROM feedback_context
                WHERE context_key IN ({key_placeholders})
                  AND suggestion_suffix IN ({suffix_placeholders})
                GROUP BY suggestion_suffix
                """,
                tuple(keys + suff),
            ).fetchall()
            for row in rows:
                suffix = str(row["suggestion_suffix"] or "")
                if suffix:
                    out[suffix] = int(row["total"] or 0)
        return out

    def get_repo_feedback_counts(
        self,
        repo_key: str,
        task_key: str,
        suffixes: List[str],
    ) -> Dict[str, int]:
        rk = str(repo_key or "").strip()
        tk = str(task_key or "").strip()
        suff = [str(x or "") for x in suffixes if str(x or "") != ""]
        if not rk or not tk or not suff:
            return {suffix: 0 for suffix in suff}
        out = {suffix: 0 for suffix in suff}
        with self._lock, self._conn() as conn:
            suffix_placeholders = ",".join("?" for _ in suff)
            rows = conn.execute(
                f"""
                SELECT suggestion_suffix, SUM(accept_count) AS total
                FROM repo_feedback_context
                WHERE repo_key = ?
                  AND task_key = ?
                  AND suggestion_suffix IN ({suffix_placeholders})
                GROUP BY suggestion_suffix
                """,
                tuple([rk, tk] + suff),
            ).fetchall()
            for row in rows:
                suffix = str(row["suggestion_suffix"] or "")
                if suffix:
                    out[suffix] = int(row["total"] or 0)
        return out

    def get_repo_execute_feedback_counts(
        self,
        repo_key: str,
        task_key: str,
        commands: List[str],
    ) -> Dict[str, int]:
        rk = str(repo_key or "").strip()
        tk = str(task_key or "").strip()
        cmds = [str(x or "") for x in commands if str(x or "") != ""]
        if not rk or not tk or not cmds:
            return {command: 0 for command in cmds}
        out = {command: 0 for command in cmds}
        with self._lock, self._conn() as conn:
            command_placeholders = ",".join("?" for _ in cmds)
            rows = conn.execute(
                f"""
                SELECT suggestion_suffix, SUM(execute_count) AS total
                FROM repo_feedback_execute_context
                WHERE repo_key = ?
                  AND task_key = ?
                  AND suggestion_suffix IN ({command_placeholders})
                GROUP BY suggestion_suffix
                """,
                tuple([rk, tk] + cmds),
            ).fetchall()
            for row in rows:
                command = str(row["suggestion_suffix"] or "")
                if command:
                    out[command] = int(row["total"] or 0)
        return out

    def list_all_commands(self, include_removed: bool = False) -> List[str]:
        with self._lock, self._conn() as conn:
            if include_removed:
                rows = conn.execute("SELECT command FROM commands ORDER BY command").fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT c.command
                    FROM commands c
                    LEFT JOIN removed_commands r ON r.command = c.command
                    WHERE r.command IS NULL
                    ORDER BY c.command
                    """
                ).fetchall()
        return [self._clean_command(str(row["command"] or "")) for row in rows if row["command"]]

    @staticmethod
    def _decode_command_run_row(row: sqlite3.Row) -> Dict[str, object]:
        evidence_raw = str(row["evidence_json"] or "[]")
        payload_raw = str(row["payload_json"] or "{}")
        try:
            evidence = json.loads(evidence_raw)
        except Exception:
            evidence = []
        if not isinstance(evidence, list):
            evidence = []
        try:
            payload = json.loads(payload_raw)
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        return {
            "run_id": str(row["run_id"] or ""),
            "ts": int(row["ts"] or 0),
            "command": str(row["command"] or ""),
            "label": str(row["label"] or ""),
            "confidence": float(row["confidence"] or 0.0),
            "agent": str(row["agent"] or ""),
            "agent_name": str(row["agent_name"] or ""),
            "provider": str(row["provider"] or ""),
            "model": str(row["model"] or ""),
            "raw_model": str(row["raw_model"] or ""),
            "normalized_model": str(row["normalized_model"] or ""),
            "model_fingerprint": str(row["model_fingerprint"] or ""),
            "evidence_tier": str(row["evidence_tier"] or ""),
            "agent_source": str(row["agent_source"] or ""),
            "registry_version": str(row["registry_version"] or ""),
            "registry_status": str(row["registry_status"] or ""),
            "source": str(row["source"] or ""),
            "working_directory": str(row["working_directory"] or ""),
            "exit_code": (int(row["exit_code"]) if row["exit_code"] is not None else None),
            "duration_ms": (int(row["duration_ms"]) if row["duration_ms"] is not None else None),
            "shell_pid": (int(row["shell_pid"]) if row["shell_pid"] is not None else None),
            "evidence": evidence,
            "payload": payload,
        }

    @staticmethod
    def _command_runs_filter_clause(
        label: str = "",
        command_contains: str = "",
        since_ts: int = 0,
        tier: str = "",
        agent: str = "",
        agent_name: str = "",
        provider: str = "",
    ) -> Tuple[List[str], List[object]]:
        query = ["WHERE ts >= ?"]
        params: List[object] = [int(since_ts or 0)]
        label_filter = str(label or "").strip()
        command_filter = str(command_contains or "").strip()
        tier_filter = str(tier or "").strip().lower()
        agent_filter = str(agent or "").strip().lower()
        agent_name_filter = str(agent_name or "").strip()
        provider_filter = str(provider or "").strip().lower()

        if label_filter:
            query.append("AND label = ?")
            params.append(label_filter)
        if command_filter:
            query.append("AND command LIKE ?")
            params.append(f"%{command_filter}%")
        if tier_filter:
            query.append("AND evidence_tier = ?")
            params.append(tier_filter)
        if agent_filter:
            query.append("AND agent = ?")
            params.append(agent_filter)
        if agent_name_filter:
            query.append("AND agent_name = ?")
            params.append(agent_name_filter)
        if provider_filter:
            query.append("AND provider = ?")
            params.append(provider_filter)
        return (query, params)

    def list_command_runs(
        self,
        limit: int = 50,
        label: str = "",
        command_contains: str = "",
        since_ts: int = 0,
        before_ts: int = 0,
        before_run_id: str = "",
        tier: str = "",
        agent: str = "",
        agent_name: str = "",
        provider: str = "",
    ) -> List[Dict[str, object]]:
        row_limit = max(1, min(500, int(limit or 50)))
        keyset_ts = int(before_ts or 0)
        keyset_run_id = str(before_run_id or "").strip()

        select_clause = """
            SELECT run_id, ts, command, label, confidence, agent, agent_name, provider, model, raw_model, normalized_model,
                   model_fingerprint, evidence_tier, agent_source, registry_version, registry_status,
                   source, working_directory, exit_code, duration_ms, shell_pid, evidence_json, payload_json
            FROM command_runs
        """
        where_clause, params = self._command_runs_filter_clause(
            label=label,
            command_contains=command_contains,
            since_ts=since_ts,
            tier=tier,
            agent=agent,
            agent_name=agent_name,
            provider=provider,
        )
        if keyset_ts > 0:
            if keyset_run_id:
                where_clause.append("AND (ts < ? OR (ts = ? AND run_id < ?))")
                params.extend([keyset_ts, keyset_ts, keyset_run_id])
            else:
                where_clause.append("AND ts < ?")
                params.append(keyset_ts)
        sql = "\n".join(
            [
                select_clause,
                *where_clause,
                "ORDER BY ts DESC, run_id DESC",
                "LIMIT ?",
            ]
        )
        params.append(row_limit)

        with self._lock, self._conn() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [self._decode_command_run_row(row) for row in rows]

    def count_command_runs(
        self,
        label: str = "",
        command_contains: str = "",
        since_ts: int = 0,
        tier: str = "",
        agent: str = "",
        agent_name: str = "",
        provider: str = "",
    ) -> int:
        where_clause, params = self._command_runs_filter_clause(
            label=label,
            command_contains=command_contains,
            since_ts=since_ts,
            tier=tier,
            agent=agent,
            agent_name=agent_name,
            provider=provider,
        )
        sql = "\n".join(
            [
                "SELECT COUNT(*) AS total",
                "FROM command_runs",
                *where_clause,
            ]
        )
        with self._lock, self._conn() as conn:
            row = conn.execute(sql, tuple(params)).fetchone()
        if row is None:
            return 0
        return int(row["total"] or 0)

    def list_latest_runs_for_commands(
        self,
        ranked_commands: List[str],
        since_ts: int = 0,
        label: str = "",
        tier: str = "",
        agent: str = "",
        agent_name: str = "",
        provider: str = "",
        limit: int = 50,
    ) -> List[Dict[str, object]]:
        ordered_unique: List[str] = []
        seen: set[str] = set()
        for value in ranked_commands:
            command = self._clean_command(value)
            if not command or command in seen:
                continue
            seen.add(command)
            ordered_unique.append(command)

        if not ordered_unique:
            return []

        row_limit = max(1, min(200, int(limit or 50)))
        rows_by_command: Dict[str, Dict[str, object]] = {}
        select_clause = """
            SELECT run_id, ts, command, label, confidence, agent, agent_name, provider, model, raw_model, normalized_model,
                   model_fingerprint, evidence_tier, agent_source, registry_version, registry_status,
                   source, working_directory, exit_code, duration_ms, shell_pid, evidence_json, payload_json
            FROM command_runs
        """
        with self._lock, self._conn() as conn:
            for command in ordered_unique:
                where_clause, params = self._command_runs_filter_clause(
                    label=label,
                    command_contains="",
                    since_ts=since_ts,
                    tier=tier,
                    agent=agent,
                    agent_name=agent_name,
                    provider=provider,
                )
                where_clause.append("AND command = ?")
                params.append(command)
                sql = "\n".join(
                    [
                        select_clause,
                        *where_clause,
                        "ORDER BY ts DESC, run_id DESC",
                        "LIMIT 1",
                    ]
                )
                row = conn.execute(sql, tuple(params)).fetchone()
                if row is None:
                    continue
                rows_by_command[command] = self._decode_command_run_row(row)
                if len(rows_by_command) >= row_limit:
                    break

        out: List[Dict[str, object]] = []
        for command in ordered_unique:
            row = rows_by_command.get(command)
            if row is None:
                continue
            out.append(row)
            if len(out) >= row_limit:
                break
        return out

    def prune_command_runs(self, older_than_ts: int) -> int:
        cutoff = int(older_than_ts or 0)
        if cutoff <= 0:
            return 0
        with self._lock, self._conn() as conn:
            cur = conn.execute("DELETE FROM command_runs WHERE ts < ?", (cutoff,))
            conn.commit()
            return int(cur.rowcount or 0)

    def get_history_index_state(self, history_file: str) -> Optional[Dict[str, int | str]]:
        key = self._clean_command(history_file)
        if not key:
            return None
        with self._lock, self._conn() as conn:
            row = conn.execute(
                """
                SELECT history_file, inode, device, offset, updated_at
                FROM history_index_state
                WHERE history_file = ?
                """,
                (key,),
            ).fetchone()
        if row is None:
            return None
        return {
            "history_file": str(row["history_file"]),
            "inode": int(row["inode"] or 0),
            "device": int(row["device"] or 0),
            "offset": int(row["offset"] or 0),
            "updated_at": int(row["updated_at"] or 0),
        }

    def set_history_index_state(
        self,
        history_file: str,
        inode: int,
        device: int,
        offset: int,
        updated_at: Optional[int] = None,
    ) -> None:
        key = self._clean_command(history_file)
        if not key:
            return
        ts = int(updated_at or time.time())
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                INSERT INTO history_index_state(history_file, inode, device, offset, updated_at)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(history_file) DO UPDATE SET
                    inode = excluded.inode,
                    device = excluded.device,
                    offset = excluded.offset,
                    updated_at = excluded.updated_at
                """,
                (key, int(inode), int(device), int(offset), ts),
            )
            conn.commit()

    def export_payload(self) -> Dict[str, object]:
        with self._lock, self._conn() as conn:
            commands = [dict(row) for row in conn.execute("SELECT * FROM commands").fetchall()]
            feedback = [dict(row) for row in conn.execute("SELECT * FROM feedback_context").fetchall()]
            repo_feedback = [dict(row) for row in conn.execute("SELECT * FROM repo_feedback_context").fetchall()]
            repo_execute_feedback = [
                dict(row) for row in conn.execute("SELECT * FROM repo_feedback_execute_context").fetchall()
            ]
            removed = [dict(row) for row in conn.execute("SELECT * FROM removed_commands").fetchall()]
            command_runs = [dict(row) for row in conn.execute("SELECT * FROM command_runs").fetchall()]
            history = [dict(row) for row in conn.execute("SELECT * FROM history_index_state").fetchall()]
            meta = [dict(row) for row in conn.execute("SELECT * FROM meta").fetchall()]
        return {
            "schema_version": 2,
            "state_backend": "sqlite",
            "exported_at": int(time.time()),
            "commands": commands,
            "feedback_context": feedback,
            "repo_feedback_context": repo_feedback,
            "repo_feedback_execute_context": repo_execute_feedback,
            "removed_commands": removed,
            "command_runs": command_runs,
            "history_index_state": history,
            "meta": meta,
            "journal_latest_ts": self.journal.latest_event_ts() if self.journal else 0,
        }

    def import_payload(self, payload: Dict[str, object]) -> Dict[str, int]:
        if not isinstance(payload, dict):
            return {
                "commands_imported": 0,
                "feedback_imported": 0,
                "removed_imported": 0,
                "provenance_imported": 0,
            }

        commands = payload.get("commands", [])
        feedback = payload.get("feedback_context", [])
        repo_feedback = payload.get("repo_feedback_context", [])
        repo_execute_feedback = payload.get("repo_feedback_execute_context", [])
        removed = payload.get("removed_commands", [])
        command_runs = payload.get("command_runs", [])
        history = payload.get("history_index_state", [])
        meta = payload.get("meta", [])

        commands_imported = 0
        feedback_imported = 0
        removed_imported = 0
        provenance_imported = 0

        with self._lock, self._conn() as conn:
            conn.execute("BEGIN")
            try:
                for row in commands if isinstance(commands, list) else []:
                    if not isinstance(row, dict):
                        continue
                    command = self._clean_command(str(row.get("command", "") or ""))
                    if not command:
                        continue
                    now_ts = int(time.time())
                    self._ensure_command_row(conn, command, now_ts)
                    existing = conn.execute(
                        "SELECT accept_count, execute_count, history_count, last_accepted_at FROM commands WHERE command = ?",
                        (command,),
                    ).fetchone()
                    accept = max(int(existing["accept_count"] or 0), int(row.get("accept_count", 0) or 0))
                    execute = max(int(existing["execute_count"] or 0), int(row.get("execute_count", 0) or 0))
                    history_count = max(int(existing["history_count"] or 0), int(row.get("history_count", 0) or 0))
                    last_accepted = max(
                        int(existing["last_accepted_at"] or 0),
                        int(row.get("last_accepted_at", 0) or 0),
                    )
                    conn.execute(
                        """
                        UPDATE commands
                        SET accept_count = ?, execute_count = ?, history_count = ?,
                            last_accepted_at = ?, updated_at = ?
                        WHERE command = ?
                        """,
                        (accept, execute, history_count, last_accepted, now_ts, command),
                    )
                    commands_imported += 1

                for row in feedback if isinstance(feedback, list) else []:
                    if not isinstance(row, dict):
                        continue
                    context_key = str(row.get("context_key", "") or "").strip()
                    suffix = str(row.get("suggestion_suffix", "") or "").strip()
                    if not context_key or not suffix:
                        continue
                    conn.execute(
                        """
                        INSERT INTO feedback_context(context_key, suggestion_suffix, accept_count, last_accepted_at)
                        VALUES(?, ?, ?, ?)
                        ON CONFLICT(context_key, suggestion_suffix)
                        DO UPDATE SET
                            accept_count = MAX(feedback_context.accept_count, excluded.accept_count),
                            last_accepted_at = MAX(feedback_context.last_accepted_at, excluded.last_accepted_at)
                        """,
                        (
                            context_key,
                            suffix,
                            int(row.get("accept_count", 0) or 0),
                            int(row.get("last_accepted_at", 0) or 0),
                        ),
                    )
                    feedback_imported += 1

                for row in repo_feedback if isinstance(repo_feedback, list) else []:
                    if not isinstance(row, dict):
                        continue
                    repo_key = str(row.get("repo_key", "") or "").strip()
                    task_key = str(row.get("task_key", "") or "").strip()
                    suffix = str(row.get("suggestion_suffix", "") or "").strip()
                    if not repo_key or not task_key or not suffix:
                        continue
                    conn.execute(
                        """
                        INSERT INTO repo_feedback_context(repo_key, task_key, suggestion_suffix, accept_count, last_accepted_at)
                        VALUES(?, ?, ?, ?, ?)
                        ON CONFLICT(repo_key, task_key, suggestion_suffix)
                        DO UPDATE SET
                            accept_count = MAX(repo_feedback_context.accept_count, excluded.accept_count),
                            last_accepted_at = MAX(repo_feedback_context.last_accepted_at, excluded.last_accepted_at)
                        """,
                        (
                            repo_key,
                            task_key,
                            suffix,
                            int(row.get("accept_count", 0) or 0),
                            int(row.get("last_accepted_at", 0) or 0),
                        ),
                    )
                    feedback_imported += 1

                for row in repo_execute_feedback if isinstance(repo_execute_feedback, list) else []:
                    if not isinstance(row, dict):
                        continue
                    repo_key = str(row.get("repo_key", "") or "").strip()
                    task_key = str(row.get("task_key", "") or "").strip()
                    suffix = str(row.get("suggestion_suffix", "") or "").strip()
                    if not repo_key or not task_key or not suffix:
                        continue
                    conn.execute(
                        """
                        INSERT INTO repo_feedback_execute_context(repo_key, task_key, suggestion_suffix, execute_count, last_executed_at)
                        VALUES(?, ?, ?, ?, ?)
                        ON CONFLICT(repo_key, task_key, suggestion_suffix)
                        DO UPDATE SET
                            execute_count = MAX(repo_feedback_execute_context.execute_count, excluded.execute_count),
                            last_executed_at = MAX(repo_feedback_execute_context.last_executed_at, excluded.last_executed_at)
                        """,
                        (
                            repo_key,
                            task_key,
                            suffix,
                            int(row.get("execute_count", 0) or 0),
                            int(row.get("last_executed_at", 0) or 0),
                        ),
                    )
                    feedback_imported += 1

                for row in removed if isinstance(removed, list) else []:
                    if isinstance(row, dict):
                        command = self._clean_command(str(row.get("command", "") or ""))
                        removed_at = int(row.get("removed_at", 0) or time.time())
                    else:
                        command = self._clean_command(str(row or ""))
                        removed_at = int(time.time())
                    if not command:
                        continue
                    conn.execute(
                        """
                        INSERT INTO removed_commands(command, removed_at)
                        VALUES(?, ?)
                        ON CONFLICT(command) DO UPDATE SET removed_at = MAX(removed_commands.removed_at, excluded.removed_at)
                        """,
                        (command, removed_at),
                    )
                    removed_imported += 1

                for row in command_runs if isinstance(command_runs, list) else []:
                    if not isinstance(row, dict):
                        continue
                    run_id = str(row.get("run_id", "") or "").strip()
                    command = self._clean_command(str(row.get("command", "") or ""))
                    label = str(row.get("label", "") or "").strip()
                    if not run_id or not command or not label:
                        continue
                    ts = int(row.get("ts", int(time.time())) or int(time.time()))
                    evidence_raw = row.get("evidence_json", row.get("evidence", []))
                    payload_raw = row.get("payload_json", row.get("payload", {}))
                    evidence_json = (
                        evidence_raw
                        if isinstance(evidence_raw, str)
                        else self._json_dumps(evidence_raw, "[]")
                    )
                    payload_json = (
                        payload_raw
                        if isinstance(payload_raw, str)
                        else self._json_dumps(payload_raw, "{}")
                    )
                    conn.execute(
                        """
                        INSERT INTO command_runs(
                            run_id, ts, command, label, confidence, agent, agent_name, provider, model,
                            raw_model, normalized_model, model_fingerprint, evidence_tier, agent_source,
                            registry_version, registry_status, source, working_directory, exit_code,
                            duration_ms, shell_pid, evidence_json, payload_json, created_at
                        )
                        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(run_id) DO UPDATE SET
                            ts = MAX(command_runs.ts, excluded.ts),
                            command = excluded.command,
                            label = excluded.label,
                            confidence = excluded.confidence,
                            agent = excluded.agent,
                            agent_name = excluded.agent_name,
                            provider = excluded.provider,
                            model = excluded.model,
                            raw_model = excluded.raw_model,
                            normalized_model = excluded.normalized_model,
                            model_fingerprint = excluded.model_fingerprint,
                            evidence_tier = excluded.evidence_tier,
                            agent_source = excluded.agent_source,
                            registry_version = excluded.registry_version,
                            registry_status = excluded.registry_status,
                            source = excluded.source,
                            working_directory = excluded.working_directory,
                            exit_code = excluded.exit_code,
                            duration_ms = excluded.duration_ms,
                            shell_pid = excluded.shell_pid,
                            evidence_json = excluded.evidence_json,
                            payload_json = excluded.payload_json
                        """,
                        (
                            run_id,
                            ts,
                            command,
                            label,
                            float(row.get("confidence", 0.0) or 0.0),
                            str(row.get("agent", "") or "").strip().lower(),
                            str(row.get("agent_name", "") or "").strip(),
                            str(row.get("provider", "") or "").strip().lower(),
                            str(row.get("model", "") or "").strip(),
                            str(row.get("raw_model", "") or "").strip(),
                            str(row.get("normalized_model", "") or "").strip().lower(),
                            str(row.get("model_fingerprint", "") or "").strip(),
                            str(row.get("evidence_tier", "") or "").strip().lower(),
                            str(row.get("agent_source", "") or "").strip().lower(),
                            str(row.get("registry_version", "") or "").strip(),
                            str(row.get("registry_status", "") or "").strip().lower(),
                            str(row.get("source", "unknown") or "unknown").strip().lower(),
                            str(row.get("working_directory", "") or "").strip(),
                            (
                                int(row.get("exit_code"))
                                if row.get("exit_code", None) is not None
                                else None
                            ),
                            (
                                max(0, int(row.get("duration_ms")))
                                if row.get("duration_ms", None) is not None
                                else None
                            ),
                            (
                                int(row.get("shell_pid"))
                                if row.get("shell_pid", None) is not None
                                else None
                            ),
                            evidence_json,
                            payload_json,
                            int(row.get("created_at", ts) or ts),
                        ),
                    )
                    provenance_imported += 1

                for row in history if isinstance(history, list) else []:
                    if not isinstance(row, dict):
                        continue
                    history_file = self._clean_command(str(row.get("history_file", "") or ""))
                    if not history_file:
                        continue
                    conn.execute(
                        """
                        INSERT INTO history_index_state(history_file, inode, device, offset, updated_at)
                        VALUES(?, ?, ?, ?, ?)
                        ON CONFLICT(history_file) DO UPDATE SET
                            inode = excluded.inode,
                            device = excluded.device,
                            offset = excluded.offset,
                            updated_at = excluded.updated_at
                        """,
                        (
                            history_file,
                            int(row.get("inode", 0) or 0),
                            int(row.get("device", 0) or 0),
                            int(row.get("offset", 0) or 0),
                            int(row.get("updated_at", int(time.time())) or int(time.time())),
                        ),
                    )

                for row in meta if isinstance(meta, list) else []:
                    if not isinstance(row, dict):
                        continue
                    key = str(row.get("key", "") or "").strip()
                    if not key:
                        continue
                    conn.execute(
                        """
                        INSERT INTO meta(key, value)
                        VALUES(?, ?)
                        ON CONFLICT(key) DO UPDATE SET value = excluded.value
                        """,
                        (key, json.dumps(row.get("value")) if not isinstance(row.get("value"), str) else row.get("value", "")),
                    )

                conn.commit()
            except Exception:
                conn.rollback()
                raise

        return {
            "commands_imported": commands_imported,
            "feedback_imported": feedback_imported,
            "removed_imported": removed_imported,
            "provenance_imported": provenance_imported,
        }

    def recover_from_latest_snapshot(
        self,
        snapshot_manager: SnapshotManager,
        journal: Optional[EventJournal],
    ) -> Dict[str, object]:
        restored, snapshot_row, restore_error = snapshot_manager.restore_latest()
        if not restored:
            return {
                "ok": False,
                "restored": False,
                "restore_error": restore_error,
                "replay": {"total": 0, "applied": 0, "skipped": 0},
                "snapshot_ts": 0,
            }

        self.init_schema()
        replay_stats = {"total": 0, "applied": 0, "skipped": 0}
        snapshot_ts = int((snapshot_row or {}).get("snapshot_ts", 0) or 0)
        if journal is not None:
            replay_stats = journal.replay(
                lambda event: self.apply_event(event, append_to_journal=False),
                since_ts=max(0, snapshot_ts),
            )
        return {
            "ok": True,
            "restored": True,
            "restore_error": "",
            "replay": replay_stats,
            "snapshot_ts": snapshot_ts,
        }
