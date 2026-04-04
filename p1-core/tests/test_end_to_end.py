from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from p1_core.cli import operator_report, operator_rollback, operator_state, operator_status
from p1_core.core.policy_engine import PolicyEngine
from p1_core.pipeline.growth_loop import build_loop
from p1_core.worker.service import WorkerService


class ApprovalClient:
    def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
        if '"task": "draft_lessons"' in user_prompt:
            return {
                "lessons": ["needs manager approval for broader change"],
                "counterexamples": [],
                "follow_up_questions": [],
            }
        if '"task": "classify"' in user_prompt:
            return {"label": "proposal", "confidence": 0.88, "rationale": "non-bounded"}
        return {"summary": "approval path summary", "keywords": ["approval"]}


class EndToEndLifecycleTests(unittest.TestCase):
    def test_external_core_lifecycle_is_visible_through_operator_surface(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worker = WorkerService(llm_client=ApprovalClient(), log_dir=root / "logs" / "worker")
            loop = build_loop(root, worker)

            proposal = PolicyEngine().classify("needs manager approval for broader change")
            response_path = root / "state" / "cloud_evaluation" / "responses" / f"{proposal.proposal_id}.json"
            response_path.parent.mkdir(parents=True, exist_ok=True)
            response_path.write_text(
                json.dumps({"proposal_id": proposal.proposal_id, "decision": "approve"}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            ingest_result = loop.ingest_text("Observation requiring approval.", date="2026-04-04")
            self.assertEqual(len(ingest_result["policy_applications"]), 1)

            status_after_ingest = operator_status(root, date="2026-04-04")
            state_after_ingest = operator_state(root)
            daily_after_ingest = operator_report(root, kind="daily", date="2026-04-04")

            self.assertEqual(status_after_ingest["policyRuleCount"], 1)
            self.assertEqual(state_after_ingest["latestPolicyRuleCount"], 1)
            self.assertIn("Short-Horizon Governance", [section["title"] for section in daily_after_ingest["sections"]])
            self.assertEqual(state_after_ingest["latestProposalSnapshotId"], "2026-04-04-proposals")

            rollback_result = operator_rollback(root, target="policies", snapshot_id="baseline-policy")
            self.assertEqual(rollback_result["restored_from_snapshot_id"], "baseline-policy")

            status_after_rollback = operator_status(root)
            state_after_rollback = operator_state(root)
            daily_after_rollback = operator_report(root, kind="daily")

            self.assertEqual(status_after_rollback["policySnapshotId"], "baseline-policy")
            self.assertEqual(state_after_rollback["latestPolicyRuleCount"], 0)
            self.assertEqual(daily_after_rollback["status"], "policy_rollback_applied")

            proposal_rollback = operator_rollback(root, target="proposals", snapshot_id="2026-04-04-proposals")
            self.assertEqual(proposal_rollback["restored_from_snapshot_id"], "2026-04-04-proposals")
            final_status = operator_status(root)
            final_daily = operator_report(root, kind="daily")
            final_state = operator_state(root)
            self.assertEqual(final_daily["status"], "rollback_applied")
            self.assertEqual(final_state["latestProposalSnapshotId"], "2026-04-04-proposals")
            self.assertEqual(final_status["status"], "rollback_applied")

    def test_governance_feedback_changes_later_operator_visible_decision(self) -> None:
        class BoundedClient:
            def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
                if '"task": "draft_lessons"' in user_prompt:
                    if "distinct content" in user_prompt:
                        return {
                            "lessons": ["another bounded lesson"],
                            "counterexamples": [],
                            "follow_up_questions": ["question"],
                        }
                    return {
                        "lessons": ["fresh bounded lesson"],
                        "counterexamples": [],
                        "follow_up_questions": ["question"],
                    }
                if '"task": "classify"' in user_prompt:
                    return {"label": "proposal", "confidence": 0.9, "rationale": "bounded"}
                return {"summary": "baseline summary", "keywords": ["baseline"]}

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            loop = build_loop(root, WorkerService(llm_client=BoundedClient(), log_dir=root / "logs" / "worker"))

            loop.ingest_text("Observation without counterexamples.", date="2026-04-04")
            loop.ingest_text("Observation without counterexamples.", date="2026-04-05")
            loop.ingest_text("Observation without counterexamples.", date="2026-04-06")
            result = loop.ingest_text("A fresh bounded lesson with distinct content.", date="2026-04-07")

            state_payload = operator_state(root)
            daily_payload = operator_report(root, kind="daily", date="2026-04-07")
            governance_feedback = state_payload["governanceProfile"]["feedback"]

            self.assertTrue(governance_feedback["freeze_low_risk_autonomy"])
            self.assertEqual(governance_feedback["rerun_deferral_count"], 2)
            self.assertEqual(
                result["proposal_reviews"][0]["evaluation"]["reason"],
                "low-risk autonomy is temporarily frozen by long-horizon governance feedback",
            )
            long_horizon_section = next(
                section for section in daily_payload["sections"] if section["title"] == "Long-Horizon Governance"
            )
            self.assertIn("feedback.freeze_low_risk_autonomy=True", long_horizon_section["points"])


if __name__ == "__main__":
    unittest.main()
