from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from p1_core.core.chat_agent import ChatAgent
from p1_core.core.background_job_store import BackgroundJobStore
from p1_core.core.conversation_store import ConversationStore
from p1_core.core.governance_store import GovernanceStore
from p1_core.core.knowledge_store import KnowledgeStore
from p1_core.core.policy_store import PolicyStore
from p1_core.core.proposal_store import ProposalStore
from p1_core.core.world_store import WorldStore
from p1_core.pipeline.growth_loop import build_loop
from p1_core.worker.ollama_client import OllamaClient
from p1_core.worker.service import WorkerService


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _latest_daily_path(root: Path, kind: str, date: str | None = None) -> Path:
    reports_dir = root / "state" / "reports" / "daily"
    if date:
        return reports_dir / f"{date}-{kind}.json"
    candidates = sorted(reports_dir.glob(f"*-{kind}.json"))
    if not candidates:
        raise FileNotFoundError(f"no {kind} reports found under {reports_dir}")
    return candidates[-1]


def operator_status(root: Path, *, date: str | None = None) -> dict[str, Any]:
    glance = _read_json(_latest_daily_path(root, "glance", date=date))
    health = _read_json(root / "state" / "health.json")
    policy = PolicyStore(root / "state" / "policies").latest()
    governance = GovernanceStore(root / "state" / "governance").latest()
    return {
        "status": glance.get("status"),
        "mainPoints": glance.get("mainPoints", []),
        "approvalPending": glance.get("tuningSummary", {}).get("approvalPending", []),
        "policySnapshotId": policy.get("snapshot_id"),
        "policyRuleCount": len(policy.get("rules", [])),
        "governanceSnapshotId": governance.get("snapshot_id"),
        "health": health,
    }


def operator_approvals(root: Path, *, date: str | None = None) -> dict[str, Any]:
    glance = _read_json(_latest_daily_path(root, "glance", date=date))
    return {
        "approvalPending": glance.get("tuningSummary", {}).get("approvalPending", []),
        "status": glance.get("status"),
    }


def operator_report(root: Path, *, kind: str = "daily", date: str | None = None) -> dict[str, Any]:
    if kind == "health":
        return _read_json(root / "state" / "health.json")
    return _read_json(_latest_daily_path(root, kind, date=date))


def operator_state(root: Path) -> dict[str, Any]:
    knowledge_store = KnowledgeStore(root / "state" / "knowledge" / "knowledge.jsonl")
    proposal_store = ProposalStore(root / "state" / "proposals")
    policy_store = PolicyStore(root / "state" / "policies")
    governance_store = GovernanceStore(root / "state" / "governance")
    background_jobs = BackgroundJobStore(root / "state" / "background_jobs")
    latest_proposals = proposal_store.latest() or {}
    return {
        "knowledgeStateCounts": knowledge_store.counts_by_state(),
        "latestKnowledgeRecords": list(knowledge_store.latest_by_id().values()),
        "latestProposalSnapshotId": latest_proposals.get("snapshot_id"),
        "latestProposalCount": len(latest_proposals.get("proposals", [])),
        "latestPolicySnapshotId": policy_store.latest().get("snapshot_id"),
        "latestPolicyRuleCount": len(policy_store.latest().get("rules", [])),
        "latestGovernanceSnapshotId": governance_store.latest().get("snapshot_id"),
        "governanceProfile": governance_store.latest(),
        "backgroundJobCounts": background_jobs.counts(),
        "queuedBackgroundJobs": background_jobs.list_queued(),
        "worldState": WorldStore(root / "state" / "world").latest(),
        "recentConversation": ConversationStore(root / "state" / "conversation").recent(),
    }


def operator_ingest(
    root: Path,
    *,
    input_text: str,
    model: str,
    background_model: str | None = None,
) -> dict[str, Any]:
    worker = WorkerService(
        llm_client=OllamaClient(model=model),
        log_dir=root / "logs" / "worker",
    )
    loop = build_loop(root, worker)
    if background_model:
        return loop.ingest_fast_and_queue_background(input_text, background_model=background_model)
    return loop.ingest_text(input_text)


def operator_run_background_job(root: Path, *, job_id: str, model: str) -> dict[str, Any]:
    fast_worker = WorkerService(
        llm_client=OllamaClient(model="qwen3:4b-instruct"),
        log_dir=root / "logs" / "worker",
    )
    background_worker = WorkerService(
        llm_client=OllamaClient(model=model),
        log_dir=root / "logs" / "worker",
    )
    loop = build_loop(root, fast_worker)
    return loop.process_background_job(job_id=job_id, background_worker_service=background_worker)


def operator_rollback(root: Path, *, target: str, snapshot_id: str) -> dict[str, Any]:
    worker = WorkerService(
        llm_client=OllamaClient(model="qwen3:4b-instruct"),
        log_dir=root / "logs" / "worker",
    )
    loop = build_loop(root, worker)
    if target == "proposals":
        return loop.rollback_proposals(snapshot_id)
    if target == "policies":
        return loop.rollback_policies(snapshot_id)
    raise ValueError(f"unsupported rollback target: {target}")


def operator_observe(root: Path, *, text: str, source: str = "operator") -> dict[str, Any]:
    return WorldStore(root / "state" / "world").observe(text, source=source)


def operator_action(root: Path, *, kind: str, payload: str, source: str = "operator") -> dict[str, Any]:
    return WorldStore(root / "state" / "world").request_action(kind, payload, source=source)


def operator_chat(root: Path, *, message: str, model: str) -> dict[str, Any]:
    agent = ChatAgent(
        llm_client=OllamaClient(model=model),
        conversation_store=ConversationStore(root / "state" / "conversation"),
        governance_store=GovernanceStore(root / "state" / "governance"),
        world_store=WorldStore(root / "state" / "world"),
    )
    return agent.reply(message)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Unified operator CLI for the P1 external core")
    parser.add_argument("--root", default="/Users/satojunichi/.openclaw/workspace/systems/p1")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    ingest_parser = subparsers.add_parser("ingest", help="Run the growth loop on an input text")
    ingest_parser.add_argument("--input-text", required=True)
    ingest_parser.add_argument("--model", default="qwen3:4b-instruct")
    ingest_parser.add_argument("--background-model")

    background_parser = subparsers.add_parser("run-background-job", help="Process a queued background analysis job")
    background_parser.add_argument("--job-id", required=True)
    background_parser.add_argument("--model", default="gemma4:e4b")

    status_parser = subparsers.add_parser("status", help="Show operator-facing P1 status")
    status_parser.add_argument("--date")

    approvals_parser = subparsers.add_parser("approvals", help="Show approval-pending items")
    approvals_parser.add_argument("--date")

    report_parser = subparsers.add_parser("report", help="Read a P1 report")
    report_parser.add_argument("--date")
    report_parser.add_argument("--kind", choices=["glance", "daily", "health"], default="daily")

    subparsers.add_parser("state", help="Inspect the latest external-core state")

    observe_parser = subparsers.add_parser("observe", help="Record an external observation for P1")
    observe_parser.add_argument("--text", required=True)
    observe_parser.add_argument("--source", default="operator")

    action_parser = subparsers.add_parser("action", help="Queue a bounded external action request")
    action_parser.add_argument("--kind", required=True)
    action_parser.add_argument("--payload", required=True)
    action_parser.add_argument("--source", default="operator")

    chat_parser = subparsers.add_parser("chat", help="Talk with P1 through the external core")
    chat_parser.add_argument("--message", required=True)
    chat_parser.add_argument("--model", default="qwen3:4b-instruct")

    rollback_parser = subparsers.add_parser("rollback", help="Rollback proposal or policy state")
    rollback_parser.add_argument("--target", choices=["proposals", "policies"], required=True)
    rollback_parser.add_argument("--snapshot-id", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = Path(args.root).expanduser()

    if args.subcommand == "ingest":
        payload = operator_ingest(root, input_text=args.input_text, model=args.model, background_model=args.background_model)
    elif args.subcommand == "run-background-job":
        payload = operator_run_background_job(root, job_id=args.job_id, model=args.model)
    elif args.subcommand == "status":
        payload = operator_status(root, date=args.date)
    elif args.subcommand == "approvals":
        payload = operator_approvals(root, date=args.date)
    elif args.subcommand == "report":
        payload = operator_report(root, kind=args.kind, date=args.date)
    elif args.subcommand == "state":
        payload = operator_state(root)
    elif args.subcommand == "observe":
        payload = operator_observe(root, text=args.text, source=args.source)
    elif args.subcommand == "action":
        payload = operator_action(root, kind=args.kind, payload=args.payload, source=args.source)
    elif args.subcommand == "chat":
        payload = operator_chat(root, message=args.message, model=args.model)
    else:
        payload = operator_rollback(root, target=args.target, snapshot_id=args.snapshot_id)

    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
