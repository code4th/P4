from __future__ import annotations

import argparse
import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from datetime import UTC, datetime

from p1_core.core.knowledge_store import EventLog, KnowledgeStore
from p1_core.core.critic import Critic
from p1_core.core.cloud_evaluation import CloudEvaluationStore
from p1_core.core.evaluator import Evaluator
from p1_core.core.governance_store import GovernanceStore
from p1_core.core.governor import Governor
from p1_core.core.policy_engine import PolicyEngine
from p1_core.core.policy_store import PolicyStore
from p1_core.core.proposal_store import ProposalStore
from p1_core.models import KnowledgeRecord, KnowledgeState
from p1_core.reporting.report_writer import ReportWriter
from p1_core.worker.service import WorkerService


@dataclass(slots=True)
class GrowthLoop:
    root: Path
    worker_service: WorkerService
    report_writer: ReportWriter
    policy_engine: PolicyEngine
    critic: Critic
    cloud_evaluation_store: CloudEvaluationStore
    evaluator: Evaluator
    governor: Governor
    knowledge_store: KnowledgeStore = field(init=False)
    event_log: EventLog = field(init=False)
    proposal_store: ProposalStore = field(init=False)
    policy_store: PolicyStore = field(init=False)
    governance_store: GovernanceStore = field(init=False)

    def __post_init__(self) -> None:
        self.knowledge_store = KnowledgeStore(self.root / "state" / "knowledge" / "knowledge.jsonl")
        self.event_log = EventLog(self.root / "state" / "events" / "event-log.jsonl")
        self.proposal_store = ProposalStore(self.root / "state" / "proposals")
        self.policy_store = PolicyStore(self.root / "state" / "policies")
        self.governance_store = GovernanceStore(self.root / "state" / "governance")

    @staticmethod
    def _approval_status(cloud_response: dict | None) -> str:
        if not cloud_response:
            return "pending"
        decision = str(cloud_response.get("decision", "pending"))
        if decision == "approve":
            return "approved"
        if decision == "reject":
            return "rejected"
        return "responded"

    def ingest_text(self, text: str, *, date: str | None = None) -> dict:
        governance_profile = self.governance_store.latest()
        lesson_result = self.worker_service.draft_lessons({"text": text})["result"]
        classify_result = self.worker_service.classify({"text": text})["result"]
        summary_result = self.worker_service.summarize({"text": text, "max_sentences": 2})["result"]

        lessons = lesson_result.get("lessons", [])
        counterexamples = lesson_result.get("counterexamples", [])
        follow_up_questions = lesson_result.get("follow_up_questions", [])

        records = [
            KnowledgeRecord(
                record_id=f"knowledge:{uuid.uuid4()}",
                title=f"Candidate lesson {index + 1}",
                body=str(lesson),
                state=KnowledgeState.CANDIDATE,
                source="growth_loop",
                tags=[str(classify_result.get("label", "observation"))],
            )
            for index, lesson in enumerate(lessons)
        ]
        for record in records:
            self.knowledge_store.append(record)
        previous_snapshot = self.proposal_store.latest()
        previous_summaries = {
            proposal.get("summary")
            for proposal in (previous_snapshot or {}).get("proposals", [])
        }

        proposals = [
            self.policy_engine.classify(str(lesson))
            for lesson in lessons
        ]
        proposal_reviews = []
        cloud_requests = []
        policy_applications = []
        for index, proposal in enumerate(proposals):
            proposal_dict = asdict(proposal)
            critique = self.critic.critique(proposal.summary, counterexamples=[str(item) for item in counterexamples])
            state_history = [
                item.get("state")
                for item in self.knowledge_store.history_for(records[index].record_id)
            ]
            evaluation = self.evaluator.compare(
                {
                    "state": records[index].state.value,
                    "state_history": state_history,
                    "previous_snapshot_exists": previous_snapshot is not None,
                    "previous_snapshot_summary": (previous_snapshot or {}).get("summary"),
                    "matched_previous_summary": proposal.summary in previous_summaries,
                    "governance_profile": governance_profile,
                },
                {
                    **proposal_dict,
                    "counterexamples_present": bool(counterexamples),
                },
            )
            cloud_response = None
            cloud_request_path = None
            if proposal.requires_approval:
                cloud_request_path = self.cloud_evaluation_store.queue_request(
                    proposal.proposal_id,
                    {
                        "proposal": proposal_dict,
                        "critique": critique,
                        "evaluation": evaluation,
                    },
                )
                cloud_response = self.cloud_evaluation_store.load_response(proposal.proposal_id)
                cloud_requests.append(str(cloud_request_path))
            governance = self.governor.gate(
                {
                    **proposal_dict,
                    "evaluation": evaluation,
                    "critique": critique,
                    "cloud_response": cloud_response,
                    "governance_profile": governance_profile,
                }
            )
            if evaluation["decision"] == "defer":
                self.transition_knowledge(
                    record_id=records[index].record_id,
                    new_state=KnowledgeState.DEFERRED,
                    reason=str(evaluation["reason"]),
                    actor="growth_loop",
                )
            elif evaluation["decision"] == "retire":
                self.transition_knowledge(
                    record_id=records[index].record_id,
                    new_state=KnowledgeState.RETIRED,
                    reason=str(evaluation["reason"]),
                    actor="growth_loop",
                )
            elif evaluation["decision"] == "candidate" and governance["approved"]:
                self.transition_knowledge(
                    record_id=records[index].record_id,
                    new_state=KnowledgeState.ACTIVE,
                    reason=(
                        "candidate promoted to active after bounded policy application"
                        if proposal.requires_approval is False
                        else "candidate promoted to active after cloud-approved policy application"
                    ),
                    actor="growth_loop",
                )
                policy_snapshot_path = self.policy_store.apply_proposal(
                    proposal_dict,
                    snapshot_name=f"{date}-{proposal.proposal_id.replace(':', '-')}-policy" if date else None,
                )
                policy_applications.append(
                    {
                        "proposal_id": proposal.proposal_id,
                        "snapshot_path": str(policy_snapshot_path),
                        "mode": governance["next_step"],
                    }
                )
            elif evaluation["decision"] == "candidate" and governance["next_step"] == "defer_after_rejection":
                self.transition_knowledge(
                    record_id=records[index].record_id,
                    new_state=KnowledgeState.DEFERRED,
                    reason="candidate deferred after cloud rejection",
                    actor="growth_loop",
                )
            proposal_reviews.append(
                {
                    "proposal": proposal_dict,
                    "critique": critique,
                    "evaluation": evaluation,
                    "governance": governance,
                    "cloud_request_path": str(cloud_request_path) if cloud_request_path else None,
                    "cloud_response": cloud_response,
                }
            )
        state_counts = self.knowledge_store.counts_by_state()

        approval_pending = [
            {
                "type": "policy_change",
                "id": proposal.proposal_id,
                "risk": proposal.risk_level,
                "status": self._approval_status(review["cloud_response"]),
            }
            for proposal, review in zip(proposals, proposal_reviews)
            if proposal.requires_approval and self._approval_status(review["cloud_response"]) == "pending"
        ]

        self.event_log.append(
            "growth_loop_ingest",
            {
                "input_text": text,
                "lesson_count": len(lessons),
                "proposal_count": len(proposals),
                "knowledge_state_counts": state_counts,
                "reviewed_proposals": len(proposal_reviews),
                "evaluation_decisions": [review["evaluation"]["decision"] for review in proposal_reviews],
                "previous_snapshot_id": (previous_snapshot or {}).get("snapshot_id"),
                "pendingCloudReviews": len(cloud_requests),
                "policyApplications": len(policy_applications),
                "governanceSnapshotId": governance_profile.get("snapshot_id"),
            },
        )

        proposal_payload = {
            "summary": summary_result.get("summary"),
            "classification": classify_result,
            "lessons": lesson_result,
            "records": [
                {
                    **asdict(record),
                    "latest_state": self.knowledge_store.latest_by_id()[record.record_id]["state"],
                }
                for record in records
            ],
            "proposals": [asdict(proposal) for proposal in proposals],
            "proposal_reviews": proposal_reviews,
        }
        proposal_comparison = self.proposal_store.compare_with_latest(proposal_payload)

        self.report_writer.write_glance(
            status="candidate_review",
            main_points=[
                f"candidate lessons extracted: {len(lessons)}",
                f"classification label: {classify_result.get('label', 'unknown')}",
            ],
            recommended_interventions=[
                "review candidate lessons before promotion",
                "preserve counterexamples and follow-up questions",
            ],
            track_summary={
                "candidateLessons": len(lessons),
                "counterexamples": len(counterexamples),
                "followUpQuestions": len(follow_up_questions),
                "knowledgeStates": state_counts,
                "proposalDelta": proposal_comparison["proposal_count_delta"],
                "governanceReviews": len(proposal_reviews),
                "deferredByPolicy": sum(1 for review in proposal_reviews if review["evaluation"]["decision"] == "defer"),
                "activeByPolicy": sum(1 for review in proposal_reviews if review["evaluation"]["decision"] == "candidate"),
                "retiredByPolicy": sum(1 for review in proposal_reviews if review["evaluation"]["decision"] == "retire"),
                "pendingCloudReviews": len(cloud_requests),
                "policyApplications": len(policy_applications),
                "governanceSnapshotId": governance_profile.get("snapshot_id"),
            },
            approval_pending=approval_pending,
            date=date,
        )
        self.report_writer.write_daily(
            status="candidate_review",
            summary=str(summary_result.get("summary", "summary unavailable")),
            sections=[
                {
                    "title": "Candidate Lessons",
                    "points": [str(item) for item in lessons] or ["none"],
                },
                {
                    "title": "Counterexamples",
                    "points": [str(item) for item in counterexamples] or ["none"],
                },
                {
                    "title": "Follow-up Questions",
                    "points": [str(item) for item in follow_up_questions] or ["none"],
                },
                {
                    "title": "Governance Review",
                    "points": [
                        (
                            f"{review['proposal']['proposal_id']}: "
                            f"{review['evaluation']['decision']} / {review['governance']['next_step']} / approved={review['governance']['approved']}"
                        )
                        for review in proposal_reviews
                    ]
                    or ["none"],
                },
                {
                    "title": "Cloud Evaluation",
                    "points": [
                        f"{review['proposal']['proposal_id']}: status={self._approval_status(review['cloud_response'])}, decision={review['governance'].get('cloud_decision') or 'pending'}"
                        for review in proposal_reviews
                        if review["proposal"]["requires_approval"]
                    ]
                    or ["none"],
                },
                {
                    "title": "Policy Applications",
                    "points": [
                        f"{item['proposal_id']}: {item['mode']} / {item['snapshot_path']}"
                        for item in policy_applications
                    ]
                    or ["none"],
                },
                {
                    "title": "Short-Horizon Governance",
                    "points": [
                        f"operations.autonomy_enabled={governance_profile.get('operations', {}).get('autonomy_enabled')}",
                        f"operations.max_autonomous_risk={governance_profile.get('operations', {}).get('max_autonomous_risk')}",
                    ],
                },
                {
                    "title": "Long-Horizon Governance",
                    "points": [
                        f"constitution.require_auditability={governance_profile.get('constitution', {}).get('require_auditability')}",
                        f"laws.high_risk_requires_cloud_approval={governance_profile.get('laws', {}).get('high_risk_requires_cloud_approval')}",
                        f"laws.medium_risk_requires_cloud_approval={governance_profile.get('laws', {}).get('medium_risk_requires_cloud_approval')}",
                    ],
                },
            ],
            proposals=[
                {
                    "id": proposal.proposal_id,
                    "summary": proposal.summary,
                    "risk": proposal.risk_level,
                    "state": (
                        "approved"
                        if proposal.proposal_id in {item["proposal_id"] for item in policy_applications}
                        else "pending_approval"
                        if proposal.requires_approval
                        else "proposed"
                    ),
                }
                for proposal in proposals
            ],
            date=date,
        )
        self.report_writer.write_health(
            status="candidate_review",
            approval_pending=approval_pending,
            notes=[
                "knowledge candidates persisted",
                "promotion remains approval-gated",
                f"proposal delta: {proposal_comparison['proposal_count_delta']}",
                f"pending cloud reviews: {len(cloud_requests)}",
                f"policy applications: {len(policy_applications)}",
                f"governance snapshot: {governance_profile.get('snapshot_id')}",
            ],
        )

        proposal_payload["comparison"] = proposal_comparison
        snapshot_name = f"{date}-proposals" if date else None
        proposal_path = self.proposal_store.write_snapshot(proposal_payload, snapshot_name=snapshot_name)

        return {
            "records_written": len(records),
            "proposals_written": len(proposals),
            "approval_pending": approval_pending,
            "proposal_path": str(proposal_path),
            "knowledge_state_counts": state_counts,
            "proposal_comparison": proposal_comparison,
            "proposal_reviews": proposal_reviews,
            "evaluation_decisions": [review["evaluation"]["decision"] for review in proposal_reviews],
            "cloud_requests": cloud_requests,
            "policy_applications": policy_applications,
            "governance_snapshot_id": governance_profile.get("snapshot_id"),
        }

    def transition_knowledge(
        self,
        *,
        record_id: str,
        new_state: KnowledgeState,
        reason: str,
        actor: str = "system",
    ) -> dict:
        updated = self.knowledge_store.transition(
            record_id=record_id,
            new_state=new_state,
            reason=reason,
            actor=actor,
        )
        self.event_log.append(
            "knowledge_transition",
            {
                "record_id": record_id,
                "new_state": str(new_state),
                "reason": reason,
                "actor": actor,
            },
        )
        return updated

    def rollback_proposals(self, snapshot_id: str) -> dict:
        restored = self.proposal_store.restore_snapshot(snapshot_id)
        restored_proposals = restored.get("proposals", [])
        approval_pending = [
            {
                "type": "policy_change",
                "id": proposal.get("proposal_id"),
                "risk": proposal.get("risk_level"),
            }
            for proposal in restored_proposals
            if proposal.get("requires_approval", True)
        ]
        report_date = datetime.now(UTC).date().isoformat()
        self.event_log.append(
            "proposal_rollback",
            {
                "snapshot_id": snapshot_id,
                "restored_from_snapshot_id": restored.get("restored_from_snapshot_id"),
            },
        )
        self.report_writer.write_glance(
            status="rollback_applied",
            main_points=[
                f"restored proposal snapshot: {snapshot_id}",
                f"proposal count after rollback: {len(restored_proposals)}",
            ],
            recommended_interventions=[
                "review restored proposal set before new promotion",
                "compare restored snapshot against the next candidate batch",
            ],
            track_summary={
                "restoredSnapshotId": snapshot_id,
                "proposalCount": len(restored_proposals),
            },
            approval_pending=approval_pending,
            date=report_date,
        )
        self.report_writer.write_daily(
            status="rollback_applied",
            summary=str(restored.get("summary", "rollback applied")),
            sections=[
                {
                    "title": "Rollback",
                    "points": [
                        f"restored snapshot: {snapshot_id}",
                        f"restored from snapshot id: {restored.get('restored_from_snapshot_id', snapshot_id)}",
                    ],
                },
                {
                    "title": "Restored Proposals",
                    "points": [str(proposal.get("summary", proposal.get("proposal_id"))) for proposal in restored_proposals]
                    or ["none"],
                },
                {
                    "title": "Restored Governance Review",
                    "points": [
                        f"{review.get('proposal', {}).get('proposal_id')}: {review.get('governance', {}).get('next_step')}"
                        for review in restored.get("proposal_reviews", [])
                    ]
                    or ["none"],
                },
            ],
            proposals=[
                {
                    "id": proposal.get("proposal_id"),
                    "summary": proposal.get("summary"),
                    "risk": proposal.get("risk_level"),
                    "state": "pending_approval" if proposal.get("requires_approval", True) else "proposed",
                }
                for proposal in restored_proposals
            ],
            date=report_date,
        )
        self.report_writer.write_health(
            status="rollback_applied",
            approval_pending=approval_pending,
            notes=[
                f"proposal snapshot restored: {snapshot_id}",
                "latest proposal pointer moved to a previous snapshot",
                f"governance reviews restored: {len(restored.get('proposal_reviews', []))}",
            ],
        )
        return restored

    def rollback_policies(self, snapshot_id: str) -> dict:
        restored = self.policy_store.restore_snapshot(snapshot_id)
        report_date = datetime.now(UTC).date().isoformat()
        self.event_log.append(
            "policy_rollback",
            {
                "snapshot_id": snapshot_id,
                "restored_from_snapshot_id": restored.get("restored_from_snapshot_id"),
                "rule_count": len(restored.get("rules", [])),
            },
        )
        self.report_writer.write_glance(
            status="policy_rollback_applied",
            main_points=[
                f"restored policy snapshot: {snapshot_id}",
                f"rule count after rollback: {len(restored.get('rules', []))}",
            ],
            recommended_interventions=[
                "compare restored policy state against the next approved proposal",
                "preserve newer policy snapshots for audit",
            ],
            track_summary={"restoredPolicySnapshotId": snapshot_id, "policyRules": len(restored.get("rules", []))},
            approval_pending=[],
            date=report_date,
        )
        self.report_writer.write_daily(
            status="policy_rollback_applied",
            summary=f"policy snapshot restored: {snapshot_id}",
            sections=[{"title": "Policy Rollback", "points": [f"restored snapshot: {snapshot_id}"]}],
            proposals=[],
            date=report_date,
        )
        self.report_writer.write_health(
            status="policy_rollback_applied",
            approval_pending=[],
            notes=[f"policy snapshot restored: {snapshot_id}", "policy latest pointer moved to restored snapshot"],
        )
        return restored


def build_loop(root: Path, worker_service: WorkerService) -> GrowthLoop:
    return GrowthLoop(
        root=root,
        worker_service=worker_service,
        report_writer=ReportWriter(root),
        policy_engine=PolicyEngine(),
        critic=Critic(),
        cloud_evaluation_store=CloudEvaluationStore(root / "state" / "cloud_evaluation"),
        evaluator=Evaluator(),
        governor=Governor(),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the minimal P1 growth loop on a text input")
    parser.add_argument("--root", default="/tmp/p1-core-smoke")
    parser.add_argument("--input-text")
    parser.add_argument("--rollback-snapshot-id")
    parser.add_argument("--rollback-policy-snapshot-id")
    parser.add_argument("--date", default=None)
    return parser.parse_args()


def main() -> None:
    from p1_core.worker.ollama_client import OllamaClient

    args = parse_args()
    root = Path(args.root).expanduser()
    worker = WorkerService(
        llm_client=OllamaClient(model="qwen2.5:7b"),
        log_dir=root / "logs" / "worker",
    )
    loop = build_loop(root, worker)
    if args.rollback_snapshot_id:
        result = loop.rollback_proposals(args.rollback_snapshot_id)
    elif args.rollback_policy_snapshot_id:
        result = loop.rollback_policies(args.rollback_policy_snapshot_id)
    else:
        if not args.input_text:
            raise SystemExit("--input-text is required unless --rollback-snapshot-id or --rollback-policy-snapshot-id is provided")
        result = loop.ingest_text(args.input_text, date=args.date)
    print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
