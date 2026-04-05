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

    def record_proposal(
        self,
        *,
        gap_id: str,
        summary: str,
        proposal_type: str,
        risk_level: str,
        requires_approval: bool,
        detail: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "proposal_id": f"capprop:{uuid.uuid4()}",
            "gap_id": gap_id,
            "proposal_type": proposal_type,
            "summary": summary,
            "risk_level": risk_level,
            "requires_approval": requires_approval,
            "detail": detail,
            "metadata": metadata or {},
            "recorded_at": _now(),
        }
        with self.proposals_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return payload

    @property
    def proposals_path(self) -> Path:
        return self.root / "proposals.jsonl"

    @property
    def reviews_path(self) -> Path:
        return self.root / "reviews.jsonl"

    def list_proposals(self, *, limit: int = 20) -> list[dict[str, Any]]:
        if not self.proposals_path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in self.proposals_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rows.append(json.loads(line))
        return rows[-limit:]

    def proposal_counts(self) -> dict[str, int]:
        rows = self.list_proposals(limit=100000)
        return {
            "total": len(rows),
            "approval_required": sum(1 for row in rows if row.get("requires_approval")),
            "autonomous_candidate": sum(1 for row in rows if not row.get("requires_approval")),
        }

    def has_proposal_for_gap(self, gap_id: str) -> bool:
        return any(row.get("gap_id") == gap_id for row in self.list_proposals(limit=100000))

    def record_review(
        self,
        *,
        proposal_id: str,
        gap_id: str,
        evaluation: dict[str, Any],
        governance: dict[str, Any],
        cloud_request_path: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "review_id": f"capreview:{uuid.uuid4()}",
            "proposal_id": proposal_id,
            "gap_id": gap_id,
            "evaluation": evaluation,
            "governance": governance,
            "cloud_request_path": cloud_request_path,
            "recorded_at": _now(),
        }
        with self.reviews_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return payload

    def list_reviews(self, *, limit: int = 20) -> list[dict[str, Any]]:
        if not self.reviews_path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in self.reviews_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rows.append(json.loads(line))
        return rows[-limit:]

    def review_counts(self) -> dict[str, int]:
        rows = self.list_reviews(limit=100000)
        return {
            "total": len(rows),
            "approval_pending": sum(1 for row in rows if row.get("governance", {}).get("next_step") == "await_cloud_approval"),
            "autonomous_apply": sum(1 for row in rows if row.get("governance", {}).get("next_step") == "autonomous_apply"),
            "deferred": sum(1 for row in rows if "defer" in str(row.get("governance", {}).get("next_step", ""))),
        }

    def has_review_for_proposal(self, proposal_id: str) -> bool:
        return any(row.get("proposal_id") == proposal_id for row in self.list_reviews(limit=100000))
