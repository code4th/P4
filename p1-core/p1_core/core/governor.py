from __future__ import annotations


class Governor:
    def gate(self, proposal: dict) -> dict[str, object]:
        evaluation = proposal.get("evaluation", {})
        if evaluation.get("decision") == "candidate":
            approved = False
            next_step = "manager_review"
        else:
            approved = False
            next_step = "defer_and_compare"
        return {
            "proposal": proposal,
            "approved": approved,
            "next_step": next_step,
        }
