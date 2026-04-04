from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class KnowledgeState(StrEnum):
    RAW = "raw"
    CANDIDATE = "candidate"
    DEFERRED = "deferred"
    ACTIVE = "active"
    RETIRED = "retired"


@dataclass(slots=True)
class KnowledgeRecord:
    record_id: str
    title: str
    body: str
    state: KnowledgeState = KnowledgeState.RAW
    source: str | None = None
    tags: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
