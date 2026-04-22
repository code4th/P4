from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from p4_core.config_defaults import DEFAULT_TOOL_CONTENT_CHUNK_BYTES
from p4_core.frames import FrameManager
from p4_core.runtime import AgentRuntime
from p4_core.workspace import WorkspacePaths, append_session_event, bootstrap_workspace, read_json, read_jsonl, write_json


class FakeBackend:
    def __init__(self, responses: list[str], *, models: list[str] | None = None) -> None:
        self.responses = list(responses)
        self.models = list(models or [])
        self.messages_seen: list[list[dict[str, str]]] = []

    def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        options: dict | None = None,
        timeout_seconds: int = 180,
    ) -> dict:
        self.messages_seen.append(messages)
        del options
        del timeout_seconds
        if not self.responses:
            raise AssertionError("no fake responses left")
        return {"model": model, "content": self.responses.pop(0), "raw": {}}

    def chat_stream(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        options: dict | None = None,
        timeout_seconds: int = 180,
    ) -> list[dict]:
        del model
        self.messages_seen.append(messages)
        del options
        del timeout_seconds
        if not self.responses:
            raise AssertionError("no fake responses left")
        response = self.responses.pop(0)
        return [{"message": {"content": response}, "done": True}]

    def list_models(self) -> dict:
        return {"models": [{"name": name} for name in self.models]}


class StreamingMetadataBackend(FakeBackend):
    def __init__(self, chunks: list[dict]) -> None:
        super().__init__([])
        self.chunks = list(chunks)

    def iter_chat_stream(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        options: dict | None = None,
        timeout_seconds: int = 180,
    ):
        del model
        self.messages_seen.append(messages)
        del options
        del timeout_seconds
        for chunk in self.chunks:
            yield chunk


class SequencedStreamingBackend(FakeBackend):
    def __init__(self, attempts: list[list[dict]]) -> None:
        super().__init__([])
        self.attempts = [list(chunks) for chunks in attempts]

    def iter_chat_stream(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        options: dict | None = None,
        timeout_seconds: int = 180,
    ):
        del model
        self.messages_seen.append(messages)
        del options
        del timeout_seconds
        if not self.attempts:
            raise AssertionError("no fake stream attempts left")
        for chunk in self.attempts.pop(0):
            yield chunk


def work_package(
    goal: str,
    *,
    work_type: str = "inspect",
    tool: str = "list_files",
    args: dict | None = None,
    evidence: str = "observable result",
) -> dict:
    return {
        "goal": goal,
        "work_type": work_type,
        "first_action": {"tool": tool, "args": dict(args or {})},
        "success_evidence": evidence,
        "why_not_direct_action": "isolate this child-frame responsibility for the test",
        "context_summary": "test context",
        "done_when": evidence,
    }


class RuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.original_semantic_check = AgentRuntime._semantic_grounding_check

        def smart_mock(self_ref, *, final_answer: str, evidence_text: str, user_message: str) -> bool:
            # Tests expect grounding failures in these specific cases
            if "ghost-output" in final_answer: return False
            if "fail" in final_answer.lower() or "bad" in final_answer.lower(): return False
            if "pwd and ls" in final_answer and "ls" not in evidence_text: return False
            return True

        AgentRuntime._semantic_grounding_check = smart_mock  # type: ignore[assignment]

    def tearDown(self) -> None:
        AgentRuntime._semantic_grounding_check = self.original_semantic_check  # type: ignore[assignment]
        super().tearDown()

    def test_runtime_processes_file_tools_and_finishes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            config = read_json(WorkspacePaths(root).config_path, fallback={})
            config.setdefault("runtime", {})["dedicated_llm_workspace"] = False
            write_json(WorkspacePaths(root).config_path, config)
            (root / "notes.txt").write_text("alpha\nbeta\n", encoding="utf-8")
            backend = FakeBackend(
                [
                    '{"assistant_message":"Inspecting file","tool_name":"read_file","tool_args":{"path":"notes.txt"}}',
                    '{"assistant_message":"Writing result","tool_name":"write_file","tool_args":{"path":"result.txt","content":"completed"}}',
                    '{"assistant_message":"Done","tool_name":"finish","tool_args":{"final_answer":"completed successfully"}}',
                ]
            )
            runtime = AgentRuntime(root, llm_backend=backend)
            result = runtime.send_message("Read notes and write result.txt", run_immediately=True)

            self.assertTrue(result["ok"])
            self.assertEqual((root / "result.txt").read_text(encoding="utf-8"), "completed")
            events = read_jsonl(WorkspacePaths(root).session_events_path("main"))
            event_types = [row["type"] for row in events]
            self.assertIn("user_message", event_types)
            self.assertIn("tool_call", event_types)
            self.assertIn("tool_result", event_types)
            self.assertIn("finish", event_types)
            status = read_json(WorkspacePaths(root).runtime_status_path, fallback={})
            self.assertEqual(status["status"], "idle")

    def test_file_tools_support_chunked_writes_and_exact_replacements(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            backend = FakeBackend(
                [
                    '{"assistant_message":"start","tool_name":"write_file","tool_args":{"path":"app.py","content":"print(\\""}}',
                    '{"assistant_message":"append","tool_name":"append_file","tool_args":{"path":"app.py","content":"hello\\")\\n"}}',
                    '{"assistant_message":"replace","tool_name":"replace_text","tool_args":{"path":"app.py","old_text":"hello","new_text":"hi"}}',
                    '{"assistant_message":"done","tool_name":"finish","tool_args":{"final_answer":"app.py updated"}}',
                ]
            )
            runtime = AgentRuntime(root, llm_backend=backend)
            runtime.send_message("app.py を作成して更新して", run_immediately=True)
            status = read_json(WorkspacePaths(root).runtime_status_path, fallback={})
            workspace = Path(status["last_llm_workspace"])
            self.assertTrue(str(workspace).startswith(str(WorkspacePaths(root).llm_runs_dir)))
            self.assertEqual((workspace / "app.py").read_text(encoding="utf-8"), 'print("hi")\n')

    def test_write_file_rejects_large_single_json_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            backend = FakeBackend(
                [
                    json.dumps(
                        {
                            "assistant_message": "write large",
                            "tool_name": "write_file",
                            "tool_args": {"path": "large.txt", "content": "x" * 2100},
                        }
                    ),
                ]
            )
            config = read_json(WorkspacePaths(root).config_path, fallback={})
            config.setdefault("runtime", {})["max_steps_per_message"] = 1
            write_json(WorkspacePaths(root).config_path, config)
            runtime = AgentRuntime(root, llm_backend=backend)
            runtime.send_message("large.txt を作成して", run_immediately=True)
            events = read_jsonl(WorkspacePaths(root).session_events_path("main"))
            result = next(event for event in events if event["type"] == "tool_result")
            payload = json.loads(result["content"])
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["suggested_tool"], "append_file")
            self.assertEqual(payload["max_bytes"], DEFAULT_TOOL_CONTENT_CHUNK_BYTES)

    def test_tool_content_chunk_budget_is_single_runtime_config_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            config = read_json(WorkspacePaths(root).config_path, fallback={})
            config.setdefault("runtime", {})["max_steps_per_message"] = 1
            config["runtime"]["tool_content_chunk_bytes"] = 32
            write_json(WorkspacePaths(root).config_path, config)
            backend = FakeBackend(
                [
                    json.dumps(
                        {
                            "assistant_message": "write over budget",
                            "tool_name": "write_file",
                            "tool_args": {"path": "large.txt", "content": "x" * 33},
                        }
                    ),
                ]
            )
            runtime = AgentRuntime(root, llm_backend=backend)
            self.assertEqual(runtime.tool_content_chunk_bytes, 32)
            prompt = runtime._system_prompt()
            self.assertIn("最大 32 UTF-8 bytes", prompt)
            self.assertIn("行境界", prompt)
            self.assertIn("次ステップで append_file", prompt)
            self.assertIn("既存ファイル全体を write_file で再生成しない", prompt)
            self.assertIn("open_child_frame", prompt)
            runtime.send_message("large.txt を作成して", run_immediately=True)
            events = read_jsonl(WorkspacePaths(root).session_events_path("main"))
            result = next(event for event in events if event["type"] == "tool_result")
            payload = json.loads(result["content"])
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["max_bytes"], 32)
            self.assertIn("line-boundary file chunk", payload["error"])

    def test_json_repair_prompt_handles_length_truncation_with_chunk_rules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            config = read_json(WorkspacePaths(root).config_path, fallback={})
            config.setdefault("runtime", {})["json_retry_limit"] = 1
            config["runtime"]["tool_content_chunk_bytes"] = 64
            write_json(WorkspacePaths(root).config_path, config)
            backend = StreamingMetadataBackend(
                [
                    {
                        "message": {"content": '{"assistant_message":"too long","tool_name":"write_file","tool_args":{"path":"x.py","content":"print('},
                        "done": True,
                        "done_reason": "length",
                    },
                    {
                        "message": {"content": '{"assistant_message":"chunk","tool_name":"write_file","tool_args":{"path":"x.py","content":"print(1)\\n"}}'},
                        "done": True,
                    },
                ]
            )
            runtime = AgentRuntime(root, llm_backend=backend)
            result = runtime._chat_with_repair(role="reasoning", model="fake", prompt="x.py を作成")

            self.assertEqual(result["attempt_count"], 2)
            retry_messages = backend.messages_seen[-1]
            repair_prompt = retry_messages[-1]["content"]
            self.assertIn("Do not continue the previous text", repair_prompt)
            self.assertIn("64 UTF-8 bytes", repair_prompt)
            self.assertIn("line-boundary chunk", repair_prompt)
            self.assertIn("Always close the JSON object", repair_prompt)

    def test_runtime_records_prompt_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            backend = FakeBackend(
                [
                    '{"assistant_message":"Short answer","tool_name":"finish","tool_args":{"final_answer":"ok"}}',
                ]
            )
            runtime = AgentRuntime(root, llm_backend=backend)
            runtime.send_message("Give me a short answer", run_immediately=True)
            prompts = read_jsonl(WorkspacePaths(root).session_prompts_path("main"))
            self.assertEqual(len(prompts), 1)
            self.assertIn("現在の目標", prompts[0]["prompt"])
            self.assertIn("現在のユーザー依頼", prompts[0]["prompt"])
            self.assertIn("model", prompts[0])

    def test_action_prompt_filters_stale_assistant_and_observer_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            append_session_event(root, "main", {"type": "user_message", "role": "user", "content": "古い別タスク"})
            append_session_event(root, "main", {"type": "assistant_message", "role": "assistant", "content": "BROKEN_LONG_CODE"})
            append_session_event(root, "main", {"type": "observer_note", "role": "observer", "content": "実況解説の長文"})
            backend = FakeBackend(
                [
                    '{"assistant_message":"ok","tool_name":"finish","tool_args":{"final_answer":"ok"}}',
                ]
            )
            runtime = AgentRuntime(root, llm_backend=backend)
            runtime.send_message("現在のタスクだけ扱って", run_immediately=True)
            prompts = read_jsonl(WorkspacePaths(root).session_prompts_path("main"))
            prompt = prompts[-1]["prompt"]
            self.assertIn("現在のタスクだけ扱って", prompt)
            self.assertNotIn("BROKEN_LONG_CODE", prompt)
            self.assertNotIn("実況解説の長文", prompt)
            self.assertNotIn("古い別タスク", prompt)

    def test_status_snapshot_exposes_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            backend = FakeBackend(
                [
                    '{"assistant_message":"Working on it","tool_name":"finish","tool_args":{"final_answer":"done"}}',
                ]
            )
            runtime = AgentRuntime(root, llm_backend=backend)
            runtime.send_message("hello", run_immediately=True)
            snapshot = runtime.status_snapshot()
            self.assertIn("recent_transcript", snapshot)
            self.assertGreaterEqual(len(snapshot["recent_transcript"]), 2)
            self.assertEqual(snapshot["last_reply"]["type"], "finish")

    def test_runtime_persists_turn_metadata_and_tool_observability(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            backend = FakeBackend(
                [
                    '{"assistant_message":"Running command","tool_name":"run_command","tool_args":{"command":"printf hello"}}',
                    '{"assistant_message":"done","tool_name":"finish","tool_args":{"final_answer":"ok"}}',
                ]
            )
            runtime = AgentRuntime(root, llm_backend=backend)
            runtime.send_message("run a command", run_immediately=True)
            events = read_jsonl(WorkspacePaths(root).session_events_path("main"))
            tool_call = next(event for event in events if event["type"] == "tool_call")
            tool_result = next(event for event in events if event["type"] == "tool_result")
            self.assertTrue(tool_call["turn_id"])
            self.assertEqual(tool_call["turn_id"], tool_result["turn_id"])
            payload = json.loads(tool_result["content"])
            self.assertEqual(payload["tool"], "run_command")
            self.assertIn("duration_ms", payload)
            self.assertIn("cwd", payload)
            self.assertIn("shell", payload)
            meta = read_json(WorkspacePaths(root).session_meta_path("main"), fallback={})
            self.assertIn("last_tool_result", meta)
            self.assertIn("last_event_summary", meta)

    def test_runtime_retries_invalid_json_once_and_records_telemetry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            backend = FakeBackend(
                [
                    "not json yet",
                    '{"assistant_message":"ok","tool_name":"finish","tool_args":{"final_answer":"done"}}',
                ]
            )
            runtime = AgentRuntime(root, llm_backend=backend)
            runtime.send_message("say hi", run_immediately=True)
            status = read_json(WorkspacePaths(root).runtime_status_path, fallback={})
            self.assertEqual(status["last_llm_attempt_count"], 2)
            self.assertIsNotNone(status["last_llm_duration_ms"])
            self.assertIn("last_llm_raw_preview", status)

    def test_runtime_tracks_current_stream_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            backend = FakeBackend(
                [
                    '{"assistant_message":"hello","tool_name":"finish","tool_args":{"final_answer":"hello"}}',
                ]
            )
            runtime = AgentRuntime(root, llm_backend=backend)
            runtime.send_message("say hello", run_immediately=True)
            status = read_json(WorkspacePaths(root).runtime_status_path, fallback={})
            self.assertIn("current_stream_text", status)
            self.assertIn("last_llm_raw_preview", status)
            self.assertIn("current_phase", status)
            self.assertIsNone(status["current_started_at"])
            self.assertIsNotNone(status["current_finished_at"])

    def test_runtime_blocks_finish_when_requested_commands_not_executed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            backend = FakeBackend(
                [
                    '{"assistant_message":"run pwd","tool_name":"run_command","tool_args":{"command":"pwd","shell":"bash"}}',
                    '{"assistant_message":"done","tool_name":"finish","tool_args":{"final_answer":"listed files"}}',
                    '{"assistant_message":"run ls","tool_name":"run_command","tool_args":{"command":"ls","shell":"bash"}}',
                    '{"assistant_message":"done","tool_name":"finish","tool_args":{"final_answer":"pwd and ls completed"}}',
                ]
            )
            runtime = AgentRuntime(root, llm_backend=backend)
            runtime._fast_path_envelope = lambda **_: None  # type: ignore[method-assign]
            runtime.send_message("pwd を実行して、その後 ls を実行して要約して", run_immediately=True)
            events = read_jsonl(WorkspacePaths(root).session_events_path("main"))
            system_notes = [event for event in events if event["type"] == "system_note"]
            self.assertTrue(any(note.get("code") == "finish_blocked" for note in system_notes))
            self.assertTrue(any(note.get("reason_code") == "missing_required_commands" for note in system_notes))
            finish = next(event for event in reversed(events) if event["type"] == "finish")
            self.assertIn("pwd:", finish["content"])
            self.assertIn("pwd", finish["content"])
            self.assertIn("ls", finish["content"])

    def test_run_command_rejects_chained_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            config = read_json(WorkspacePaths(root).config_path, fallback={})
            config.setdefault("runtime", {})["max_steps_per_message"] = 1
            write_json(WorkspacePaths(root).config_path, config)
            backend = FakeBackend(
                [
                    '{"assistant_message":"run both","tool_name":"run_command","tool_args":{"command":"pwd && ls","shell":"bash"}}',
                ]
            )
            runtime = AgentRuntime(root, llm_backend=backend)
            runtime._fast_path_envelope = lambda **_: None  # type: ignore[method-assign]
            result = runtime.send_message("pwd を実行して、その後 ls を実行して", run_immediately=True)
            events = read_jsonl(WorkspacePaths(root).session_events_path("main"))
            tool_results = [event for event in events if event["type"] == "tool_result"]
            payloads = [json.loads(row["content"]) for row in tool_results]
            denied = next(payload for payload in payloads if not payload["ok"])
            self.assertIn("exactly one command per step", denied["error"])
            self.assertFalse(result["run"]["last_result"]["ok"])

    def test_runtime_records_reflection_after_failed_turn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            config = read_json(WorkspacePaths(root).config_path, fallback={})
            config.setdefault("runtime", {})["max_steps_per_message"] = 1
            write_json(WorkspacePaths(root).config_path, config)
            backend = FakeBackend(
                [
                    '{"assistant_message":"done","tool_name":"finish","tool_args":{"final_answer":"`ghost-output` was seen"}}',
                ]
            )
            runtime = AgentRuntime(root, llm_backend=backend)
            result = runtime.send_message("結果を確認して", run_immediately=True)
            self.assertFalse(result["run"]["last_result"]["ok"])
            reflections = read_jsonl(WorkspacePaths(root).reflections_path)
            self.assertEqual(len(reflections), 1)
            self.assertIn("失敗パターン", reflections[0]["reflection"])
            self.assertIn("failure_class", reflections[0])
            status = read_json(WorkspacePaths(root).runtime_status_path, fallback={})
            self.assertIn("失敗パターン", str(status.get("last_reflection") or ""))

    def test_runtime_records_grounding_judge_trace_when_finish_is_blocked(self) -> None:
        original = AgentRuntime._semantic_grounding_check
        AgentRuntime._semantic_grounding_check = self.original_semantic_check  # type: ignore[assignment]
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                bootstrap_workspace(root, force=True)
                config = read_json(WorkspacePaths(root).config_path, fallback={})
                config.setdefault("runtime", {})["max_steps_per_message"] = 1
                write_json(WorkspacePaths(root).config_path, config)
                backend = FakeBackend(
                    [
                        '{"assistant_message":"done","tool_name":"finish","tool_args":{"final_answer":"ghost-output"}}',
                        "1. Analyze first, but never emit OK",
                    ]
                )
                runtime = AgentRuntime(root, llm_backend=backend)
                result = runtime.send_message("結果を確認して", run_immediately=True)
                self.assertFalse(result["run"]["last_result"]["ok"])
                events = read_jsonl(WorkspacePaths(root).session_events_path("main"))
                judge = next(event for event in events if event.get("code") == "grounding_judge")
                self.assertEqual(judge.get("reason_code"), "invalid_json")
                self.assertIn("INVALID_OUTPUT", judge["content"])
                self.assertEqual(judge["details"]["raw_response"], "1. Analyze first, but never emit OK")
                blocked = next(event for event in events if event.get("code") == "finish_blocked")
                self.assertEqual(blocked["reason_code"], "judge_invalid_output")
                self.assertEqual(blocked["details"]["judge"]["decision"], "invalid_json")
        finally:
            AgentRuntime._semantic_grounding_check = original  # type: ignore[assignment]

    def test_grounding_judge_accepts_structured_json_verdict(self) -> None:
        original = AgentRuntime._semantic_grounding_check
        AgentRuntime._semantic_grounding_check = self.original_semantic_check  # type: ignore[assignment]
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                bootstrap_workspace(root, force=True)
                runtime = AgentRuntime(
                    root,
                    llm_backend=FakeBackend([
                        '{"verdict":"ok","reason_code":"supported","unsupported_claims":[],"rationale":"stdoutと一致"}'
                    ]),
                )
                ok = runtime._semantic_grounding_check(
                    final_answer="cwd is /tmp",
                    evidence_text='{"stdout":"/tmp"}',
                    user_message="pwdを確認して",
                )
                self.assertTrue(ok)
                self.assertEqual(runtime._last_grounding_judge_trace["decision"], "ok")
                self.assertEqual(runtime._last_grounding_judge_trace["parsed"]["verdict"], "ok")
        finally:
            AgentRuntime._semantic_grounding_check = original  # type: ignore[assignment]

    def test_passive_observer_records_step_commentary_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            config = read_json(WorkspacePaths(root).config_path, fallback={})
            config.setdefault("runtime", {})["max_steps_per_message"] = 1
            config.setdefault("runtime", {})["observer_enabled"] = True
            write_json(WorkspacePaths(root).config_path, config)
            backend = FakeBackend(
                [
                    '{"assistant_message":"run pwd","tool_name":"run_command","tool_args":{"command":"pwd","shell":"bash"}}',
                    "1. 起きたこと: pwdを実行しました。\n2. 気になる点: まだ完了していません。\n3. 次に確認すべきこと: 出力を回答に反映すること。",
                ]
            )
            runtime = AgentRuntime(root, llm_backend=backend)
            runtime._fast_path_envelope = lambda **_: None  # type: ignore[method-assign]
            runtime.send_message("pwdを実行して", run_immediately=True)
            events = read_jsonl(WorkspacePaths(root).session_events_path("main"))
            observer = next(event for event in events if event.get("type") == "observer_note")
            self.assertEqual(observer.get("code"), "live_commentator")
            self.assertIn("起きたこと", observer["content"])

    def test_observer_records_llm_output_issue_before_tool_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            config = read_json(WorkspacePaths(root).config_path, fallback={})
            config.setdefault("runtime", {})["max_steps_per_message"] = 1
            config.setdefault("runtime", {})["observer_enabled"] = True
            config.setdefault("runtime", {})["json_retry_limit"] = 0
            write_json(WorkspacePaths(root).config_path, config)
            backend = FakeBackend(["I will create the maze script and run it."])
            runtime = AgentRuntime(root, llm_backend=backend)
            runtime.send_message("サンプルプログラムを作成して実行して表示して", run_immediately=True)
            events = read_jsonl(WorkspacePaths(root).session_events_path("main"))
            self.assertTrue(any(event.get("code") == "llm_output_issue" for event in events))
            observer = next(
                event
                for event in events
                if event.get("type") == "observer_note"
                and event.get("reason_code") == "llm_output_issue_commentary"
            )
            self.assertIn("tool_call JSON", observer["content"])

    def test_llm_output_issue_classifies_length_truncated_stream(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            config = read_json(WorkspacePaths(root).config_path, fallback={})
            config.setdefault("runtime", {})["max_steps_per_message"] = 1
            config.setdefault("runtime", {})["json_retry_limit"] = 0
            config.setdefault("runtime", {})["thinking_only_repair_limit"] = 0
            write_json(WorkspacePaths(root).config_path, config)
            backend = StreamingMetadataBackend(
                [
                    {"message": {"content": '{"assistant_message":"writing","tool_name":"write_file","tool_args":{"path":"maze_gen.py","content":"import random\\n'}},
                    {"done": True, "done_reason": "length", "eval_count": 384},
                ]
            )
            runtime = AgentRuntime(root, llm_backend=backend)
            runtime.send_message("maze_gen.py を作成して", run_immediately=True)
            status = read_json(WorkspacePaths(root).runtime_status_path, fallback={})
            self.assertEqual(status["last_llm_parse_issue"], "length_truncated")
            self.assertEqual(status["last_llm_stream_metadata"]["done_reason"], "length")
            events = read_jsonl(WorkspacePaths(root).session_events_path("main"))
            issue = next(event for event in events if event.get("code") == "llm_output_issue")
            self.assertEqual(issue["reason_code"], "length_truncated")

    def test_streaming_thinking_is_not_parsed_as_tool_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            config = read_json(WorkspacePaths(root).config_path, fallback={})
            config.setdefault("runtime", {})["max_steps_per_message"] = 1
            config.setdefault("runtime", {})["json_retry_limit"] = 0
            write_json(WorkspacePaths(root).config_path, config)
            backend = StreamingMetadataBackend(
                [
                    {"message": {"thinking": "I should plan before answering. "}},
                    {"message": {"content": '{"assistant_message":"ok","tool_name":"finish","tool_args":{"final_answer":"done"}}'}, "done": True},
                ]
            )
            runtime = AgentRuntime(root, llm_backend=backend)
            result = runtime.send_message("say done", run_immediately=True)
            self.assertTrue(result["ok"])
            status = read_json(WorkspacePaths(root).runtime_status_path, fallback={})
            self.assertEqual(status["last_llm_raw_preview"], '{"assistant_message":"ok","tool_name":"finish","tool_args":{"final_answer":"done"}}')
            self.assertIn("I should plan", status["last_llm_thinking_preview"])
            events = read_jsonl(WorkspacePaths(root).session_events_path("main"))
            self.assertFalse(any(event.get("code") == "llm_output_issue" for event in events))
            assistant = next(event for event in events if event.get("type") == "assistant_message")
            self.assertEqual(assistant["content"], "ok")

    def test_thinking_only_stream_records_block_evidence_without_parsing_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            config = read_json(WorkspacePaths(root).config_path, fallback={})
            config.setdefault("runtime", {})["max_steps_per_message"] = 1
            config.setdefault("runtime", {})["json_retry_limit"] = 0
            write_json(WorkspacePaths(root).config_path, config)
            backend = StreamingMetadataBackend(
                [
                    {"message": {"thinking": "Goal: write a maze script, then run it."}, "done": True},
                ]
            )
            runtime = AgentRuntime(root, llm_backend=backend)
            runtime.send_message("迷路を作成して実行して表示して", run_immediately=True)
            status = read_json(WorkspacePaths(root).runtime_status_path, fallback={})
            self.assertEqual(status["last_llm_parse_issue"], "thinking_only_output")
            self.assertEqual(status["last_llm_raw_preview"], "")
            self.assertIn("maze script", status["last_llm_thinking_preview"])
            events = read_jsonl(WorkspacePaths(root).session_events_path("main"))
            issue = next(event for event in events if event.get("code") == "llm_output_issue")
            self.assertEqual(issue["reason_code"], "thinking_only_output")
            self.assertEqual(issue["details"]["parse_target"], "content")
            self.assertEqual(issue["details"]["raw_text"], "")
            self.assertIn("maze script", issue["details"]["thinking_text"])
            self.assertIn("[thinking]", issue["details"]["combined_text"])

    def test_thinking_only_stream_gets_one_visible_json_repair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            config = read_json(WorkspacePaths(root).config_path, fallback={})
            config.setdefault("runtime", {})["max_steps_per_message"] = 2
            config.setdefault("runtime", {})["json_retry_limit"] = 0
            config.setdefault("runtime", {})["thinking_only_repair_limit"] = 1
            write_json(WorkspacePaths(root).config_path, config)
            backend = SequencedStreamingBackend(
                [
                    [{"message": {"thinking": "The Beatles and Bon Jovi are different bands."}, "done": True}],
                    [{"message": {"content": '{"analysis":"direct answer","assistant_message":"別のバンドです。","tool_name":"final_answer","tool_args":{"answer":"ビートルズとボン・ジョヴィは別のバンドです。"}}'}, "done": True}],
                ]
            )
            runtime = AgentRuntime(root, llm_backend=backend)
            runtime._fast_path_envelope = lambda **_: None  # type: ignore[method-assign]
            result = runtime.send_message("ビートルズってボンジョビ？", run_immediately=True)
            self.assertTrue(result["ok"])
            self.assertEqual(result["run"]["last_result"]["final_answer"], "ビートルズとボン・ジョヴィは別のバンドです。")
            self.assertEqual(len(backend.messages_seen), 2)
            self.assertIn("assistant content was empty", backend.messages_seen[1][-1]["content"])
            events = read_jsonl(WorkspacePaths(root).session_events_path("main"))
            self.assertFalse(any(event.get("code") == "llm_output_issue" for event in events))
            finish = next(event for event in events if event.get("type") == "finish")
            self.assertEqual(finish["content"], "ビートルズとボン・ジョヴィは別のバンドです。")

    def test_extract_requested_commands_handles_japanese_connectors_and_git(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            runtime = AgentRuntime(root, llm_backend=FakeBackend([]))
            commands = runtime._extract_requested_commands("pwd を実行して、その後 git status を実行して、続けて ls を実行して")
            self.assertEqual(commands, ["pwd", "git status", "ls"])

    def test_terminal_model_falls_back_to_available_terminal_chain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            backend = FakeBackend([], models=["qwen3-coder:latest", "gemma4:26b"])
            runtime = AgentRuntime(root, llm_backend=backend)
            selected = runtime._resolve_terminal_model("devstral:latest")
            self.assertEqual(selected, "qwen3-coder:latest")

    def test_runtime_blocks_repeated_successful_command_loop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            backend = FakeBackend(
                [
                    '{"assistant_message":"run pwd","tool_name":"run_command","tool_args":{"command":"pwd","shell":"bash"}}',
                    '{"assistant_message":"run pwd again","tool_name":"run_command","tool_args":{"command":"pwd","shell":"bash"}}',
                    '{"assistant_message":"done","tool_name":"finish","tool_args":{"final_answer":"/tmp/path"}}',
                ]
            )
            runtime = AgentRuntime(root, llm_backend=backend)
            runtime._controller_terminal_finish = lambda **_: None  # type: ignore[method-assign]
            runtime.send_message("pwd を実行して、結果だけ返して", run_immediately=True)
            events = read_jsonl(WorkspacePaths(root).session_events_path("main"))
            system_notes = [event for event in events if event["type"] == "system_note"]
            self.assertTrue(any(note.get("code") == "command_blocked" for note in system_notes))
            self.assertTrue(any(note.get("reason_code") == "repeated_command" for note in system_notes))

    def test_runtime_blocks_repeated_failed_command_loop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            backend = FakeBackend(
                [
                    '{"assistant_message":"run bad","tool_name":"run_command","tool_args":{"command":"false","shell":"bash"}}',
                    '{"assistant_message":"retry bad","tool_name":"run_command","tool_args":{"command":"false","shell":"bash"}}',
                    '{"assistant_message":"done","tool_name":"finish","tool_args":{"final_answer":"stopped after failure"}}',
                ]
            )
            runtime = AgentRuntime(root, llm_backend=backend)
            runtime._controller_terminal_finish = lambda **_: None  # type: ignore[method-assign]
            runtime.send_message("失敗するコマンドを実行して確認して", run_immediately=True)
            events = read_jsonl(WorkspacePaths(root).session_events_path("main"))
            system_notes = [event for event in events if event["type"] == "system_note"]
            self.assertTrue(any(note.get("code") == "command_blocked" for note in system_notes))
            self.assertTrue(any(note.get("reason_code") == "repeated_command" for note in system_notes))
            self.assertTrue(any(note.get("code") == "command_failed" for note in system_notes))
            self.assertTrue(any(note.get("reason_code") == "recovery_guidance" for note in system_notes))

    def test_terminal_fast_path_executes_first_requested_command_without_initial_llm_step(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            backend = FakeBackend(
                [
                    '{"assistant_message":"done","tool_name":"finish","tool_args":{"final_answer":"ok"}}',
                ]
            )
            runtime = AgentRuntime(root, llm_backend=backend)
            runtime.run_terminal_agent("pwd を実行して、結果だけ返して", model="devstral:latest", shell_name="bash")
            events = read_jsonl(WorkspacePaths(root).session_events_path("main"))
            tool_call = next(event for event in events if event["type"] == "tool_call")
            self.assertEqual(tool_call["tool_name"], "run_command")
            self.assertEqual(tool_call["tool_args"]["command"], "pwd")

    def test_terminal_agent_uses_llm_for_maze_requests_instead_of_fixed_scaffold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            backend = FakeBackend(
                [
                    '{"assistant_message":"writing","tool_name":"write_file","tool_args":{"path":"maze_gen.py","content":"print(\\"MODEL_MAZE\\")\\n"}}',
                    '{"assistant_message":"running","tool_name":"run_command","tool_args":{"command":"python3 maze_gen.py","shell":"bash"}}',
                ]
            )
            runtime = AgentRuntime(root, llm_backend=backend)
            result = runtime.run_terminal_agent("迷路を実装して実行し表示して結果を見せて", model="devstral:latest", shell_name="bash")
            self.assertTrue(result["run"]["last_result"]["ok"])
            status = read_json(WorkspacePaths(root).runtime_status_path, fallback={})
            workspace = Path(status["last_llm_workspace"])
            self.assertTrue((workspace / "maze_gen.py").exists())
            self.assertEqual((workspace / "maze_gen.py").read_text(), 'print("MODEL_MAZE")\n')
            events = read_jsonl(WorkspacePaths(root).session_events_path("main"))
            tool_calls = [event for event in events if event["type"] == "tool_call"]
            self.assertEqual([event["tool_name"] for event in tool_calls], ["write_file", "run_command"])
            self.assertTrue(any(event["type"] == "assistant_message" and "writing" in event["content"] for event in events))
            run_result = next(
                json.loads(event["content"])
                for event in events
                if event["type"] == "tool_result" and event["tool_name"] == "run_command"
            )
            self.assertTrue(run_result["ok"])
            self.assertIn("MODEL_MAZE", run_result["stdout"])

    def test_terminal_fast_path_continues_with_next_missing_command_before_llm_finish(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            backend = FakeBackend(
                [
                    '{"assistant_message":"done","tool_name":"finish","tool_args":{"final_answer":"pwd and ls completed"}}',
                ]
            )
            runtime = AgentRuntime(root, llm_backend=backend)
            runtime.run_terminal_agent("pwd を実行して、その後 ls を実行して要約して", model="devstral:latest", shell_name="bash")
            events = read_jsonl(WorkspacePaths(root).session_events_path("main"))
            tool_calls = [event for event in events if event["type"] == "tool_call" and event["tool_name"] == "run_command"]
            self.assertEqual([event["tool_args"]["command"] for event in tool_calls], ["pwd", "ls"])
            finish = next(event for event in reversed(events) if event["type"] == "finish")
            self.assertIn("pwd:", finish["content"])
            self.assertIn("pwd", finish["content"])
            self.assertIn("ls", finish["content"])

    def test_terminal_controller_finish_uses_grounded_evidence_without_second_llm_turn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            backend = FakeBackend(
                [
                    '{"assistant_message":"done","tool_name":"finish","tool_args":{"final_answer":"ignored"}}',
                ]
            )
            runtime = AgentRuntime(root, llm_backend=backend)
            runtime.run_terminal_agent("pwd を実行して、結果だけ短く返して", model="devstral:latest", shell_name="bash")
            events = read_jsonl(WorkspacePaths(root).session_events_path("main"))
            finish = next(event for event in reversed(events) if event["type"] == "finish")
            self.assertIn(str(root), finish["content"])
            system_notes = [event for event in events if event["type"] == "system_note"]
            self.assertTrue(any(note.get("code") == "controller_finish" for note in system_notes))
            self.assertEqual(len(backend.responses), 1)

    def test_terminal_controller_finish_handles_multi_command_grounding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            backend = FakeBackend(
                [
                    '{"assistant_message":"done","tool_name":"finish","tool_args":{"final_answer":"ignored"}}',
                ]
            )
            runtime = AgentRuntime(root, llm_backend=backend)
            runtime.run_terminal_agent("pwd を実行して、その後 ls を実行して要約して", model="devstral:latest", shell_name="bash")
            events = read_jsonl(WorkspacePaths(root).session_events_path("main"))
            finish = next(event for event in reversed(events) if event["type"] == "finish")
            self.assertIn("pwd:", finish["content"])
            self.assertIn("ls:", finish["content"])

    def test_controller_finish_handles_git_status_then_pwd_without_false_unexecuted_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            runtime = AgentRuntime(root, llm_backend=FakeBackend([]))
            steps = [
                {
                    "tool_name": "run_command",
                    "tool_result": {
                        "ok": True,
                        "tool": "run_command",
                        "command": "git status",
                        "shell": "bash",
                        "cwd": "/Users/satojunichi/Documents/openclaw",
                        "returncode": 0,
                        "stdout": "On branch main\nChanges not staged for commit:\n  modified: p4-core/p4_core/runtime.py\n",
                        "stderr": "",
                    },
                },
                {
                    "tool_name": "run_command",
                    "tool_result": {
                        "ok": True,
                        "tool": "run_command",
                        "command": "pwd",
                        "shell": "bash",
                        "cwd": "/Users/satojunichi/Documents/openclaw",
                        "returncode": 0,
                        "stdout": "/Users/satojunichi/Documents/openclaw\n",
                        "stderr": "",
                    },
                },
            ]
            answer = runtime._controller_terminal_finish(
                selection={"role": "terminal"},
                goal_text="",
                user_message="git status を実行して、その後 pwd を実行して、どのディレクトリで status を見たか短く返して",
                steps=steps,
            )
            self.assertIsNotNone(answer)
            self.assertIn("/Users/satojunichi/Documents/openclaw", str(answer))

    def test_finish_blocked_when_expected_artifact_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            config = read_json(WorkspacePaths(root).config_path, fallback={})
            config.setdefault("runtime", {})["max_steps_per_message"] = 1
            write_json(WorkspacePaths(root).config_path, config)
            backend = FakeBackend(
                [
                    '{"assistant_message":"done","tool_name":"finish","tool_args":{"final_answer":"created maze_gen.py"}}',
                ]
            )
            runtime = AgentRuntime(root, llm_backend=backend)
            result = runtime.send_message("maze_gen.py を作成して", run_immediately=True)
            self.assertFalse(result["run"]["last_result"]["ok"])
            events = read_jsonl(WorkspacePaths(root).session_events_path("main"))
            system_notes = [event for event in events if event["type"] == "system_note"]
            self.assertTrue(any(note.get("code") == "finish_blocked" for note in system_notes))
            self.assertTrue(any(note.get("reason_code") == "missing_expected_artifacts" for note in system_notes))

    def test_successful_turn_clears_stale_runtime_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            paths = WorkspacePaths(root)
            write_json(
                paths.runtime_status_path,
                {
                    **read_json(paths.runtime_status_path, fallback={}),
                    "status": "idle",
                    "last_error": "old error",
                    "last_system_note": "old note",
                },
            )
            backend = FakeBackend(
                [
                    '{"assistant_message":"done","tool_name":"finish","tool_args":{"final_answer":"ok"}}',
                ]
            )
            runtime = AgentRuntime(root, llm_backend=backend)
            runtime.send_message("short answer", run_immediately=True)
            status = read_json(paths.runtime_status_path, fallback={})
            self.assertIsNone(status.get("last_error"))
            self.assertIsNone(status.get("last_system_note"))

    def test_run_until_idle_clears_stale_current_fields_when_queue_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            paths = WorkspacePaths(root)
            write_json(
                paths.runtime_status_path,
                {
                    **read_json(paths.runtime_status_path, fallback={}),
                    "status": "running",
                    "current_turn_id": "stale-turn",
                    "current_queue_id": "stale-queue",
                    "current_user_message": "stale message",
                    "current_prompt_preview": "stale prompt",
                    "current_stream_text": "stale stream",
                    "current_plan": "stale plan",
                    "current_phase": "DISCOVER_REQUIRED_COMMANDS",
                    "current_started_at": "2026-04-18T00:00:00Z",
                },
            )
            runtime = AgentRuntime(root, llm_backend=FakeBackend([]))
            result = runtime.run_until_idle()
            self.assertEqual(result["processed"], 0)
            status = read_json(paths.runtime_status_path, fallback={})
            self.assertEqual(status["status"], "idle")
            self.assertIsNone(status.get("current_turn_id"))
            self.assertIsNone(status.get("current_queue_id"))
            self.assertIsNone(status.get("current_user_message"))
            self.assertIsNone(status.get("current_prompt_preview"))
            self.assertEqual(status.get("current_stream_text"), "")
            self.assertIsNone(status.get("current_plan"))
            self.assertIsNone(status.get("current_phase"))
            self.assertIsNone(status.get("current_started_at"))

    def test_frame_open_return_keeps_child_events_out_of_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            (root / "notes.txt").write_text("alpha\n", encoding="utf-8")
            config = read_json(WorkspacePaths(root).config_path, fallback={})
            config.setdefault("runtime", {})["dedicated_llm_workspace"] = False
            write_json(WorkspacePaths(root).config_path, config)
            backend = FakeBackend(
                [
                    json.dumps({"assistant_message": "split", "tool_name": "open_child_frame", "tool_args": {"work_package": work_package("Read notes", tool="read_file", args={"path": "notes.txt"}, evidence="notes content read")}}),
                    '{"assistant_message":"read","tool_name":"read_file","tool_args":{"path":"notes.txt"}}',
                    '{"assistant_message":"bad finish","tool_name":"finish","tool_args":{"final_answer":"alpha"}}',
                    '{"assistant_message":"return","tool_name":"return_to_parent","tool_args":{"summary":"notes contain alpha","findings":["alpha"]}}',
                    '{"assistant_message":"done","tool_name":"finish","tool_args":{"final_answer":"notes contain alpha"}}',
                ]
            )
            runtime = AgentRuntime(root, llm_backend=backend)
            result = runtime.send_message("notesを階層分解して確認して", run_immediately=True)

            self.assertTrue(result["ok"])
            events = read_jsonl(WorkspacePaths(root).session_events_path("main"))
            event_types = [event["type"] for event in events]
            self.assertIn("frame_opened", event_types)
            self.assertIn("frame_returned", event_types)
            self.assertIn("child_return", event_types)
            self.assertEqual(event_types.count("child_return"), 1)
            frames = list(runtime.frame_manager.frames.values())
            root_frame = next(frame for frame in frames if frame.depth == 0)
            child_frame = next(frame for frame in frames if frame.depth == 1)
            self.assertEqual(child_frame.status, "returned")
            self.assertTrue(any(event.get("type") == "tool_result" for event in child_frame.session_events))
            self.assertFalse(any(event.get("type") == "tool_result" for event in root_frame.session_events))
            self.assertTrue(any(event.get("type") == "child_return" for event in root_frame.session_events))

    def test_controller_finish_is_blocked_inside_child_frame(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            config = read_json(WorkspacePaths(root).config_path, fallback={})
            config.setdefault("runtime", {})["max_steps_per_message"] = 4
            write_json(WorkspacePaths(root).config_path, config)
            backend = FakeBackend(
                [
                    json.dumps({"assistant_message": "open", "tool_name": "open_child_frame", "tool_args": {"work_package": work_package("pwd child", work_type="run_test", tool="run_command", args={"command": "pwd", "shell": "bash"}, evidence="pwd output collected")}}),
                    '{"assistant_message":"run","tool_name":"run_command","tool_args":{"command":"pwd","shell":"bash"}}',
                    '{"assistant_message":"return","tool_name":"return_to_parent","tool_args":{"summary":"pwd collected","findings":["pwd"]}}',
                    '{"assistant_message":"done","tool_name":"finish","tool_args":{"final_answer":"pwd collected"}}',
                ]
            )
            runtime = AgentRuntime(
                root,
                llm_backend=backend,
            )
            runtime.router.models["terminal"] = "devstral:latest"
            runtime.send_message("open_child_frame を使って pwd を実行して", run_immediately=True)

            events = read_jsonl(WorkspacePaths(root).session_events_path("main"))
            child_return = [event for event in events if event.get("type") == "child_return"]
            finishes = [event for event in events if event.get("type") == "finish"]
            self.assertEqual(len(child_return), 1)
            self.assertEqual(len(finishes), 1)
            self.assertEqual(runtime.frame_manager.current_frame().depth, 0)
            self.assertEqual(runtime.frame_manager.snapshot()["metrics"]["child_return"], 1)

    def test_parent_can_plan_multiple_child_tasks_and_process_next_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            config = read_json(WorkspacePaths(root).config_path, fallback={})
            config.setdefault("runtime", {})["max_steps_per_message"] = 6
            write_json(WorkspacePaths(root).config_path, config)
            backend = FakeBackend(
                [
                    json.dumps(
                        {
                            "assistant_message": "split into child tasks",
                            "tool_name": "decompose_tasks",
                            "tool_args": {
                                "rationale": "separate discovery from execution",
                                "tasks": [
                                    {
                                        "goal": "discover maze requirements",
                                        "work_type": "inspect",
                                        "first_action": {"tool": "list_files", "args": {"path": "."}},
                                        "success_evidence": "requirements are summarized",
                                        "why_not_direct_action": "keep discovery isolated from implementation",
                                        "context_summary": "Need constraints before writing code",
                                        "done_when": "requirements are summarized",
                                    },
                                    {
                                        "goal": "implement and run maze",
                                        "work_type": "edit",
                                        "first_action": {"tool": "write_file", "args": {"path": "maze.py", "content": "print('maze')\n"}},
                                        "success_evidence": "execution output is collected",
                                        "why_not_direct_action": "implementation and execution are a separate responsibility",
                                        "context_summary": "Use discovered requirements",
                                        "done_when": "execution output is collected",
                                    },
                                ],
                            },
                        }
                    ),
                    '{"assistant_message":"return discovery","tool_name":"return_to_parent","tool_args":{"summary":"requirements collected","findings":["ascii maze"]}}',
                    '{"assistant_message":"open next","tool_name":"open_child_frame","tool_args":{}}',
                    '{"assistant_message":"return execution","tool_name":"return_to_parent","tool_args":{"summary":"maze executed","findings":["output collected"]}}',
                    '{"assistant_message":"done","tool_name":"finish","tool_args":{"final_answer":"requirements collected and maze executed"}}',
                ]
            )
            runtime = AgentRuntime(root, llm_backend=backend)
            result = runtime.send_message("迷路作成を良い単位で分割して実行して", run_immediately=True)

            self.assertTrue(result["ok"])
            events = read_jsonl(WorkspacePaths(root).session_events_path("main"))
            self.assertEqual([event.get("type") for event in events].count("task_plan"), 1)
            self.assertEqual([event.get("type") for event in events].count("child_return"), 2)
            root_frame = next(frame for frame in runtime.frame_manager.frames.values() if frame.depth == 0)
            self.assertEqual(len(root_frame.working_memory.child_tasks), 2)
            self.assertEqual(len(root_frame.working_memory.completed_child_tasks), 2)
            self.assertEqual(root_frame.working_memory.completed_child_tasks[0]["task_id"], "task-1")
            self.assertEqual(root_frame.working_memory.completed_child_tasks[1]["task_id"], "task-2")
            self.assertIsNone(runtime.frame_manager.next_pending_child_task(root_frame))

    def test_open_child_frame_rejects_goal_without_work_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            config = read_json(WorkspacePaths(root).config_path, fallback={})
            config.setdefault("runtime", {})["max_steps_per_message"] = 1
            write_json(WorkspacePaths(root).config_path, config)
            backend = FakeBackend(
                [
                    '{"assistant_message":"abstract split","tool_name":"open_child_frame","tool_args":{"goal":"understand runtime","context_summary":"too vague"}}',
                ]
            )
            runtime = AgentRuntime(root, llm_backend=backend)
            runtime.send_message("抽象的な子フレームを開いて", run_immediately=True)

            events = read_jsonl(WorkspacePaths(root).session_events_path("main"))
            invalid = [event for event in events if event.get("code") == "work_package_invalid"]
            self.assertEqual(len(invalid), 1)
            self.assertIn("work_type", invalid[0]["content"])
            self.assertFalse(any(event.get("type") == "frame_opened" for event in events))

    def test_decompose_tasks_rejects_task_without_first_action_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            config = read_json(WorkspacePaths(root).config_path, fallback={})
            config.setdefault("runtime", {})["max_steps_per_message"] = 1
            write_json(WorkspacePaths(root).config_path, config)
            backend = FakeBackend(
                [
                    json.dumps(
                        {
                            "assistant_message": "bad split",
                            "tool_name": "decompose_tasks",
                            "tool_args": {
                                "tasks": [
                                    {
                                        "goal": "understand runtime.py",
                                        "context_summary": "abstract analysis",
                                    }
                                ]
                            },
                        }
                    ),
                ]
            )
            runtime = AgentRuntime(root, llm_backend=backend)
            runtime.send_message("runtime.pyを分解して理解して", run_immediately=True)

            events = read_jsonl(WorkspacePaths(root).session_events_path("main"))
            invalid = [event for event in events if event.get("code") == "work_package_invalid"]
            self.assertEqual(len(invalid), 1)
            self.assertIn("first_action.tool", invalid[0]["content"])
            self.assertFalse(any(event.get("type") == "task_plan" for event in events))
            self.assertFalse(any(event.get("type") == "frame_opened" for event in events))

    def test_frame_action_goals_are_not_extracted_as_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            runtime = AgentRuntime(root, llm_backend=FakeBackend([]))
            commands = runtime._extract_requested_commands(
                '必ず open_child_frame を使い、goal は "pwd evidence child"、context_summary は "verify child return" にしてください。子フレームでは run_command で pwd を実行してください。'
            )
            self.assertEqual(commands, ["pwd"])

    def test_frame_depth_limit_blocks_opening_beyond_depth_four(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            config = read_json(WorkspacePaths(root).config_path, fallback={})
            config.setdefault("runtime", {})["max_steps_per_message"] = 6
            write_json(WorkspacePaths(root).config_path, config)
            backend = FakeBackend(
                [
                    json.dumps({"assistant_message": "open1", "tool_name": "open_child_frame", "tool_args": {"work_package": work_package("d1")}}),
                    json.dumps({"assistant_message": "open2", "tool_name": "open_child_frame", "tool_args": {"work_package": work_package("d2")}}),
                    json.dumps({"assistant_message": "open3", "tool_name": "open_child_frame", "tool_args": {"work_package": work_package("d3")}}),
                    json.dumps({"assistant_message": "open4", "tool_name": "open_child_frame", "tool_args": {"work_package": work_package("d4")}}),
                    json.dumps({"assistant_message": "open5", "tool_name": "open_child_frame", "tool_args": {"work_package": work_package("d5")}}),
                    '{"assistant_message":"return","tool_name":"return_to_parent","tool_args":{"summary":"blocked","findings":["depth"]}}',
                ]
            )
            runtime = AgentRuntime(root, llm_backend=backend)
            runtime.send_message("深く分解して", run_immediately=True)

            events = read_jsonl(WorkspacePaths(root).session_events_path("main"))
            blocked = [event for event in events if event.get("code") == "frame_open_blocked"]
            self.assertEqual(len(blocked), 1)
            self.assertIn("depth limit", blocked[0]["content"])

    def test_child_frame_step_safety_valve_returns_to_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            config = read_json(WorkspacePaths(root).config_path, fallback={})
            config.setdefault("runtime", {})["max_steps_per_message"] = 18
            write_json(WorkspacePaths(root).config_path, config)
            responses = [
                json.dumps({"assistant_message": "open", "tool_name": "open_child_frame", "tool_args": {"work_package": work_package("loop child", evidence="safety valve")}}),
            ]
            responses.extend(
                '{"assistant_message":"list","tool_name":"list_files","tool_args":{"path":"."}}'
                for _ in range(16)
            )
            runtime = AgentRuntime(root, llm_backend=FakeBackend(responses))
            runtime.send_message("子フレームのstep safety valveを確認して", run_immediately=True)

            events = read_jsonl(WorkspacePaths(root).session_events_path("main"))
            child_returns = [event for event in events if event.get("type") == "child_return"]
            self.assertEqual(len(child_returns), 1)
            self.assertIn("ステップ上限", child_returns[0]["content"])
            self.assertEqual(runtime.frame_manager.current_frame().depth, 0)

    def test_frame_working_memory_updates_and_persists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            (root / "notes.txt").write_text("alpha\n", encoding="utf-8")
            config = read_json(WorkspacePaths(root).config_path, fallback={})
            config.setdefault("runtime", {})["dedicated_llm_workspace"] = False
            config.setdefault("runtime", {})["max_steps_per_message"] = 2
            write_json(WorkspacePaths(root).config_path, config)
            backend = FakeBackend(
                [
                    '{"assistant_message":"read","tool_name":"read_file","tool_args":{"path":"notes.txt"}}',
                    '{"assistant_message":"fail","tool_name":"run_command","tool_args":{"command":"missing_command_xyz","shell":"bash"}}',
                ]
            )
            runtime = AgentRuntime(root, llm_backend=backend)
            runtime.send_message("notesを読み、失敗コマンドも確認して", run_immediately=True)

            frame = runtime.frame_manager.current_frame()
            self.assertIsNotNone(frame)
            observations = frame.working_memory.observations
            self.assertTrue(any("read_file: notes.txt" in item for item in observations))
            self.assertIn("missing_command_xyz", frame.working_memory.avoid_repeating)
            reloaded = FrameManager(root)
            reloaded_frame = reloaded.current_frame()
            self.assertIsNotNone(reloaded_frame)
            self.assertEqual(reloaded_frame.working_memory.avoid_repeating, frame.working_memory.avoid_repeating)


if __name__ == "__main__":
    unittest.main()
