from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class CapabilityTaskStore:
    root: Path

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.in_progress_dir.mkdir(parents=True, exist_ok=True)
        self.deferred_dir.mkdir(parents=True, exist_ok=True)
        self.done_dir.mkdir(parents=True, exist_ok=True)
        self.failed_dir.mkdir(parents=True, exist_ok=True)

    @property
    def tasks_dir(self) -> Path:
        return self.root / "tasks"

    @property
    def done_dir(self) -> Path:
        return self.root / "done"

    @property
    def failed_dir(self) -> Path:
        return self.root / "failed"

    @property
    def in_progress_dir(self) -> Path:
        return self.root / "in-progress"

    @property
    def deferred_dir(self) -> Path:
        return self.root / "deferred"

    def list_tasks(self, *, limit: int = 20) -> list[dict[str, Any]]:
        if not self.tasks_dir.exists():
            return []
        rows: list[dict[str, Any]] = []
        for path in sorted(self.tasks_dir.glob("*.json")):
            rows.append(json.loads(path.read_text(encoding="utf-8")))
        return rows[-limit:]

    def list_all_tasks(self, *, limit: int = 20) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for source_dir in (
            self.tasks_dir,
            self.in_progress_dir,
            self.deferred_dir,
            self.done_dir,
            self.failed_dir,
        ):
            if not source_dir.exists():
                continue
            for path in sorted(source_dir.glob("*.json")):
                rows.append(json.loads(path.read_text(encoding="utf-8")))
        rows.sort(key=lambda row: row.get("created_at", row.get("started_at", row.get("done_at", row.get("failed_at", row.get("deferred_at", ""))))))
        return rows[-limit:]

    def counts(self) -> dict[str, int]:
        return {
            "pending": len(list(self.tasks_dir.glob("*.json"))),
            "in_progress": len(list(self.in_progress_dir.glob("*.json"))),
            "deferred": len(list(self.deferred_dir.glob("*.json"))),
            "done": len(list(self.done_dir.glob("*.json"))),
            "failed": len(list(self.failed_dir.glob("*.json"))),
        }

    def requeue_due_deferred(self, *, now: str) -> list[dict[str, Any]]:
        if not self.deferred_dir.exists():
            return []
        requeued: list[dict[str, Any]] = []
        for path in sorted(self.deferred_dir.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            retry_after_at = payload.get("retry_after_at")
            if retry_after_at:
                try:
                    retry_at = datetime.fromisoformat(str(retry_after_at))
                except ValueError:
                    retry_at = None
                if retry_at is not None and retry_at > datetime.fromisoformat(now):
                    continue
            if int(payload.get("retry_count", 0)) >= int(payload.get("max_retries", 3)):
                payload["status"] = "failed"
                payload["failed_at"] = _now()
                payload["error"] = payload.get("defer_reason", "retry limit reached")
                target = self.failed_dir / path.name
                target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                path.unlink()
                continue
            payload["status"] = "pending"
            payload["requeued_at"] = now
            target = self.tasks_dir / path.name
            target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            path.unlink()
            requeued.append(payload)
        return requeued

    def next_task(self) -> dict[str, Any] | None:
        tasks = self.list_tasks(limit=1000)
        return tasks[0] if tasks else None

    def mark_started(self, task_id: str) -> dict[str, Any]:
        payload = self._read(self.tasks_dir, task_id)
        payload["status"] = "started"
        payload["started_at"] = _now()
        return self._move_payload(self.tasks_dir, self.in_progress_dir, task_id, payload)

    def mark_done(self, task_id: str, result: dict[str, Any]) -> dict[str, Any]:
        payload = self._read_any(task_id)
        payload["status"] = "done"
        payload["done_at"] = _now()
        payload["result"] = result
        return self._move_payload(self._source_dir(task_id), self.done_dir, task_id, payload)

    def mark_failed(self, task_id: str, error: str) -> dict[str, Any]:
        payload = self._read_any(task_id)
        payload["status"] = "failed"
        payload["failed_at"] = _now()
        payload["error"] = error
        return self._move_payload(self._source_dir(task_id), self.failed_dir, task_id, payload)

    def mark_deferred(self, task_id: str, reason: str, *, retry_after_at: str) -> dict[str, Any]:
        payload = self._read_any(task_id)
        retries = int(payload.get("retry_count", 0)) + 1
        payload["status"] = "deferred"
        payload["deferred_at"] = _now()
        payload["defer_reason"] = reason
        payload["retry_after_at"] = retry_after_at
        payload["retry_count"] = retries
        return self._move_payload(self._source_dir(task_id), self.deferred_dir, task_id, payload)

    def _read(self, source_dir: Path, task_id: str) -> dict[str, Any]:
        path = source_dir / f"{task_id}.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def _read_any(self, task_id: str) -> dict[str, Any]:
        for source_dir in (self.in_progress_dir, self.tasks_dir, self.deferred_dir, self.done_dir, self.failed_dir):
            path = source_dir / f"{task_id}.json"
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        raise KeyError(f"unknown task_id: {task_id}")

    def _source_dir(self, task_id: str) -> Path:
        for source_dir in (self.in_progress_dir, self.tasks_dir, self.deferred_dir, self.done_dir, self.failed_dir):
            path = source_dir / f"{task_id}.json"
            if path.exists():
                return source_dir
        raise KeyError(f"unknown task_id: {task_id}")

    def _move_payload(self, source_dir: Path, target_dir: Path, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        source = source_dir / f"{task_id}.json"
        target = target_dir / f"{task_id}.json"
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if source_dir != target_dir and source.exists():
            source.unlink()
        return payload
