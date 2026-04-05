from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from p1_core.core.action_runtime import ActionExecutor, ActionPolicy, ActionSpec, ActionStore, OpenClawActionBackend
from p1_core.core.background_job_store import BackgroundJobStore
from p1_core.core.chat_agent import ChatAgent
from p1_core.core.capability_store import CapabilityStore
from p1_core.core.conversation_store import ConversationStore
from p1_core.core.governance_store import GovernanceStore
from p1_core.core.llm_runtime import LLMRouter, LLMUsageStore, TextLLMBackend
from p1_core.core.policy_engine import PolicyEngine
from p1_core.core.world_store import WorldStore


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(moment: datetime | None = None) -> str:
    return (moment or _now()).isoformat()


def _read_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return fallback
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass(slots=True)
class InboxStore:
    root: Path

    def __post_init__(self) -> None:
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        self.processed_dir.mkdir(parents=True, exist_ok=True)

    @property
    def queue_dir(self) -> Path:
        return self.root / "queue"

    @property
    def processed_dir(self) -> Path:
        return self.root / "processed"

    def enqueue(self, content: str, *, source: str = "user") -> dict[str, Any]:
        payload = {
            "message_id": f"message:{uuid.uuid4()}",
            "content": content,
            "source": source,
            "status": "queued",
            "created_at": _iso(),
        }
        path = self.queue_dir / f"{payload['message_id'].replace(':', '-')}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return payload

    def next_message(self) -> dict[str, Any] | None:
        files = sorted(self.queue_dir.glob("*.json"))
        if not files:
            return None
        return json.loads(files[0].read_text(encoding="utf-8"))

    def complete(self, message_id: str, *, reply: str, backend: str) -> dict[str, Any]:
        source = self.queue_dir / f"{message_id.replace(':', '-')}.json"
        payload = json.loads(source.read_text(encoding="utf-8"))
        payload["status"] = "processed"
        payload["processed_at"] = _iso()
        payload["reply"] = reply
        payload["reply_backend"] = backend
        target = self.processed_dir / source.name
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        source.unlink()
        return payload

    def defer(self, message_id: str, *, reason: str) -> dict[str, Any]:
        source = self.queue_dir / f"{message_id.replace(':', '-')}.json"
        payload = json.loads(source.read_text(encoding="utf-8"))
        payload["status"] = "deferred"
        payload["deferred_at"] = _iso()
        payload["defer_reason"] = reason
        target = self.processed_dir / source.name
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        source.unlink()
        return payload

    def counts(self) -> dict[str, int]:
        return {
            "queued": len(list(self.queue_dir.glob("*.json"))),
            "processed": len(list(self.processed_dir.glob("*.json"))),
        }


@dataclass(slots=True)
class AutonomyRuntime:
    root: Path
    local_llm_backend: TextLLMBackend
    openclaw_llm_backend: TextLLMBackend | None = None
    openclaw_action_backend: OpenClawActionBackend | None = None
    action_store: ActionStore = field(init=False)
    background_jobs: BackgroundJobStore = field(init=False)
    world_store: WorldStore = field(init=False)
    conversation_store: ConversationStore = field(init=False)
    governance_store: GovernanceStore = field(init=False)
    capability_store: CapabilityStore = field(init=False)
    inbox: InboxStore = field(init=False)
    usage_store: LLMUsageStore = field(init=False)
    executor: ActionExecutor = field(init=False)
    policy_engine: PolicyEngine = field(init=False)

    def __post_init__(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.action_store = ActionStore(self.root / "state" / "actions")
        self.background_jobs = BackgroundJobStore(self.root / "state" / "background_jobs")
        self.world_store = WorldStore(self.root / "state" / "world")
        self.conversation_store = ConversationStore(self.root / "state" / "conversation")
        self.governance_store = GovernanceStore(self.root / "state" / "governance")
        self.capability_store = CapabilityStore(self.root / "state" / "capabilities")
        self.inbox = InboxStore(self.state_dir / "inbox")
        self.usage_store = LLMUsageStore(self.root / "state" / "budgets")
        self.policy_engine = PolicyEngine()
        self.executor = ActionExecutor(
            root=self.root,
            background_job_store=self.background_jobs,
            world_store=self.world_store,
            openclaw_backend=self.openclaw_action_backend,
        )

    @property
    def state_dir(self) -> Path:
        return self.root / "state" / "autonomy"

    @property
    def runtime_state_path(self) -> Path:
        return self.state_dir / "runtime-state.json"

    @property
    def ticks_path(self) -> Path:
        return self.state_dir / "ticks.jsonl"

    @property
    def lease_path(self) -> Path:
        return self.state_dir / "leases" / "current-lease.json"

    def default_state(self) -> dict[str, Any]:
        return {
            "mode": "cooperative_tick",
            "current_focus": None,
            "last_tick_at": None,
            "next_wake_at": None,
            "last_tick_summary": "runtime not started",
            "cooldowns": {"openclaw_until": None},
            "budget_policy": {
                "local_first": True,
                "per_tick_openclaw_cap": 1,
                "openclaw_3h_soft_cap": 20,
                "openclaw_daily_soft_cap": 40,
                "default_wake_seconds": 300,
                "idle_wake_seconds": 900,
                "lease_seconds": 120,
            },
        }

    def load_state(self) -> dict[str, Any]:
        return _read_json(self.runtime_state_path, self.default_state())

    def save_state(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.runtime_state_path.parent.mkdir(parents=True, exist_ok=True)
        self.runtime_state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return payload

    def enqueue_message(self, content: str, *, source: str = "user") -> dict[str, Any]:
        return self.inbox.enqueue(content, source=source)

    def show_state(self) -> dict[str, Any]:
        state = self.load_state()
        return {
            **state,
            "inboxCounts": self.inbox.counts(),
            "actionCounts": self.action_store.counts(),
            "backgroundJobCounts": self.background_jobs.counts(),
            "capabilityGapCounts": self.capability_store.counts(),
            "recentCapabilityGaps": self.capability_store.list_gaps(limit=5),
            "capabilityProposalCounts": self.capability_store.proposal_counts(),
            "recentCapabilityProposals": self.capability_store.list_proposals(limit=5),
            "llmUsage": self.usage_store.counts(),
        }

    def tick_once(self, *, now: datetime | None = None) -> dict[str, Any]:
        moment = now or _now()
        state = self.load_state()
        lease = self._acquire_lease(state, moment)
        if not lease["acquired"]:
            result = {
                "status": "lease_held",
                "summary": "another autonomy tick currently holds the lease",
                "next_wake_at": state.get("next_wake_at"),
            }
            self._record_tick(result)
            return result

        try:
            queued_message = self.inbox.next_message()
            if self._should_sleep(state, moment, has_inbox=queued_message is not None):
                result = {
                    "status": "sleeping",
                    "summary": "no pending work and next wake has not arrived",
                    "next_wake_at": state.get("next_wake_at"),
                }
                self._record_tick(result)
                return result

            if queued_message:
                result = self._process_message(queued_message, state, moment)
            else:
                result = self._process_internal_work(state, moment)

            self._record_tick(result)
            return result
        finally:
            self._release_lease()

    def _process_message(self, message: dict[str, Any], state: dict[str, Any], moment: datetime) -> dict[str, Any]:
        policy = state.get("budget_policy", {})
        router = LLMRouter(
            local_backend=self.local_llm_backend,
            openclaw_backend=self.openclaw_llm_backend,
            usage_store=self.usage_store,
            openclaw_3h_soft_cap=int(policy.get("openclaw_3h_soft_cap", 20)),
            openclaw_daily_soft_cap=int(policy.get("openclaw_daily_soft_cap", 40)),
            per_tick_openclaw_cap=int(policy.get("per_tick_openclaw_cap", 1)),
            local_first=bool(policy.get("local_first", True)),
            local_failure_allows_openclaw=False,
        )
        governance = self.governance_store.latest()
        world = self.world_store.latest()
        recent = self.conversation_store.recent(limit=8)
        system_prompt = (
            "You are P1, an autonomous growth agent. "
            "Respond briefly, preserve auditability, and avoid expensive reasoning unless needed."
        )
        user_prompt = (
            f"Governance: {governance}\n"
            f"World state: {world}\n"
            f"Recent conversation: {recent}\n"
            f"Inbox message: {message['content']}\n"
            "Reply in plain text."
        )
        try:
            routed = router.route_text(system_prompt, user_prompt, allow_openclaw=False)
        except Exception as exc:
            self.capability_store.record_gap(
                title="conversation backend unavailable",
                detail=f"local reply generation failed without OpenClaw fallback: {exc}",
                source="autonomy.message",
                severity="medium",
                metadata={"message_id": message["message_id"]},
            )
            self.inbox.defer(message["message_id"], reason=str(exc))
            next_wake = moment + timedelta(seconds=int(policy.get("default_wake_seconds", 300)))
            self.save_state(
                {
                    **state,
                    "current_focus": "conversation_retry",
                    "last_tick_at": _iso(moment),
                    "next_wake_at": _iso(next_wake),
                    "last_tick_summary": f"local reply generation failed: {exc}",
                }
            )
            return {
                "status": "conversation_deferred",
                "summary": "local reply generation failed; keeping conservative no-openclaw path",
                "error": str(exc),
                "message_id": message["message_id"],
                "next_wake_at": _iso(next_wake),
            }
        self.conversation_store.append("user", message["content"], metadata={"source": message["source"], "message_id": message["message_id"]})
        self.conversation_store.append(
            "assistant",
            routed["text"],
            metadata={"reply_backend": routed["backend"], "message_id": message["message_id"], "governance_snapshot_id": governance.get("snapshot_id")},
        )
        self.inbox.complete(message["message_id"], reply=routed["text"], backend=routed["backend"])

        next_wake = moment + timedelta(seconds=int(policy.get("default_wake_seconds", 300)))
        next_state = {
            **state,
            "current_focus": "conversation_followup" if self.inbox.counts()["queued"] > 0 else None,
            "last_tick_at": _iso(moment),
            "next_wake_at": _iso(next_wake),
            "last_tick_summary": f"replied via {routed['backend']}",
        }
        self.save_state(next_state)
        return {
            "status": "replied",
            "reply": routed["text"],
            "reply_backend": routed["backend"],
            "message_id": message["message_id"],
            "next_wake_at": _iso(next_wake),
        }

    def _process_internal_work(self, state: dict[str, Any], moment: datetime) -> dict[str, Any]:
        queued_action = self.action_store.next_queued()
        if queued_action:
            spec = ActionSpec(
                action_id=queued_action["action_id"],
                kind=queued_action["kind"],
                inputs=dict(queued_action["inputs"]),
                risk_level=str(queued_action.get("risk_level", "low")),
                backend=str(queued_action.get("backend", "local")),
                goal=queued_action.get("goal"),
                idempotency_key=queued_action.get("idempotency_key"),
                status=str(queued_action.get("status", "queued")),
                requested_at=str(queued_action.get("requested_at", _iso(moment))),
            )
            decision, reason = ActionPolicy(self.governance_store.latest()).decide(spec)
            if decision == "approval_required":
                queued = self.action_store.mark_approval_required(spec.action_id, reason)
                next_wake = moment + timedelta(seconds=300)
                self.save_state(
                    {
                        **state,
                        "current_focus": "approval_pending",
                        "last_tick_at": _iso(moment),
                        "next_wake_at": _iso(next_wake),
                        "last_tick_summary": reason,
                    }
                )
                return {"status": "approval_required", "action": queued, "next_wake_at": _iso(next_wake)}
            if decision == "reject":
                rejected = self.action_store.mark_failed(spec.action_id, reason)
                self.capability_store.record_gap(
                    title="unsupported autonomous action",
                    detail=reason,
                    source="autonomy.action",
                    severity="medium",
                    metadata={"action_id": spec.action_id, "kind": spec.kind, "backend": spec.backend},
                )
                next_wake = moment + timedelta(seconds=300)
                self.save_state(
                    {
                        **state,
                        "current_focus": "rejected_action",
                        "last_tick_at": _iso(moment),
                        "next_wake_at": _iso(next_wake),
                        "last_tick_summary": reason,
                    }
                )
                return {"status": "rejected", "action": rejected, "next_wake_at": _iso(next_wake)}
            if decision == "defer":
                next_wake = moment + timedelta(seconds=300)
                self.save_state(
                    {
                        **state,
                        "current_focus": "deferred_action",
                        "last_tick_at": _iso(moment),
                        "next_wake_at": _iso(next_wake),
                        "last_tick_summary": reason,
                    }
                )
                return {"status": "deferred", "summary": reason, "next_wake_at": _iso(next_wake)}

            result = self.executor.execute(spec)
            if result.status == "completed":
                stored = self.action_store.mark_completed(spec.action_id, result)
            else:
                stored = self.action_store.mark_failed(spec.action_id, result.stderr or "action failed")
                if "not configured" in (result.stderr or "") or "unsupported" in (result.stderr or ""):
                    self.capability_store.record_gap(
                        title="missing backend capability",
                        detail=result.stderr or "action backend capability is missing",
                        source="autonomy.action",
                        severity="high" if spec.backend == "openclaw" else "medium",
                        metadata={"action_id": spec.action_id, "kind": spec.kind, "backend": spec.backend},
                    )
            next_wake = moment + timedelta(seconds=300)
            self.save_state(
                {
                    **state,
                    "current_focus": None,
                    "last_tick_at": _iso(moment),
                    "next_wake_at": _iso(next_wake),
                    "last_tick_summary": f"{spec.kind} {result.status}",
                }
            )
            return {"status": "action_executed", "action": stored, "next_wake_at": _iso(next_wake)}

        if self.background_jobs.counts()["queued"] > 0:
            next_wake = moment + timedelta(seconds=300)
            self.save_state(
                {
                    **state,
                    "current_focus": "background_jobs_pending",
                    "last_tick_at": _iso(moment),
                    "next_wake_at": _iso(next_wake),
                    "last_tick_summary": "background jobs are pending explicit processing",
                }
            )
            return {
                "status": "background_jobs_pending",
                "summary": "background jobs remain queued for conservative processing",
                "next_wake_at": _iso(next_wake),
            }

        pending_gap = self._next_unproposed_gap()
        if pending_gap:
            proposal = self._proposal_from_gap(pending_gap)
            next_wake = moment + timedelta(seconds=300)
            self.save_state(
                {
                    **state,
                    "current_focus": "capability_extension_planning",
                    "last_tick_at": _iso(moment),
                    "next_wake_at": _iso(next_wake),
                    "last_tick_summary": f"capability proposal recorded for {pending_gap['gap_id']}",
                }
            )
            return {
                "status": "capability_proposal_recorded",
                "proposal": proposal,
                "next_wake_at": _iso(next_wake),
            }

        idle_seconds = int(state.get("budget_policy", {}).get("idle_wake_seconds", 900))
        next_wake = moment + timedelta(seconds=idle_seconds)
        self.save_state(
            {
                **state,
                "current_focus": None,
                "last_tick_at": _iso(moment),
                "next_wake_at": _iso(next_wake),
                "last_tick_summary": "idle tick completed without LLM usage",
            }
        )
        return {"status": "idle", "summary": "no inbox or actions pending", "next_wake_at": _iso(next_wake)}

    def _next_unproposed_gap(self) -> dict[str, Any] | None:
        for gap in reversed(self.capability_store.list_gaps(limit=100)):
            if not self.capability_store.has_proposal_for_gap(str(gap.get("gap_id"))):
                return gap
        return None

    def _proposal_from_gap(self, gap: dict[str, Any]) -> dict[str, Any]:
        summary = (
            f"Implement missing capability: {gap.get('title')} "
            f"from {gap.get('source')} ({gap.get('severity')} severity)"
        )
        policy = self.policy_engine.classify(summary)
        return self.capability_store.record_proposal(
            gap_id=str(gap["gap_id"]),
            summary=policy.summary,
            proposal_type="capability_extension",
            risk_level=policy.risk_level,
            requires_approval=policy.requires_approval,
            detail=str(gap.get("detail", "")),
            metadata={
                "source": gap.get("source"),
                "severity": gap.get("severity"),
                "gap_metadata": gap.get("metadata", {}),
            },
        )

    def _should_sleep(self, state: dict[str, Any], moment: datetime, *, has_inbox: bool) -> bool:
        if has_inbox:
            return False
        next_wake_at = state.get("next_wake_at")
        if not next_wake_at:
            return False
        try:
            wake = datetime.fromisoformat(next_wake_at)
        except ValueError:
            return False
        return moment < wake

    def _acquire_lease(self, state: dict[str, Any], moment: datetime) -> dict[str, Any]:
        self.lease_path.parent.mkdir(parents=True, exist_ok=True)
        lease_seconds = int(state.get("budget_policy", {}).get("lease_seconds", 120))
        if self.lease_path.exists():
            current = json.loads(self.lease_path.read_text(encoding="utf-8"))
            expires_at = current.get("expires_at")
            if expires_at:
                try:
                    expires = datetime.fromisoformat(expires_at)
                except ValueError:
                    expires = None
                if expires and expires > moment:
                    return {"acquired": False, "lease": current}
        lease = {
            "lease_id": f"lease:{uuid.uuid4()}",
            "acquired_at": _iso(moment),
            "expires_at": _iso(moment + timedelta(seconds=lease_seconds)),
        }
        self.lease_path.write_text(json.dumps(lease, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {"acquired": True, "lease": lease}

    def _release_lease(self) -> None:
        if self.lease_path.exists():
            self.lease_path.unlink()

    def _record_tick(self, payload: dict[str, Any]) -> None:
        self.ticks_path.parent.mkdir(parents=True, exist_ok=True)
        with self.ticks_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"timestamp": _iso(), **payload}, ensure_ascii=False) + "\n")
