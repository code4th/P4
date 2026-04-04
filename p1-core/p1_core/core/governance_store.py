from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class GovernanceStore:
    root: Path

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.latest_path.exists():
            self.latest_path.write_text(
                json.dumps(self.default_profile(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

    @property
    def latest_path(self) -> Path:
        return self.root / "latest-governance.json"

    def default_profile(self) -> dict[str, Any]:
        return {
            "snapshot_id": "baseline-governance",
            "created_at": datetime.now(UTC).isoformat(),
            "constitution": {
                "preserve_counterexamples": True,
                "preserve_logs": True,
                "require_auditability": True,
            },
            "laws": {
                "high_risk_requires_cloud_approval": True,
                "medium_risk_requires_cloud_approval": True,
                "allow_duplicate_retirement": True,
            },
            "operations": {
                "autonomy_enabled": True,
                "max_autonomous_risk": "low",
                "require_comparison_before_rerun": True,
            },
            "feedback": {
                "autonomous_execution_count": 0,
                "rerun_deferral_count": 0,
                "last_experiment_outcome": None,
                "freeze_low_risk_autonomy": False,
                "notes": [],
            },
        }

    def latest(self) -> dict[str, Any]:
        return json.loads(self.latest_path.read_text(encoding="utf-8"))

    def record_feedback(
        self,
        *,
        feedback_type: str,
        proposal_id: str,
        outcome: str,
        note: str,
    ) -> dict[str, Any]:
        current = self.latest()
        feedback = {**current.get("feedback", {})}
        notes = list(feedback.get("notes", []))
        notes.append(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "type": feedback_type,
                "proposal_id": proposal_id,
                "outcome": outcome,
                "note": note,
            }
        )
        feedback["notes"] = notes[-10:]
        feedback["last_experiment_outcome"] = outcome
        if feedback_type == "autonomous_execution":
            feedback["autonomous_execution_count"] = int(feedback.get("autonomous_execution_count", 0)) + 1
        if feedback_type == "rerun_deferral":
            feedback["rerun_deferral_count"] = int(feedback.get("rerun_deferral_count", 0)) + 1
        if int(feedback.get("rerun_deferral_count", 0)) >= 2:
            feedback["freeze_low_risk_autonomy"] = True
        updated = {
            **current,
            "updated_at": datetime.now(UTC).isoformat(),
            "feedback": feedback,
        }
        self.latest_path.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return updated
