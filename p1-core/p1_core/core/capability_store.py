from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class CapabilityStore:
    root: Path

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    @property
    def gaps_path(self) -> Path:
        return self.root / "gaps.jsonl"

    def record_gap(
        self,
        *,
        title: str,
        detail: str,
        source: str,
        severity: str = "medium",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "gap_id": f"capgap:{uuid.uuid4()}",
            "title": title,
            "detail": detail,
            "source": source,
            "severity": severity,
            "metadata": metadata or {},
            "recorded_at": _now(),
        }
        with self.gaps_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return payload

    def list_gaps(self, *, limit: int = 20) -> list[dict[str, Any]]:
        if not self.gaps_path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in self.gaps_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rows.append(json.loads(line))
        return rows[-limit:]

    def counts(self) -> dict[str, int]:
        rows = self.list_gaps(limit=100000)
        by_severity = {"low": 0, "medium": 0, "high": 0}
        for row in rows:
            severity = row.get("severity", "medium")
            by_severity[severity] = by_severity.get(severity, 0) + 1
        return {
            "total": len(rows),
            "low": by_severity.get("low", 0),
            "medium": by_severity.get("medium", 0),
            "high": by_severity.get("high", 0),
        }
