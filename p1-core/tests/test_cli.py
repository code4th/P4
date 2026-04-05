from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from p1_core.cli import operator_action, operator_approvals, operator_enqueue_message, operator_ingest, operator_observe, operator_queue_action, operator_report, operator_run_background_job, operator_show_autonomy_state, operator_show_capability_gaps, operator_show_capability_tasks, operator_state, operator_status, operator_tick
from p1_core.core.chat_agent import ChatAgent
from p1_core.core.conversation_store import ConversationStore
from p1_core.core.governance_store import GovernanceStore
from p1_core.core.policy_store import PolicyStore
from p1_core.core.world_store import WorldStore


class OperatorCliTests(unittest.TestCase):
    def test_operator_state_includes_background_job_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = operator_state(root)
            self.assertEqual(payload["backgroundJobCounts"]["queued"], 0)
            self.assertEqual(payload["queuedBackgroundJobs"], [])

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

    def test_operator_observe_and_action_write_world_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            observation = operator_observe(root, text="tool execution failed during search")
            action = operator_action(root, kind="note", payload="queue bounded follow-up")
            payload = operator_state(root)
            self.assertEqual(observation["source"], "operator")
            self.assertEqual(action["status"], "queued")
            self.assertEqual(len(payload["worldState"]["observations"]), 1)
            self.assertEqual(len(payload["worldState"]["actionRequests"]), 1)

    def test_chat_agent_records_conversation(self) -> None:
        class FakeTextClient:
            def generate_text(self, system_prompt: str, user_prompt: str) -> str:
                return "P1 response"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent = ChatAgent(
                llm_client=FakeTextClient(),
                conversation_store=ConversationStore(root / "state" / "conversation"),
                governance_store=GovernanceStore(root / "state" / "governance"),
                world_store=WorldStore(root / "state" / "world"),
            )
            payload = agent.reply("hello")
            state = operator_state(root)
            self.assertEqual(payload["reply"], "P1 response")
            self.assertEqual(len(state["recentConversation"]), 2)

    def test_operator_enqueue_message_and_tick_process_conversation(self) -> None:
        class FakeClient:
            def __init__(self, model: str, base_url: str = "http://127.0.0.1:11434", timeout_seconds: float = 60.0) -> None:
                self.model = model

            def generate_text(self, system_prompt: str, user_prompt: str) -> str:
                return "local autonomy reply"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("p1_core.cli.OllamaClient", FakeClient):
                queued = operator_enqueue_message(root, content="hello from inbox")
                tick = operator_tick(root)
                autonomy_state = operator_show_autonomy_state(root)
            self.assertEqual(queued["status"], "queued")
            self.assertEqual(tick["status"], "replied")
            self.assertEqual(tick["reply_backend"], "local")
            self.assertEqual(autonomy_state["inboxCounts"]["queued"], 0)

    def test_operator_queue_action_and_tick_execute_it(self) -> None:
        class FakeClient:
            def __init__(self, model: str, base_url: str = "http://127.0.0.1:11434", timeout_seconds: float = 60.0) -> None:
                self.model = model

            def generate_text(self, system_prompt: str, user_prompt: str) -> str:
                return "unused"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("p1_core.cli.OllamaClient", FakeClient):
                queued = operator_queue_action(root, kind="append_note", inputs={"content": "queued from cli"})
                tick = operator_tick(root)
                autonomy_state = operator_show_autonomy_state(root)
            self.assertEqual(queued["status"], "queued")
            self.assertEqual(tick["status"], "action_executed")
            self.assertEqual(autonomy_state["actionCounts"]["completed"], 1)

    def test_operator_show_capability_gaps(self) -> None:
        class FailingClient:
            def __init__(self, model: str, base_url: str = "http://127.0.0.1:11434", timeout_seconds: float = 60.0) -> None:
                self.model = model

            def generate_text(self, system_prompt: str, user_prompt: str) -> str:
                raise RuntimeError("local backend unavailable")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("p1_core.cli.OllamaClient", FailingClient):
                operator_enqueue_message(root, content="hello from inbox")
                operator_tick(root)
                payload = operator_show_capability_gaps(root)
            self.assertEqual(payload["counts"]["total"], 1)
            self.assertEqual(payload["gaps"][0]["source"], "autonomy.message")
            self.assertEqual(payload["proposalCounts"]["total"], 0)

    def test_capability_gap_turns_into_proposal_on_next_tick(self) -> None:
        class FailingClient:
            def __init__(self, model: str, base_url: str = "http://127.0.0.1:11434", timeout_seconds: float = 60.0) -> None:
                self.model = model

            def generate_text(self, system_prompt: str, user_prompt: str) -> str:
                raise RuntimeError("local backend unavailable")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("p1_core.cli.OllamaClient", FailingClient):
                operator_enqueue_message(root, content="hello from inbox")
                operator_tick(root)
                runtime_state_path = root / "state" / "autonomy" / "runtime-state.json"
                runtime_state = json.loads(runtime_state_path.read_text(encoding="utf-8"))
                runtime_state["next_wake_at"] = None
                runtime_state_path.write_text(json.dumps(runtime_state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                operator_tick(root)
                payload = operator_show_capability_gaps(root)
            self.assertEqual(payload["proposalCounts"]["total"], 1)
            self.assertEqual(payload["proposals"][0]["proposal_type"], "capability_extension")
            self.assertEqual(payload["reviewCounts"]["total"], 0)

    def test_approved_capability_review_turns_into_execution(self) -> None:
        class FakeClient:
            def __init__(self, model: str, base_url: str = "http://127.0.0.1:11434", timeout_seconds: float = 60.0) -> None:
                self.model = model

            def generate_text(self, system_prompt: str, user_prompt: str) -> str:
                return "local autonomy reply"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("p1_core.cli.OllamaClient", FakeClient):
                runtime_state_path = root / "state" / "autonomy" / "runtime-state.json"
                operator_tick(root)
                runtime_state = json.loads(runtime_state_path.read_text(encoding="utf-8"))
                runtime_state["next_wake_at"] = None
                runtime_state_path.write_text(json.dumps(runtime_state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                from p1_core.autonomy import AutonomyRuntime
                runtime = AutonomyRuntime(root=root, local_llm_backend=FakeClient("qwen3:4b-instruct"))
                proposal = runtime.capability_store.record_proposal(
                    gap_id="capgap:test",
                    summary="Implement missing capability: low severity task",
                    proposal_type="capability_extension",
                    risk_level="low",
                    requires_approval=False,
                    detail="safe low-risk task",
                )
                runtime.capability_store.record_review(
                    proposal_id=proposal["proposal_id"],
                    gap_id=proposal["gap_id"],
                    evaluation={"decision": "candidate"},
                    governance={"proposal": proposal, "next_step": "autonomous_apply"},
                )
                payload = operator_tick(root)
                view = operator_show_capability_gaps(root)
            self.assertEqual(payload["status"], "capability_execution_queued")
            self.assertEqual(view["executionCounts"]["queued_action"], 1)

    def test_show_capability_tasks_lists_planned_tasks(self) -> None:
        class FakeClient:
            def __init__(self, model: str, base_url: str = "http://127.0.0.1:11434", timeout_seconds: float = 60.0) -> None:
                self.model = model

            def generate_text(self, system_prompt: str, user_prompt: str) -> str:
                return "local autonomy reply"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("p1_core.cli.OllamaClient", FakeClient):
                runtime = operator_show_autonomy_state(root)
                from p1_core.autonomy import AutonomyRuntime
                aruntime = AutonomyRuntime(root=root, local_llm_backend=FakeClient("qwen3:4b-instruct"))
                proposal = aruntime.capability_store.record_proposal(
                    gap_id="capgap:test",
                    summary="Implement missing capability: low severity task",
                    proposal_type="capability_extension",
                    risk_level="low",
                    requires_approval=False,
                    detail="safe low-risk task",
                )
                aruntime.capability_store.record_review(
                    proposal_id=proposal["proposal_id"],
                    gap_id=proposal["gap_id"],
                    evaluation={"decision": "candidate"},
                    governance={"proposal": proposal, "next_step": "autonomous_apply"},
                )
                operator_tick(root)
                operator_tick(root)
                tasks = operator_show_capability_tasks(root)
            self.assertEqual(tasks["taskCounts"]["pending"], 1)
            self.assertEqual(tasks["tasks"][0]["status"], "pending")

    def test_capability_execution_completes_and_is_auditable(self) -> None:
        class FakeClient:
            def __init__(self, model: str, base_url: str = "http://127.0.0.1:11434", timeout_seconds: float = 60.0) -> None:
                self.model = model

            def generate_text(self, system_prompt: str, user_prompt: str) -> str:
                return "local autonomy reply"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("p1_core.cli.OllamaClient", FakeClient):
                operator_tick(root)
                runtime_state_path = root / "state" / "autonomy" / "runtime-state.json"
                runtime_state = json.loads(runtime_state_path.read_text(encoding="utf-8"))
                runtime_state["next_wake_at"] = None
                runtime_state_path.write_text(json.dumps(runtime_state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                from p1_core.autonomy import AutonomyRuntime
                runtime = AutonomyRuntime(root=root, local_llm_backend=FakeClient("qwen3:4b-instruct"))
                proposal = runtime.capability_store.record_proposal(
                    gap_id="capgap:test",
                    summary="Implement missing capability: low severity task",
                    proposal_type="capability_extension",
                    risk_level="low",
                    requires_approval=False,
                    detail="safe low-risk task",
                )
                runtime.capability_store.record_review(
                    proposal_id=proposal["proposal_id"],
                    gap_id=proposal["gap_id"],
                    evaluation={"decision": "candidate"},
                    governance={"proposal": proposal, "next_step": "autonomous_apply"},
                )
                operator_tick(root)
                runtime_state = json.loads(runtime_state_path.read_text(encoding="utf-8"))
                runtime_state["next_wake_at"] = None
                runtime_state_path.write_text(json.dumps(runtime_state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                operator_tick(root)
                view = operator_show_capability_gaps(root)
            self.assertEqual(view["executionCounts"]["completed"], 1)
            self.assertEqual(view["executions"][0]["status"], "completed")

    def test_operator_ingest_can_queue_background_analysis(self) -> None:
        class FakeClient:
            def __init__(self, model: str, base_url: str = "http://127.0.0.1:11434", timeout_seconds: float = 60.0) -> None:
                self.model = model

            def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
                if '"task": "classify"' in user_prompt:
                    return {"label": "proposal", "confidence": 0.7, "rationale": "triage"}
                return {"summary": "fast summary", "keywords": ["fast", "summary"]}

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("p1_core.cli.OllamaClient", FakeClient):
                payload = operator_ingest(
                    root,
                    input_text="background me",
                    model="qwen3:4b-instruct",
                    background_model="gemma4:e4b",
                )
            self.assertTrue(payload["queued"])
            self.assertEqual(payload["background_job"]["model"], "gemma4:e4b")
            state = operator_state(root)
            self.assertEqual(state["backgroundJobCounts"]["queued"], 1)

    def test_operator_run_background_job_processes_queue(self) -> None:
        class FakeClient:
            def __init__(self, model: str, base_url: str = "http://127.0.0.1:11434", timeout_seconds: float = 60.0) -> None:
                self.model = model

            def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
                if self.model == "qwen3:4b-instruct":
                    if '"task": "classify"' in user_prompt:
                        return {"label": "proposal", "confidence": 0.9, "rationale": "triage"}
                    return {"summary": "fast summary", "keywords": ["fast", "summary"]}
                return {
                    "lessons": ["fresh bounded lesson"],
                    "counterexamples": [],
                    "follow_up_questions": ["question"],
                }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("p1_core.cli.OllamaClient", FakeClient):
                queued = operator_ingest(
                    root,
                    input_text="background me",
                    model="qwen3:4b-instruct",
                    background_model="gemma4:e4b",
                )
                completed = operator_run_background_job(
                    root,
                    job_id=queued["background_job"]["job_id"],
                    model="gemma4:e4b",
                )
            self.assertEqual(completed["status"], "completed")
            self.assertEqual(completed["result"]["records_written"], 1)


if __name__ == "__main__":
    unittest.main()
