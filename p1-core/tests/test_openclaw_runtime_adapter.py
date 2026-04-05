from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from p1_core.adapters.openclaw_runtime import OpenClawAgentTextBackend, OpenClawNodeActionBackend
from p1_core.core.action_runtime import ActionExecutor, ActionSpec
from p1_core.core.background_job_store import BackgroundJobStore
from p1_core.core.world_store import WorldStore


class OpenClawRuntimeAdapterTests(unittest.TestCase):
    def test_agent_text_backend_extracts_reply_from_json(self) -> None:
        backend = OpenClawAgentTextBackend(agent_id="main")

        with patch("p1_core.adapters.openclaw_runtime.subprocess.run") as mocked_run:
            mocked_run.return_value = type(
                "Completed",
                (),
                {"returncode": 0, "stdout": json.dumps({"reply": "backend reply"}), "stderr": ""},
            )()
            text = backend.generate_text("system", "user")

        self.assertEqual(text, "backend reply")
        command = mocked_run.call_args.args[0]
        self.assertEqual(command[:4], ["openclaw", "agent", "--agent", "main"])
        self.assertIn("--json", command)

    def test_node_action_backend_invokes_mapped_command(self) -> None:
        backend = OpenClawNodeActionBackend(
            node_id="node-1",
            command_map={"run_command": "system.exec"},
        )

        with patch("p1_core.adapters.openclaw_runtime.subprocess.run") as mocked_run:
            mocked_run.return_value = type(
                "Completed",
                (),
                {"returncode": 0, "stdout": json.dumps({"status": "ok"}), "stderr": ""},
            )()
            payload = backend.run_command(argv=["pwd"], cwd="/tmp", timeout_seconds=5)

        self.assertEqual(payload["status"], "ok")
        command = mocked_run.call_args.args[0]
        self.assertEqual(command[:3], ["openclaw", "nodes", "invoke"])
        self.assertIn("system.exec", command)
        self.assertIn("--json", command)

    def test_action_executor_can_delegate_to_openclaw_backend(self) -> None:
        class FakeOpenClawBackend:
            def run_command(self, *, argv: list[str], cwd: str, timeout_seconds: int) -> dict[str, str]:
                return {"stdout": f"ran {' '.join(argv)} in {cwd} ({timeout_seconds})"}

            def read_file(self, *, path: str) -> dict[str, str]:
                return {"content": f"read {path}"}

            def write_file(self, *, path: str, content: str) -> dict[str, str]:
                return {"status": f"wrote {path}", "content": content}

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executor = ActionExecutor(
                root=root,
                background_job_store=BackgroundJobStore(root / "state" / "background_jobs"),
                world_store=WorldStore(root / "state" / "world"),
                openclaw_backend=FakeOpenClawBackend(),
            )

            result = executor.execute(
                ActionSpec(kind="run_command", backend="openclaw", inputs={"argv": ["pwd"], "cwd": "."})
            )

        self.assertEqual(result.status, "completed")
        self.assertIn("ran pwd", result.stdout)


if __name__ == "__main__":
    unittest.main()
