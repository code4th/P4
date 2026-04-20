from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from p2_core.dashboard import create_dashboard_server
from p2_core.workspace import bootstrap_workspace, now_iso, write_json


class DashboardTests(unittest.TestCase):
    def test_dashboard_serves_health_snapshot_and_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)

            server = create_dashboard_server(root, host="127.0.0.1", port=0)
            port = server.server_address[1]
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
            thread.start()
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                conn.request("GET", "/api/health")
                response = conn.getresponse()
                body = response.read()
                conn.close()
                self.assertEqual(response.status, 200)
                self.assertEqual(body.strip(), b"ok")

                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                conn.request("GET", "/api/snapshot")
                response = conn.getresponse()
                snapshot = json.loads(response.read().decode("utf-8"))
                conn.close()
                self.assertEqual(snapshot["goal"]["status"], "active")
                self.assertEqual(snapshot["active_generation"], 1)
                self.assertIn("operator_insights", snapshot)
                self.assertIn("latest_completed_attempt", snapshot)
                self.assertIn("current_stream_text", snapshot)
                self.assertIn("current_stream_sections", snapshot)
                self.assertIn("llm_timing_trend", snapshot)
                self.assertIn("latest_context_frame", snapshot)
                self.assertIn("task_hierarchy", snapshot)
                self.assertIn("implementation_notes", snapshot)
                self.assertIn("latest_session_events", snapshot)
                self.assertIn("latest_runtime_kernel", snapshot)
                self.assertIn("context_audit", snapshot)
                self.assertIn("latest_prompt_snapshots", snapshot)
                self.assertIn("latest_prompt_snapshot", snapshot)
                self.assertIn("generation_report", snapshot)

                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                conn.request(
                    "POST",
                    "/api/goal",
                    body=json.dumps({"goal_text": "迷路作成CLIを完成させる", "reset_mode": "none"}),
                    headers={"Content-Type": "application/json"},
                )
                response = conn.getresponse()
                payload = json.loads(response.read().decode("utf-8"))
                conn.close()
                self.assertEqual(response.status, 200)
                self.assertTrue(payload.get("ok"))
                self.assertEqual(payload.get("goal_text"), "迷路作成CLIを完成させる")

                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                conn.request("GET", "/api/snapshot")
                response = conn.getresponse()
                updated = json.loads(response.read().decode("utf-8"))
                conn.close()
                self.assertEqual(updated["goal"]["text"], "迷路作成CLIを完成させる")

                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                conn.request(
                    "POST",
                    "/api/goal",
                    body=json.dumps({"goal_text": "", "reset_mode": "none"}),
                    headers={"Content-Type": "application/json"},
                )
                response = conn.getresponse()
                error_payload = json.loads(response.read().decode("utf-8"))
                conn.close()
                self.assertEqual(response.status, 400)
                self.assertFalse(error_payload.get("ok"))
                self.assertIn("goal_text", error_payload.get("error", ""))

                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                conn.request(
                    "POST",
                    "/api/control",
                    body=json.dumps({"action": "stop"}),
                    headers={"Content-Type": "application/json"},
                )
                response = conn.getresponse()
                control_payload = json.loads(response.read().decode("utf-8"))
                conn.close()
                self.assertEqual(response.status, 200)
                self.assertTrue(control_payload.get("ok"))
                self.assertEqual(control_payload.get("action"), "stop")

                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                conn.request("GET", "/api/snapshot")
                response = conn.getresponse()
                paused_snapshot = json.loads(response.read().decode("utf-8"))
                conn.close()
                self.assertEqual(paused_snapshot["goal"]["status"], "paused")

                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                conn.request(
                    "POST",
                    "/api/control",
                    body=json.dumps({"action": "invalid"}),
                    headers={"Content-Type": "application/json"},
                )
                response = conn.getresponse()
                invalid_payload = json.loads(response.read().decode("utf-8"))
                conn.close()
                self.assertEqual(response.status, 400)
                self.assertFalse(invalid_payload.get("ok"))
                self.assertIn("action must be start or stop", invalid_payload.get("error", ""))

                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                conn.request("GET", "/api/events")
                response = conn.getresponse()
                first = response.fp.readline().decode("utf-8")
                second = response.fp.readline().decode("utf-8")
                conn.close()
                self.assertEqual(response.status, 200)
                self.assertTrue(first.startswith("event: snapshot"))
                self.assertTrue(second.startswith("data: "))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_dashboard_notify_ignores_posted_snapshot_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            attempts_dir = root / "state" / "attempts"
            with (root / "state" / "memos.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "memo_id": "m0001",
                            "title": "構文エラー前に因果を絞る",
                            "tactic": "直近差分と失敗位置を付き合わせる",
                            "why": "同じ場所を壊しているかを確認するため",
                            "confidence": 0.75,
                            "source_candidate_id": "c0000",
                            "tags": ["構文エラー", "因果", "レビュー"],
                            "evidence": {
                                "error_type": "SyntaxError",
                                "failure_detail": "\"(\" was never closed",
                            },
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
            write_json(
                attempts_dir / "c0001.json",
                {
                    "candidate_id": "c0001",
                    "status": "rejected",
                    "target_file": "agent/goal_logic.py",
                    "decision_reason": "</script><script>alert(1)</script>",
                    "reasoning_summary": {"diagnosis": "x"},
                    "situation_report": {"known": [], "suspected": [], "unknown": [], "chosen_response": ""},
                    "meta_diagnosis": {"status": "normal", "search_mode": "direct_improvement", "observation_bundle": {}},
                    "search_mode": "direct_improvement",
                },
            )

            server = create_dashboard_server(root, host="127.0.0.1", port=0)
            port = server.server_address[1]
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
            thread.start()
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                conn.request(
                    "POST",
                    "/api/notify",
                    body=json.dumps({"goal": {"text": "pwned<script>alert(1)</script>"}}),
                    headers={"Content-Type": "application/json"},
                )
                response = conn.getresponse()
                response.read()
                conn.close()
                self.assertEqual(response.status, 200)

                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                conn.request("GET", "/api/snapshot")
                response = conn.getresponse()
                snapshot = json.loads(response.read().decode("utf-8"))
                conn.close()
                self.assertNotIn("pwned", snapshot["goal"]["text"])

                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                conn.request("GET", "/")
                response = conn.getresponse()
                html = response.read().decode("utf-8")
                conn.close()
                self.assertEqual(response.status, 200)
                self.assertNotIn("</script><script>alert(1)</script>", html)
                self.assertIn("今わかっている重要点", html)
                self.assertIn("今回実装した内容", html)
                self.assertIn("直近の完了試行の説明", html)
                self.assertIn("追加文脈と局所失敗差分", html)
                self.assertIn("現在の階層コンテキスト", html)
                self.assertIn("LLM に渡した prompt", html)
                self.assertIn("モデルのリアルタイム出力", html)
                self.assertIn("session action/result 履歴", html)
                self.assertIn("P2 世代更新レポート", html)
                self.assertIn(".scroll-selectable", html)
                self.assertIn("SCROLLABLE_SELECTORS", html)
                self.assertIn("markScrollableSelectable", html)
                self.assertIn("event.metaKey", html)
                self.assertIn("selectNodeContents", html)
                insight_index = html.index("今わかっている重要点")
                generation_index = html.index("P2 世代更新レポート")
                hierarchy_index = html.index("現在の階層コンテキスト")
                prompt_index = html.index("LLM に渡した prompt")
                stream_index = html.index("モデルのリアルタイム出力")
                self.assertLess(insight_index, hierarchy_index)
                self.assertLess(insight_index, generation_index)
                self.assertLess(insight_index, stream_index)
                self.assertLess(stream_index, hierarchy_index)
                self.assertLess(hierarchy_index, prompt_index)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_dashboard_renders_operator_insights_and_reflection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            attempts_dir = root / "state" / "attempts"
            with (root / "state" / "memos.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "memo_id": "m0001",
                            "title": "構文エラー前に因果を絞る",
                            "tactic": "直近差分と失敗位置を付き合わせる",
                            "why": "同じ場所を壊しているかを確認するため",
                            "confidence": 0.75,
                            "source_candidate_id": "c0000",
                            "tags": ["構文エラー", "因果", "レビュー"],
                            "evidence": {
                                "error_type": "SyntaxError",
                                "failure_detail": "\"(\" was never closed",
                            },
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
            write_json(
                attempts_dir / "c0001.json",
                {
                    "candidate_id": "c0001",
                    "status": "rejected",
                    "target_file": "agent/goal_logic.py",
                    "decision_reason": "validation failed",
                    "reasoning_summary": {
                        "problem_statement": "構文エラーが続いている",
                        "diagnosis": "同じ場所を壊している",
                        "edit_intent": "出力を明確化する",
                        "why_this_file": "中心ファイルだから",
                        "expected_effect": "運用者に伝わりやすくなる",
                        "validation_hypothesis": "テストが通る",
                        "next_if_fail": "変更範囲を狭める",
                    },
                    "situation_report": {
                        "known": ["構文エラーが続いている"],
                        "suspected": ["同じファイルに集中している"],
                        "unknown": ["別の対象に切り替えるべきか"],
                        "chosen_response": "より慎重に変更する",
                    },
                    "pre_edit_reflection": {
                        "what_i_tried": "同じファイルを直そうとした",
                        "what_kept_happening": "構文エラーが続いた",
                    },
                    "post_edit_reflection": {
                        "did_i_actually_change_behavior": "まだ弱い",
                    },
                    "meta_diagnosis": {
                        "status": "stagnating",
                        "search_mode": "constraint_probe",
                        "observation_bundle": {
                            "target_histogram": {"agent/goal_logic.py": 5},
                            "recent_validation_summaries": ["validation failed: SyntaxError"],
                        },
                    },
                    "search_mode": "constraint_probe",
                    "purpose": "同じ失敗の反復を止めるために局所差分を見直す",
                    "change_summary": {"added_lines": 1, "removed_lines": 2},
                    "selected_context": {
                        "selected_context": ["attempt:c0000", "tests_context"],
                        "question_to_answer": "同じ失敗を繰り返しているか確認する",
                        "commitment": "局所失敗差分を確認し、同じ場所を壊さない最小変更を作る",
                    },
                    "resolved_context": {
                        "attempt:c0000": {"status": "rejected"},
                        "tests_context": {"files": ["tests/test_goal_logic.py"]},
                    },
                    "delta_context": {
                        "latest_failure": {
                            "error_type": "SyntaxError",
                            "file": "/tmp/agent/goal_logic.py",
                            "line": 182,
                            "detail": "\"(\" was never closed",
                        },
                        "must_avoid_next": ["同じ場所を大きく壊さない"],
                    },
                    "llm_timings": {
                        "generating": {"duration_ms": 1200, "first_output_latency_ms": 180, "streamed_chars": 320},
                        "total_duration_ms": 1200,
                    },
                    "self_memo": {
                        "title": "局所失敗差分を先に見る",
                        "tactic": "diff と validation を同時に読む",
                        "why": "同じ失敗の反復を止めるため",
                        "confidence": 0.7,
                        "tags": ["レビュー", "差分", "検証"],
                    },
                    "selected_coding_model": "devstral:latest",
                    "stream_log_path": str(root / "state" / "attempts" / "c0001.stream.txt"),
                },
            )
            (root / "state" / "attempts" / "c0001.stream.txt").write_text(
                "\n\n===== 追加文脈選択 =====\nctx output\n\n===== 自己診断 =====\nreflect output\n\n===== コード生成 =====\ncode output\n",
                encoding="utf-8",
            )
            (root / "state" / "attempts" / "c0001.events.jsonl").write_text(
                json.dumps(
                    {
                        "timestamp": "2026-04-11T00:00:00+00:00",
                        "frame_id": "c0001:d1:f1",
                        "frame_depth": 1,
                        "step": 1,
                        "action": "read_file",
                        "action_input": {"path": "agent/goal_logic.py"},
                        "thinking": "まず対象を読む。",
                        "result": {"ok": True, "relative_path": "agent/goal_logic.py"},
                    },
                    ensure_ascii=False,
                )
                + "\n"
                + json.dumps(
                    {
                        "timestamp": "2026-04-11T00:00:01+00:00",
                        "frame_id": "c0001:d1:f1",
                        "frame_depth": 1,
                        "step": 2,
                        "action": "run_validation",
                        "action_input": {},
                        "thinking": "直近の状態を検証する。",
                        "result": {"ok": False, "passed": False, "summary": "validation failed"},
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            (root / "state" / "attempts" / "c0001.prompts.jsonl").write_text(
                json.dumps(
                    {
                        "phase": "acting",
                        "step": 2,
                        "frame_id": "c0001:d1:f1",
                        "frame_depth": 1,
                        "model": "gemma4:26b",
                        "system_prompt": "system prompt body",
                        "user_prompt": "user prompt body",
                        "request": {
                            "transport": "ollama_chat",
                            "url": "http://127.0.0.1:11434/api/chat",
                            "request_body": '{"model":"gemma4:26b","stream":true,"messages":[{"role":"system","content":"system prompt body"},{"role":"user","content":"user prompt body"}]}',
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            write_json(
                root / "state" / "runtime" / "status.json",
                {
                    "status": "running",
                    "current_candidate_id": "c0001",
                    "current_runtime_kernel": "session_action_loop_v1",
                    "current_action": "run_validation",
                    "current_action_step": 2,
                    "current_task_stack": [
                        {
                            "frame_id": "c0001:d0:f0",
                            "parent_frame_id": None,
                            "depth": 0,
                            "goal": "親フレーム: 同じ失敗の反復を止める",
                            "context": {
                                "parent_context": {
                                    "goal": "自己改善ループを継続する",
                                    "why_this_frame_exists": "親レベルの失敗傾向を把握する",
                                },
                                "local_context": {
                                    "target_file": "agent/goal_logic.py",
                                    "search_mode": "constraint_probe",
                                    "selected_context": ["attempt:c0000"],
                                    "resolved_context_keys": ["attempt:c0000"],
                                },
                                "local_working_memory": {
                                    "observed_files": ["agent/goal_logic.py"],
                                    "observed_symbols": [],
                                    "observed_tests": [],
                                    "learned_findings": ["親で goal_logic.py を確認した"],
                                    "focus_candidates": ["render_operator_message"],
                                    "current_focus": "agent/goal_logic.py",
                                    "unresolved_questions": [],
                                    "done_criteria": [],
                                    "avoid_repeating": [],
                                },
                                "local_tool_results": [
                                    {"step": 1, "action": "read_file"},
                                ],
                                "child_return_payloads": [],
                                "return_payload": {},
                                "delta_context": {
                                    "latest_failure": {
                                        "error_type": "SyntaxError",
                                        "file": "/tmp/agent/goal_logic.py",
                                        "line": 182,
                                        "detail": "\"(\" was never closed",
                                    },
                                    "must_avoid_next": ["同じ場所を大きく壊さない"],
                                },
                            },
                            "commitment": "親レベルで失敗傾向を整理する",
                            "continue_or_return": {
                                "decision": "open_child_frame",
                                "reason": "局所問題へ分解したい",
                                "next_goal": "子フレームで局所差分を見直す",
                            },
                            "result": {"status": "delegated", "summary": "子フレームへ委譲"},
                        },
                        {
                            "frame_id": "c0001:d1:f1",
                            "parent_frame_id": "c0001:d0:f0",
                            "depth": 1,
                            "goal": "同じ失敗の反復を止めるために局所差分を見直す",
                            "context": {
                                "parent_context": {
                                    "goal": "親フレーム: 同じ失敗の反復を止める",
                                    "why_this_frame_exists": "同じ失敗を繰り返しているか確認する",
                                },
                                "local_context": {
                                    "target_file": "agent/goal_logic.py",
                                    "search_mode": "constraint_probe",
                                    "selected_context": ["attempt:c0000", "tests_context"],
                                    "resolved_context_keys": ["attempt:c0000", "tests_context"],
                                },
                                "inherited_context": {
                                    "ancestor_frame_ids": ["c0001:d0:f0"],
                                    "ancestor_goals": ["親フレーム: 同じ失敗の反復を止める"],
                                    "inherited_working_memory": {
                                        "learned_findings": ["親で goal_logic.py を確認した"],
                                    },
                                    "ancestor_tool_results": [{"step": 1, "action": "read_file"}],
                                    "ancestor_return_payloads": [],
                                },
                                "local_working_memory": {
                                    "observed_files": ["agent/goal_logic.py"],
                                    "observed_symbols": ["render_operator_message"],
                                    "observed_tests": [],
                                    "learned_findings": ["goal_logic.py を確認した", "render_operator_message を検索した"],
                                    "focus_candidates": ["render_operator_message"],
                                    "current_focus": "render_operator_message",
                                    "unresolved_questions": ["最小変更で止められるか"],
                                    "done_criteria": [],
                                    "avoid_repeating": ["同じ粒度の再読を繰り返さない"],
                                },
                                "local_tool_results": [
                                    {"step": 1, "action": "read_file"},
                                    {"step": 2, "action": "search_code"},
                                ],
                                "child_return_payloads": [],
                                "return_payload": {"summary": "局所差分の方針を親へ返せる"},
                                "delta_context": {
                                    "latest_failure": {
                                        "error_type": "SyntaxError",
                                        "file": "/tmp/agent/goal_logic.py",
                                        "line": 182,
                                        "detail": "\"(\" was never closed",
                                    },
                                    "must_avoid_next": ["同じ場所を大きく壊さない"],
                                },
                            },
                            "commitment": "局所失敗差分を確認し、同じ場所を壊さない最小変更を作る",
                            "continue_or_return": {
                                "decision": "continue_here",
                                "reason": "この階層で直接修正できる",
                                "next_goal": "最小変更を作る",
                            },
                            "result": {"status": "active", "summary": "フレーム実行中"},
                        },
                    ],
                },
            )

            server = create_dashboard_server(root, host="127.0.0.1", port=0)
            port = server.server_address[1]
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
            thread.start()
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                conn.request("GET", "/api/snapshot")
                response = conn.getresponse()
                snapshot = json.loads(response.read().decode("utf-8"))
                conn.close()
                self.assertEqual(len(snapshot["operator_insights"]), 3)
                self.assertEqual(snapshot["latest_completed_attempt"]["candidate_id"], "c0001")
                self.assertEqual(snapshot["latest_completed_attempt"]["validation_summary"], "validation failed")
                self.assertEqual(snapshot["latest_completed_attempt"]["decision_explanation"], "検証に失敗しました。validation failed")
                self.assertEqual(snapshot["latest_llm_timings"]["total_duration_ms"], 1200)
                self.assertIn("追加文脈選択", snapshot["current_stream_sections"])
                self.assertEqual(snapshot["current_stream_sections"]["追加文脈選択"], "ctx output")
                self.assertEqual(snapshot["current_stream_sections"]["自己診断"], "reflect output")
                self.assertEqual(snapshot["current_stream_sections"]["コード生成"], "code output")
                self.assertEqual(snapshot["latest_completed_attempt"]["selected_coding_model"], "devstral:latest")
                self.assertEqual(snapshot["runtime_status"]["current_runtime_kernel"], "session_action_loop_v1")
                self.assertEqual(snapshot["runtime_status"]["current_action"], "run_validation")
                self.assertEqual(snapshot["latest_runtime_kernel"], "session_action_loop_v1")
                self.assertEqual(len(snapshot["latest_session_events"]), 2)
                self.assertEqual(snapshot["latest_prompt_snapshot"]["model"], "gemma4:26b")
                self.assertEqual(snapshot["latest_prompt_snapshot"]["request"]["transport"], "ollama_chat")
                self.assertEqual(
                    snapshot["latest_context_frame"]["frame_goal"],
                    "同じ失敗の反復を止めるために局所差分を見直す",
                )
                self.assertEqual(
                    snapshot["latest_context_frame"]["question_to_answer"],
                    "同じ失敗を繰り返しているか確認する",
                )
                self.assertEqual(
                    snapshot["latest_context_frame"]["commitment"],
                    "局所失敗差分を確認し、同じ場所を壊さない最小変更を作る",
                )
                self.assertEqual(
                    snapshot["latest_context_frame"]["resolved_context_keys"],
                    ["attempt:c0000", "tests_context"],
                )
                self.assertEqual(snapshot["latest_context_frame"]["latest_failure"]["line"], 182)
                self.assertEqual(
                    snapshot["latest_context_frame"]["must_avoid_next"],
                    ["同じ場所を大きく壊さない"],
                )
                self.assertEqual(len(snapshot["task_hierarchy"]), 2)
                self.assertEqual(snapshot["task_hierarchy"][0]["goal"], "親フレーム: 同じ失敗の反復を止める")
                self.assertEqual(snapshot["task_hierarchy"][1]["goal"], "同じ失敗の反復を止めるために局所差分を見直す")
                self.assertEqual(snapshot["task_hierarchy"][1]["parent_frame_id"], "c0001:d0:f0")
                self.assertEqual(snapshot["task_hierarchy"][1]["search_mode"], "constraint_probe")
                self.assertTrue(snapshot["task_hierarchy"][1]["is_current"])
                self.assertFalse(snapshot["task_hierarchy"][0]["is_current"])
                self.assertEqual(len(snapshot["thought_action_chain"]), 2)
                self.assertEqual(snapshot["thought_action_chain_source"], "current_snapshot")
                self.assertEqual(snapshot["thought_action_chain"][0]["action"], "read_file")
                self.assertEqual(snapshot["thought_action_chain"][0]["next_action"], "run_validation")
                self.assertIn("まず対象を読む。", snapshot["thought_action_chain"][0]["thinking"])
                self.assertIn("agent/goal_logic.py", snapshot["thought_action_chain"][0]["result_text"])
                self.assertEqual(snapshot["thought_action_chain"][1]["frame_goal"], "同じ失敗の反復を止めるために局所差分を見直す")
                self.assertGreaterEqual(len(snapshot["thought_history"]), 1)
                self.assertIn(snapshot["thought_history"][0]["type"], {"frame_context", "return_to_parent", "open_child_frame"})
                self.assertEqual(snapshot["context_audit"]["checks"][0]["label"], "上位コンテキスト継承")
                self.assertEqual(snapshot["context_audit"]["checks"][0]["status"], "ok")
                self.assertIn("継承元フレーム 1 件", snapshot["context_audit"]["checks"][0]["detail"])
                self.assertEqual(snapshot["context_audit"]["checks"][1]["status"], "ok")
                self.assertIn("ローカル tool result 2 件", snapshot["context_audit"]["checks"][1]["detail"])
                self.assertEqual(snapshot["context_audit"]["checks"][3]["status"], "ok")
                self.assertIn("局所差分の方針を親へ返せる", snapshot["context_audit"]["checks"][3]["detail"])
                self.assertGreaterEqual(len(snapshot["implementation_notes"]), 4)
                self.assertEqual(snapshot["implementation_notes"][0]["title"], "参照選択")
                self.assertTrue(any(skill["skill_id"] == "decompose_problem" for skill in snapshot["system_skills"]))
                self.assertEqual(snapshot["recent_memos"][0]["title"], "構文エラー前に因果を絞る")
                self.assertEqual(snapshot["latest_self_memo"]["title"], "局所失敗差分を先に見る")

                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                conn.request("GET", "/")
                response = conn.getresponse()
                html = response.read().decode("utf-8")
                conn.close()
                self.assertIn("thinking:</strong> まず対象を読む。", html)
                self.assertIn("thought-history-source", html)
                self.assertIn("thought-history-current-event-count", html)
                self.assertIn("thought-history-current-event-presence", html)
                self.assertIn("action_input:</strong> {", html)
                self.assertIn("result:</strong> step 1: read_file -&gt; agent/goal_logic.py", html)
                self.assertIn("next:</strong> 次action=run_validation", html)
                self.assertIn("問題認識:</strong> 構文エラーが続いている", html)
                self.assertIn("1. P2 が自分で気づいていること", html)
                self.assertIn("2. 気づきはあるが、まだ前進に変わっていない", html)
                self.assertIn("3. 今の停滞パターン", html)
                self.assertIn("P2 の自己診断", html)
                self.assertIn("どんな代替を提案したか", html)
                self.assertIn("なぜクローンしたか", html)
                self.assertIn("追加文脈と局所失敗差分", html)
                self.assertIn("コンテキスト管理監査", html)
                self.assertIn("現在の階層コンテキスト", html)
                self.assertIn("LLM に渡した prompt", html)
                self.assertIn("request_body", html)
                self.assertIn("system prompt body", html)
                self.assertIn("user prompt body", html)
                self.assertIn("ollama_chat", html)
                self.assertIn("今回実装した内容", html)
                self.assertIn("システムスキルとメモ", html)
                self.assertIn("再帰フレーム", html)
                self.assertIn("役割別モデル構成", html)
                self.assertIn("システムスキル", html)
                self.assertIn("問題分解", html)
                self.assertIn("永続メモ", html)
                self.assertIn("構文エラー前に因果を絞る", html)
                self.assertIn("今回の自己メモ", html)
                self.assertIn("何を知るために読んだか", html)
                self.assertIn("検証に失敗しました。validation failed", html)
                self.assertIn("モデルのリアルタイム出力", html)
                self.assertIn("追加文脈選択", html)
                self.assertIn("自己診断", html)
                self.assertIn("コード生成", html)
                self.assertIn("今回選ばれたコーディングモデル", html)
                self.assertIn("現在kernel", html)
                self.assertIn("現在action", html)
                self.assertIn("session action/result 履歴", html)
                self.assertIn("全体構造", html)
                self.assertIn("各フレームの詳細", html)
                self.assertIn("思考履歴の階層表示", html)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_dashboard_renders_proposed_child_goals_from_invalid_open_child_frame(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            runtime_status_path = root / "state" / "runtime" / "status.json"
            frame = {
                "frame_id": "c0001:d0:f1",
                "parent_frame_id": None,
                "depth": 0,
                "goal": "迷路生成を仕上げる",
                "context": {
                    "local_context": {
                        "target_file": "agent/maze_cli.py",
                        "search_mode": "constraint_probe",
                    },
                    "local_working_memory": {
                        "current_focus": "tests/test_maze_cli.py",
                        "observed_files": ["tests/test_maze_cli.py"],
                        "observed_symbols": [],
                        "learned_findings": [],
                        "unresolved_questions": [],
                    },
                    "local_tool_results": [
                        {
                            "timestamp": now_iso(),
                            "frame_id": "c0001:d0:f1",
                            "frame_depth": 0,
                            "step": 3,
                            "action": "invalid_response",
                            "action_input": {
                                "proposed_action": "open_child_frame",
                                "proposed_action_input": {
                                    "next_goal": "迷路生成アルゴリズムを実装する",
                                    "child_goals": [
                                        "迷路生成アルゴリズムを実装する",
                                        "開始地点と終了地点を配置する",
                                    ],
                                },
                            },
                            "thinking": "child frame に分けたい",
                            "result": {
                                "ok": False,
                                "error": "runtime_validation_failed",
                                "proposed_action": "open_child_frame",
                                "proposed_action_input": {
                                    "next_goal": "迷路生成アルゴリズムを実装する",
                                    "child_goals": [
                                        "迷路生成アルゴリズムを実装する",
                                        "開始地点と終了地点を配置する",
                                    ],
                                },
                            },
                        }
                    ],
                },
                "continue_or_return": {"decision": "continue_here", "reason": "提案を見直す", "next_goal": ""},
                "result": {"status": "active", "summary": "フレーム実行中"},
            }
            write_json(
                runtime_status_path,
                {
                    "status": "running",
                    "current_candidate_id": "c0001",
                    "current_task_stack": [frame],
                    "phase": "acting",
                    "last_event": "action_invalid",
                    "updated_at": now_iso(),
                },
            )
            server = create_dashboard_server(root, host="127.0.0.1", port=0)
            port = server.server_address[1]
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
            thread.start()
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                conn.request("GET", "/")
                response = conn.getresponse()
                html = response.read().decode("utf-8")
                conn.close()
                self.assertEqual(response.status, 200)
                self.assertIn("提案child=迷路生成アルゴリズムを実装する | 開始地点と終了地点を配置する", html)
                self.assertIn("モデルは child goal として提案したが、runtime ではまだ確定していません。", html)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_dashboard_falls_back_to_attempt_frame_trace_for_thought_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            attempts_dir = root / "state" / "attempts"
            write_json(
                attempts_dir / "c0001.json",
                {
                    "candidate_id": "c0001",
                    "status": "failed",
                    "target_file": "agent/goal_logic.py",
                    "created_at": "2026-04-11T10:00:00+00:00",
                    "frame_trace": [
                        {
                            "frame_id": "c0001:d2:f3",
                            "depth": 2,
                            "goal": "局所ゴール",
                            "decision": "return_to_parent",
                            "result": {"status": "returned"},
                        },
                        {
                            "frame_id": "c0001:d1:f2",
                            "depth": 1,
                            "goal": "中間ゴール",
                            "decision": "open_child_frame",
                            "result": {"status": "delegated"},
                        },
                    ],
                },
            )
            write_json(
                root / "state" / "runtime" / "status.json",
                {
                    "status": "running",
                    "current_candidate_id": "c0002",
                    "current_task_stack": [
                        {
                            "frame_id": "c0002:d0:f1",
                            "depth": 0,
                            "goal": "最上位フレーム",
                        }
                    ],
                    "current_runtime_kernel": "session_action_loop_v1",
                },
            )
            write_json(
                attempts_dir / "c0002.json",
                {
                    "candidate_id": "c0002",
                    "status": "started",
                    "target_file": "agent/goal_logic.py",
                    "created_at": "2026-04-11T10:05:00+00:00",
                },
            )

            server = create_dashboard_server(root, host="127.0.0.1", port=0)
            port = server.server_address[1]
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
            thread.start()
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                conn.request("GET", "/api/snapshot")
                response = conn.getresponse()
                snapshot = json.loads(response.read().decode("utf-8"))
                conn.close()
                self.assertGreaterEqual(len(snapshot["thought_history"]), 2)
                self.assertEqual(snapshot["thought_history"][0]["type"], "recent_attempt_frame")
                self.assertEqual(snapshot["thought_history"][0]["frame_id"], "c0001:d2:f3")
                self.assertEqual(snapshot["thought_history"][1]["frame_id"], "c0001:d1:f2")
                self.assertEqual(snapshot["thought_history"][0]["summary"], "局所ゴール")
                self.assertEqual(snapshot["thought_history"][0]["outcome"], "returned")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_dashboard_uses_latest_completed_attempt_for_insights_when_newest_is_started(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            attempts_dir = root / "state" / "attempts"
            write_json(
                attempts_dir / "c0001.json",
                {
                    "candidate_id": "c0001",
                    "status": "rejected",
                    "target_file": "agent/goal_logic.py",
                    "decision_reason": "validation failed",
                    "pre_edit_reflection": {
                        "what_kept_happening": "構文エラーが続いた",
                        "what_this_suggests_about_my_search": "同じ壊し方を繰り返している",
                    },
                    "situation_report": {
                        "chosen_response": "まず構文エラーを止める",
                    },
                    "meta_diagnosis": {
                        "status": "stagnating",
                        "search_mode": "constraint_probe",
                        "observation_bundle": {
                            "target_histogram": {"agent/goal_logic.py": 5},
                            "recent_validation_summaries": ["validation failed: SyntaxError"],
                        },
                    },
                    "change_summary": {"added_lines": 1, "removed_lines": 2},
                },
            )
            write_json(
                attempts_dir / "c0002.json",
                {
                    "candidate_id": "c0002",
                    "status": "started",
                    "target_file": "agent/goal_logic.py",
                    "decision_reason": None,
                    "pre_edit_reflection": {
                        "what_kept_happening": "これは使われないはず",
                    },
                },
            )

            server = create_dashboard_server(root, host="127.0.0.1", port=0)
            port = server.server_address[1]
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
            thread.start()
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                conn.request("GET", "/api/snapshot")
                response = conn.getresponse()
                snapshot = json.loads(response.read().decode("utf-8"))
                conn.close()
                self.assertEqual(snapshot["latest_completed_attempt"]["candidate_id"], "c0001")
                self.assertIn("構文エラーが続いた", snapshot["operator_insights"][0]["body"])
                self.assertNotIn("これは使われないはず", snapshot["operator_insights"][0]["body"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
