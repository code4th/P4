from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class ConversationStore:
    root: Path

    def __post_init__(self) -> None:
        self.transcript_path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def transcript_path(self) -> Path:
        return self.root / "transcript.jsonl"

    def append(self, role: str, content: str, *, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "role": role,
            "content": content,
            "metadata": metadata or {},
        }
        with self.transcript_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return payload

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        if not self.transcript_path.exists():
            return []
        rows = [
            json.loads(line)
            for line in self.transcript_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        return rows[-limit:]
