from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from p1_core.cli import operator_approvals, operator_report, operator_state, operator_status
from p1_core.core.governance_store import GovernanceStore
from p1_core.core.policy_store import PolicyStore


class OperatorCliTests(unittest.TestCase):
    def test_operator_status_and_approvals_read_generated_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            daily_dir = root / "state" / "reports" / "daily"
            daily_dir.mkdir(parents=True, exist_ok=True)
            (daily_dir / "2026-04-04-glance.json").write_text(
                json.dumps(
                    {
                        "status": "candidate_review",
                        "mainPoints": ["candidate lessons extracted: 1"],
                        "tuningSummary": {
                            "approvalPending": [{"type": "policy_change", "id": "proposal:1"}],
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                ) + "\n",
                encoding="utf-8",
            )
            (daily_dir / "2026-04-04-daily.json").write_text(
                json.dumps({"status": "candidate_review", "sections": []}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            (root / "state" / "health.json").parent.mkdir(parents=True, exist_ok=True)
            (root / "state" / "health.json").write_text(
                json.dumps({"status": "candidate_review", "notes": ["ok"]}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            PolicyStore(root / "state" / "policies")
            GovernanceStore(root / "state" / "governance")

            status = operator_status(root, date="2026-04-04")
            approvals = operator_approvals(root, date="2026-04-04")
            self.assertEqual(status["status"], "candidate_review")
            self.assertEqual(len(status["approvalPending"]), 1)
            self.assertEqual(approvals["approvalPending"][0]["id"], "proposal:1")

    def test_operator_state_reads_latest_external_core_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            knowledge_path = root / "state" / "knowledge" / "knowledge.jsonl"
            knowledge_path.parent.mkdir(parents=True, exist_ok=True)
            knowledge_path.write_text(
                json.dumps(
                    {
                        "record_id": "knowledge:1",
                        "title": "Candidate lesson 1",
                        "body": "body",
                        "state": "active",
                        "source": "test",
                        "tags": ["proposal"],
                    },
                    ensure_ascii=False,
                ) + "\n",
                encoding="utf-8",
            )
            policy_store = PolicyStore(root / "state" / "policies")
            policy_store.apply_proposal({"proposal_id": "proposal:1", "summary": "summary", "risk_level": "low"})
            GovernanceStore(root / "state" / "governance")

            proposals_dir = root / "state" / "proposals"
            proposals_dir.mkdir(parents=True, exist_ok=True)
            (proposals_dir / "latest-proposals.json").write_text(
                json.dumps({"snapshot_id": "2026-04-04-proposals", "proposals": [{"proposal_id": "proposal:1"}]}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            payload = operator_state(root)
            self.assertEqual(payload["knowledgeStateCounts"]["active"], 1)
            self.assertEqual(payload["latestProposalSnapshotId"], "2026-04-04-proposals")
            self.assertEqual(payload["latestPolicyRuleCount"], 1)
            self.assertEqual(payload["latestGovernanceSnapshotId"], "baseline-governance")

    def test_operator_report_reads_health_kind(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            health_path = root / "state" / "health.json"
            health_path.parent.mkdir(parents=True, exist_ok=True)
            health_path.write_text(
                json.dumps({"status": "ok", "notes": ["healthy"]}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            payload = operator_report(root, kind="health")
            self.assertEqual(payload["status"], "ok")


if __name__ == "__main__":
    unittest.main()
