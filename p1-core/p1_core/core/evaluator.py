from __future__ import annotations


class Evaluator:
    def compare(self, before: dict, after: dict) -> dict[str, object]:
        before_text = " ".join(str(value) for value in before.values()).lower()
        after_text = " ".join(str(value) for value in after.values()).lower()
        state_history = before.get("state_history", [])
        matched_previous_summary = bool(before.get("matched_previous_summary"))
        previous_snapshot_exists = bool(before.get("previous_snapshot_exists"))
        if "obsolete" in after_text or "superseded" in after_text or matched_previous_summary:
            decision = "retire"
            reason = "proposal is obsolete or duplicates a previous snapshot"
        elif "deferred" in state_history:
            decision = "defer"
            reason = "previously deferred knowledge should not be promoted without new evidence"
        elif after.get("counterexamples_present") or "rollback" in after_text or "delete" in after_text:
            decision = "defer"
            reason = "high-impact or contradicted change needs broader comparison before promotion"
        elif previous_snapshot_exists and before_text == after_text:
            decision = "retire"
            reason = "no meaningful delta from previous snapshot was detected"
        else:
            decision = "candidate"
            reason = "proposal differs from prior snapshot and can advance to governance review"
        return {
            "before": before,
            "after": after,
            "decision": decision,
            "reason": reason,
        }
