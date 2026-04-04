from __future__ import annotations


class Governor:
    def gate(self, proposal: dict) -> dict[str, object]:
        evaluation = proposal.get("evaluation", {})
        cloud_response = proposal.get("cloud_response") or {}
        cloud_decision = cloud_response.get("decision")
        if proposal.get("requires_approval") is False and evaluation.get("decision") == "candidate":
            approved = True
            next_step = "autonomous_apply"
        elif evaluation.get("decision") == "candidate" and cloud_decision == "approve":
            approved = True
            next_step = "approved_for_policy_apply"
        elif evaluation.get("decision") == "candidate" and cloud_decision == "reject":
            approved = False
            next_step = "defer_after_rejection"
        elif evaluation.get("decision") == "candidate":
            approved = False
            next_step = "await_cloud_approval"
        else:
            approved = False
            next_step = "defer_and_compare"
        return {
            "proposal": proposal,
            "approved": approved,
            "next_step": next_step,
            "cloud_decision": cloud_decision,
        }
