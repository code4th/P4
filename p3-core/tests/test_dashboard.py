from __future__ import annotations

import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from p3_core.dashboard import create_dashboard_server
from p3_core.models import ModelRouter
from p3_core.workspace import bootstrap_workspace


class DashboardTests(unittest.TestCase):
    def test_dashboard_serves_snapshot_and_accepts_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)

            def fake_chat_runner(current_root: Path, content: str, model: str, mode: str, shell_name: str) -> None:
                from p3_core.workspace import active_session_id, append_session_event
                from p3_core.dashboard import _update_runtime

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

                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                conn.request("GET", "/")
                response = conn.getresponse()
                html = response.read().decode("utf-8")
                self.assertEqual(response.status, 200)
                self.assertIn("P3 ダッシュボード", html)
                self.assertIn("進捗ログ", html)
                self.assertIn("実行操作", html)
                self.assertIn("現在の実行", html)
                self.assertIn('id="statusPill"', html)
                self.assertIn('id="currentRunStream"', html)
                self.assertIn("function renderSnapshot(snapshot)", html)
                self.assertIn('id="operationsPanel"', html)
                self.assertIn('id="updatesPanel"', html)
                self.assertIn("flow-phase", html)
                self.assertIn("まだ実行操作はありません。", html)
                self.assertIn("function renderFlowSteps(steps)", html)
                self.assertIn("function renderFlowItem(item)", html)
                self.assertIn("onclick=\"toggleNested('current-stream')\"", html)
                self.assertIn("onclick=\"sendMessage()\"", html)
                self.assertIn("ライブ出力", html)
                self.assertIn("ターミナルエージェント", html)
                self.assertIn("自動シェル", html)
                self.assertIn("ollama native generate stream", html)
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
            from p3_core.dashboard import build_snapshot
            from p3_core.workspace import WorkspacePaths, active_session_id, append_session_event, write_json

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
            from p3_core.dashboard import build_snapshot
            from p3_core.workspace import WorkspacePaths, active_session_id, append_session_event, write_json

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
            runtime["current_started_at"] = "2026-04-18T10:05:00+00:00"
            runtime["last_event_at"] = "2026-04-18T10:06:00+00:00"
            write_json(paths.runtime_status_path, runtime)

            snapshot = build_snapshot(root)
            operations = snapshot.get("recent_operations") or []
            self.assertEqual(len(operations), 2)
            current = next(item for item in operations if item["operation_id"] == "op-current")
            stale = next(item for item in operations if item["operation_id"] == "op-old")
            self.assertEqual(current["status"], "running")
            self.assertEqual(stale["status"], "failed")


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
