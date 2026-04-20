from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from p1_core.autonomy import AutonomyRuntime
from p1_core.core.action_runtime import ActionExecutor, ActionPolicy, ActionSpec


class FakeLocalBackend:
    def __init__(self) -> None:
        self.calls = 0

    def generate_text(self, system_prompt: str, user_prompt: str) -> str:
        self.calls += 1
        return "local reply"


class FakeOpenClawBackend:
    def __init__(self) -> None:
        self.calls = 0

    def generate_text(self, system_prompt: str, user_prompt: str) -> str:
        self.calls += 1
        return "openclaw reply"


class FailingLocalBackend:
    def generate_text(self, system_prompt: str, user_prompt: str) -> str:
        raise RuntimeError("local backend unavailable")


class AutonomyRuntimeTests(unittest.TestCase):
    def test_tick_replies_to_inbox_with_local_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            local = FakeLocalBackend()
            openclaw = FakeOpenClawBackend()
            runtime = AutonomyRuntime(root=root, local_llm_backend=local, openclaw_llm_backend=openclaw)

            runtime.enqueue_message("hello P1")
            result = runtime.tick_once()

            self.assertEqual(result["status"], "replied")
            self.assertEqual(result["reply_backend"], "local")
            self.assertEqual(local.calls, 1)
            self.assertEqual(openclaw.calls, 0)
            self.assertEqual(runtime.inbox.counts()["queued"], 0)
            self.assertEqual(len(runtime.conversation_store.recent()), 2)

    def test_tick_sleeps_before_next_wake_without_work(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = AutonomyRuntime(root=root, local_llm_backend=FakeLocalBackend())
            state = runtime.load_state()
            state["next_wake_at"] = "9999-01-01T00:00:00+00:00"
            runtime.save_state(state)

            result = runtime.tick_once()

            self.assertEqual(result["status"], "sleeping")
            self.assertTrue(runtime.heartbeat_store.list_recent(limit=1))
            self.assertIn("目的を確認した。", runtime.heartbeat_store.list_recent(limit=1)[0]["note"])
            self.assertTrue(runtime.initiative_store.list_recent(limit=1))
            initiative = runtime.initiative_store.list_recent(limit=1)[0]
            self.assertIn("problem_statement", initiative["proposal"])
            self.assertIn("diagnosis", initiative["proposal"])
            self.assertGreaterEqual(len(initiative["proposal"]["candidates"]), 1)
            self.assertIn("selected_candidate", initiative["proposal"])
            self.assertIn("selection_reason", initiative["proposal"])
            self.assertEqual(initiative["proposal"]["next_step"]["scope"], "internal")
            self.assertEqual(initiative["proposal"]["purpose"], runtime.load_state()["purpose"]["statement"] + "\nroot_objectives: " + "; ".join(runtime.load_state()["purpose"]["root_objectives"]))
            self.assertNotIn("friend", json.dumps(initiative, ensure_ascii=False).lower())

    def test_tick_executes_low_risk_queued_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = AutonomyRuntime(root=root, local_llm_backend=FakeLocalBackend())
            queued = runtime.action_store.enqueue(
                ActionSpec(kind="append_note", inputs={"content": "autonomy note"}, risk_level="low")
            )

            result = runtime.tick_once()

            self.assertEqual(result["status"], "action_executed")
            self.assertEqual(runtime.action_store.counts()["completed"], 1)
            self.assertEqual(runtime.action_store.counts()["queued"], 0)
            self.assertEqual(queued["action_id"], result["action"]["action_id"])

    def test_tick_routes_high_risk_action_to_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = AutonomyRuntime(root=root, local_llm_backend=FakeLocalBackend())
            runtime.action_store.enqueue(ActionSpec(kind="run_command", inputs={"argv": ["pwd"]}, risk_level="low"))

            result = runtime.tick_once()

            self.assertEqual(result["status"], "approval_required")
            self.assertEqual(runtime.action_store.counts()["approval_required"], 1)

    def test_tick_defers_when_governance_freezes_low_risk_autonomy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = AutonomyRuntime(root=root, local_llm_backend=FakeLocalBackend())
            governance = runtime.governance_store.latest()
            governance["feedback"]["freeze_low_risk_autonomy"] = True
            runtime.governance_store.latest_path.write_text(
                json.dumps(governance, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            runtime.action_store.enqueue(ActionSpec(kind="append_note", inputs={"content": "frozen"}, risk_level="low"))

            result = runtime.tick_once()

            self.assertEqual(result["status"], "deferred")
            self.assertEqual(runtime.action_store.counts()["queued"], 0)
            self.assertEqual(runtime.action_store.counts()["deferred"], 1)
            self.assertEqual(runtime.action_store.counts()["completed"], 0)

    def test_local_failure_does_not_fallback_to_openclaw_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            openclaw = FakeOpenClawBackend()
            runtime = AutonomyRuntime(root=root, local_llm_backend=FailingLocalBackend(), openclaw_llm_backend=openclaw)
            runtime.enqueue_message("hello")

            result = runtime.tick_once()

            self.assertEqual(result["status"], "conversation_deferred")
            self.assertEqual(openclaw.calls, 0)
            self.assertEqual(runtime.capability_store.counts()["total"], 1)
            self.assertEqual(runtime.inbox.counts()["queued"], 0)
            self.assertEqual(runtime.inbox.counts()["deferred"], 1)

    def test_run_command_is_not_treated_as_low_risk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = AutonomyRuntime(root=root, local_llm_backend=FakeLocalBackend())
            spec = ActionSpec(kind="run_command", inputs={"argv": ["pwd"], "timeout_seconds": 999}, risk_level="low")
            policy = ActionPolicy(runtime.governance_store.latest())
            self.assertEqual(policy.decide(spec)[0], "approval_required")

    def test_run_command_timeout_is_capped_in_executor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = AutonomyRuntime(root=root, local_llm_backend=FakeLocalBackend())
            executor = ActionExecutor(root=root, background_job_store=runtime.background_jobs, world_store=runtime.world_store)
            spec = ActionSpec(kind="run_command", inputs={"argv": ["pwd"], "timeout_seconds": 999})

            with patch("p1_core.core.action_runtime.subprocess.run") as mocked_run:
                mocked_run.return_value = type(
                    "Completed",
                    (),
                    {"stdout": "", "stderr": "", "returncode": 0},
                )()
                result = executor.execute(spec)

            self.assertEqual(result.status, "completed")
            self.assertEqual(mocked_run.call_args.kwargs["timeout"], 15)

    def test_show_state_includes_usage_and_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = AutonomyRuntime(root=root, local_llm_backend=FakeLocalBackend())
            payload = runtime.show_state()
            self.assertEqual(payload["inboxCounts"]["queued"], 0)
            self.assertEqual(payload["actionCounts"]["queued"], 0)
            self.assertIn("capabilityGapCounts", payload)
            self.assertIn("capabilityProposalCounts", payload)
            self.assertIn("capabilityReviewCounts", payload)
            self.assertIn("capabilityExecutionCounts", payload)
            self.assertIn("llmUsage", payload)

    def test_load_state_merges_purpose_into_existing_runtime_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = AutonomyRuntime(root=root, local_llm_backend=FakeLocalBackend())
            runtime.runtime_state_path.parent.mkdir(parents=True, exist_ok=True)
            runtime.runtime_state_path.write_text(
                json.dumps({"mode": "cooperative_tick"}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            state = runtime.load_state()

            self.assertIn("purpose", state)
            self.assertIn("help make the world better", state["purpose"]["statement"])
            self.assertTrue(state["purpose"]["mutable"])

    def test_update_purpose_allows_mutable_master_purpose(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = AutonomyRuntime(root=root, local_llm_backend=FakeLocalBackend())

            purpose = runtime.update_purpose(
                statement="I exist to support better outcomes through careful, reversible action.",
                root_objectives=["support better outcomes", "stay reversible"],
                mutable=True,
            )

            state = runtime.load_state()
            self.assertEqual(purpose["statement"], "I exist to support better outcomes through careful, reversible action.")
            self.assertEqual(state["purpose"]["statement"], purpose["statement"])
            self.assertTrue(state["purpose"]["mutable"])

    def test_load_state_has_single_source_of_truth_coordination(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = AutonomyRuntime(root=root, local_llm_backend=FakeLocalBackend())
            state = runtime.load_state()

            self.assertIn("coordination", state)
            self.assertEqual(state["coordination"]["source_of_truth"], "runtime-state.json")
            self.assertEqual(state["coordination"]["single_writer"], "autonomy_runtime")

    def test_openclaw_action_without_backend_records_capability_gap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = AutonomyRuntime(root=root, local_llm_backend=FakeLocalBackend())
            runtime.action_store.enqueue(
                ActionSpec(kind="read_file", backend="openclaw", inputs={"path": "prompt.md"}, risk_level="low")
            )

            result = runtime.tick_once()

            self.assertEqual(result["status"], "action_executed")
            self.assertEqual(runtime.action_store.counts()["failed"], 1)
            self.assertEqual(runtime.capability_store.counts()["total"], 1)

    def test_gap_is_promoted_to_capability_proposal_on_following_tick(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = AutonomyRuntime(root=root, local_llm_backend=FakeLocalBackend())
            runtime.capability_store.record_gap(
                title="missing read backend",
                detail="openclaw action backend is not configured",
                source="autonomy.action",
                severity="high",
            )

            result = runtime.tick_once()

            self.assertEqual(result["status"], "capability_proposal_recorded")
            self.assertEqual(runtime.capability_store.proposal_counts()["total"], 1)
            proposal = runtime.capability_store.list_proposals(limit=1)[0]
            self.assertEqual(proposal["proposal_type"], "capability_extension")

    def test_repeated_same_gap_does_not_create_duplicate_proposals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = AutonomyRuntime(root=root, local_llm_backend=FakeLocalBackend())
            first = runtime.capability_store.record_gap(
                title="missing read backend",
                detail="openclaw action backend is not configured",
                source="autonomy.action",
                severity="high",
                metadata={"kind": "read_file", "backend": "openclaw"},
            )
            second = runtime.capability_store.record_gap(
                title="missing read backend",
                detail="openclaw action backend is not configured",
                source="autonomy.action",
                severity="high",
                metadata={"kind": "read_file", "backend": "openclaw"},
            )

            runtime.tick_once()
            runtime.save_state({**runtime.load_state(), "next_wake_at": None})
            second_tick = runtime.tick_once()

            self.assertEqual(first["gap_id"], second["gap_id"])

    def test_gap_dedupe_preserves_meaningful_backend_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = AutonomyRuntime(root=root, local_llm_backend=FakeLocalBackend())
            first = runtime.capability_store.record_gap(
                title="missing backend capability",
                detail="action backend capability is missing",
                source="autonomy.action",
                severity="high",
                metadata={"backend_id": "openclaw-a", "kind": "read_file"},
            )
            second = runtime.capability_store.record_gap(
                title="missing backend capability",
                detail="action backend capability is missing",
                source="autonomy.action",
                severity="high",
                metadata={"backend_id": "openclaw-b", "kind": "read_file"},
            )

            self.assertNotEqual(first["gap_id"], second["gap_id"])
            self.assertEqual(runtime.capability_store.counts()["total"], 2)

    def test_gap_dedupe_ignores_volatile_message_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = AutonomyRuntime(root=root, local_llm_backend=FakeLocalBackend())
            first = runtime.capability_store.record_gap(
                title="conversation backend unavailable",
                detail="local reply generation failed without OpenClaw fallback: local backend unavailable",
                source="autonomy.message",
                severity="medium",
                metadata={"message_id": "message:1", "backend": "local"},
            )
            second = runtime.capability_store.record_gap(
                title="conversation backend unavailable",
                detail="local reply generation failed without OpenClaw fallback: local backend unavailable",
                source="autonomy.message",
                severity="medium",
                metadata={"message_id": "message:2", "backend": "local"},
            )

            self.assertEqual(first["gap_id"], second["gap_id"])

    def test_capability_proposal_is_reviewed_and_queues_cloud_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = AutonomyRuntime(root=root, local_llm_backend=FakeLocalBackend())
            runtime.capability_store.record_proposal(
                gap_id="capgap:test",
                summary="Implement missing capability: conversation backend unavailable from autonomy.message (medium severity)",
                proposal_type="capability_extension",
                risk_level="medium",
                requires_approval=True,
                detail="missing backend",
            )

            result = runtime.tick_once()

            self.assertEqual(result["status"], "capability_review_recorded")
            self.assertEqual(runtime.capability_store.review_counts()["approval_pending"], 1)
            reviews = runtime.capability_store.list_reviews(limit=1)
            self.assertEqual(reviews[0]["governance"]["next_step"], "await_cloud_approval")

    def test_approved_capability_review_queues_bounded_execution_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = AutonomyRuntime(root=root, local_llm_backend=FakeLocalBackend())
            proposal = runtime.capability_store.record_proposal(
                gap_id="capgap:test",
                summary="Implement missing capability: low severity task",
                proposal_type="capability_extension",
                risk_level="low",
                requires_approval=False,
                detail="safe low-risk task",
            )
            review = runtime.capability_store.record_review(
                proposal_id=proposal["proposal_id"],
                gap_id=proposal["gap_id"],
                evaluation={"decision": "candidate"},
                governance={"proposal": proposal, "next_step": "autonomous_apply"},
            )

            result = runtime.tick_once()

            self.assertEqual(result["status"], "capability_execution_queued")
            self.assertEqual(runtime.capability_store.execution_counts()["queued_action"], 1)
            self.assertEqual(runtime.action_store.counts()["queued"], 1)

    def test_capability_execution_is_reconciled_after_action_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = AutonomyRuntime(root=root, local_llm_backend=FakeLocalBackend())
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

            first = runtime.tick_once()
            runtime.save_state({**runtime.load_state(), "next_wake_at": None})
            second = runtime.tick_once()
            execution = runtime.capability_store.list_executions(limit=1)[0]

            self.assertEqual(first["status"], "capability_execution_queued")
            self.assertEqual(second["status"], "action_executed")
            self.assertEqual(execution["status"], "completed")
            self.assertIn("rollback_hint", execution["metadata"])
            self.assertTrue(execution["metadata"]["artifacts"][0].endswith(".json"))

    def test_capability_task_lifecycle_moves_to_done_after_task_artifact_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = AutonomyRuntime(root=root, local_llm_backend=FakeLocalBackend())
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

            first = runtime.tick_once()
            second = runtime.tick_once()
            third = runtime.tick_once()
            fourth = runtime.tick_once()

            self.assertEqual(first["status"], "capability_execution_queued")
            self.assertEqual(second["status"], "action_executed")
            self.assertEqual(third["status"], "capability_task_planned")
            self.assertEqual(fourth["status"], "action_executed")
            self.assertEqual(runtime.capability_task_store.counts()["pending"], 0)
            self.assertEqual(runtime.capability_task_store.counts()["in_progress"], 0)
            self.assertEqual(runtime.capability_task_store.counts()["done"], 1)
            task = runtime.capability_task_store.list_all_tasks(limit=1)[0]
            self.assertIn("rollback_hint", task["result"])

    def test_deferred_capability_task_can_be_read_and_completed_after_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = AutonomyRuntime(root=root, local_llm_backend=FakeLocalBackend())
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

            runtime.tick_once()
            runtime.tick_once()
            governance = runtime.governance_store.latest()
            governance["feedback"]["freeze_low_risk_autonomy"] = True
            runtime.governance_store.latest_path.write_text(
                json.dumps(governance, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            runtime.tick_once()
            runtime.tick_once()
            runtime.tick_once()
            deferred_dir = root / "state" / "capabilities" / "deferred"
            deferred = next(deferred_dir.glob("*.json"))
            payload = json.loads(deferred.read_text(encoding="utf-8"))
            payload["retry_after_at"] = "2000-01-01T00:00:00+00:00"
            self.assertGreaterEqual(int(payload.get("retry_count", 0)), 1)
            deferred.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            action_deferred_dir = root / "state" / "actions" / "deferred"
            action_deferred = next(action_deferred_dir.glob("*.json"))
            action_payload = json.loads(action_deferred.read_text(encoding="utf-8"))
            action_payload["retry_after_at"] = "2000-01-01T00:00:00+00:00"
            action_deferred.write_text(json.dumps(action_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            governance["feedback"]["freeze_low_risk_autonomy"] = False
            runtime.governance_store.latest_path.write_text(
                json.dumps(governance, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            runtime.tick_once()
            runtime.tick_once()

            self.assertGreaterEqual(runtime.capability_task_store.counts()["done"], 1)
            self.assertEqual(runtime.capability_task_store.counts()["deferred"], 0)

    def test_deferred_capability_task_stops_after_retry_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = AutonomyRuntime(root=root, local_llm_backend=FakeLocalBackend())
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

            runtime.tick_once()
            runtime.tick_once()
            runtime.tick_once()
            governance = runtime.governance_store.latest()
            governance["feedback"]["freeze_low_risk_autonomy"] = True
            runtime.governance_store.latest_path.write_text(
                json.dumps(governance, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            runtime.tick_once()

            deferred_dir = root / "state" / "capabilities" / "deferred"
            deferred = next(deferred_dir.glob("*.json"))
            payload = json.loads(deferred.read_text(encoding="utf-8"))
            payload["retry_after_at"] = "2000-01-01T00:00:00+00:00"
            payload["retry_count"] = 3
            deferred.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            action_deferred_dir = root / "state" / "actions" / "deferred"
            action_deferred = next(action_deferred_dir.glob("*.json"))
            action_payload = json.loads(action_deferred.read_text(encoding="utf-8"))
            action_payload["retry_after_at"] = "2000-01-01T00:00:00+00:00"
            action_payload["retry_count"] = 3
            action_deferred.write_text(json.dumps(action_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            result = runtime.tick_once()

            self.assertEqual(result["status"], "sleeping")
            self.assertEqual(runtime.capability_task_store.counts()["deferred"], 0)
            self.assertGreaterEqual(runtime.capability_task_store.counts()["failed"], 1)

    def test_write_file_records_backup_path_for_rollbacks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = AutonomyRuntime(root=root, local_llm_backend=FakeLocalBackend())
            target = root / "workspace" / "note.txt"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("original\n", encoding="utf-8")
            executor = ActionExecutor(
                root=root,
                background_job_store=runtime.background_jobs,
                world_store=runtime.world_store,
            )
            result = executor.execute(
                ActionSpec(kind="write_file", inputs={"path": "workspace/note.txt", "content": "updated"})
            )

            self.assertEqual(result.status, "completed")
            self.assertIsNotNone(result.rollback_hint)
            self.assertIn("restore ", result.rollback_hint or "")
            self.assertIn("to", result.rollback_hint or "")
            backups = list((root / "state" / "rollback" / "backups").glob("*.bak"))
            self.assertEqual(len(backups), 1)

    def test_deferred_action_is_requeued_after_retry_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = AutonomyRuntime(root=root, local_llm_backend=FakeLocalBackend())
            governance = runtime.governance_store.latest()
            governance["feedback"]["freeze_low_risk_autonomy"] = True
            runtime.governance_store.latest_path.write_text(
                json.dumps(governance, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            runtime.action_store.enqueue(ActionSpec(kind="append_note", inputs={"content": "frozen"}, risk_level="low"))
            runtime.tick_once()
            deferred = list((root / "state" / "actions" / "deferred").glob("*.json"))[0]
            payload = json.loads(deferred.read_text(encoding="utf-8"))
            payload["retry_after_at"] = "2000-01-01T00:00:00+00:00"
            deferred.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            governance["feedback"]["freeze_low_risk_autonomy"] = False
            runtime.governance_store.latest_path.write_text(
                json.dumps(governance, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            result = runtime.tick_once()

            self.assertEqual(result["status"], "action_executed")
            self.assertEqual(runtime.action_store.counts()["completed"], 1)
            self.assertEqual(runtime.action_store.counts()["deferred"], 0)

    def test_deferred_message_is_requeued_after_retry_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = AutonomyRuntime(root=root, local_llm_backend=FailingLocalBackend())
            runtime.enqueue_message("hello")
            runtime.tick_once()
            deferred = list((root / "state" / "autonomy" / "inbox" / "deferred").glob("*.json"))[0]
            payload = json.loads(deferred.read_text(encoding="utf-8"))
            payload["retry_after_at"] = "2000-01-01T00:00:00+00:00"
            deferred.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            runtime.tick_once()

            self.assertEqual(runtime.inbox.counts()["deferred"], 1)
            self.assertEqual(runtime.capability_store.counts()["total"], 1)

    def test_deferred_message_stops_after_retry_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = AutonomyRuntime(root=root, local_llm_backend=FailingLocalBackend())
            runtime.enqueue_message("hello")
            runtime.tick_once()
            deferred = list((root / "state" / "autonomy" / "inbox" / "deferred").glob("*.json"))[0]
            payload = json.loads(deferred.read_text(encoding="utf-8"))
            payload["retry_after_at"] = "2000-01-01T00:00:00+00:00"
            payload["retry_count"] = 3
            deferred.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            runtime.tick_once()

            self.assertEqual(runtime.inbox.counts()["deferred"], 0)
            self.assertGreaterEqual(runtime.inbox.counts()["processed"], 1)

    def test_deferred_action_stops_after_retry_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = AutonomyRuntime(root=root, local_llm_backend=FakeLocalBackend())
            governance = runtime.governance_store.latest()
            governance["feedback"]["freeze_low_risk_autonomy"] = True
            runtime.governance_store.latest_path.write_text(
                json.dumps(governance, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            runtime.action_store.enqueue(ActionSpec(kind="append_note", inputs={"content": "frozen"}, risk_level="low"))
            runtime.tick_once()
            deferred = list((root / "state" / "actions" / "deferred").glob("*.json"))[0]
            payload = json.loads(deferred.read_text(encoding="utf-8"))
            payload["retry_after_at"] = "2000-01-01T00:00:00+00:00"
            payload["retry_count"] = 3
            deferred.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            result = runtime.tick_once()

            self.assertEqual(result["status"], "sleeping")
            self.assertEqual(runtime.action_store.counts()["deferred"], 0)
            self.assertEqual(runtime.action_store.counts()["failed"], 1)


if __name__ == "__main__":
    unittest.main()
