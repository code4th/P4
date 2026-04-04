from __future__ import annotations


class Critic:
    def critique(self, candidate: str, *, counterexamples: list[str] | None = None) -> dict[str, object]:
        counterexamples = counterexamples or []
        risks = ["preserve rollback path"]
        if counterexamples:
            risks.append("counterexamples present")
        return {
            "candidate": candidate,
            "risks": risks,
            "status": "needs_review" if counterexamples else "reviewable",
        }
