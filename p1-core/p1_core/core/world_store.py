from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class WorldStore:
    root: Path

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    @property
    def observations_path(self) -> Path:
        return self.root / "observations.jsonl"

    @property
    def action_requests_path(self) -> Path:
        return self.root / "action-requests.jsonl"

    @property
    def action_results_path(self) -> Path:
        return self.root / "action-results.jsonl"

    def observe(self, text: str, *, source: str = "operator") -> dict[str, Any]:
        payload = {
            "observation_id": f"observation:{uuid.uuid4()}",
            "timestamp": datetime.now(UTC).isoformat(),
            "source": source,
            "text": text,
        }
        with self.observations_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return payload

    def request_action(self, kind: str, payload_text: str, *, source: str = "operator") -> dict[str, Any]:
        payload = {
            "action_id": f"action:{uuid.uuid4()}",
            "timestamp": datetime.now(UTC).isoformat(),
            "source": source,
            "kind": kind,
            "payload": payload_text,
            "status": "queued",
        }
        with self.action_requests_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return payload

    def latest(self) -> dict[str, Any]:
        def load_jsonl(path: Path) -> list[dict[str, Any]]:
            if not path.exists():
                return []
            return [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        return {
            "observations": load_jsonl(self.observations_path)[-20:],
            "actionRequests": load_jsonl(self.action_requests_path)[-20:],
            "actionResults": load_jsonl(self.action_results_path)[-20:],
        }
