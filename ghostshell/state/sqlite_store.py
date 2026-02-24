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

                CREATE TABLE IF NOT EXISTS removed_commands (
                    command TEXT PRIMARY KEY,
                    removed_at INTEGER NOT NULL
                );

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
            conn.commit()

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

    def record_execute(self, command: str, delta: int = 1, ts: Optional[int] = None) -> bool:
        normalized = self._clean_command(command)
        if not normalized:
            return False
        event = {
            "type": "command_execute",
            "command": normalized,
            "delta": max(1, int(delta or 1)),
            "ts": int(ts or time.time()),
        }
        return self.apply_event(event, append_to_journal=True)

    def record_feedback(
        self,
        command: str,
        context_pairs: List[Tuple[str, str]],
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
        return self.apply_events(events, append_to_journal=True)

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
            removed = [dict(row) for row in conn.execute("SELECT * FROM removed_commands").fetchall()]
            history = [dict(row) for row in conn.execute("SELECT * FROM history_index_state").fetchall()]
            meta = [dict(row) for row in conn.execute("SELECT * FROM meta").fetchall()]
        return {
            "schema_version": 2,
            "state_backend": "sqlite",
            "exported_at": int(time.time()),
            "commands": commands,
            "feedback_context": feedback,
            "removed_commands": removed,
            "history_index_state": history,
            "meta": meta,
            "journal_latest_ts": self.journal.latest_event_ts() if self.journal else 0,
        }

    def import_payload(self, payload: Dict[str, object]) -> Dict[str, int]:
        if not isinstance(payload, dict):
            return {"commands_imported": 0, "feedback_imported": 0, "removed_imported": 0}

        commands = payload.get("commands", [])
        feedback = payload.get("feedback_context", [])
        removed = payload.get("removed_commands", [])
        history = payload.get("history_index_state", [])
        meta = payload.get("meta", [])

        commands_imported = 0
        feedback_imported = 0
        removed_imported = 0

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
