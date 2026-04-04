from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from p1_core.core.policy_engine import PolicyEngine
from p1_core.models import KnowledgeState
from p1_core.pipeline.growth_loop import build_loop
from p1_core.worker.service import WorkerService


class FakeGrowthClient:
    def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
        if '"task": "draft_lessons"' in user_prompt:
            return {
                "lessons": [
                    "repeated timeout clusters should remain candidate until compared",
                    "repair proposals need explicit rollback points",
                ],
                "counterexamples": ["some retries succeeded without policy change"],
                "follow_up_questions": ["did tool selection change before the failures?"],
            }
        if '"task": "classify"' in user_prompt:
            return {"label": "proposal", "confidence": 0.81, "rationale": "contains operational lessons"}
        return {"summary": "Timeout clusters suggest cautious policy review.", "keywords": ["timeout", "rollback"]}


class GrowthLoopTests(unittest.TestCase):
    def test_growth_loop_persists_knowledge_and_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worker = WorkerService(llm_client=FakeGrowthClient(), log_dir=root / "logs" / "worker")
            loop = build_loop(root, worker)
            result = loop.ingest_text("Repeated timeout failures were observed.", date="2026-04-04")
            self.assertEqual(result["records_written"], 2)
            knowledge_path = root / "state" / "knowledge" / "knowledge.jsonl"
            self.assertTrue(knowledge_path.exists())
            records = [json.loads(line) for line in knowledge_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(records[0]["state"], "candidate")
            self.assertIn("defer", result["evaluation_decisions"])
            proposal_path = root / "state" / "proposals" / "latest-proposals.json"
            self.assertTrue(proposal_path.exists())
            proposal_payload = json.loads(proposal_path.read_text(encoding="utf-8"))
            self.assertIn("proposal_reviews", proposal_payload)
            self.assertEqual(len(result["proposal_reviews"]), 2)
            self.assertEqual(len(result["cloud_requests"]), 2)
            snapshot_path = root / "state" / "proposals" / "snapshots" / "2026-04-04-proposals.json"
            self.assertTrue(snapshot_path.exists())
            glance_path = root / "state" / "reports" / "daily" / "2026-04-04-glance.json"
            self.assertTrue(glance_path.exists())
            daily_payload = json.loads((root / "state" / "reports" / "daily" / "2026-04-04-daily.json").read_text(encoding="utf-8"))
            section_titles = [section["title"] for section in daily_payload["sections"]]
            self.assertIn("Governance Review", section_titles)
            self.assertIn("Cloud Evaluation", section_titles)
            self.assertIn("Autonomous Experiments", section_titles)
            self.assertIn("Short-Horizon Governance", section_titles)
            self.assertIn("Long-Horizon Governance", section_titles)
            health_path = root / "state" / "health.json"
            self.assertTrue(health_path.exists())
            event_log = root / "state" / "events" / "event-log.jsonl"
            self.assertTrue(event_log.exists())
            cloud_request = root / "state" / "cloud_evaluation" / "requests"
            self.assertTrue(cloud_request.exists())

    def test_growth_loop_transitions_knowledge_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worker = WorkerService(llm_client=FakeGrowthClient(), log_dir=root / "logs" / "worker")
            loop = build_loop(root, worker)
            loop.ingest_text("Repeated timeout failures were observed.", date="2026-04-04")
            initial_counts = loop.knowledge_store.counts_by_state()
            latest_records = loop.knowledge_store.latest_by_id()
            record_id = next(iter(latest_records))
            updated = loop.transition_knowledge(
                record_id=record_id,
                new_state=KnowledgeState.DEFERRED,
                reason="needs broader comparison before promotion",
                actor="manager",
            )
            self.assertEqual(updated["state"], "deferred")
            counts = loop.knowledge_store.counts_by_state()
            self.assertGreaterEqual(counts["deferred"], initial_counts["deferred"])

    def test_growth_loop_can_restore_previous_proposal_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worker = WorkerService(llm_client=FakeGrowthClient(), log_dir=root / "logs" / "worker")
            loop = build_loop(root, worker)
            loop.ingest_text("Repeated timeout failures were observed.", date="2026-04-04")
            loop.ingest_text("Repeated timeout failures were observed again.", date="2026-04-05")
            restored = loop.rollback_proposals("2026-04-04-proposals")
            self.assertEqual(restored["restored_from_snapshot_id"], "2026-04-04-proposals")
            latest = json.loads((root / "state" / "proposals" / "latest-proposals.json").read_text(encoding="utf-8"))
            self.assertEqual(latest["snapshot_id"], "2026-04-04-proposals")
            self.assertIn("restored_at", latest)

    def test_growth_loop_applies_and_restores_policy_snapshot(self) -> None:
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

            result = loop.ingest_text("Observation requiring approval.", date="2026-04-04")
            self.assertEqual(len(result["policy_applications"]), 1)
            latest_policy = json.loads((root / "state" / "policies" / "latest-policy.json").read_text(encoding="utf-8"))
            self.assertEqual(len(latest_policy["rules"]), 1)
            snapshot_id = latest_policy["snapshot_id"]

            baseline = loop.rollback_policies("baseline-policy")
            self.assertEqual(baseline["restored_from_snapshot_id"], "baseline-policy")
            restored_policy = json.loads((root / "state" / "policies" / "latest-policy.json").read_text(encoding="utf-8"))
            self.assertEqual(restored_policy["snapshot_id"], "baseline-policy")
            self.assertEqual(restored_policy["rules"], [])
            self.assertNotEqual(snapshot_id, "baseline-policy")

    def test_growth_loop_respects_governance_autonomy_freeze(self) -> None:
        class BoundedClient:
            def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
                if '"task": "draft_lessons"' in user_prompt:
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
            governance_path = root / "state" / "governance" / "latest-governance.json"
            governance_path.parent.mkdir(parents=True, exist_ok=True)
            governance_path.write_text(
                json.dumps(
                    {
                        "snapshot_id": "frozen-autonomy",
                        "constitution": {
                            "preserve_counterexamples": True,
                            "preserve_logs": True,
                            "require_auditability": True,
                        },
                        "laws": {
                            "high_risk_requires_cloud_approval": True,
                            "medium_risk_requires_cloud_approval": True,
                            "allow_duplicate_retirement": True,
                        },
                        "operations": {
                            "autonomy_enabled": False,
                            "max_autonomous_risk": "low",
                            "require_comparison_before_rerun": True,
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                ) + "\n",
                encoding="utf-8",
            )
            worker = WorkerService(llm_client=BoundedClient(), log_dir=root / "logs" / "worker")
            loop = build_loop(root, worker)
            result = loop.ingest_text("Observation without counterexamples.", date="2026-04-04")
            self.assertEqual(result["governance_snapshot_id"], "frozen-autonomy")
            self.assertEqual(result["policy_applications"], [])
            self.assertEqual(loop.knowledge_store.counts_by_state()["candidate"], 1)
            self.assertEqual(result["proposal_reviews"][0]["governance"]["next_step"], "await_governance_update")

    def test_growth_loop_promotes_counterexample_free_candidate_to_active(self) -> None:
        class NoCounterexampleClient:
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
            worker = WorkerService(llm_client=NoCounterexampleClient(), log_dir=root / "logs" / "worker")
            loop = build_loop(root, worker)
            result = loop.ingest_text("Observation without counterexamples.", date="2026-04-04")
            self.assertIn("candidate", result["evaluation_decisions"])
            counts = loop.knowledge_store.counts_by_state()
            self.assertEqual(counts["active"], 1)
            self.assertEqual(len(result["policy_applications"]), 1)
            self.assertEqual(len(result["experiment_results"]), 1)
            self.assertTrue((root / "state" / "experiments" / "latest-experiment.json").exists())

    def test_growth_loop_defers_rerun_after_prior_bounded_experiment(self) -> None:
        class NoCounterexampleClient:
            def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
                if '"task": "draft_lessons"' in user_prompt:
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
            worker = WorkerService(llm_client=NoCounterexampleClient(), log_dir=root / "logs" / "worker")
            loop = build_loop(root, worker)
            first = loop.ingest_text("Observation without counterexamples.", date="2026-04-04")
            second = loop.ingest_text("Observation without counterexamples.", date="2026-04-05")
            self.assertEqual(len(first["experiment_results"]), 1)
            self.assertEqual(len(second["experiment_results"]), 0)
            self.assertIn("defer", second["evaluation_decisions"])
            self.assertEqual(
                second["proposal_reviews"][0]["evaluation"]["reason"],
                "prior bounded experiment exists and should be reviewed before rerunning",
            )
            governance = json.loads((root / "state" / "governance" / "latest-governance.json").read_text(encoding="utf-8"))
            self.assertEqual(governance["feedback"]["rerun_deferral_count"], 1)

    def test_growth_loop_freezes_low_risk_autonomy_after_repeated_rerun_deferrals(self) -> None:
        class NoCounterexampleClient:
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
            worker = WorkerService(llm_client=NoCounterexampleClient(), log_dir=root / "logs" / "worker")
            loop = build_loop(root, worker)
            loop.ingest_text("Observation without counterexamples.", date="2026-04-04")
            loop.ingest_text("Observation without counterexamples.", date="2026-04-05")
            loop.ingest_text("Observation without counterexamples.", date="2026-04-06")
            governance = json.loads((root / "state" / "governance" / "latest-governance.json").read_text(encoding="utf-8"))
            self.assertTrue(governance["feedback"]["freeze_low_risk_autonomy"])
            fourth = loop.ingest_text("A fresh bounded lesson with distinct content.", date="2026-04-07")
            self.assertIn("defer", fourth["evaluation_decisions"])
            self.assertEqual(
                fourth["proposal_reviews"][0]["evaluation"]["reason"],
                "low-risk autonomy is temporarily frozen by long-horizon governance feedback",
            )

    def test_growth_loop_retires_obsolete_candidate(self) -> None:
        class ObsoleteClient:
            def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
                if '"task": "draft_lessons"' in user_prompt:
                    return {
                        "lessons": ["obsolete rule should be retired"],
                        "counterexamples": [],
                        "follow_up_questions": [],
                    }
                if '"task": "classify"' in user_prompt:
                    return {"label": "proposal", "confidence": 0.8, "rationale": "obsolete"}
                return {"summary": "baseline summary", "keywords": ["baseline"]}

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worker = WorkerService(llm_client=ObsoleteClient(), log_dir=root / "logs" / "worker")
            loop = build_loop(root, worker)
            result = loop.ingest_text("Observation about obsolete rule.", date="2026-04-04")
            self.assertIn("retire", result["evaluation_decisions"])
            counts = loop.knowledge_store.counts_by_state()
            self.assertEqual(counts["retired"], 1)

    def test_growth_loop_retires_duplicate_from_previous_snapshot(self) -> None:
        class RepeatedClient:
            def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
                if '"task": "draft_lessons"' in user_prompt:
                    return {
                        "lessons": ["same repeated lesson"],
                        "counterexamples": [],
                        "follow_up_questions": [],
                    }
                if '"task": "classify"' in user_prompt:
                    return {"label": "proposal", "confidence": 0.8, "rationale": "repeat"}
                return {"summary": "baseline summary", "keywords": ["baseline"]}

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worker = WorkerService(llm_client=RepeatedClient(), log_dir=root / "logs" / "worker")
            loop = build_loop(root, worker)
            first = loop.ingest_text("First observation.", date="2026-04-04")
            second = loop.ingest_text("Second observation.", date="2026-04-05")
            self.assertIn("candidate", first["evaluation_decisions"])
            self.assertIn("retire", second["evaluation_decisions"])


if __name__ == "__main__":
    unittest.main()
