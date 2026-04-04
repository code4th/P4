from __future__ import annotations


class Governor:
    def gate(self, proposal: dict) -> dict[str, object]:
        evaluation = proposal.get("evaluation", {})
        cloud_response = proposal.get("cloud_response") or {}
        cloud_decision = cloud_response.get("decision")
        governance = proposal.get("governance_profile", {})
        operations = governance.get("operations", {})
        laws = governance.get("laws", {})
        risk_level = proposal.get("risk_level")
        autonomy_enabled = operations.get("autonomy_enabled", True)
        max_autonomous_risk = operations.get("max_autonomous_risk", "low")
        autonomy_permitted = autonomy_enabled and risk_level == max_autonomous_risk
        cloud_required = (
            (risk_level == "high" and laws.get("high_risk_requires_cloud_approval", True))
            or (risk_level == "medium" and laws.get("medium_risk_requires_cloud_approval", True))
        )
        if proposal.get("requires_approval") is False and evaluation.get("decision") == "candidate" and autonomy_permitted:
            approved = True
            next_step = "autonomous_apply"
        elif proposal.get("requires_approval") is False and evaluation.get("decision") == "candidate":
            approved = False
            next_step = "await_governance_update"
        elif evaluation.get("decision") == "candidate" and cloud_decision == "approve":
            approved = True
            next_step = "approved_for_policy_apply"
        elif evaluation.get("decision") == "candidate" and cloud_decision == "reject":
            approved = False
            next_step = "defer_after_rejection"
        elif evaluation.get("decision") == "candidate" and cloud_required:
            approved = False
            next_step = "await_cloud_approval"
        elif evaluation.get("decision") == "candidate":
            approved = False
            next_step = "await_governance_update"
        else:
            approved = False
            next_step = "defer_and_compare"
        return {
            "proposal": proposal,
            "approved": approved,
            "next_step": next_step,
            "cloud_decision": cloud_decision,
            "governance_profile": governance,
        }
