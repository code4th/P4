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
        if "rollback" in lower or "delete" in lower:
            risk_level = "high"
            requires_approval = True
        elif "bounded" in lower or "small experiment" in lower or "minor" in lower:
            risk_level = "low"
            requires_approval = False
        elif any(kw in lower for kw in ("improve", "enhance", "add logging", "error handling", "refactor", "optimize", "robust")):
            risk_level = "low"
            requires_approval = False
        else:
            risk_level = "medium"
            requires_approval = True
        return PolicyProposal(
            proposal_id=f"proposal:{abs(hash(summary))}",
            summary=summary,
            risk_level=risk_level,
            requires_approval=requires_approval,
        )
