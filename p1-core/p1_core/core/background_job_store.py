from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class BackgroundJobStore:
    root: Path

    def __post_init__(self) -> None:
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        self.completed_dir.mkdir(parents=True, exist_ok=True)
        self.failed_dir.mkdir(parents=True, exist_ok=True)

    @property
    def queue_dir(self) -> Path:
        return self.root / "queue"

    @property
    def completed_dir(self) -> Path:
        return self.root / "completed"

    @property
    def failed_dir(self) -> Path:
        return self.root / "failed"

    def enqueue(
        self,
        *,
        job_type: str,
        model: str,
        payload: dict[str, Any],
        date: str | None = None,
    ) -> dict[str, Any]:
        job = {
            "job_id": f"bgjob:{uuid.uuid4()}",
            "job_type": job_type,
            "status": "queued",
            "model": model,
            "payload": payload,
            "date": date,
            "queued_at": datetime.now(UTC).isoformat(),
        }
        self._write(self.queue_dir / f"{job['job_id'].replace(':', '-')}.json", job)
        return job

    def list_queued(self) -> list[dict[str, Any]]:
        jobs = [self._read(path) for path in sorted(self.queue_dir.glob("*.json"))]
        return sorted(jobs, key=lambda item: item.get("queued_at", ""))

    def get_queued(self, job_id: str) -> dict[str, Any] | None:
        path = self.queue_dir / f"{job_id.replace(':', '-')}.json"
        if not path.exists():
            return None
        return self._read(path)

    def complete(self, job_id: str, result: dict[str, Any]) -> dict[str, Any]:
        path = self.queue_dir / f"{job_id.replace(':', '-')}.json"
        job = self._read(path)
        completed = {
            **job,
            "status": "completed",
            "completed_at": datetime.now(UTC).isoformat(),
            "result": result,
        }
        self._write(self.completed_dir / path.name, completed)
        path.unlink()
        return completed

    def fail(self, job_id: str, error: str) -> dict[str, Any]:
        path = self.queue_dir / f"{job_id.replace(':', '-')}.json"
        job = self._read(path)
        failed = {
            **job,
            "status": "failed",
            "failed_at": datetime.now(UTC).isoformat(),
            "error": error,
        }
        self._write(self.failed_dir / path.name, failed)
        path.unlink()
        return failed

    def counts(self) -> dict[str, int]:
        return {
            "queued": len(list(self.queue_dir.glob("*.json"))),
            "completed": len(list(self.completed_dir.glob("*.json"))),
            "failed": len(list(self.failed_dir.glob("*.json"))),
        }

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _write(path: Path, payload: dict[str, Any]) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
