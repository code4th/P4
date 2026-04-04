from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class CloudEvaluationStore:
    root: Path
    requests_dir: Path = field(init=False)
    responses_dir: Path = field(init=False)

    def __post_init__(self) -> None:
        self.requests_dir = self.root / "requests"
        self.responses_dir = self.root / "responses"
        self.requests_dir.mkdir(parents=True, exist_ok=True)
        self.responses_dir.mkdir(parents=True, exist_ok=True)

    def queue_request(self, proposal_id: str, payload: dict[str, Any]) -> Path:
        request_path = self.requests_dir / f"{proposal_id}.json"
        request_payload = {
            "proposal_id": proposal_id,
            "requested_at": datetime.now(UTC).isoformat(),
            "status": "pending_cloud_review",
            **payload,
        }
        request_path.write_text(json.dumps(request_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return request_path

    def response_path(self, proposal_id: str) -> Path:
        return self.responses_dir / f"{proposal_id}.json"

    def load_response(self, proposal_id: str) -> dict[str, Any] | None:
        path = self.response_path(proposal_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
