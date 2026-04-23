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
from p4_core.workspace import bootstrap_workspace


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
                self.assertIn("進捗ログ", html)
                self.assertIn("実行操作", html)
                self.assertIn("メッセージ送信", html)
                self.assertNotIn("現在の実行", html)
                self.assertIn('id="statusPill"', html)
                self.assertIn('id="sendButton"', html)
                self.assertNotIn('id="currentRunStream"', html)
                self.assertNotIn('id="currentRunMessage"', html)
                self.assertIn("function renderSnapshot(snapshot)", html)
                self.assertIn('id="operationsPanel"', html)
                self.assertIn('id="updatesPanel"', html)
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
            self.assertEqual(operations[0]["status"], "failed")
            self.assertEqual(operations[0]["finished_at"], "2026-04-18T10:01:00+00:00")

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
            self.assertEqual(current["status"], "running")
            self.assertEqual(stale["status"], "failed")

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
            self.assertEqual(operation["status"], "running")
            live_items = [
                item
                for step in operation["flow_steps"]
                for item in step.get("items", [])
                if item.get("label") == "live_stream"
            ]
            self.assertEqual(len(live_items), 1)
            self.assertIn("Beatles", live_items[0]["content"])
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
            self.assertEqual(operation["status"], "failed")
            self.assertIn("stale output", operation["output_preview"])
            self.assertIn("no active worker and no recent activity", operation["detail"])

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
            self.assertIn("frame_opened", labels)
            self.assertIn("frame_returned", labels)
            self.assertIn("child_return", labels)
            html = render_dashboard_html(snapshot)
            self.assertIn("フレーム開始", html)
            self.assertIn("フレーム帰還", html)
            self.assertIn("子フレーム結果", html)
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
                    "type": "assistant_message",
                    "role": "assistant",
                    "content": long_output,
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
