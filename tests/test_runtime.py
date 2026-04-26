from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from p4_core.config_defaults import DEFAULT_TOOL_CONTENT_CHUNK_BYTES
from p4_core.frames import FrameManager
from p4_core.runtime import AgentRuntime
from p4_core.schemas import FINISH_ACCEPTANCE_SCHEMA, JUDGE_VERDICT_SCHEMA, TOOL_ACTION_SCHEMA
from p4_core.workspace import WorkspacePaths, append_session_event, bootstrap_workspace, read_json, read_jsonl, write_json


class FakeBackend:
    def __init__(self, responses: list[str], *, models: list[str] | None = None) -> None:
        self.responses = list(responses)
        self.models = list(models or [])
        self.messages_seen: list[list[dict[str, str]]] = []
        self.options_seen: list[dict] = []

    def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        options: dict | None = None,
        timeout_seconds: int = 180,
    ) -> dict:
        self.messages_seen.append(messages)
        self.options_seen.append(dict(options or {}))
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
        self.options_seen.append(dict(options or {}))
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
        self.options_seen.append(dict(options or {}))
        del timeout_seconds
        for chunk in self.chunks:
            yield chunk

    def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        options: dict | None = None,
        timeout_seconds: int = 180,
    ) -> dict:
        self.messages_seen.append(messages)
        self.options_seen.append(dict(options or {}))
        del timeout_seconds
        content_parts: list[str] = []
        thinking_parts: list[str] = []
        raw: dict = {}
        for chunk in self.chunks:
            raw = dict(chunk)
            message = chunk.get("message") or {}
            content_parts.append(str(message.get("content") or ""))
            thinking_parts.append(str(message.get("thinking") or ""))
        return {
            "model": model,
            "content": "".join(content_parts),
            "content_text": "".join(content_parts),
            "thinking_text": "".join(thinking_parts),
            "raw": raw,
        }


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
        self.options_seen.append(dict(options or {}))
        del timeout_seconds
        if not self.attempts:
            raise AssertionError("no fake stream attempts left")
        for chunk in self.attempts.pop(0):
            yield chunk

    def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        options: dict | None = None,
        timeout_seconds: int = 180,
    ) -> dict:
        self.messages_seen.append(messages)
        self.options_seen.append(dict(options or {}))
        del timeout_seconds
        if not self.attempts:
            raise AssertionError("no fake stream attempts left")
        content_parts: list[str] = []
        thinking_parts: list[str] = []
        raw: dict = {}
        for chunk in self.attempts.pop(0):
            raw = dict(chunk)
            message = chunk.get("message") or {}
            content_parts.append(str(message.get("content") or ""))
            thinking_parts.append(str(message.get("thinking") or ""))
        return {
            "model": model,
            "content": "".join(content_parts),
            "content_text": "".join(content_parts),
            "thinking_text": "".join(thinking_parts),
            "raw": raw,
        }


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
            self.assertIn("付け焼き刃の局所対応で塞がず", prompt)
            self.assertIn("局所整合で閉じる形を優先してください", prompt)
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
            backend = SequencedStreamingBackend(
                [
                    [
                        {
                            "message": {"content": '{"assistant_message":"too long","tool_name":"write_file","tool_args":{"path":"x.py","content":"print('},
                            "done": True,
                            "done_reason": "length",
                        },
                    ],
                    [
                        {
                            "message": {"content": '{"assistant_message":"chunk","tool_name":"write_file","tool_args":{"path":"x.py","content":"print(1)\\n"}}'},
                            "done": True,
                        },
                    ],
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

    def test_tool_action_generation_uses_json_schema_format(self) -> None:
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
            self.assertEqual(backend.options_seen[0]["format"], TOOL_ACTION_SCHEMA)
            self.assertFalse(backend.options_seen[0]["think"])
            status = read_json(WorkspacePaths(root).runtime_status_path, fallback={})
            self.assertTrue(status["raw_output_is_machine_json"])
            self.assertTrue(status["schema_validation_ok"])

    def test_machine_control_uses_nonstream_structured_call(self) -> None:
        class Backend(StreamingMetadataBackend):
            def __init__(self) -> None:
                super().__init__(
                    [
                        {
                            "message": {
                                "content": '{"assistant_message":"hello","tool_name":"finish","tool_args":{"final_answer":"hello"}}'
                            },
                            "done": True,
                        }
                    ]
                )
                self.stream_called = False

            def iter_chat_stream(self, **kwargs):  # type: ignore[no-untyped-def]
                self.stream_called = True
                return super().iter_chat_stream(**kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            backend = Backend()
            runtime = AgentRuntime(root, llm_backend=backend)
            result = runtime.send_message("say hello", run_immediately=True)
            self.assertTrue(result["ok"])
            self.assertFalse(backend.stream_called)
            events = read_jsonl(WorkspacePaths(root).session_events_path("main"))
            response = next(event for event in events if event.get("event_name") == "llm_response_received")
            self.assertEqual(response["details"]["transport"], "chat_nonstream")

    def test_tool_action_schema_validation_rejects_extra_top_level_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            config = read_json(WorkspacePaths(root).config_path, fallback={})
            config.setdefault("runtime", {})["json_retry_limit"] = 0
            write_json(WorkspacePaths(root).config_path, config)
            backend = FakeBackend(
                [
                    '{"assistant_message":"hello","tool_name":"finish","tool_args":{"final_answer":"hello"},"extra":"bad"}',
                ]
            )
            runtime = AgentRuntime(root, llm_backend=backend)
            runtime.send_message("say hello", run_immediately=True)
            status = read_json(WorkspacePaths(root).runtime_status_path, fallback={})
            self.assertEqual(status["last_llm_parse_issue"], "schema_validation_failed")
            self.assertFalse(status["last_llm_schema_validation"]["ok"])
            self.assertTrue(status["raw_output_is_machine_json"])
            self.assertFalse(status["schema_validation_ok"])

    def test_tool_action_retries_json_wrapped_in_markdown_fence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            config = read_json(WorkspacePaths(root).config_path, fallback={})
            config.setdefault("runtime", {})["json_retry_limit"] = 1
            write_json(WorkspacePaths(root).config_path, config)
            backend = FakeBackend(
                [
                    '```json\n{"assistant_message":"hello","tool_name":"finish","tool_args":{"final_answer":"hello"}}\n```',
                    '{"assistant_message":"hello","tool_name":"finish","tool_args":{"final_answer":"hello"}}',
                ]
            )
            runtime = AgentRuntime(root, llm_backend=backend)
            result = runtime.send_message("say hello", run_immediately=True)
            self.assertTrue(result["ok"])
            self.assertEqual(result["run"]["last_result"]["final_answer"], "hello")
            self.assertEqual(read_json(WorkspacePaths(root).runtime_status_path, fallback={})["last_llm_attempt_count"], 2)

    def test_markdown_wrapped_json_records_raw_machine_json_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            config = read_json(WorkspacePaths(root).config_path, fallback={})
            config.setdefault("runtime", {})["json_retry_limit"] = 0
            write_json(WorkspacePaths(root).config_path, config)
            backend = FakeBackend(
                [
                    '```json\n{"assistant_message":"hello","tool_name":"finish","tool_args":{"final_answer":"hello"}}\n```',
                ]
            )
            runtime = AgentRuntime(root, llm_backend=backend)
            runtime.send_message("say hello", run_immediately=True)
            status = read_json(WorkspacePaths(root).runtime_status_path, fallback={})
            self.assertEqual(status["last_llm_parse_issue"], "json_extraneous_text")
            self.assertFalse(status["raw_output_is_machine_json"])
            self.assertTrue(status["schema_validation_ok"])

    def test_runtime_identity_query_bypasses_agent_loop_and_grounding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            backend = FakeBackend([])
            runtime = AgentRuntime(root, llm_backend=backend)
            result = runtime.send_message("お名前は？", run_immediately=True)
            self.assertTrue(result["ok"])
            self.assertEqual(result["route"], "runtime_identity")
            self.assertEqual(backend.messages_seen, [])
            status = read_json(WorkspacePaths(root).runtime_status_path, fallback={})
            self.assertEqual(status["current_role"], "runtime_profile")
            self.assertIsNone(status["last_llm_parse_issue"])
            events = read_jsonl(WorkspacePaths(root).session_events_path("main"))
            assistant = next(event for event in events if event.get("type") == "assistant_message")
            self.assertEqual(assistant["reason_code"], "runtime_profile_identity")

    def test_terminal_runtime_identity_preserves_run_result_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            backend = FakeBackend([])
            runtime = AgentRuntime(root, llm_backend=backend)
            result = runtime.run_terminal_agent("お名前は？", model="gemma4:26b", shell_name="zsh")
            self.assertTrue(result["ok"])
            self.assertEqual(result["route"], "runtime_identity")
            self.assertEqual(result["run"]["processed"], 1)
            self.assertTrue(result["run"]["last_result"]["ok"])
            self.assertEqual(result["run"]["last_result"]["route"], "runtime_identity")
            self.assertEqual(result["run"]["last_result"]["final_answer"], "私はP4、ローカルエージェントランタイムです。")
            self.assertEqual(backend.messages_seen, [])

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

    def test_runtime_logs_structured_llm_lifecycle_events(self) -> None:
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

            events = read_jsonl(WorkspacePaths(root).session_events_path("main"))
            runtime_events = [event for event in events if event.get("type") == "runtime_event"]
            names = [event.get("event_name") for event in runtime_events]

            self.assertIn("llm_call_started", names)
            self.assertIn("llm_response_received", names)
            self.assertIn("llm_call_finished", names)
            response = next(event for event in runtime_events if event.get("event_name") == "llm_response_received")
            self.assertEqual(response["details"]["transport"], "chat_nonstream")
            finished = next(event for event in runtime_events if event.get("event_name") == "llm_call_finished")
            self.assertEqual(finished["details"]["content_text"], '{"assistant_message":"hello","tool_name":"finish","tool_args":{"final_answer":"hello"}}')
            self.assertEqual(finished["step_index"], 1)

    def test_runtime_logs_machine_control_response_as_single_nonstream_observation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            backend = StreamingMetadataBackend(
                [
                    {"message": {"content": '{"assistant_message":"'}, "done": False},
                    {"message": {"content": "hello"}, "done": False},
                    {"message": {"content": '","tool_name":"finish","tool_args":{"final_answer":"hello"}}'}, "done": True},
                ]
            )
            runtime = AgentRuntime(root, llm_backend=backend)
            runtime.send_message("say hello", run_immediately=True)

            events = read_jsonl(WorkspacePaths(root).session_events_path("main"))
            self.assertFalse(any(event.get("event_name") == "llm_stream_chunk" for event in events))
            response = next(
                event
                for event in events
                if event.get("type") == "runtime_event" and event.get("event_name") == "llm_response_received"
            )
            self.assertEqual(
                response["details"]["content_text"],
                '{"assistant_message":"hello","tool_name":"finish","tool_args":{"final_answer":"hello"}}',
            )
            finished = next(
                event
                for event in events
                if event.get("type") == "runtime_event" and event.get("event_name") == "llm_call_finished"
            )
            self.assertIn('"final_answer":"hello"', finished["details"]["content_text"])

    def test_runtime_logs_structured_tool_lifecycle_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            backend = FakeBackend(
                [
                    '{"assistant_message":"write","tool_name":"write_file","tool_args":{"path":"hello.txt","content":"hello\\n"}}',
                    '{"assistant_message":"done","tool_name":"finish","tool_args":{"final_answer":"hello.txt created"}}',
                ]
            )
            runtime = AgentRuntime(root, llm_backend=backend)
            runtime.send_message("`hello.txt` を作って", run_immediately=True)

            events = read_jsonl(WorkspacePaths(root).session_events_path("main"))
            runtime_events = [event for event in events if event.get("type") == "runtime_event"]
            names = [event.get("event_name") for event in runtime_events]

            self.assertIn("tool_call_started", names)
            self.assertIn("tool_call_finished", names)
            finished = next(event for event in runtime_events if event.get("event_name") == "tool_call_finished")
            self.assertEqual(finished["details"]["tool_name"], "write_file")
            self.assertTrue(finished["details"]["tool_result"]["ok"])

    def test_runtime_creates_operation_for_cli_queue_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            paths = WorkspacePaths(root)
            backend = FakeBackend(
                [
                    '{"assistant_message":"hello","tool_name":"finish","tool_args":{"final_answer":"hello"}}',
                ]
            )
            runtime = AgentRuntime(root, llm_backend=backend)
            runtime.send_message("say hello", run_immediately=True)

            events = read_jsonl(paths.session_events_path("main"))
            operations = [event for event in events if event.get("type") == "operation"]
            self.assertEqual([event.get("status") for event in operations], ["running", "finished"])
            operation_id = str(operations[0].get("operation_id") or "")
            self.assertTrue(operation_id)
            tagged = [event for event in events if event.get("type") in {"user_message", "planning_note", "runtime_event", "assistant_message", "finish"}]
            self.assertTrue(tagged)
            self.assertTrue(all(event.get("operation_id") == operation_id for event in tagged))
            runtime_status = read_json(paths.runtime_status_path, fallback={})
            self.assertIsNone(runtime_status.get("current_operation_id"))

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
            result = runtime.send_message("pwd を実行して、その後 ls を実行して", run_immediately=True)
            events = read_jsonl(WorkspacePaths(root).session_events_path("main"))
            tool_results = [event for event in events if event["type"] == "tool_result"]
            payloads = [json.loads(row["content"]) for row in tool_results]
            denied = next(payload for payload in payloads if not payload["ok"])
            self.assertIn("exactly one command per step", denied["error"])
            self.assertFalse(result["run"]["last_result"]["ok"])

    def test_run_command_normalizes_python_to_python3_when_python_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            (root / "script.py").write_text("print('ok')\n", encoding="utf-8")
            runtime = AgentRuntime(root, llm_backend=FakeBackend([]))
            result = runtime.tools.execute("run_command", {"command": "python script.py", "shell": "bash"})
            if result["ok"]:
                self.assertEqual(result["command"], "python3 script.py")
                self.assertEqual(result["normalized_from"], "python script.py")
                self.assertEqual(result["stdout"].strip(), "ok")

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
                self.assertEqual(runtime._last_grounding_judge_trace["attempts"][0]["options"]["format"], JUDGE_VERDICT_SCHEMA)
                self.assertFalse(runtime._last_grounding_judge_trace["attempts"][0]["options"]["think"])
        finally:
            AgentRuntime._semantic_grounding_check = original  # type: ignore[assignment]

    def test_grounding_judge_rejects_schema_invalid_verdict(self) -> None:
        original = AgentRuntime._semantic_grounding_check
        AgentRuntime._semantic_grounding_check = self.original_semantic_check  # type: ignore[assignment]
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                bootstrap_workspace(root, force=True)
                runtime = AgentRuntime(
                    root,
                    llm_backend=FakeBackend([
                        '{"verdict":"maybe","reason_code":"supported","unsupported_claims":[],"rationale":"曖昧"}',
                    ]),
                )
                ok = runtime._semantic_grounding_check(
                    final_answer="cwd is /tmp",
                    evidence_text='{"stdout":"/tmp"}',
                    user_message="pwdを確認して",
                )
                self.assertFalse(ok)
                self.assertEqual(runtime._last_grounding_judge_trace["decision"], "invalid_output")
                self.assertFalse(runtime._last_grounding_judge_trace["attempts"][0]["schema_validation"]["ok"])
        finally:
            AgentRuntime._semantic_grounding_check = original  # type: ignore[assignment]

    def test_finish_acceptance_review_uses_json_schema_format(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            backend = FakeBackend([
                '{"status":"success","reason_code":"supported","rationale":"観測と一致","observed_mismatch":""}',
            ])
            runtime = AgentRuntime(root, llm_backend=backend)
            review = runtime._semantic_finish_acceptance_review(
                user_message="結果を表示して",
                final_answer="ok",
                evidence_text='[{"tool_name":"run_command","ok":true,"stdout":"ok"}]',
            )
            self.assertEqual(review["status"], "success")
            self.assertEqual(review["attempts"][0]["options"]["format"], FINISH_ACCEPTANCE_SCHEMA)
            self.assertFalse(review["attempts"][0]["options"]["think"])

    def test_grounding_judge_invalid_json_does_not_block_general_knowledge_chat(self) -> None:
        original = AgentRuntime._semantic_grounding_check
        AgentRuntime._semantic_grounding_check = self.original_semantic_check  # type: ignore[assignment]
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                bootstrap_workspace(root, force=True)
                runtime = AgentRuntime(
                    root,
                    llm_backend=FakeBackend(["1. Analyze first, but never emit JSON"]),
                )
                ok = runtime._semantic_grounding_check(
                    final_answer="いいえ、ビートルズとボンジョビは別のバンドです。",
                    evidence_text="",
                    user_message="ビートルズってボンジョビ？",
                )
                self.assertTrue(ok)
                self.assertEqual(runtime._last_grounding_judge_trace["decision"], "general_knowledge_fallback")
        finally:
            AgentRuntime._semantic_grounding_check = original  # type: ignore[assignment]

    def test_grounding_judge_invalid_json_still_blocks_evidence_required_task(self) -> None:
        original = AgentRuntime._semantic_grounding_check
        AgentRuntime._semantic_grounding_check = self.original_semantic_check  # type: ignore[assignment]
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                bootstrap_workspace(root, force=True)
                runtime = AgentRuntime(
                    root,
                    llm_backend=FakeBackend(["1. Analyze first, but never emit JSON"]),
                )
                ok = runtime._semantic_grounding_check(
                    final_answer="ghost-output",
                    evidence_text="",
                    user_message="結果を確認して",
                )
                self.assertFalse(ok)
                self.assertEqual(runtime._last_grounding_judge_trace["decision"], "invalid_json")
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
            config.setdefault("runtime", {})["max_steps_per_message"] = 3
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
            result = runtime.send_message("ビートルズってボンジョビ？", run_immediately=True)
            self.assertTrue(result["ok"])
            self.assertEqual(result["run"]["last_result"]["final_answer"], "ビートルズとボン・ジョヴィは別のバンドです。")
            self.assertEqual(len(backend.messages_seen), 2)
            self.assertIn("assistant content was empty", backend.messages_seen[1][-1]["content"])
            events = read_jsonl(WorkspacePaths(root).session_events_path("main"))
            self.assertFalse(any(event.get("code") == "llm_output_issue" for event in events))
            finish = next(event for event in events if event.get("type") == "finish")
            self.assertEqual(finish["content"], "ビートルズとボン・ジョヴィは別のバンドです。")

    def test_prompt_skips_reflection_from_unrelated_previous_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            runtime = AgentRuntime(root, llm_backend=FakeBackend([]))
            runtime._record_reflection(
                session_id="main",
                turn_id="turn",
                queue_id="queue",
                reason="step limit reached before finish",
                steps=[],
                user_message="迷路を作成して実行して表示して",
            )
            prompt = runtime._build_prompt(
                goal_text="",
                recent_events=[{"type": "user_message", "content": "こんばんわ"}],
                steps=[],
                user_message="こんばんわ",
            )
            self.assertNotIn("迷路を作成して実行して表示して", prompt)
            self.assertIn("(直近のリフレクションはありません)", prompt)

    def test_prompt_keeps_reflection_for_related_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            runtime = AgentRuntime(root, llm_backend=FakeBackend([]))
            runtime._record_reflection(
                session_id="main",
                turn_id="turn",
                queue_id="queue",
                reason="step limit reached before finish",
                steps=[],
                user_message="迷路を作成して実行して表示して",
            )
            prompt = runtime._build_prompt(
                goal_text="",
                recent_events=[{"type": "user_message", "content": "迷路を作って実行して表示して"}],
                steps=[],
                user_message="迷路を作って実行して表示して",
            )
            self.assertIn("迷路を作成して実行して表示して", prompt)

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

    def test_terminal_agent_uses_llm_for_maze_requests_instead_of_fixed_scaffold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            maze_program = "print('''#####\n#   #\n### #\n#   #\n#####''')\n"
            backend = FakeBackend(
                [
                    json.dumps({"assistant_message": "writing", "tool_name": "write_file", "tool_args": {"path": "maze_gen.py", "content": maze_program}}, ensure_ascii=False),
                    '{"assistant_message":"running","tool_name":"run_command","tool_args":{"command":"python3 maze_gen.py","shell":"bash"}}',
                    '{"verdict":"ok","reason_code":"supported","unsupported_claims":[],"rationale":"the stdout came from the executed maze script"}',
                    '{"status":"success","reason_code":"supported","rationale":"the model-created script was executed and its output was displayed","observed_mismatch":""}',
                ]
            )
            runtime = AgentRuntime(root, llm_backend=backend)
            result = runtime.run_terminal_agent("迷路を実装して実行し表示して結果を見せて", model="devstral:latest", shell_name="bash")
            self.assertTrue(result["run"]["last_result"]["ok"])
            status = read_json(WorkspacePaths(root).runtime_status_path, fallback={})
            workspace = Path(status["last_llm_workspace"])
            self.assertTrue((workspace / "maze_gen.py").exists())
            self.assertEqual((workspace / "maze_gen.py").read_text(), maze_program)
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
            self.assertIn("#####", run_result["stdout"])
            self.assertIn("#   #", run_result["stdout"])

    def test_terminal_controller_finish_uses_grounded_evidence_without_second_llm_turn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            backend = FakeBackend(
                [
                    '{"assistant_message":"run pwd","tool_name":"run_command","tool_args":{"command":"pwd","shell":"bash"}}',
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
                    '{"assistant_message":"run pwd","tool_name":"run_command","tool_args":{"command":"pwd","shell":"bash"}}',
                    '{"assistant_message":"run ls","tool_name":"run_command","tool_args":{"command":"ls","shell":"bash"}}',
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

    def test_finish_blocked_when_acceptance_review_finds_obvious_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            backend = FakeBackend(
                [
                    '{"status":"needs_revision","reason_code":"obvious_mismatch","rationale":"出力が迷路として成立していない","observed_mismatch":"通路や開始終了が確認できない"}',
                ]
            )
            runtime = AgentRuntime(root, llm_backend=backend)
            acceptance = runtime._finish_acceptance_evaluation(
                user_message="迷路を作って実行して表示して",
                final_answer="# # #",
                steps=[
                    {"tool_name": "write_file", "tool_result": {"ok": True, "path": "maze.py"}},
                    {
                        "tool_name": "run_command",
                        "tool_result": {
                            "ok": True,
                            "command": "python3 maze.py",
                            "returncode": 0,
                            "stdout": "# # #\n",
                            "stderr": "",
                        },
                    },
                ],
            )
            self.assertEqual(acceptance["status"], "needs_revision")
            self.assertEqual(acceptance["semantic_status"], "needs_revision")
            self.assertEqual(acceptance["review"]["parsed"]["reason_code"], "obvious_mismatch")

    def test_finish_acceptance_review_unavailable_accepts_complete_observable_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            backend = FakeBackend(["レビューJSONではない出力", "まだJSONではない出力"])
            runtime = AgentRuntime(root, llm_backend=backend)
            acceptance = runtime._finish_acceptance_evaluation(
                user_message="迷路を作って実行して表示して",
                final_answer="maze generated",
                steps=[
                    {"tool_name": "write_file", "tool_result": {"ok": True, "path": "maze.py"}},
                    {
                        "tool_name": "run_command",
                        "tool_result": {
                            "ok": True,
                            "command": "python3 maze.py",
                            "returncode": 0,
                            "stdout": "###\n# #\n###\n",
                            "stderr": "",
                        },
                    },
                ],
            )
            self.assertEqual(acceptance["status"], "success")
            self.assertEqual(acceptance["semantic_status"], "review_unavailable")
            self.assertTrue(acceptance["fallback"]["ok"])
            self.assertEqual(acceptance["fallback"]["reason"], "observable_evidence_complete")

    def test_finish_acceptance_uses_observation_warning_when_judge_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            backend = FakeBackend(["レビューJSONではない出力", "まだJSONではない出力"])
            runtime = AgentRuntime(root, llm_backend=backend)
            stdout = "#####\n#   #\n### #\n#   #\n#####\n"
            acceptance = runtime._finish_acceptance_evaluation(
                user_message="迷路を作って実行して表示して",
                final_answer=stdout,
                steps=[
                    {"tool_name": "write_file", "tool_result": {"ok": True, "path": "maze.py"}},
                    {
                        "tool_name": "run_command",
                        "tool_result": {
                            "ok": True,
                            "command": "python3 maze.py",
                            "returncode": 0,
                            "stdout": stdout,
                            "stderr": "",
                        },
                    },
                ],
            )
            # Canonical contract (per p4-symmetry-audit-2026-04-26):
            # - status stays canonical: "success"
            # - semantic_status stays canonical: "review_unavailable"
            # - the override is signalled via a separate field that callers
            #   translate into a kind=decision event with explicit reason_code.
            self.assertEqual(acceptance["status"], "success")
            self.assertEqual(acceptance["semantic_status"], "review_unavailable")
            self.assertTrue(acceptance["fallback"]["ok"])
            override = acceptance.get("acceptance_override") or {}
            self.assertEqual(override.get("reason_code"), "judge_unavailable_observation_accepted")
            # Effective reason_code surfaced in the canonical decision event.
            self.assertEqual(
                runtime._finish_acceptance_reason_code(acceptance),
                "judge_unavailable_observation_accepted",
            )

    def test_finish_acceptance_evaluation_uses_only_canonical_values(self) -> None:
        """Design-invariant test (p4-symmetry-audit-2026-04-26).

        finish acceptance evaluation must only produce canonical status and
        semantic_status values. Any new asymmetric value (e.g. an ad-hoc
        "accepted_with_warning") must be lifted to a higher-level decision
        event instead.
        """
        canonical_statuses = {"success", "partial_success", "needs_revision"}
        canonical_semantic = {
            "unchecked",
            "not_required",
            "reviewed",
            "needs_revision",
            "partial_success",
            "review_unavailable",
        }
        scenarios = [
            # judge returns invalid output, evidence complete (override path)
            (
                FakeBackend(["レビューJSONではない出力", "まだJSONではない出力"]),
                "迷路を作って実行して表示して",
                "#####\n#   #\n### #\n#   #\n#####\n",
                [
                    {"tool_name": "write_file", "tool_result": {"ok": True, "path": "maze.py"}},
                    {
                        "tool_name": "run_command",
                        "tool_result": {
                            "ok": True,
                            "command": "python3 maze.py",
                            "returncode": 0,
                            "stdout": "#####\n#   #\n### #\n#   #\n#####\n",
                            "stderr": "",
                        },
                    },
                ],
            ),
            # judge returns invalid output, no observation evidence
            (
                FakeBackend(["bad", "still bad"]),
                "ファイルの中身を確認して",
                "no answer",
                [],
            ),
        ]
        for backend, user_message, final_answer, steps in scenarios:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                bootstrap_workspace(root, force=True)
                runtime = AgentRuntime(root, llm_backend=backend)
                acceptance = runtime._finish_acceptance_evaluation(
                    user_message=user_message,
                    final_answer=final_answer,
                    steps=steps,
                )
                self.assertIn(acceptance["status"], canonical_statuses)
                self.assertIn(acceptance["semantic_status"], canonical_semantic)

    def test_decision_status_from_finish_acceptance_event_is_canonical(self) -> None:
        """Canonical decision events must be one of accepted|blocked|failed."""
        from p4_core.workspace import _canonical_event_from_session_event

        canonical = {"accepted", "blocked", "failed"}
        cases = [
            {"reason_code": "reviewed", "expected": "accepted"},
            {"reason_code": "not_required", "expected": "accepted"},
            {"reason_code": "judge_unavailable_observation_accepted", "expected": "accepted"},
            {"reason_code": "review_unavailable", "expected": "blocked"},
            {"reason_code": "needs_revision", "expected": "blocked"},
            {"reason_code": "partial_success", "expected": "blocked"},
        ]
        for case in cases:
            session_event = {
                "type": "system_note",
                "code": "finish_acceptance",
                "reason_code": case["reason_code"],
                "content": "test",
            }
            canonical_event = _canonical_event_from_session_event(session_event)
            self.assertIsNotNone(canonical_event)
            self.assertEqual(canonical_event["kind"], "decision")
            self.assertIn(canonical_event["status"], canonical)
            self.assertEqual(canonical_event["status"], case["expected"])

    def test_activity_update_status_is_payload_variation_not_canonical_status(self) -> None:
        from p4_core.workspace import _canonical_event_from_session_event

        for raw_status in ["info", "success", "error", "blocked"]:
            canonical_event = _canonical_event_from_session_event(
                {
                    "type": "activity_update",
                    "status": raw_status,
                    "content": "dashboard activity",
                }
            )
            self.assertIsNotNone(canonical_event)
            self.assertEqual(canonical_event["kind"], "observation")
            self.assertEqual(canonical_event["status"], "finished")
            self.assertEqual(canonical_event["payload"]["raw_status"], raw_status)

    def test_extract_json_object_ignores_braces_inside_strings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            runtime = AgentRuntime(root, llm_backend=FakeBackend([]))
            payload = runtime._extract_json_object('prefix {"status":"success","text":"brace } in string"} suffix')
            self.assertEqual(json.loads(payload or "{}")["status"], "success")

    def test_similar_command_warning_does_not_block_changed_python_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            runtime = AgentRuntime(root, llm_backend=FakeBackend([]))
            warning = runtime._similar_command_warning(
                tool_args={"command": "python3 maze_generator.py"},
                steps=[
                    {
                        "tool_name": "run_command",
                        "tool_result": {"ok": False, "command": "python maze_generator.py"},
                    }
                ],
            )
            self.assertIsNone(warning)

    def test_controller_finish_does_not_repeat_same_acceptance_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            fixed_maze_program = "print('''#####\n#   #\n### #\n#   #\n#####''')\n"
            backend = FakeBackend(
                [
                    '{"assistant_message":"write bad","tool_name":"write_file","tool_args":{"path":"maze.py","content":"print(\\"bad\\")\\n"}}',
                    '{"assistant_message":"run bad","tool_name":"run_command","tool_args":{"command":"python3 maze.py","shell":"bash"}}',
                    '{"assistant_message":"done bad","tool_name":"finish","tool_args":{"final_answer":"bad"}}',
                    '{"status":"needs_revision","reason_code":"obvious_mismatch","rationale":"displayed output does not satisfy the requested artifact","observed_mismatch":"bad output"}',
                    json.dumps({"assistant_message": "write fixed", "tool_name": "write_file", "tool_args": {"path": "maze.py", "content": fixed_maze_program}}, ensure_ascii=False),
                    '{"assistant_message":"run fixed","tool_name":"run_command","tool_args":{"command":"python3 maze.py","shell":"bash"}}',
                    '{"assistant_message":"done fixed","tool_name":"finish","tool_args":{"final_answer":"fixed output displayed"}}',
                    '{"status":"success","reason_code":"supported","rationale":"corrected output was displayed","observed_mismatch":""}',
                ]
            )
            runtime = AgentRuntime(root, llm_backend=backend)
            result = runtime.send_message("迷路を作って実行して表示して", run_immediately=True)

            self.assertTrue(result["run"]["last_result"]["ok"])
            events = read_jsonl(WorkspacePaths(root).session_events_path("main"))
            blocked = [event for event in events if event.get("reason_code") == "finish_acceptance_failed"]
            self.assertEqual(len(blocked), 1)
            tool_calls = [event.get("tool_name") for event in events if event.get("type") == "tool_call"]
            self.assertEqual(tool_calls, ["write_file", "run_command", "write_file", "run_command"])

    def test_finish_records_success_acceptance_when_review_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            maze_program = "print('''#####\n#   #\n### #\n#   #\n#####''')\n"
            backend = FakeBackend(
                [
                    json.dumps({"assistant_message": "write maze", "tool_name": "write_file", "tool_args": {"path": "maze.py", "content": maze_program}}, ensure_ascii=False),
                    '{"assistant_message":"run maze","tool_name":"run_command","tool_args":{"command":"python3 maze.py","shell":"bash"}}',
                    '{"status":"success","reason_code":"supported","rationale":"stdout contains displayed maze endpoints","observed_mismatch":""}',
                ]
            )
            runtime = AgentRuntime(root, llm_backend=backend)
            result = runtime.send_message("迷路を作って実行して表示して", run_immediately=True)
            self.assertTrue(result["run"]["last_result"]["ok"])
            events = read_jsonl(WorkspacePaths(root).session_events_path("main"))
            acceptance = next(event for event in events if event.get("code") == "finish_acceptance")
            self.assertEqual(acceptance["details"]["status"], "success")
            self.assertEqual(acceptance["details"]["semantic_status"], "reviewed")

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
            config.setdefault("runtime", {})["max_steps_per_message"] = 9
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
                    '{"assistant_message":"list discovery","tool_name":"list_files","tool_args":{"path":"."}}',
                    '{"assistant_message":"open next","tool_name":"open_child_frame","tool_args":{}}',
                    '{"assistant_message":"write maze","tool_name":"write_file","tool_args":{"path":"maze.py","content":"print(\'maze\')\\n"}}',
                    '{"assistant_message":"done","tool_name":"finish","tool_args":{"final_answer":"requirements collected and maze executed"}}',
                    '{"verdict":"ok","reason_code":"supported","unsupported_claims":[],"rationale":"child return evidence supports the final answer"}',
                    '{"status":"success","reason_code":"supported","rationale":"child returns satisfy the user request","observed_mismatch":""}',
                ]
            )
            runtime = AgentRuntime(root, llm_backend=backend)
            result = runtime.send_message("作業を良い単位で分割して実行して", run_immediately=True)

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

    def test_child_frame_with_tool_evidence_must_return_before_redecomposing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            config = read_json(WorkspacePaths(root).config_path, fallback={})
            config.setdefault("runtime", {})["max_steps_per_message"] = 2
            write_json(WorkspacePaths(root).config_path, config)
            backend = FakeBackend(
                [
                    json.dumps({"assistant_message": "open", "tool_name": "open_child_frame", "tool_args": {"work_package": work_package("inspect files", args={"path": "."})}}),
                    '{"assistant_message":"list","tool_name":"list_files","tool_args":{"path":"."}}',
                ]
            )
            runtime = AgentRuntime(root, llm_backend=backend)
            runtime.send_message("子フレームのfirst_action成功後に自動帰還する", run_immediately=True)

            events = read_jsonl(WorkspacePaths(root).session_events_path("main"))
            self.assertTrue(any(event.get("type") == "frame_returned" for event in events))
            child_returns = [event for event in events if event.get("type") == "child_return"]
            self.assertEqual(len(child_returns), 1)
            self.assertIn("first_action succeeded", child_returns[0]["content"])
            current = runtime.frame_manager.current_frame()
            self.assertIsNotNone(current)
            self.assertIsNone(current.parent_frame_id)
            self.assertFalse(any(event.get("type") == "task_plan" for event in events))

    def test_frame_action_goals_are_not_extracted_as_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            runtime = AgentRuntime(root, llm_backend=FakeBackend([]))
            commands = runtime._extract_requested_commands(
                '必ず open_child_frame を使い、goal は "pwd evidence child"、context_summary は "verify child return" にしてください。子フレームでは run_command で pwd を実行してください。'
            )
            self.assertEqual(commands, ["pwd"])

    def test_child_frame_first_action_auto_executes_before_another_child_can_open(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            config = read_json(WorkspacePaths(root).config_path, fallback={})
            config.setdefault("runtime", {})["max_steps_per_message"] = 2
            write_json(WorkspacePaths(root).config_path, config)
            backend = FakeBackend(
                [
                    json.dumps({"assistant_message": "open1", "tool_name": "open_child_frame", "tool_args": {"work_package": work_package("d1", args={"path": "."})}}),
                    json.dumps({"assistant_message": "open2", "tool_name": "open_child_frame", "tool_args": {"work_package": work_package("d2", args={"path": "."})}}),
                ]
            )
            AgentRuntime(root, llm_backend=backend).send_message("子フレーム開始直後にさらに分解しようとする", run_immediately=True)

            events = read_jsonl(WorkspacePaths(root).session_events_path("main"))
            self.assertFalse(any(event.get("code") == "first_action_required" for event in events))
            self.assertEqual(len([event for event in events if event.get("type") == "frame_opened"]), 2)
            self.assertEqual(len([event for event in events if event.get("type") == "child_return"]), 2)

    def test_child_frame_step_safety_valve_returns_to_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            config = read_json(WorkspacePaths(root).config_path, fallback={})
            config.setdefault("runtime", {})["max_steps_per_message"] = 18
            write_json(WorkspacePaths(root).config_path, config)
            responses = [
                json.dumps({"assistant_message": "open", "tool_name": "open_child_frame", "tool_args": {"work_package": work_package("loop child", tool="read_file", args={"path": "notes.txt"}, evidence="safety valve")}}),
            ]
            responses.extend(
                '{"assistant_message":"premature finish","tool_name":"finish","tool_args":{"final_answer":"not yet"}}'
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
