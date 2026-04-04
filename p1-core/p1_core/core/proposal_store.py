from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class ProposalStore:
    """Versioned proposal snapshots with a latest pointer for easy rollback."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.snapshots_dir = self.root / "snapshots"
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.latest_path = self.root / "latest-proposals.json"

    def write_snapshot(self, payload: dict[str, Any], *, snapshot_name: str | None = None) -> Path:
        stamp = snapshot_name or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        snapshot_path = self.snapshots_dir / f"{stamp}.json"
        snapshot_payload = {
            "snapshot_id": stamp,
            "generated_at": datetime.now(UTC).isoformat(),
            **payload,
        }
        snapshot_path.write_text(
            json.dumps(snapshot_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.latest_path.write_text(
            json.dumps(snapshot_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return snapshot_path

    def latest(self) -> dict[str, Any] | None:
        if not self.latest_path.exists():
            return None
        return json.loads(self.latest_path.read_text(encoding="utf-8"))

    def compare_with_latest(self, payload: dict[str, Any]) -> dict[str, Any]:
        previous = self.latest()
        if previous is None:
            return {
                "has_previous": False,
                "previous_snapshot_id": None,
                "proposal_count_delta": len(payload.get("proposals", [])),
                "summary_changed": True,
            }
        previous_proposals = previous.get("proposals", [])
        current_proposals = payload.get("proposals", [])
        return {
            "has_previous": True,
            "previous_snapshot_id": previous.get("snapshot_id"),
            "proposal_count_delta": len(current_proposals) - len(previous_proposals),
            "summary_changed": previous.get("summary") != payload.get("summary"),
        }

    def snapshot_path(self, snapshot_id: str) -> Path:
        return self.snapshots_dir / f"{snapshot_id}.json"

    def restore_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        path = self.snapshot_path(snapshot_id)
        if not path.exists():
            raise FileNotFoundError(f"unknown proposal snapshot: {snapshot_id}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        restored = {
            **payload,
            "restored_at": datetime.now(UTC).isoformat(),
            "restored_from_snapshot_id": snapshot_id,
        }
        self.latest_path.write_text(
            json.dumps(restored, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return restored
