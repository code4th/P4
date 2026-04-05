from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _gap_key(*, title: str, detail: str, source: str, metadata: dict[str, Any] | None = None) -> str:
    payload = {
        "title": title.strip(),
        "detail": detail.strip(),
        "source": source.strip(),
        "metadata": metadata or {},
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return f"capgapkey:{sha256(encoded.encode('utf-8')).hexdigest()[:16]}"


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
        gap_key = _gap_key(title=title, detail=detail, source=source, metadata=metadata)
        existing = self.find_gap_by_key(gap_key)
        if existing is not None:
            return existing
        payload = {
            "gap_id": f"capgap:{uuid.uuid4()}",
            "gap_key": gap_key,
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

    def find_gap_by_key(self, gap_key: str) -> dict[str, Any] | None:
        for row in reversed(self.list_gaps(limit=100000)):
            if row.get("gap_key") == gap_key:
                return row
        return None

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
            "gap_key": metadata.get("gap_key") if metadata else None,
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

    @property
    def executions_path(self) -> Path:
        return self.root / "executions.jsonl"

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

    def has_proposal_for_gap_key(self, gap_key: str) -> bool:
        return any(row.get("gap_key") == gap_key for row in self.list_proposals(limit=100000))

    def record_review(
        self,
        *,
        proposal_id: str,
        gap_id: str,
        evaluation: dict[str, Any],
        governance: dict[str, Any],
        cloud_request_path: str | None = None,
    ) -> dict[str, Any]:
        existing = self.find_review_for_proposal(proposal_id)
        if existing is not None:
            return existing
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

    def find_review_for_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        for row in reversed(self.list_reviews(limit=100000)):
            if row.get("proposal_id") == proposal_id:
                return row
        return None

    def record_execution(
        self,
        *,
        review_id: str,
        proposal_id: str,
        action_id: str | None,
        status: str,
        detail: str,
        metadata: dict[str, Any] | None = None,
        execution_id: str | None = None,
    ) -> dict[str, Any]:
        current_execution_id = execution_id
        if current_execution_id is None:
            existing = self.find_execution_for_proposal(proposal_id)
            if existing is not None:
                return existing
            current_execution_id = f"capexec:{uuid.uuid4()}"
        payload = {
            "execution_id": current_execution_id,
            "review_id": review_id,
            "proposal_id": proposal_id,
            "action_id": action_id,
            "status": status,
            "detail": detail,
            "metadata": metadata or {},
            "recorded_at": _now(),
        }
        with self.executions_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return payload

    def list_executions(self, *, limit: int = 20) -> list[dict[str, Any]]:
        if not self.executions_path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in self.executions_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rows.append(json.loads(line))
        return rows[-limit:]

    def execution_counts(self) -> dict[str, int]:
        rows = self.list_executions(limit=100000)
        return {
            "total": len(rows),
            "queued_action": sum(1 for row in rows if row.get("status") == "queued_action"),
            "completed": sum(1 for row in rows if row.get("status") == "completed"),
            "failed": sum(1 for row in rows if row.get("status") == "failed"),
            "rejected": sum(1 for row in rows if row.get("status") == "rejected"),
        }

    def has_execution_for_review(self, review_id: str) -> bool:
        return any(row.get("review_id") == review_id for row in self.list_executions(limit=100000))

    def has_execution_for_proposal(self, proposal_id: str) -> bool:
        return any(row.get("proposal_id") == proposal_id for row in self.list_executions(limit=100000))

    def find_execution_for_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        for row in reversed(self.list_executions(limit=100000)):
            if row.get("proposal_id") == proposal_id:
                return row
        return None

    def find_execution_for_action(self, action_id: str) -> dict[str, Any] | None:
        for row in reversed(self.list_executions(limit=100000)):
            if row.get("action_id") == action_id:
                return row
        return None

    def update_execution(
        self,
        execution_id: str,
        *,
        status: str,
        detail: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        rows = self.list_executions(limit=100000)
        updated: dict[str, Any] | None = None
        rewritten: list[dict[str, Any]] = []
        for row in rows:
            if row.get("execution_id") == execution_id:
                row = {
                    **row,
                    "status": status,
                    "detail": detail,
                    "metadata": {**row.get("metadata", {}), **(metadata or {})},
                    "updated_at": _now(),
                }
                updated = row
            rewritten.append(row)
        if updated is None:
            raise KeyError(f"unknown execution_id: {execution_id}")
        self.executions_path.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in rewritten) + "\n",
            encoding="utf-8",
        )
        return updated
