from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class ExperimentRunner:
    root: Path
    latest_path: Path = field(init=False)
    history_dir: Path = field(init=False)
    actions_dir: Path = field(init=False)

    def __post_init__(self) -> None:
        base = self.root / "state" / "experiments"
        self.latest_path = base / "latest-experiment.json"
        self.history_dir = base / "history"
        self.actions_dir = base / "actions"
        self.latest_path.parent.mkdir(parents=True, exist_ok=True)
        self.history_dir.mkdir(parents=True, exist_ok=True)
        self.actions_dir.mkdir(parents=True, exist_ok=True)

    def execute_bounded_action(self, proposal_id: str, summary: str) -> dict[str, Any]:
        timestamp = datetime.now(UTC).isoformat()
        action_payload = {
            "proposal_id": proposal_id,
            "summary": summary,
            "mode": "bounded_file_action",
            "outcome": "executed",
            "result": "wrote bounded action note for operator review",
            "executed_at": timestamp,
        }
        action_path = self.actions_dir / f"{proposal_id}.json"
        action_path.write_text(json.dumps(action_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        payload = {**action_payload, "action_path": str(action_path)}
        self.latest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (self.history_dir / f"{proposal_id}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return payload

    def latest_for(self, proposal_id: str) -> dict[str, Any] | None:
        path = self.history_dir / f"{proposal_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
