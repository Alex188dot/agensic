import json
import os
import time
import uuid
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from agensic.utils import enforce_private_file, ensure_private_dir


class EventJournal:
    def __init__(self, events_dir: str):
        self.events_dir = os.path.expanduser(events_dir)
        ensure_private_dir(self.events_dir)

    @staticmethod
    def _hour_segment(ts: int) -> str:
        return time.strftime("%Y%m%d%H", time.localtime(int(ts)))

    def _segment_path(self, ts: int) -> str:
        name = f"events-{self._hour_segment(ts)}.ndjson"
        return os.path.join(self.events_dir, name)

    def append(self, event: Dict[str, object]) -> Dict[str, object]:
        payload = dict(event or {})
        now_ts = int(payload.get("ts", 0) or 0) or int(time.time())
        payload["ts"] = now_ts
        payload["event_id"] = str(payload.get("event_id") or uuid.uuid4())
        path = self._segment_path(now_ts)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, separators=(",", ":")) + "\n")
        enforce_private_file(path)
        return payload

    def latest_event_ts(self) -> int:
        latest = 0
        for event in self.iter_events(since_ts=0):
            latest = max(latest, int(event.get("ts", 0) or 0))
        return latest

    def _list_segments(self) -> List[Path]:
        root = Path(self.events_dir)
        if not root.exists():
            return []
        segments = [p for p in root.glob("events-*.ndjson") if p.is_file()]
        segments.sort(key=lambda p: p.name)
        return segments

    def iter_events(self, since_ts: int = 0) -> Iterable[Dict[str, object]]:
        since = int(since_ts or 0)
        for segment in self._list_segments():
            try:
                with open(segment, "r", encoding="utf-8") as f:
                    for line in f:
                        raw = line.strip()
                        if not raw:
                            continue
                        try:
                            event = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(event, dict):
                            continue
                        ts = int(event.get("ts", 0) or 0)
                        if ts < since:
                            continue
                        yield event
            except OSError:
                continue

    def replay(
        self,
        apply_event,
        since_ts: int = 0,
    ) -> Dict[str, int]:
        total = 0
        applied = 0
        skipped = 0
        for event in self.iter_events(since_ts=since_ts):
            total += 1
            try:
                changed = bool(apply_event(event))
            except Exception:
                skipped += 1
                continue
            if changed:
                applied += 1
            else:
                skipped += 1
        return {"total": total, "applied": applied, "skipped": skipped}

    def prune(self, max_age_seconds: int, max_total_bytes: int) -> Dict[str, int]:
        now = int(time.time())
        removed = 0
        removed_bytes = 0
        segments = self._list_segments()

        for segment in segments:
            try:
                mtime = int(segment.stat().st_mtime)
                if now - mtime <= int(max_age_seconds):
                    continue
                size = int(segment.stat().st_size)
                segment.unlink(missing_ok=True)
                removed += 1
                removed_bytes += size
            except OSError:
                continue

        segments = self._list_segments()
        total_size = sum(int(p.stat().st_size) for p in segments if p.exists())
        if total_size > int(max_total_bytes):
            for segment in segments:
                if total_size <= int(max_total_bytes):
                    break
                try:
                    size = int(segment.stat().st_size)
                    segment.unlink(missing_ok=True)
                    removed += 1
                    removed_bytes += size
                    total_size -= size
                except OSError:
                    continue

        return {"removed_files": removed, "removed_bytes": removed_bytes}
