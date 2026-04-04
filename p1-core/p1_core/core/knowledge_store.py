from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from p1_core.models import KnowledgeRecord, KnowledgeState


class KnowledgeStore:
    """Append-only JSONL store to preserve reviewability and rollback."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: KnowledgeRecord) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")

    def load_all(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [
            json.loads(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def latest_by_id(self) -> dict[str, dict]:
        latest: dict[str, dict] = {}
        for item in self.load_all():
            latest[item["record_id"]] = item
        return latest

    def history_for(self, record_id: str) -> list[dict]:
        return [
            item
            for item in self.load_all()
            if item.get("record_id") == record_id
        ]

    def transition(
        self,
        *,
        record_id: str,
        new_state: KnowledgeState,
        reason: str,
        actor: str = "system",
    ) -> dict:
        current = self.latest_by_id().get(record_id)
        if current is None:
            raise KeyError(f"unknown knowledge record: {record_id}")
        updated = {
            **current,
            "state": str(new_state),
            "transitioned_at": datetime.now(UTC).isoformat(),
            "transition_reason": reason,
            "transition_actor": actor,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(updated, ensure_ascii=False) + "\n")
        return updated

    def counts_by_state(self) -> dict[str, int]:
        counts = {state.value: 0 for state in KnowledgeState}
        for item in self.latest_by_id().values():
            state = str(item.get("state", KnowledgeState.RAW.value))
            counts[state] = counts.get(state, 0) + 1
        return counts


class EventLog:
    """Append-only event log for auditable growth-loop execution."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event_type: str, payload: dict) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "timestamp": datetime.now(UTC).isoformat(),
                        "event_type": event_type,
                        "payload": payload,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
