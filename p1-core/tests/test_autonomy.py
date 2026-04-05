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
            self.assertEqual(runtime.action_store.counts()["queued"], 1)
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
            self.assertIn("llmUsage", payload)

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


if __name__ == "__main__":
    unittest.main()
