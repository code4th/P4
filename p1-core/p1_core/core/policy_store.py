from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class PolicyStore:
    root: Path

    def __post_init__(self) -> None:
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        baseline = {
            "snapshot_id": "baseline-policy",
            "created_at": datetime.now(UTC).isoformat(),
            "rules": [],
        }
        baseline_text = json.dumps(baseline, ensure_ascii=False, indent=2) + "\n"
        baseline_path = self.snapshots_dir / "baseline-policy.json"
        if not baseline_path.exists():
            baseline_path.write_text(baseline_text, encoding="utf-8")
        if not self.latest_path.exists():
            self.latest_path.write_text(baseline_text, encoding="utf-8")

    @property
    def latest_path(self) -> Path:
        return self.root / "latest-policy.json"

    @property
    def snapshots_dir(self) -> Path:
        return self.root / "snapshots"

    def latest(self) -> dict[str, Any]:
        return json.loads(self.latest_path.read_text(encoding="utf-8"))

    def write_snapshot(self, payload: dict[str, Any], *, snapshot_name: str | None = None) -> Path:
        stamp = snapshot_name or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        snapshot = {
            "snapshot_id": stamp,
            "created_at": datetime.now(UTC).isoformat(),
            **payload,
        }
        text = json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n"
        path = self.snapshots_dir / f"{stamp}.json"
        path.write_text(text, encoding="utf-8")
        self.latest_path.write_text(text, encoding="utf-8")
        return path

    def apply_proposal(self, proposal: dict[str, Any], *, snapshot_name: str | None = None) -> Path:
        current = self.latest()
        rules = list(current.get("rules", []))
        rules.append(
            {
                "proposal_id": proposal.get("proposal_id"),
                "summary": proposal.get("summary"),
                "risk_level": proposal.get("risk_level"),
                "applied_at": datetime.now(UTC).isoformat(),
            }
        )
        return self.write_snapshot(
            {
                "restored_from_snapshot_id": current.get("snapshot_id"),
                "rules": rules,
            },
            snapshot_name=snapshot_name,
        )

    def restore_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        path = self.snapshots_dir / f"{snapshot_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"unknown policy snapshot: {snapshot_id}")
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        restored = {
            **snapshot,
            "restored_at": datetime.now(UTC).isoformat(),
            "restored_from_snapshot_id": snapshot_id,
        }
        self.latest_path.write_text(json.dumps(restored, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return restored
