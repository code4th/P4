from __future__ import annotations

import http.client
import json
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from p4_core.dashboard import create_dashboard_server
from p4_core.models import ModelRouter
from p4_core.workspace import WorkspacePaths, append_jsonl, bootstrap_workspace, write_json


class DashboardTests(unittest.TestCase):
    def test_dashboard_serves_snapshot_and_accepts_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)

            def fake_chat_runner(current_root: Path, content: str, model: str, mode: str, shell_name: str) -> None:
                from p4_core.workspace import active_session_id, append_session_event
                from p4_core.dashboard import _update_runtime

                self.assertIn(mode, {"native_chat", "terminal_agent"})
                self.assertIn(shell_name, {"auto", "bash"})
                session_id = active_session_id(current_root)
                append_session_event(current_root, session_id, {"type": "user_message", "role": "user", "content": content})
                _update_runtime(
                    current_root,
                    status="running",
                    current_model=model,
                    current_model_reason="ollama native generate stream",
                    current_user_message=content,
                    current_stream_text="Thinking...\n\nhello",
                )
                append_session_event(
                    current_root,
                    session_id,
                    {"type": "assistant_message", "role": "assistant", "content": "Thinking...\n\nhello", "model": model},
                )
                _update_runtime(
                    current_root,
                    status="idle",
                    current_model=model,
                    current_model_reason="ollama native generate stream",
                    current_user_message=content,
                    current_stream_text="Thinking...\n\nhello",
                    last_llm_raw_preview="Thinking...\n\nhello",
                )

            server = create_dashboard_server(root, host="127.0.0.1", port=0, chat_runner=fake_chat_runner)
            port = server.server_address[1]
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
            thread.start()
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                conn.request("GET", "/api/health")
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.read(), b"ok")
                conn.close()

                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                conn.request(
                    "POST",
                    "/api/message",
                    body=json.dumps({"content": "hello", "model": "devstral:latest", "shell": "bash"}),
                    headers={"Content-Type": "application/json"},
                )
                response = conn.getresponse()
                payload = json.loads(response.read().decode("utf-8"))
                self.assertTrue(payload["ok"])
                self.assertEqual(payload["model"], "devstral:latest")
                self.assertEqual(payload["mode"], "native_chat")
                self.assertEqual(payload["shell"], "bash")
                conn.close()

                deadline = time.time() + 2.0
                snapshot = {}
                while time.time() < deadline:
                    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                    conn.request("GET", "/api/snapshot")
                    response = conn.getresponse()
                    snapshot = json.loads(response.read().decode("utf-8"))
                    conn.close()
                    if len(snapshot.get("recent_transcript") or []) >= 2:
                        break
                    time.sleep(0.1)

                self.assertGreaterEqual(len(snapshot["recent_transcript"]), 2)
                self.assertEqual(snapshot["runtime"]["current_stream_text"], "Thinking...\n\nhello")
                self.assertEqual(snapshot["runtime"]["current_model"], "devstral:latest")
                self.assertIn("frames", snapshot)
                self.assertIn("frame_stack", snapshot["frames"])

                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                conn.request("GET", "/")
                response = conn.getresponse()
                html = response.read().decode("utf-8")
                self.assertEqual(response.status, 200)
                self.assertIn("P4 ダッシュボード", html)
                self.assertIn("実行操作", html)
                self.assertIn("メッセージ送信", html)
                self.assertNotIn("現在の実行", html)
                self.assertIn('id="statusPill"', html)
                self.assertIn('id="sendButton"', html)
                self.assertNotIn('id="currentRunStream"', html)
                self.assertNotIn('id="currentRunMessage"', html)
                self.assertIn("function renderSnapshot(snapshot)", html)
                self.assertIn('id="operationsPanel"', html)
                self.assertNotIn('id="updatesPanel"', html)
                self.assertNotIn("進捗ログ", html)
                self.assertNotIn('id="framesPanel"', html)
                self.assertNotIn("フレーム階層", html)
                self.assertIn("flow-phase", html)
                self.assertIn("階層深度:", html)
                self.assertIn("インデント:", html)
                self.assertIn("まだ実行操作はありません。", html)
                self.assertIn("function renderFlowSteps(steps, opId", html)
                self.assertIn("function renderFlowItem(item, scrollId", html)
                self.assertIn("function renderFrameFlowContent(item)", html)
                self.assertNotIn("onclick=\"toggleNested('current-stream')\"", html)
                self.assertIn("onclick=\"sendMessage()\"", html)
                self.assertIn("ライブ出力", html)
                self.assertNotIn('id="latestResult"', html)
                self.assertIn("ターミナルエージェント", html)
                self.assertIn("自動シェル", html)
                self.assertNotIn('id="frameMetrics"', html)
                self.assertNotIn("function renderFrameMetrics(metrics)", html)
                self.assertIn("setInterval(refresh, 2000)", html)
                conn.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_snapshot_normalizes_stale_running_operation_when_runtime_is_idle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            from p4_core.dashboard import build_snapshot
            from p4_core.workspace import WorkspacePaths, active_session_id, append_session_event, write_json

            session_id = active_session_id(root)
            append_session_event(
                root,
                session_id,
                {
                    "type": "operation",
                    "role": "system",
                    "operation_id": "op-stale",
                    "title": "Terminal agent",
                    "detail": "stale detail",
                    "status": "running",
                    "started_at": "2026-04-18T10:00:00+00:00",
                },
            )
            paths = WorkspacePaths(root)
            runtime = json.loads(paths.runtime_status_path.read_text(encoding="utf-8"))
            runtime["status"] = "idle"
            runtime["last_event_at"] = "2026-04-18T10:01:00+00:00"
            write_json(paths.runtime_status_path, runtime)

            snapshot = build_snapshot(root)
            operations = snapshot.get("recent_operations") or []
            self.assertEqual(len(operations), 1)
            # Canonical operation status is "started" (legacy "running" is
            # normalized at the canonical event boundary; UI must not silently
            # downgrade it to "failed" — see p4-event-contract-audit-2026-04-24).
            self.assertEqual(operations[0]["status"], "started")
            self.assertIsNone(operations[0]["finished_at"])

    def test_snapshot_normalizes_older_running_operation_when_new_run_is_active(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            from p4_core.dashboard import build_snapshot
            from p4_core.workspace import WorkspacePaths, active_session_id, append_session_event, write_json

            session_id = active_session_id(root)
            append_session_event(
                root,
                session_id,
                {
                    "type": "operation",
                    "role": "system",
                    "operation_id": "op-old",
                    "title": "Terminal agent",
                    "detail": "old stale run",
                    "status": "running",
                    "started_at": "2026-04-18T10:00:00+00:00",
                },
            )
            append_session_event(
                root,
                session_id,
                {
                    "type": "operation",
                    "role": "system",
                    "operation_id": "op-current",
                    "title": "Terminal agent",
                    "detail": "current run",
                    "status": "running",
                    "started_at": "2026-04-18T10:05:00+00:00",
                },
            )
            paths = WorkspacePaths(root)
            runtime = json.loads(paths.runtime_status_path.read_text(encoding="utf-8"))
            runtime["status"] = "running"
            runtime["current_operation_id"] = "op-current"
            runtime["current_started_at"] = "2026-04-18T10:05:00+00:00"
            runtime["last_event_at"] = datetime.now(timezone.utc).isoformat()
            write_json(paths.runtime_status_path, runtime)

            snapshot = build_snapshot(root)
            operations = snapshot.get("recent_operations") or []
            self.assertEqual(len(operations), 2)
            current = next(item for item in operations if item["operation_id"] == "op-current")
            stale = next(item for item in operations if item["operation_id"] == "op-old")
            self.assertEqual(current["status"], "started")
            self.assertEqual(stale["status"], "started")

    def test_snapshot_keeps_runtime_current_operation_running_with_live_stream(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            from p4_core.dashboard import build_snapshot, render_dashboard_html
            from p4_core.workspace import WorkspacePaths, active_session_id, append_session_event, write_json

            session_id = active_session_id(root)
            append_session_event(
                root,
                session_id,
                {
                    "type": "operation",
                    "role": "system",
                    "operation_id": "op-current",
                    "title": "Terminal agent",
                    "detail": "current run",
                    "status": "running",
                    "started_at": "2026-04-18T10:00:00+00:00",
                },
            )
            append_session_event(
                root,
                session_id,
                {
                    "type": "user_message",
                    "role": "user",
                    "content": "ビートルズってボンジョビ？",
                    "step_index": 0,
                    "timestamp": "2026-04-18T10:00:01+00:00",
                },
            )
            paths = WorkspacePaths(root)
            runtime = json.loads(paths.runtime_status_path.read_text(encoding="utf-8"))
            now = datetime.now(timezone.utc).isoformat()
            runtime["status"] = "running"
            runtime["worker_running"] = False
            runtime["current_operation_id"] = "op-current"
            runtime["current_stream_text"] = "[thinking]\nanswering Beatles question"
            runtime["last_event_at"] = now
            write_json(paths.runtime_status_path, runtime)

            snapshot = build_snapshot(root)
            operation = snapshot["recent_operations"][0]
            self.assertEqual(operation["status"], "started")
            live_items = [
                item
                for step in operation["flow_steps"]
                for item in step.get("items", [])
                if item.get("label") == "live_stream"
            ]
            self.assertEqual(len(live_items), 0)
            html = render_dashboard_html(snapshot)
            self.assertIn("LLMライブ", html)

    def test_snapshot_marks_runtime_current_operation_failed_when_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            from p4_core.dashboard import build_snapshot
            from p4_core.workspace import WorkspacePaths, active_session_id, append_session_event, write_json

            session_id = active_session_id(root)
            append_session_event(
                root,
                session_id,
                {
                    "type": "operation",
                    "role": "system",
                    "operation_id": "op-stale-current",
                    "title": "Terminal agent",
                    "detail": "stale current run",
                    "status": "running",
                    "started_at": "2026-04-18T10:00:00+00:00",
                },
            )
            paths = WorkspacePaths(root)
            runtime = json.loads(paths.runtime_status_path.read_text(encoding="utf-8"))
            runtime["status"] = "running"
            runtime["worker_running"] = False
            runtime["current_operation_id"] = "op-stale-current"
            runtime["current_stream_text"] = "[thinking]\nstale output"
            runtime["last_event_at"] = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
            write_json(paths.runtime_status_path, runtime)

            snapshot = build_snapshot(root)
            operation = snapshot["recent_operations"][0]
            self.assertEqual(operation["status"], "started")
            self.assertNotIn("stale output", str(operation.get("output_preview") or ""))
            self.assertNotIn("no active worker and no recent activity", str(operation.get("detail") or ""))

    def test_snapshot_renders_frame_events_inside_operation_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            from p4_core.dashboard import build_snapshot, render_dashboard_html
            from p4_core.workspace import active_session_id, append_session_event

            session_id = active_session_id(root)
            append_session_event(
                root,
                session_id,
                {
                    "type": "operation",
                    "role": "system",
                    "operation_id": "op-frame",
                    "title": "Terminal agent",
                    "detail": "frame run",
                    "status": "running",
                    "started_at": "2026-04-18T10:00:00+00:00",
                },
            )
            append_session_event(
                root,
                session_id,
                {
                    "type": "frame_opened",
                    "role": "system",
                    "frame_id": "child-1",
                    "parent_frame_id": "root-1",
                    "goal": "Inspect notes",
                    "content": "Opened child frame: Inspect notes",
                    "depth": 1,
                    "operation_id": "op-frame",
                    "turn_id": "turn-frame",
                    "step_index": 1,
                },
            )
            append_session_event(
                root,
                session_id,
                {
                    "type": "frame_returned",
                    "role": "system",
                    "frame_id": "child-1",
                    "parent_frame_id": "root-1",
                    "return_payload": {"summary": "notes inspected", "findings": ["alpha"]},
                    "content": "Returned to parent frame: notes inspected",
                    "depth": 1,
                    "operation_id": "op-frame",
                    "turn_id": "turn-frame",
                    "step_index": 2,
                },
            )
            append_session_event(
                root,
                session_id,
                {
                    "type": "child_return",
                    "role": "system",
                    "child_frame_id": "child-1",
                    "return_payload": {"summary": "notes inspected", "findings": ["alpha"]},
                    "content": "notes inspected",
                    "operation_id": "op-frame",
                    "turn_id": "turn-frame",
                    "step_index": 2,
                },
            )
            append_session_event(
                root,
                session_id,
                {
                    "type": "operation",
                    "role": "system",
                    "operation_id": "op-frame",
                    "title": "Terminal agent",
                    "detail": "frame run",
                    "status": "success",
                    "started_at": "2026-04-18T10:00:00+00:00",
                    "finished_at": "2026-04-18T10:00:02+00:00",
                },
            )

            snapshot = build_snapshot(root)
            operation = snapshot["recent_operations"][0]
            labels = [
                item["label"]
                for step in operation["flow_steps"]
                for item in step.get("items", [])
            ]
            self.assertIn("frame", labels)
            html = render_dashboard_html(snapshot)
            self.assertIn("フレーム", html)
            self.assertIn("Inspect notes", html)
            self.assertNotIn("フレーム階層", html)

    def test_dashboard_keeps_llm_output_full_and_scrollable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            from p4_core.dashboard import build_snapshot, render_dashboard_html
            from p4_core.workspace import WorkspacePaths, active_session_id, append_session_event, write_json

            session_id = active_session_id(root)
            long_output = "LLM-BEGIN\n" + ("0123456789abcdef\n" * 120) + "LLM-END"
            append_session_event(
                root,
                session_id,
                {
                    "type": "operation",
                    "role": "system",
                    "operation_id": "op-long",
                    "title": "Terminal agent",
                    "detail": "long LLM output",
                    "status": "running",
                    "started_at": "2026-04-18T10:00:00+00:00",
                },
            )
            append_session_event(
                root,
                session_id,
                {
                    "type": "runtime_event",
                    "role": "system",
                    "operation_id": "op-long",
                    "event_name": "llm_call_finished",
                    "content": long_output,
                    "details": {
                        "model": "gemma4:26b",
                        "content_text": long_output,
                    },
                    "turn_id": "turn-long",
                    "step_index": 1,
                },
            )
            paths = WorkspacePaths(root)
            runtime = json.loads(paths.runtime_status_path.read_text(encoding="utf-8"))
            runtime["status"] = "running"
            runtime["current_stream_text"] = long_output
            write_json(paths.runtime_status_path, runtime)

            html = render_dashboard_html(build_snapshot(root))
            self.assertIn("LLM-BEGIN", html)
            self.assertIn("LLM-END", html)
            self.assertIn("0123456789abcdef\nLLM-END", html)
            self.assertIn(".flow-content { background: #0c1013; padding: 8px; border-radius: 4px; border: 1px solid #1f272e; max-height: 420px; overflow: auto; }", html)
            self.assertIn(".operation-output { max-height: 520px; overflow: auto;", html)
            self.assertIn(".operation-output .flow-content { max-height: none; overflow: visible; }", html)
            self.assertIn("--flow-llm: #5b8def;", html)
            self.assertIn(".flow-item.llm .flow-content { border-left: 3px solid var(--flow-llm);", html)
            self.assertIn(".flow-item.decision .flow-content { border-left: 3px solid var(--flow-system);", html)
            self.assertIn(".flow-item.tool .flow-content { border-left: 3px solid var(--flow-tool);", html)

    def test_dashboard_shows_judge_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            from p4_core.dashboard import build_snapshot, render_dashboard_html
            from p4_core.workspace import active_session_id, append_session_event

            session_id = active_session_id(root)
            append_session_event(
                root,
                session_id,
                {
                    "type": "system_note",
                    "role": "system",
                    "content": "完了受理判定: accepted_with_warning",
                    "code": "finish_acceptance",
                    "reason_code": "review_unavailable_observation_accepted",
                    "details": {
                        "status": "accepted_with_warning",
                        "semantic_status": "review_unavailable_observation_accepted",
                        "review": {"retry_count": 1},
                    },
                },
            )
            append_session_event(
                root,
                session_id,
                {
                    "type": "system_note",
                    "role": "system",
                    "content": "完了がブロックされました",
                    "code": "finish_blocked",
                    "reason_code": "finish_acceptance_failed",
                },
            )

            snapshot = build_snapshot(root)
            self.assertEqual(snapshot["judge_metrics"]["consecutive_finish_blocks"], 1)
            self.assertEqual(snapshot["judge_metrics"]["judge_retry_count"], 1)
            self.assertTrue(snapshot["judge_metrics"]["fallback_used"])
            html = render_dashboard_html(snapshot)
            self.assertIn("judge: blocks=1", html)
            self.assertIn("fallback=yes", html)

    def test_dashboard_renders_structured_runtime_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            from p4_core.dashboard import build_snapshot, render_dashboard_html
            from p4_core.workspace import active_session_id, append_session_event

            session_id = active_session_id(root)
            append_session_event(
                root,
                session_id,
                {
                    "type": "operation",
                    "role": "system",
                    "operation_id": "op-runtime",
                    "title": "Terminal agent",
                    "detail": "structured runtime event",
                    "status": "running",
                    "started_at": "2026-04-18T10:00:00+00:00",
                },
            )
            append_session_event(
                root,
                session_id,
                {
                    "type": "runtime_event",
                    "role": "system",
                    "operation_id": "op-runtime",
                    "event_name": "llm_call_finished",
                    "content": "[content]\nhello",
                    "details": {
                        "model": "devstral",
                        "attempt_count": 1,
                        "content_text": "hello",
                    },
                    "turn_id": "turn-runtime",
                    "step_index": 1,
                },
            )

            snapshot = build_snapshot(root)
            operation = snapshot["recent_operations"][0]
            labels = [
                item["label"]
                for step in operation["flow_steps"]
                for item in step.get("items", [])
            ]
            self.assertIn("llm", labels)
            html = render_dashboard_html(snapshot)
            self.assertIn("LLM", html)
            self.assertIn("llm_call_finished", html)
            self.assertIn("devstral", html)

    def test_dashboard_renders_task_plan_flow_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            from p4_core.dashboard import build_snapshot, render_dashboard_html
            from p4_core.workspace import active_session_id, append_session_event

            session_id = active_session_id(root)
            append_session_event(
                root,
                session_id,
                {
                    "type": "operation",
                    "role": "system",
                    "operation_id": "op-plan",
                    "title": "Terminal agent",
                    "detail": "task plan",
                    "status": "running",
                    "started_at": "2026-04-18T10:00:00+00:00",
                },
            )
            append_session_event(
                root,
                session_id,
                {
                    "type": "task_plan",
                    "role": "system",
                    "operation_id": "op-plan",
                    "content": "Planned 2 child tasks.",
                    "rationale": "separate inspect and execute",
                    "tasks": [
                        {"task_id": "task-1", "goal": "inspect"},
                        {"task_id": "task-2", "goal": "execute"},
                    ],
                    "turn_id": "turn-plan",
                    "step_index": 1,
                },
            )

            snapshot = build_snapshot(root)
            operation = snapshot["recent_operations"][0]
            labels = [
                item["label"]
                for step in operation["flow_steps"]
                for item in step.get("items", [])
            ]
            self.assertIn("decision", labels)
            html = render_dashboard_html(snapshot)
            self.assertIn("判定", html)
            self.assertIn("separate inspect and execute", html)
            self.assertIn("task-2", html)

    def test_operation_window_prefers_operation_id_over_time_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            from p4_core.dashboard import build_snapshot
            from p4_core.workspace import active_session_id, append_session_event

            session_id = active_session_id(root)
            append_session_event(
                root,
                session_id,
                {
                    "type": "operation",
                    "role": "system",
                    "operation_id": "op-a",
                    "title": "A",
                    "detail": "A",
                    "status": "success",
                    "started_at": "2026-04-18T10:00:00+00:00",
                    "finished_at": "2026-04-18T10:00:01+00:00",
                },
            )
            append_session_event(root, session_id, {"type": "finish", "role": "assistant", "operation_id": "op-a", "content": "A", "step_index": 1})
            append_session_event(
                root,
                session_id,
                {
                    "type": "operation",
                    "role": "system",
                    "operation_id": "op-b",
                    "title": "B",
                    "detail": "B",
                    "status": "success",
                    "started_at": "2026-04-18T10:00:01+00:00",
                    "finished_at": "2026-04-18T10:00:02+00:00",
                },
            )
            append_session_event(root, session_id, {"type": "finish", "role": "assistant", "operation_id": "op-b", "content": "B", "step_index": 1})

            snapshot = build_snapshot(root)
            by_title = {operation["title"]: operation for operation in snapshot["recent_operations"]}
            labels_a = [item["content"] for step in by_title["A"]["flow_steps"] for item in step["items"] if item["label"] == "decision"]
            labels_b = [item["content"] for step in by_title["B"]["flow_steps"] for item in step["items"] if item["label"] == "decision"]
            self.assertEqual(labels_a, ["A"])
            self.assertEqual(labels_b, ["B"])

    def test_canonical_stream_events_stay_in_live_output_not_flow_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            from p4_core.dashboard import build_snapshot
            from p4_core.workspace import active_session_id, append_session_event

            session_id = active_session_id(root)
            append_session_event(
                root,
                session_id,
                {
                    "type": "operation",
                    "role": "system",
                    "operation_id": "op-canonical-stream",
                    "title": "Terminal agent",
                    "detail": "canonical stream run",
                    "status": "running",
                    "started_at": "2026-04-18T10:00:00+00:00",
                },
            )
            append_session_event(
                root,
                session_id,
                {
                    "type": "runtime_event",
                    "role": "system",
                    "operation_id": "op-canonical-stream",
                    "event_name": "llm_stream_chunk",
                    "content": "streaming answer",
                    "details": {"content_text": "streaming answer"},
                    "turn_id": "turn-canonical-stream",
                    "step_index": 1,
                },
            )
            append_session_event(
                root,
                session_id,
                {
                    "type": "runtime_event",
                    "role": "system",
                    "operation_id": "op-canonical-stream",
                    "event_name": "llm_call_started",
                    "content": "llm started",
                    "details": {},
                    "turn_id": "turn-canonical-stream",
                    "step_index": 2,
                },
            )
            append_session_event(
                root,
                session_id,
                {
                    "type": "runtime_event",
                    "role": "system",
                    "operation_id": "op-canonical-stream",
                    "event_name": "llm_stream_chunk",
                    "content": " plus more",
                    "details": {"delta_content": " plus more", "content_text": " plus more"},
                    "turn_id": "turn-canonical-stream",
                    "step_index": 1,
                },
            )
            append_session_event(
                root,
                session_id,
                {
                    "type": "runtime_event",
                    "role": "system",
                    "operation_id": "op-canonical-stream",
                    "event_name": "llm_call_finished",
                    "content": "final answer",
                    "details": {"content_text": "final answer"},
                    "turn_id": "turn-canonical-stream",
                    "step_index": 2,
                },
            )
            paths = WorkspacePaths(root)
            runtime = json.loads(paths.runtime_status_path.read_text(encoding="utf-8"))
            runtime["status"] = "running"
            runtime["current_operation_id"] = "op-canonical-stream"
            runtime["current_stream_text"] = "live text from runtime"
            write_json(paths.runtime_status_path, runtime)

            snapshot = build_snapshot(root)
            operation = snapshot["recent_operations"][0]
            self.assertEqual(operation["output_preview"], "live text from runtime")
            self.assertEqual(snapshot["recent_updates"], [])
            stream_items = [
                item
                for step in operation["flow_steps"]
                for item in step.get("items", [])
                if item.get("label") in {"llm", "tool"} and item.get("status") == "stream"
            ]
            self.assertEqual(stream_items, [])
            started_items = [
                item
                for step in operation["flow_steps"]
                for item in step.get("items", [])
                if item.get("label") == "llm" and item.get("status") == "started"
            ]
            self.assertEqual(started_items, [])
            finished_items = [
                item
                for step in operation["flow_steps"]
                for item in step.get("items", [])
                if item.get("label") == "llm" and item.get("status") == "finished"
            ]
            self.assertEqual(len(finished_items), 1)

    def test_fallback_stream_events_do_not_split_operation_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            from p4_core.dashboard import build_snapshot
            from p4_core.workspace import active_session_id

            session_id = active_session_id(root)
            paths = WorkspacePaths(root)
            append_jsonl(
                paths.session_events_path(session_id),
                {
                    "type": "operation",
                    "role": "system",
                    "operation_id": "op-fallback-stream",
                    "title": "Terminal agent",
                    "detail": "fallback stream run",
                    "status": "running",
                    "started_at": "2026-04-18T10:00:00+00:00",
                    "timestamp": "2026-04-18T10:00:00+00:00",
                },
            )
            append_jsonl(
                paths.session_events_path(session_id),
                {
                    "type": "runtime_event",
                    "role": "system",
                    "operation_id": "op-fallback-stream",
                    "event_name": "llm_stream_chunk",
                    "content": "fallback live output",
                    "details": {"content_text": "fallback live output"},
                    "turn_id": "turn-fallback-stream",
                    "step_index": 1,
                    "timestamp": "2026-04-18T10:00:01+00:00",
                },
            )
            runtime = json.loads(paths.runtime_status_path.read_text(encoding="utf-8"))
            runtime["status"] = "running"
            runtime["worker_running"] = True
            runtime["current_operation_id"] = "op-fallback-stream"
            runtime["current_stream_text"] = "fallback live output"
            runtime["last_event_at"] = "2026-04-18T10:00:01+00:00"
            write_json(paths.runtime_status_path, runtime)

            snapshot = build_snapshot(root)
            operation = snapshot["recent_operations"][0]
            live_items = [
                item
                for step in operation["flow_steps"]
                for item in step.get("items", [])
                if item.get("label") == "live_stream"
            ]
            runtime_stream_items = [
                item
                for step in operation["flow_steps"]
                for item in step.get("items", [])
                if item.get("label") == "runtime_event" and item.get("event_name") == "llm_stream_chunk"
            ]
            self.assertEqual(len(live_items), 1)
            self.assertEqual(runtime_stream_items, [])

    def test_dashboard_surfaces_finish_rejection_judge_error_details(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            from p4_core.dashboard import build_snapshot, render_dashboard_html
            from p4_core.workspace import active_session_id, append_canonical_event

            session_id = active_session_id(root)
            operation_id = "op-judge-error"
            append_canonical_event(
                root,
                session_id,
                {
                    "operation_id": operation_id,
                    "turn_id": "turn-judge-error",
                    "step_index": 0,
                    "kind": "operation",
                    "status": "started",
                    "payload": {
                        "title": "Terminal agent",
                        "detail": "おはよう",
                        "started_at": "2026-04-29T05:00:00+00:00",
                    },
                },
            )
            judge_payload = {
                "decision_type": "grounding_judge",
                "reason_code": "error",
                "message": "根拠判定: ERROR。judge 実行中にエラーが発生したため、完了判定に失敗しました。",
                "details": {
                    "prompt": "あなたは事実確認のエキスパートです。次の最終回答が証拠に基づいているか判定してください。",
                    "decision": "error",
                    "response_model": "fast",
                    "final_answer": "おはようございます！",
                    "attempts": [
                        {
                            "attempt": 1,
                            "decision": "error",
                            "error": "failed to reach Ollama at http://127.0.0.1:11434: HTTP Error 404: Not Found",
                        }
                    ],
                },
            }
            append_canonical_event(
                root,
                session_id,
                {
                    "operation_id": operation_id,
                    "turn_id": "turn-judge-error",
                    "step_index": 1,
                    "kind": "decision",
                    "status": "blocked",
                    "payload": judge_payload,
                },
            )
            append_canonical_event(
                root,
                session_id,
                {
                    "operation_id": operation_id,
                    "turn_id": "turn-judge-error",
                    "step_index": 1,
                    "kind": "decision",
                    "status": "blocked",
                    "payload": {
                        "decision_type": "finish_blocked",
                        "reason_code": "judge_error",
                        "message": "完了がブロックされました: 最終回答が事実に基づいていない、または証拠から逸脱しています。",
                        "details": {"issues": ["judge failed"], "judge": judge_payload["details"]},
                    },
                },
            )

            snapshot = build_snapshot(root)
            operation = snapshot["recent_operations"][0]
            finish_cards = [
                item
                for task in operation["flow_steps"]
                for step in task.get("steps", [])
                for item in step.get("items", [])
                if item.get("card_type") == "finish"
            ]
            self.assertEqual(len(finish_cards), 1)
            self.assertIn("grounding_judge", finish_cards[0]["details"])
            html = render_dashboard_html(snapshot)
            self.assertIn("judge実行エラー", html)
            self.assertIn("grounding judge details", html)
            self.assertIn("blocked judge details", html)
            self.assertIn("judge_error", html)
            self.assertIn("fast", html)
            self.assertIn("HTTP Error 404: Not Found", html)
            self.assertIn("failed to reach Ollama", html)
            grounding_start = html.index('<div class="flow-k">grounding judge details</div>')
            decision_start = html.index("P4 completion decision", grounding_start)
            judge_section = html[grounding_start:decision_start]
            self.assertLess(judge_section.index("P4 → judge LLM input"), judge_section.index("judge LLM → P4 output"))

    def test_dashboard_model_select_uses_all_ollama_models(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            from p4_core.dashboard import snapshot as snapshot_module

            class FakeOllamaClient:
                def __init__(self, *, base_url: str) -> None:
                    self.base_url = base_url

                def list_models(self, *, timeout_seconds: float = 30) -> dict:
                    return {
                        "ok": True,
                        "base_url": self.base_url,
                        "models": [
                            {"name": "gemma4:26b"},
                            {"name": "devstral:latest"},
                            {"name": "qwen3-coder:latest"},
                        ],
                    }

            original_client = snapshot_module.OllamaChatClient
            snapshot_module.OllamaChatClient = FakeOllamaClient
            try:
                snapshot = snapshot_module.build_snapshot(root)
            finally:
                snapshot_module.OllamaChatClient = original_client

            self.assertEqual(
                snapshot["available_models"],
                ["gemma4:26b", "devstral:latest", "qwen3-coder:latest"],
            )
            from p4_core.dashboard import render_dashboard_html

            html = render_dashboard_html(snapshot)
            self.assertIn('<option value="gemma4:26b" selected>gemma4:26b</option>', html)
            self.assertIn('<option value="devstral:latest">devstral:latest</option>', html)
            self.assertIn('<option value="qwen3-coder:latest">qwen3-coder:latest</option>', html)

    def test_dashboard_llm_card_shows_prompt_and_analysis_as_causal_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            from p4_core.dashboard import build_snapshot, render_dashboard_html
            from p4_core.workspace import active_session_id, append_canonical_event, append_prompt_snapshot

            session_id = active_session_id(root)
            operation_id = "op-llm-causal"
            turn_id = "turn-llm-causal"
            append_canonical_event(
                root,
                session_id,
                {
                    "operation_id": operation_id,
                    "turn_id": turn_id,
                    "step_index": 0,
                    "kind": "operation",
                    "status": "started",
                    "payload": {"title": "Terminal agent", "detail": "こんにちわ", "started_at": "2026-04-29T05:00:00+00:00"},
                },
            )
            append_prompt_snapshot(
                root,
                session_id,
                {
                    "turn_id": turn_id,
                    "queue_id": "queue-1",
                    "step_index": 1,
                    "model": "qwen3.6:latest",
                    "model_reason": "terminal agent mode via auto",
                    "prompt": "現在のユーザー依頼:\nこんにちわ\n\n最適と思われる次の一手を決定してください。",
                },
            )
            append_canonical_event(
                root,
                session_id,
                {
                    "operation_id": operation_id,
                    "turn_id": turn_id,
                    "step_index": 1,
                    "kind": "llm",
                    "status": "started",
                    "payload": {
                        "event_name": "llm_call_started",
                        "role": "terminal",
                        "model": "qwen3.6:latest",
                        "attempt_count": 1,
                        "transport": "chat_stream",
                        "schema_required": True,
                    },
                },
            )
            append_canonical_event(
                root,
                session_id,
                {
                    "operation_id": operation_id,
                    "turn_id": turn_id,
                    "step_index": 1,
                    "kind": "llm",
                    "status": "finished",
                    "payload": {
                        "event_name": "llm_call_finished",
                        "role": "terminal",
                        "model": "qwen3.6:latest",
                        "content_text": json.dumps(
                            {
                                "analysis": "挨拶なのでツール実行は不要で、短く返答すればよい。",
                                "assistant_message": "こんにちは。",
                                "tool_name": "final_answer",
                                "tool_args": {"answer": "こんにちは。"},
                            },
                            ensure_ascii=False,
                        ),
                        "schema_validation_ok": True,
                    },
                },
            )

            html = render_dashboard_html(build_snapshot(root))
            self.assertIn("P4 → Agent LLM Input", html)
            self.assertIn("LLM → P4 Output", html)
            self.assertIn("現在のユーザー依頼", html)
            self.assertIn("挨拶なのでツール実行は不要", html)
            self.assertLess(html.index("P4 → Agent LLM Input"), html.index("LLM → P4 Output"))

class ModelNormalizationTests(unittest.TestCase):
    def test_router_prefers_fast_model_for_short_japanese_prompt(self) -> None:
        router = ModelRouter(
            {
                "reasoning": "gemma4:26b",
                "fast": "glm-4.7-flash",
                "coding": "qwen3-coder",
                "terminal": "devstral",
            }
        )
        selection = router.select_model(
            goal_text="短く挨拶して終了する",
            pending_message="一言だけ挨拶して終わって",
            recent_events=[],
        )
        self.assertEqual(selection["model"], "glm-4.7-flash:latest")


if __name__ == "__main__":
    unittest.main()
