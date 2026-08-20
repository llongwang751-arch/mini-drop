"""Durable local outbox for at-least-once Agent result delivery."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class OutboxEntry:
    path: Path
    payload: dict[str, Any]


class ResultOutbox:
    """Persist collector results before making the gRPC callback."""

    def __init__(self, directory: str, *, max_entries: int = 256) -> None:
        self.directory = Path(directory)
        self.max_entries = max(1, max_entries)
        self.directory.mkdir(parents=True, exist_ok=True)

    def enqueue(
        self, task_id: str, ok: bool, reason: str, artifacts: list[dict[str, Any]],
    ) -> OutboxEntry:
        path = self._path_for(task_id)
        payload = {
            "schema_version": "mini-drop.notify-result.v1",
            "task_id": task_id,
            "ok": bool(ok),
            "reason": str(reason or ""),
            "artifacts": artifacts,
        }
        temporary = path.with_suffix(".tmp")
        with temporary.open("wb") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        self._trim()
        return OutboxEntry(path, payload)

    def pending(self) -> list[OutboxEntry]:
        entries: list[OutboxEntry] = []
        for path in sorted(self.directory.glob("*.json"), key=lambda item: item.stat().st_mtime):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                path.replace(path.with_suffix(".corrupt"))
                continue
            if not isinstance(payload, dict) or not payload.get("task_id"):
                path.replace(path.with_suffix(".corrupt"))
                continue
            entries.append(OutboxEntry(path, payload))
        return entries

    @staticmethod
    def acknowledge(entry: OutboxEntry) -> None:
        entry.path.unlink(missing_ok=True)

    def _path_for(self, task_id: str) -> Path:
        digest = hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:16]
        return self.directory / f"{digest}.json"

    def _trim(self) -> None:
        files = sorted(self.directory.glob("*.json"), key=lambda item: item.stat().st_mtime)
        for stale in files[:-self.max_entries]:
            stale.replace(stale.with_suffix(".overflow"))
