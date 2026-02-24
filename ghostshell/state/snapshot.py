import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .journal import EventJournal


class SnapshotManager:
    def __init__(self, sqlite_path: str, snapshots_dir: str):
        self.sqlite_path = os.path.expanduser(sqlite_path)
        self.snapshots_dir = os.path.expanduser(snapshots_dir)
        os.makedirs(self.snapshots_dir, exist_ok=True)

    def _snapshot_name(self, ts: int) -> str:
        return time.strftime("state-%Y%m%d%H%M%S.sqlite", time.localtime(int(ts)))

    def _meta_path(self, snapshot_path: str) -> str:
        return f"{snapshot_path}.json"

    def create_snapshot(self, metadata: Optional[Dict[str, object]] = None) -> Dict[str, object]:
        if not os.path.exists(self.sqlite_path):
            raise FileNotFoundError(f"SQLite DB not found: {self.sqlite_path}")

        ts = int(time.time())
        target_path = os.path.join(self.snapshots_dir, self._snapshot_name(ts))
        tmp_path = f"{target_path}.tmp"

        src = sqlite3.connect(self.sqlite_path, timeout=5)
        dst = sqlite3.connect(tmp_path, timeout=5)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()

        os.replace(tmp_path, target_path)
        payload = {
            "snapshot_ts": ts,
            "sqlite_path": self.sqlite_path,
            "snapshot_path": target_path,
            "size_bytes": int(os.path.getsize(target_path)),
        }
        if metadata:
            payload.update(dict(metadata))
        with open(self._meta_path(target_path), "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        return payload

    def list_snapshots(self) -> List[Dict[str, object]]:
        out: List[Dict[str, object]] = []
        root = Path(self.snapshots_dir)
        if not root.exists():
            return out
        for p in sorted(root.glob("state-*.sqlite")):
            if not p.is_file():
                continue
            row = {
                "snapshot_path": str(p),
                "size_bytes": int(p.stat().st_size),
                "snapshot_ts": int(p.stat().st_mtime),
            }
            meta_path = self._meta_path(str(p))
            if os.path.exists(meta_path):
                try:
                    with open(meta_path, "r", encoding="utf-8") as f:
                        payload = json.load(f)
                    if isinstance(payload, dict):
                        row.update(payload)
                except Exception:
                    pass
            out.append(row)
        out.sort(key=lambda item: int(item.get("snapshot_ts", 0) or 0))
        return out

    def latest_snapshot(self) -> Optional[Dict[str, object]]:
        all_rows = self.list_snapshots()
        if not all_rows:
            return None
        return all_rows[-1]

    def restore_latest(self) -> Tuple[bool, Optional[Dict[str, object]], str]:
        row = self.latest_snapshot()
        if not row:
            return (False, None, "no_snapshot")
        path = str(row.get("snapshot_path", "") or "")
        if not path or not os.path.exists(path):
            return (False, row, "snapshot_missing")
        tmp_path = f"{self.sqlite_path}.tmp"
        try:
            src = sqlite3.connect(path, timeout=5)
            dst = sqlite3.connect(tmp_path, timeout=5)
            try:
                src.backup(dst)
            finally:
                dst.close()
                src.close()
            os.replace(tmp_path, self.sqlite_path)
            return (True, row, "")
        except Exception as exc:
            return (False, row, str(exc))

    def prune(self, max_age_seconds: int, max_total_bytes: int) -> Dict[str, int]:
        now = int(time.time())
        removed = 0
        removed_bytes = 0
        rows = self.list_snapshots()

        for row in list(rows):
            ts = int(row.get("snapshot_ts", 0) or 0)
            if now - ts <= int(max_age_seconds):
                continue
            path = str(row.get("snapshot_path", "") or "")
            if not path:
                continue
            try:
                size = int(os.path.getsize(path))
                os.remove(path)
                meta = self._meta_path(path)
                if os.path.exists(meta):
                    os.remove(meta)
                removed += 1
                removed_bytes += size
            except OSError:
                continue

        rows = self.list_snapshots()
        total_bytes = sum(int(row.get("size_bytes", 0) or 0) for row in rows)
        if total_bytes > int(max_total_bytes):
            for row in rows:
                if total_bytes <= int(max_total_bytes):
                    break
                path = str(row.get("snapshot_path", "") or "")
                if not path:
                    continue
                try:
                    size = int(os.path.getsize(path))
                    os.remove(path)
                    meta = self._meta_path(path)
                    if os.path.exists(meta):
                        os.remove(meta)
                    removed += 1
                    removed_bytes += size
                    total_bytes -= size
                except OSError:
                    continue

        return {"removed_files": removed, "removed_bytes": removed_bytes}


class SnapshotScheduler:
    def __init__(
        self,
        snapshot_manager: SnapshotManager,
        journal: EventJournal,
        interval_seconds: int = 300,
        retention_seconds: int = 24 * 3600,
        max_total_bytes: int = 200 * 1024 * 1024,
    ):
        self.snapshot_manager = snapshot_manager
        self.journal = journal
        self.interval_seconds = max(30, int(interval_seconds))
        self.retention_seconds = int(retention_seconds)
        self.max_total_bytes = int(max_total_bytes)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_hour = ""

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="ghostshell-snapshot")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

    def _run(self) -> None:
        while not self._stop.is_set():
            now = int(time.time())
            hour_key = time.strftime("%Y%m%d%H", time.localtime(now))
            if hour_key != self._last_hour and os.path.exists(self.snapshot_manager.sqlite_path):
                try:
                    self.snapshot_manager.create_snapshot(
                        metadata={"journal_latest_ts": self.journal.latest_event_ts()}
                    )
                except Exception:
                    pass
                try:
                    self.snapshot_manager.prune(self.retention_seconds, self.max_total_bytes)
                except Exception:
                    pass
                try:
                    self.journal.prune(self.retention_seconds, self.max_total_bytes)
                except Exception:
                    pass
                self._last_hour = hour_key
            self._stop.wait(self.interval_seconds)

