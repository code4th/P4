from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class PolicyProposal:
    proposal_id: str
    summary: str
    risk_level: str
    requires_approval: bool = True


class PolicyEngine:
    """Minimal placeholder for operation-rule mutation, not knowledge mutation."""

    def classify(self, summary: str) -> PolicyProposal:
        lower = summary.lower()
        risk_level = "high" if "rollback" in lower or "delete" in lower else "medium"
        return PolicyProposal(
            proposal_id=f"proposal:{abs(hash(summary))}",
            summary=summary,
            risk_level=risk_level,
            requires_approval=True,
        )
