from __future__ import annotations

import json
import os
import re
import signal
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from p4_core.frames import FrameManager
from p4_core.config_defaults import DEFAULT_TOOL_CONTENT_CHUNK_BYTES
from p4_core.models import ModelRouter
from p4_core.ollama_client import OllamaChatClient
from p4_core.tools import ToolExecutor
from p4_core.workspace import (
    WorkspacePaths,
    active_session_id,
    append_jsonl,
    append_prompt_snapshot,
    append_session_event,
    enqueue_message,
    now_iso,
    pop_next_queue_item,
    queue_items,
    read_json,
    read_jsonl,
    write_json,
)
from p4_core.grounding import (
    _grounding_issues,
    _parse_grounding_judge_payload,
    _semantic_grounding_check,
)
from p4_core.guards import (
    _classify_failure,
    _expected_artifacts,
    _extract_requested_commands,
    _failed_command_guardrail,
    _missing_expected_artifacts,
    _missing_requested_commands,
    _redundant_command_reason,
)
from p4_core.llm_comm import (
    _chat_with_repair,
    _classify_llm_parse_issue,
    _extract_json_object,
    _extract_stream_metadata,
    _format_llm_stream_text,
    _json_repair_prompt,
    _tail_stream_text,
    _thinking_only_repair_prompt,
    _looks_like_structured_envelope,
    _looks_like_truncated_json,
    _raw_contains_json_object,
)
from p4_core.observer import (
    _context_risk_summary,
    _deterministic_step_commentary,
    _maybe_record_observer_note,
    _observer_enabled,
    _record_observer_judgement_note,
    _record_observer_llm_output_issue_note,
    _usable_japanese_observer_text,
)
from p4_core.prompts import (
    _build_deliberation_note,
    _build_planning_note,
    _build_prompt,
    _compact_context_text,
    _current_phase,
    _deliberation_reasons,
    _output_budget_prompt,
    _reflection_relevant_to_user,
    _reflection_prompt_block,
    _render_action_context_events,
    _system_prompt,
)
from p4_core.terminal import (
    _controller_terminal_finish,
    _deterministic_terminal_final_answer,
    _normalize_run_command_evidence,
    _output_preview,
    _preferred_shell_from_extra_prompt,
    _resolve_terminal_model,
    _synthesize_terminal_final_answer,
    _terminal_answer_is_direct_evidence,
    run_terminal_agent,
)


_UNSET = object()
WORK_PACKAGE_TYPES = {"inspect", "edit", "run_test", "search"}
NON_DECOMPOSE_ACTION_TOOLS = {
    "list_files",
    "read_file",
    "search_code",
    "write_file",
    "append_file",
    "replace_text",
    "run_command",
}


class AgentRuntime:
    def __init__(self, root: Path, *, llm_backend: Any | None = None) -> None:
        self.root = Path(root).expanduser().resolve()
        self.paths = WorkspacePaths(self.root)
        self.config = read_json(self.paths.config_path, fallback={})
        self.router = ModelRouter(self.config.get("models", {}))
        self.llm_backend = llm_backend or OllamaChatClient(base_url=str(self.config.get("ollama_base_url") or "http://127.0.0.1:11434"))
        self.ollama_options = dict(self.config.get("ollama_options", {}))
        self.runtime_config = dict(self.config.get("runtime", {}))
        execution_root = str(self.runtime_config.get("execution_root") or self.root)
        self.base_execution_root = Path(execution_root).expanduser().resolve()
        self.execution_root = self.base_execution_root
        self.tool_content_chunk_bytes = int(self.runtime_config.get("tool_content_chunk_bytes") or DEFAULT_TOOL_CONTENT_CHUNK_BYTES)
        self.tools = ToolExecutor(self.execution_root, content_chunk_max_bytes=self.tool_content_chunk_bytes)
        self.frame_manager = FrameManager(self.root)
        self._last_grounding_judge_trace: dict[str, Any] | None = None

    def _append_session_event(self, *args: Any) -> dict[str, Any]:
        if len(args) == 2:
            session_id, payload = args
        elif len(args) == 3:
            _root, session_id, payload = args
        else:
            raise TypeError("_append_session_event expects session_id,payload or root,session_id,payload")
        event = append_session_event(self.root, session_id, payload)
        if payload.get("type") not in {"user_message"}:
            self.frame_manager.append_event(event)
        return event

    def _start_turn_frame(self, *, user_message: str) -> None:
        if self.frame_manager.frames:
            self.frame_manager.abandon_all()
        frame = self.frame_manager.create_root_frame(user_message)
        self.frame_manager.append_event(
            {
                "type": "user_message",
                "role": "user",
                "content": user_message,
                "timestamp": now_iso(),
            }
        )

    def _frame_snapshot(self) -> dict[str, Any]:
        return self.frame_manager.snapshot()

    def _normalize_child_tasks(self, raw_tasks: Any) -> list[dict[str, Any]]:
        tasks: list[dict[str, Any]] = []
        for index, item in enumerate(raw_tasks if isinstance(raw_tasks, list) else [], start=1):
            if isinstance(item, dict):
                goal = str(item.get("goal") or item.get("task") or "").strip()
                context_summary = str(item.get("context_summary") or item.get("context") or "").strip()
                done_when = str(item.get("done_when") or item.get("completion_criteria") or "").strip()
                work_type = str(item.get("work_type") or "").strip()
                first_action = item.get("first_action") or {}
                success_evidence = item.get("success_evidence") or item.get("done_when") or item.get("completion_criteria") or ""
                why_not_direct_action = str(item.get("why_not_direct_action") or "").strip()
            else:
                goal = str(item or "").strip()
                context_summary = ""
                done_when = ""
                work_type = ""
                first_action = {}
                success_evidence = ""
                why_not_direct_action = ""
            if not goal:
                continue
            tasks.append(
                {
                    "task_id": f"task-{index}",
                    "goal": goal,
                    "work_type": work_type,
                    "first_action": first_action,
                    "success_evidence": success_evidence,
                    "why_not_direct_action": why_not_direct_action,
                    "context_summary": context_summary,
                    "done_when": done_when,
                    "status": "pending",
                }
            )
        return tasks

    def _normalize_work_package(self, raw: Any) -> dict[str, Any]:
        data = dict(raw or {}) if isinstance(raw, dict) else {}
        first_action = data.get("first_action") or {}
        if not isinstance(first_action, dict):
            first_action = {}
        first_action = {
            "tool": str(first_action.get("tool") or "").strip(),
            "args": dict(first_action.get("args") or {}) if isinstance(first_action.get("args") or {}, dict) else {},
        }
        evidence = data.get("success_evidence") or data.get("done_when") or ""
        if isinstance(evidence, list):
            success_evidence = [str(item).strip() for item in evidence if str(item).strip()]
        else:
            success_evidence = str(evidence or "").strip()
        return {
            "goal": str(data.get("goal") or "").strip(),
            "work_type": str(data.get("work_type") or "").strip(),
            "first_action": first_action,
            "success_evidence": success_evidence,
            "why_not_direct_action": str(data.get("why_not_direct_action") or "").strip(),
            "context_summary": str(data.get("context_summary") or data.get("context") or "").strip(),
            "done_when": str(data.get("done_when") or "").strip(),
            "task_id": str(data.get("task_id") or data.get("child_task_id") or "").strip(),
            "status": str(data.get("status") or "pending").strip(),
        }

    def _work_package_issues(self, work_package: dict[str, Any]) -> list[str]:
        issues: list[str] = []
        if not str(work_package.get("goal") or "").strip():
            issues.append("goal is required")
        work_type = str(work_package.get("work_type") or "").strip()
        if work_type not in WORK_PACKAGE_TYPES:
            issues.append(f"work_type must be one of {sorted(WORK_PACKAGE_TYPES)}")
        first_action = work_package.get("first_action") or {}
        if not isinstance(first_action, dict) or not str(first_action.get("tool") or "").strip():
            issues.append("first_action.tool is required")
        elif str(first_action.get("tool") or "").strip() not in NON_DECOMPOSE_ACTION_TOOLS:
            issues.append(f"first_action.tool must be a non-decomposition tool: {sorted(NON_DECOMPOSE_ACTION_TOOLS)}")
        if not isinstance(first_action.get("args") if isinstance(first_action, dict) else None, dict):
            issues.append("first_action.args must be an object")
        evidence = work_package.get("success_evidence")
        if not evidence:
            issues.append("success_evidence is required")
        if not str(work_package.get("why_not_direct_action") or "").strip():
            issues.append("why_not_direct_action is required")
        return issues

    def _work_package_blocked_event(
        self,
        *,
        session_id: str,
        turn_id: str,
        queue_id: str,
        step_index: int,
        turn_workspace: Path,
        tool_name: str,
        issues: list[str],
    ) -> dict[str, Any]:
        return self._append_session_event(
            session_id,
            {
                "type": "system_note",
                "role": "system",
                "content": f"{tool_name} requires a concrete work_package: {'; '.join(issues)}",
                "code": "work_package_invalid",
                "reason_code": "missing_work_package_contract",
                "turn_id": turn_id,
                "queue_id": queue_id,
                "step_index": step_index,
                "llm_workspace": str(turn_workspace),
            },
        )

    def _handle_decompose_tasks(
        self,
        *,
        session_id: str,
        turn_id: str,
        queue_id: str,
        step_index: int,
        tool_args: dict[str, Any],
        turn_workspace: Path,
    ) -> dict[str, Any]:
        parent = self.frame_manager.current_frame()
        tasks = self._normalize_child_tasks(tool_args.get("tasks") or [])
        if not tasks:
            note = self._append_session_event(
                session_id,
                {
                    "type": "system_note",
                    "role": "system",
                    "content": "decompose_tasks requires at least one task with a goal",
                    "code": "decompose_tasks_blocked",
                    "reason_code": "empty_task_plan",
                    "turn_id": turn_id,
                    "queue_id": queue_id,
                    "step_index": step_index,
                    "llm_workspace": str(turn_workspace),
                },
            )
            return {"ok": False, "event": note, "error": "empty task plan"}
        invalid: list[str] = []
        for task in tasks:
            issues = self._work_package_issues(self._normalize_work_package(task))
            if issues:
                invalid.append(f"{task.get('task_id')}: {', '.join(issues)}")
        if invalid:
            note = self._work_package_blocked_event(
                session_id=session_id,
                turn_id=turn_id,
                queue_id=queue_id,
                step_index=step_index,
                turn_workspace=turn_workspace,
                tool_name="decompose_tasks",
                issues=invalid,
            )
            return {"ok": False, "event": note, "error": "invalid work package"}
        self.frame_manager.set_child_tasks(tasks)
        plan_event = self._append_session_event(
            session_id,
            {
                "type": "task_plan",
                "role": "system",
                "frame_id": parent.frame_id if parent else None,
                "tasks": tasks,
                "rationale": str(tool_args.get("rationale") or ""),
                "content": f"Planned {len(tasks)} child tasks.",
                "turn_id": turn_id,
                "queue_id": queue_id,
                "step_index": step_index,
                "llm_workspace": str(turn_workspace),
            },
        )
        first_task = tasks[0]
        self._handle_open_child_frame(
            session_id=session_id,
            turn_id=turn_id,
            queue_id=queue_id,
            step_index=step_index,
            tool_args={
                "work_package": first_task,
                "child_task_id": first_task["task_id"],
            },
            turn_workspace=turn_workspace,
        )
        return {"ok": True, "event": plan_event, "tasks": tasks}

    def _handle_open_child_frame(
        self,
        *,
        session_id: str,
        turn_id: str,
        queue_id: str,
        step_index: int,
        tool_args: dict[str, Any],
        turn_workspace: Path,
    ) -> dict[str, Any]:
        parent = self.frame_manager.current_frame()
        parent_id = parent.frame_id if parent else None
        planned_task = None
        requested_task_id = str(tool_args.get("child_task_id") or "").strip()
        if parent is not None and parent.working_memory.child_tasks:
            if requested_task_id:
                planned_task = next(
                    (task for task in parent.working_memory.child_tasks if str(task.get("task_id") or "") == requested_task_id),
                    None,
                )
            else:
                planned_task = self.frame_manager.next_pending_child_task(parent)
        raw_work_package = tool_args.get("work_package") or planned_task
        if not raw_work_package and any(key in tool_args for key in ("goal", "work_type", "first_action", "success_evidence", "why_not_direct_action")):
            raw_work_package = tool_args
        work_package = self._normalize_work_package(raw_work_package)
        if not work_package["goal"]:
            work_package["goal"] = str(tool_args.get("goal") or (planned_task or {}).get("goal") or "").strip()
        if not work_package["task_id"]:
            work_package["task_id"] = str(tool_args.get("child_task_id") or (planned_task or {}).get("task_id") or "")
        issues = self._work_package_issues(work_package)
        if issues:
            note = self._work_package_blocked_event(
                session_id=session_id,
                turn_id=turn_id,
                queue_id=queue_id,
                step_index=step_index,
                turn_workspace=turn_workspace,
                tool_name="open_child_frame",
                issues=issues,
            )
            return {"ok": False, "event": note, "error": "invalid work package"}
        goal = str(work_package.get("goal") or "child frame")
        context_summary = str(work_package.get("context_summary") or "")
        inherited_context = {
            "parent_frame_id": parent_id,
            "parent_goal": parent.goal if parent else "",
            "context_summary": context_summary,
            "done_when": str(work_package.get("done_when") or ""),
            "child_task_id": str(work_package.get("task_id") or ""),
            "work_package": work_package,
            "parent_working_memory": parent.working_memory.__dict__ if parent else {},
        }
        try:
            child = self.frame_manager.open_child_frame(
                goal=goal,
                inherited_context=inherited_context,
            )
        except ValueError as exc:
            note = self._append_session_event(
                session_id,
                {
                    "type": "system_note",
                    "role": "system",
                    "content": str(exc),
                    "code": "frame_open_blocked",
                    "reason_code": "depth_limit",
                    "turn_id": turn_id,
                    "queue_id": queue_id,
                    "step_index": step_index,
                    "llm_workspace": str(turn_workspace),
                },
            )
            return {"ok": False, "event": note, "error": str(exc)}
        event = self._append_session_event(
            session_id,
            {
                "type": "frame_opened",
                "role": "system",
                "frame_id": child.frame_id,
                "parent_frame_id": parent_id,
                "goal": child.goal,
                "content": f"Opened child frame: {child.goal}",
                "turn_id": turn_id,
                "queue_id": queue_id,
                "step_index": step_index,
                "llm_workspace": str(turn_workspace),
            },
        )
        self.frame_manager.append_event(
            {
                "type": "planning_note",
                "role": "system",
                "content": f"子フレーム開始: {child.goal}",
                "turn_id": turn_id,
                "queue_id": queue_id,
                "step_index": step_index,
            }
        )
        if self._observer_enabled():
            self._append_session_event(
                session_id,
                {
                    "type": "observer_note",
                    "role": "observer",
                    "content": f"フレーム遷移: 親の問題を局所目的「{child.goal}」に分解しました。戻る条件は、親が次を判断できる発見を得ることです。",
                    "reason_code": "frame_opened",
                    "turn_id": turn_id,
                    "queue_id": queue_id,
                    "step_index": step_index,
                    "llm_workspace": str(turn_workspace),
                },
            )
        return {"ok": True, "event": event, "frame": child.to_dict()}

    def _handle_return_to_parent(
        self,
        *,
        session_id: str,
        turn_id: str,
        queue_id: str,
        step_index: int,
        tool_args: dict[str, Any],
        turn_workspace: Path,
    ) -> dict[str, Any]:
        current = self.frame_manager.current_frame()
        payload = {
            "summary": str(tool_args.get("summary") or ""),
            "findings": list(tool_args.get("findings") or []),
        }
        try:
            parent = self.frame_manager.return_to_parent(payload)
        except ValueError as exc:
            note = self._append_session_event(
                session_id,
                {
                    "type": "system_note",
                    "role": "system",
                    "content": str(exc),
                    "code": "frame_return_blocked",
                    "reason_code": "root_frame",
                    "turn_id": turn_id,
                    "queue_id": queue_id,
                    "step_index": step_index,
                    "llm_workspace": str(turn_workspace),
                },
            )
            return {"ok": False, "event": note, "error": str(exc)}
        child_id = current.frame_id if current else None
        self.frame_manager.mark_child_task_completed(parent=parent, child=current, return_payload=payload)
        next_task = self.frame_manager.next_pending_child_task(parent)
        frame_event = self._append_session_event(
            session_id,
            {
                "type": "frame_returned",
                "role": "system",
                "frame_id": child_id,
                "parent_frame_id": parent.frame_id,
                "return_payload": payload,
                "content": f"Returned to parent frame: {payload['summary']}",
                "turn_id": turn_id,
                "queue_id": queue_id,
                "step_index": step_index,
                "llm_workspace": str(turn_workspace),
            },
        )
        self._append_session_event(
            session_id,
            {
                "type": "child_return",
                "role": "system",
                "child_frame_id": child_id,
                "return_payload": payload,
                "next_child_task": next_task,
                "content": payload["summary"],
                "turn_id": turn_id,
                "queue_id": queue_id,
                "step_index": step_index,
                "llm_workspace": str(turn_workspace),
            },
        )
        if self._observer_enabled():
            findings = " / ".join(str(item) for item in payload.get("findings") or [])
            self._append_session_event(
                session_id,
                {
                    "type": "observer_note",
                    "role": "observer",
                    "content": f"フレーム帰還: 子フレームは「{payload['summary']}」を持ち帰りました。親はこの発見を使って次の判断に進めます。主要発見: {findings}",
                    "reason_code": "frame_returned",
                    "turn_id": turn_id,
                    "queue_id": queue_id,
                    "step_index": step_index,
                    "llm_workspace": str(turn_workspace),
                },
            )
        return {"ok": True, "event": frame_event, "parent": parent.to_dict()}


    def send_message(self, content: str, *, session_id: str | None = None, run_immediately: bool = False) -> dict[str, Any]:
        payload = enqueue_message(self.root, content, session_id=session_id)
        if run_immediately:
            loop_result = self.run_until_idle()
            payload["run"] = loop_result
        return payload

    def simple_chat(self, content: str, *, session_id: str | None = None) -> dict[str, Any]:
        clean = str(content or "").strip()
        if not clean:
            raise ValueError("message content must not be empty")
        session_id = session_id or active_session_id(self.root)
        user_event = self._append_session_event(
            self.root,
            session_id,
            {"type": "user_message", "role": "user", "content": clean},
        )
        conversation = self._conversation_messages(session_id)
        model = str(self.router.models.get("reasoning") or self.config.get("models", {}).get("reasoning") or "gemma4:26b")
        timeout_seconds = int(self.runtime_config.get("chat_timeout_seconds") or 180)
        options = dict(self.ollama_options.get("reasoning", {}))
        started_at = now_iso()
        self._write_runtime_status(
            status="running",
            current_role="chat",
            current_turn_id=user_event["event_id"],
            current_user_message=clean,
            current_prompt_preview=clean,
            current_stream_text="",
            current_model=model,
            current_model_reason="plain chat mode",
            current_tool=None,
            last_error=None,
            worker_running=self._worker_running(),
        )

        chunks = self.llm_backend.chat_stream(
            model=model,
            messages=conversation,
            options=options,
            timeout_seconds=timeout_seconds,
        )
        thinking_started = False
        content_started = False
        stream_parts: list[str] = []
        thinking_parts: list[str] = []
        content_parts: list[str] = []
        for chunk in chunks:
            message = chunk.get("message") or {}
            delta_thinking = str(message.get("thinking") or "")
            delta_content = str(message.get("content") or "")
            if delta_thinking:
                if not thinking_started:
                    stream_parts.append("Thinking...\n\n")
                    thinking_started = True
                stream_parts.append(delta_thinking)
                thinking_parts.append(delta_thinking)
            if delta_content:
                if thinking_started and not content_started:
                    stream_parts.append("\n\n")
                content_started = True
                stream_parts.append(delta_content)
                content_parts.append(delta_content)
            current_stream = "".join(stream_parts)
            self._write_runtime_status(
                status="running",
                current_role="chat",
                current_turn_id=user_event["event_id"],
                current_user_message=clean,
                current_prompt_preview=clean,
                current_stream_text=current_stream[-12000:],
                current_model=model,
                current_model_reason="plain chat mode",
                worker_running=self._worker_running(),
            )

        final_stream = "".join(stream_parts)
        assistant_event = self._append_session_event(
            self.root,
            session_id,
            {
                "type": "assistant_message",
                "role": "assistant",
                "content": final_stream,
                "model": model,
                "thinking_text": "".join(thinking_parts),
                "content_text": "".join(content_parts),
            },
        )
        self._write_runtime_status(
            status="idle",
            current_role="chat",
            current_turn_id=None,
            current_queue_id=None,
            current_user_message=clean,
            current_prompt_preview=clean,
            current_stream_text=final_stream[-12000:],
            current_model=model,
            current_model_reason="plain chat mode",
            current_tool=None,
            last_error=None,
            last_llm_started_at=started_at,
            last_llm_finished_at=now_iso(),
            last_llm_duration_ms=None,
            last_llm_attempt_count=1,
            last_llm_raw_preview=final_stream[:1000],
            worker_running=self._worker_running(),
        )
        return {"ok": True, "session_id": session_id, "user_event_id": user_event["event_id"], "assistant_event_id": assistant_event["event_id"]}


    def _dedicated_llm_workspace_enabled(self) -> bool:
        return bool(self.runtime_config.get("dedicated_llm_workspace", True))

    def _prepare_turn_workspace(self, *, turn_id: str) -> Path:
        if not self._dedicated_llm_workspace_enabled():
            self.execution_root = self.base_execution_root
            self.tools = ToolExecutor(self.execution_root, content_chunk_max_bytes=self.tool_content_chunk_bytes)
            return self.execution_root
        workspace = (self.paths.llm_runs_dir / turn_id).resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        self.execution_root = workspace
        self.tools = ToolExecutor(self.execution_root, content_chunk_max_bytes=self.tool_content_chunk_bytes)
        return workspace


    def _conversation_messages(self, session_id: str) -> list[dict[str, str]]:
        # Increased limit to 100 to capture enough history for complex loops
        current_frame = self.frame_manager.current_frame()
        if current_frame is not None and current_frame.session_events:
            events = current_frame.session_events[-100:]
        else:
            events = read_jsonl(self.paths.session_events_path(session_id), limit=100)
        messages: list[dict[str, str]] = []
        if current_frame is not None and current_frame.inherited_context:
            messages.append(
                {
                    "role": "user",
                    "content": "[Inherited Frame Context] "
                    + json.dumps(current_frame.inherited_context, ensure_ascii=False),
                }
            )
        for event in events:
            event_type = str(event.get("type") or "")
            role = str(event.get("role") or "")
            if event_type == "user_message" and role == "user":
                messages.append({"role": "user", "content": str(event.get("content") or "")})
            elif event_type == "assistant_message" and role == "assistant":
                content = str(event.get("content_text") or event.get("content") or "")
                messages.append({"role": "assistant", "content": content})
            elif event_type == "system_note":
                content = str(event.get("content") or "")
                # We map system_note to 'user' role with a clear prefix so the LLM treats it as feedback
                messages.append({"role": "user", "content": f"[System Note] {content}"})
            elif event_type == "tool_call":
                tool_name = str(event.get("tool_name") or "")
                tool_args = event.get("tool_args") or {}
                # Represent tool calls in context
                messages.append({"role": "assistant", "content": f"Tool Call: {tool_name} {json.dumps(tool_args, ensure_ascii=False)}"})
            elif event_type == "tool_result":
                tool_name = str(event.get("tool_name") or "")
                content = str(event.get("content") or "")
                # Represent tool results in context
                messages.append({"role": "user", "content": f"Tool Result ({tool_name}): {content}"})
        return messages

    def set_goal(self, text: str) -> dict[str, Any]:
        from p4_core.workspace import update_goal

        return update_goal(self.root, text)

    def run_until_idle(
        self,
        *,
        max_work_items: int | None = None,
        selection_override: dict[str, str] | None = None,
        extra_prompt: str | None = None,
    ) -> dict[str, Any]:
        processed = 0
        last_result: dict[str, Any] | None = None
        while True:
            if max_work_items is not None and processed >= max_work_items:
                break
            item = pop_next_queue_item(self.root)
            if item is None:
                self._write_runtime_status(
                    status="idle",
                    current_turn_id=None,
                    current_queue_id=None,
                    current_user_message=None,
                    current_prompt_preview=None,
                    current_stream_text="",
                    current_plan=None,
                    current_phase=None,
                    current_started_at=None,
                    current_tool=None,
                    current_llm_workspace=None,
                )
                break
            processed += 1
            last_result = self._process_queue_item(
                item,
                selection_override=selection_override,
                extra_prompt=extra_prompt,
            )
        return {
            "ok": True,
            "processed": processed,
            "last_result": last_result,
            "pending_queue": len(queue_items(self.root)),
        }

    def worker_loop(self) -> None:
        poll_seconds = int(self.config.get("runtime", {}).get("worker_poll_seconds") or 2)
        self.paths.worker_pid_path.write_text(str(os.getpid()), encoding="utf-8")
        self._write_runtime_status(status="worker_idle", worker_running=True)
        stop_requested = False

        def _handle_stop(signum: int, frame: Any) -> None:
            del signum
            del frame
            nonlocal stop_requested
            stop_requested = True

        signal.signal(signal.SIGTERM, _handle_stop)
        signal.signal(signal.SIGINT, _handle_stop)

        while not stop_requested:
            self.run_until_idle(max_work_items=1)
            time.sleep(poll_seconds)

        self._write_runtime_status(status="stopped", worker_running=False)
        if self.paths.worker_pid_path.exists():
            self.paths.worker_pid_path.unlink()

    def status_snapshot(self) -> dict[str, Any]:
        session_id = active_session_id(self.root)
        goal = read_json(self.paths.goal_path, fallback={})
        meta = read_json(self.paths.session_meta_path(session_id), fallback={})
        runtime = read_json(self.paths.runtime_status_path, fallback={})
        events = read_jsonl(self.paths.session_events_path(session_id), limit=20)
        prompts = read_jsonl(self.paths.session_prompts_path(session_id), limit=5)
        transcript = [event for event in events if event.get("type") in {"user_message", "assistant_message", "finish"}]
        last_reply = None
        for event in reversed(transcript):
            if event.get("type") in {"assistant_message", "finish"}:
                last_reply = event
                break
        return {
            "goal": goal,
            "runtime": runtime,
            "session": meta,
            "pending_queue": len(queue_items(self.root)),
            "recent_events": events,
            "recent_prompts": prompts,
            "recent_transcript": transcript,
            "last_reply": last_reply,
            "frames": self._frame_snapshot(),
        }

    def _process_queue_item(
        self,
        item: dict[str, Any],
        *,
        selection_override: dict[str, str] | None = None,
        extra_prompt: str | None = None,
    ) -> dict[str, Any]:
        session_id = str(item.get("session_id") or active_session_id(self.root))
        max_steps = int(self.config.get("runtime", {}).get("max_steps_per_message") or 12)
        recent_user_message = str(item.get("content") or "")
        queue_id = str(item.get("queue_id") or "")
        turn_id = uuid.uuid4().hex
        turn_workspace = self._prepare_turn_workspace(turn_id=turn_id)
        self._start_turn_frame(user_message=recent_user_message)
        steps: list[dict[str, Any]] = []
        planning_note = self._build_planning_note(user_message=recent_user_message, goal_text=str(read_json(self.paths.goal_path, fallback={}).get("text") or ""))
        append_jsonl(
            self.paths.planning_path,
            {
                "timestamp": now_iso(),
                "turn_id": turn_id,
                "queue_id": queue_id,
                "session_id": session_id,
                "note": planning_note,
                "llm_workspace": str(turn_workspace),
            },
        )
        self._append_session_event(
            self.root,
            session_id,
            {
                "type": "planning_note",
                "role": "system",
                "content": planning_note,
                "turn_id": turn_id,
                "queue_id": queue_id,
                "step_index": 0,
                "llm_workspace": str(turn_workspace),
            },
        )
        for step_index in range(1, max_steps + 1):
            current_frame = self.frame_manager.current_frame()
            if current_frame is not None:
                frame_step_count = self.frame_manager.increment_step()
                if frame_step_count > 15:
                    if current_frame.parent_frame_id is not None:
                        self._handle_return_to_parent(
                            session_id=session_id,
                            turn_id=turn_id,
                            queue_id=queue_id,
                            step_index=step_index,
                            tool_args={
                                "summary": "ステップ上限に達したため親フレームに戻ります。",
                                "findings": list(current_frame.working_memory.observations),
                            },
                            turn_workspace=turn_workspace,
                        )
                        continue
                    final_answer = "ステップ上限に達しました。現時点の結果を報告します。"
                    self._append_session_event(
                        session_id,
                        {
                            "type": "finish",
                            "role": "assistant",
                            "content": final_answer,
                            "turn_id": turn_id,
                            "queue_id": queue_id,
                            "step_index": step_index,
                            "llm_workspace": str(turn_workspace),
                        },
                    )
                    return {"ok": False, "session_id": session_id, "steps": steps, "final_answer": final_answer, "error": "frame step limit reached"}
                recent_events = current_frame.session_events[-30:]
            else:
                recent_events = read_jsonl(self.paths.session_events_path(session_id), limit=30)
            goal_text = str(read_json(self.paths.goal_path, fallback={}).get("text") or "")
            current_phase = self._current_phase(user_message=recent_user_message, steps=steps, recent_events=recent_events)
            selection = selection_override or self.router.select_model(
                goal_text=goal_text,
                pending_message=recent_user_message,
                recent_events=recent_events,
                current_phase=current_phase,
            )
            controller_finish = self._controller_terminal_finish(
                selection=selection,
                goal_text=goal_text,
                user_message=recent_user_message,
                steps=steps,
            )
            current_frame = self.frame_manager.current_frame()
            if controller_finish is not None and current_frame is not None and current_frame.parent_frame_id is not None:
                controller_finish = None
            if controller_finish is not None:
                self._append_session_event(
                    self.root,
                    session_id,
                    {
                        "type": "system_note",
                        "role": "system",
                        "content": "コントローラーによる自動完了: ターミナルの実行結果に基づき、根拠のある最終回答を合成しました",
                        "code": "controller_finish",
                        "reason_code": "grounded_terminal_evidence",
                        "turn_id": turn_id,
                        "queue_id": queue_id,
                        "step_index": step_index,
                        "llm_workspace": str(turn_workspace),
                    },
                )
                self._append_session_event(
                    self.root,
                    session_id,
                    {
                        "type": "finish",
                        "role": "assistant",
                        "content": controller_finish,
                        "model": selection["model"],
                        "model_reason": f"{selection['reason']} + controller-finish",
                        "llm_attempt_count": 0,
                        "turn_id": turn_id,
                        "queue_id": queue_id,
                        "step_index": step_index,
                        "llm_workspace": str(turn_workspace),
                    },
                )
                self._write_runtime_status(
                    status="idle",
                    current_role=selection["role"],
                    current_turn_id=None,
                    current_queue_id=None,
                    current_user_message=None,
                    current_prompt_preview=None,
                    current_stream_text="",
                    current_plan=None,
                    current_phase="FINISH",
                    current_model=selection["model"],
                    current_model_reason=f"{selection['reason']} + controller-finish",
                    current_tool="finish",
                    current_llm_workspace=None,
                    last_llm_workspace=str(turn_workspace),
                    last_error=None,
                    last_system_note=None,
                    current_started_at=None,
                    current_finished_at=now_iso(),
                    worker_running=self._worker_running(),
                )
                return {"ok": True, "session_id": session_id, "steps": steps, "final_answer": controller_finish}
            prompt = self._build_prompt(
                goal_text=goal_text,
                recent_events=recent_events,
                extra_prompt=extra_prompt,
                steps=steps,
                current_phase=current_phase,
                user_message=recent_user_message,
            )
            append_prompt_snapshot(
                self.root,
                session_id,
                {
                    "turn_id": turn_id,
                    "queue_id": queue_id,
                    "step_index": step_index,
                    "model": selection["model"],
                    "model_reason": selection["reason"],
                    "prompt": prompt,
                },
            )
            self._write_runtime_status(
                status="running",
                current_role=selection["role"],
                current_turn_id=turn_id,
                current_queue_id=queue_id,
                current_user_message=recent_user_message,
                current_prompt_preview=prompt[:2000],
                current_stream_text="Waiting for model response...",
                current_plan=planning_note,
                current_phase=current_phase,
                current_model=selection["model"],
                current_model_reason=selection["reason"],
                current_tool=None,
                current_llm_workspace=str(turn_workspace),
                last_llm_workspace=str(turn_workspace),
                last_error=None,
                last_system_note=None,
                current_started_at=now_iso(),
                current_finished_at=None,
                worker_running=self._worker_running(),
            )
            telemetry = self._chat_with_repair(
                role=str(selection["role"]),
                model=str(selection["model"]),
                prompt=prompt,
                session_id=session_id,
            )
            envelope = telemetry["envelope"]
            assistant_message = str(envelope.get("assistant_message") or "").strip()
            if assistant_message:
                self._append_session_event(
                    self.root,
                    session_id,
                    {
                        "type": "assistant_message",
                        "role": "assistant",
                        "content": assistant_message,
                        "model": selection["model"],
                        "model_reason": selection["reason"],
                        "llm_attempt_count": telemetry["attempt_count"],
                        "turn_id": turn_id,
                        "queue_id": queue_id,
                        "step_index": step_index,
                        "llm_workspace": str(turn_workspace),
                    },
                )
            if telemetry.get("parse_issue"):
                self._append_session_event(
                    self.root,
                    session_id,
                    {
                        "type": "system_note",
                        "role": "system",
                        "content": f"LLM応答がツール呼び出しJSONとして解釈できませんでした: {telemetry.get('parse_issue')}",
                        "code": "llm_output_issue",
                        "reason_code": str(telemetry.get("parse_issue") or "invalid_tool_envelope"),
                        "details": {
                            "parse_target": "content",
                            "raw_text": str(telemetry.get("raw_text") or "")[:4000],
                            "thinking_text": str(telemetry.get("thinking_text") or "")[:4000],
                            "combined_text": str(telemetry.get("combined_text") or "")[:4000],
                            "stream_metadata": telemetry.get("stream_metadata") or {},
                        },
                        "turn_id": turn_id,
                        "queue_id": queue_id,
                        "step_index": step_index,
                        "llm_workspace": str(turn_workspace),
                    },
                )
                self._record_observer_llm_output_issue_note(
                    session_id=session_id,
                    turn_id=turn_id,
                    queue_id=queue_id,
                    step_index=step_index,
                    user_message=recent_user_message,
                    assistant_message=assistant_message
                    or str(telemetry.get("combined_text") or telemetry.get("raw_text") or ""),
                    parse_issue=str(telemetry.get("parse_issue") or "invalid_tool_envelope"),
                    prompt_snapshot=prompt,
                    steps=steps,
                )
            tool_name = str(envelope.get("tool_name") or "").strip() or "finish"
            tool_args = envelope.get("tool_args") or {}
            if tool_name == "decompose_tasks":
                self._handle_decompose_tasks(
                    session_id=session_id,
                    turn_id=turn_id,
                    queue_id=queue_id,
                    step_index=step_index,
                    tool_args=tool_args,
                    turn_workspace=turn_workspace,
                )
                self._write_runtime_status(
                    status="running",
                    current_role=selection["role"],
                    current_turn_id=turn_id,
                    current_queue_id=queue_id,
                    current_user_message=recent_user_message,
                    current_prompt_preview=prompt[:2000],
                    current_stream_text="Planned child tasks and opened the first child frame.",
                    current_plan=planning_note,
                    current_phase="TASK_DECOMPOSED",
                    current_model=selection["model"],
                    current_model_reason=selection["reason"],
                    current_tool="decompose_tasks",
                    current_llm_workspace=str(turn_workspace),
                    last_llm_workspace=str(turn_workspace),
                    current_finished_at=now_iso(),
                    worker_running=self._worker_running(),
                )
                continue
            if tool_name == "open_child_frame":
                self._handle_open_child_frame(
                    session_id=session_id,
                    turn_id=turn_id,
                    queue_id=queue_id,
                    step_index=step_index,
                    tool_args=tool_args,
                    turn_workspace=turn_workspace,
                )
                self._write_runtime_status(
                    status="running",
                    current_role=selection["role"],
                    current_turn_id=turn_id,
                    current_queue_id=queue_id,
                    current_user_message=recent_user_message,
                    current_prompt_preview=prompt[:2000],
                    current_stream_text="Opened child frame.",
                    current_plan=planning_note,
                    current_phase="FRAME_OPENED",
                    current_model=selection["model"],
                    current_model_reason=selection["reason"],
                    current_tool="open_child_frame",
                    current_llm_workspace=str(turn_workspace),
                    last_llm_workspace=str(turn_workspace),
                    current_finished_at=now_iso(),
                    worker_running=self._worker_running(),
                )
                continue
            if tool_name == "return_to_parent":
                self._handle_return_to_parent(
                    session_id=session_id,
                    turn_id=turn_id,
                    queue_id=queue_id,
                    step_index=step_index,
                    tool_args=tool_args,
                    turn_workspace=turn_workspace,
                )
                self._write_runtime_status(
                    status="running",
                    current_role=selection["role"],
                    current_turn_id=turn_id,
                    current_queue_id=queue_id,
                    current_user_message=recent_user_message,
                    current_prompt_preview=prompt[:2000],
                    current_stream_text="Returned to parent frame.",
                    current_plan=planning_note,
                    current_phase="FRAME_RETURNED",
                    current_model=selection["model"],
                    current_model_reason=selection["reason"],
                    current_tool="return_to_parent",
                    current_llm_workspace=str(turn_workspace),
                    last_llm_workspace=str(turn_workspace),
                    current_finished_at=now_iso(),
                    worker_running=self._worker_running(),
                )
                continue
            if tool_name == "finish":
                active_frame = self.frame_manager.current_frame()
                if active_frame is not None and active_frame.parent_frame_id is not None:
                    message = "子フレームでは finish ではなく return_to_parent を使用してください。"
                    self._append_session_event(
                        session_id,
                        {
                            "type": "system_note",
                            "role": "system",
                            "content": message,
                            "code": "finish_blocked",
                            "reason_code": "child_frame_must_return",
                            "turn_id": turn_id,
                            "queue_id": queue_id,
                            "step_index": step_index,
                            "llm_workspace": str(turn_workspace),
                        },
                    )
                    self._write_runtime_status(
                        status="running",
                        current_role=selection["role"],
                        current_turn_id=turn_id,
                        current_queue_id=queue_id,
                        current_user_message=recent_user_message,
                        current_prompt_preview=prompt[:2000],
                        current_stream_text=message,
                        current_plan=planning_note,
                        current_phase="RETURN_TO_PARENT_REQUIRED",
                        current_model=selection["model"],
                        current_model_reason=selection["reason"],
                        current_tool=None,
                        current_llm_workspace=str(turn_workspace),
                        last_llm_workspace=str(turn_workspace),
                        last_error=message,
                        last_system_note=message,
                        worker_running=self._worker_running(),
                    )
                    continue
                missing_commands = self._missing_requested_commands(
                    user_message=recent_user_message,
                    steps=steps,
                )
                if missing_commands:
                    self._append_session_event(
                        self.root,
                        session_id,
                        {
                            "type": "system_note",
                            "role": "system",
                            "content": f"完了がブロックされました: 要求されたコマンドがまだ実行されていません: {', '.join(missing_commands)}",
                            "code": "finish_blocked",
                            "reason_code": "missing_required_commands",
                            "details": {"missing_commands": list(missing_commands)},
                            "turn_id": turn_id,
                            "queue_id": queue_id,
                            "step_index": step_index,
                            "llm_workspace": str(turn_workspace),
                        },
                    )
                    self._record_observer_judgement_note(
                        session_id=session_id,
                        turn_id=turn_id,
                        queue_id=queue_id,
	                        step_index=step_index,
	                        user_message=recent_user_message,
	                        assistant_message=assistant_message,
	                        system_decision=f"完了をブロックしました: 要求されたコマンドがまだ実行されていません: {', '.join(missing_commands)}",
	                        reason_code="missing_required_commands",
                            prompt_snapshot=prompt,
                            steps=steps,
	                    )
                    self._write_runtime_status(
                        status="running",
                        current_role=selection["role"],
                        current_turn_id=turn_id,
                        current_queue_id=queue_id,
                        current_user_message=recent_user_message,
                        current_prompt_preview=prompt[:2000],
                        current_stream_text=f"Finish blocked. Missing commands: {', '.join(missing_commands)}",
                        current_plan=planning_note,
                        current_phase="EXECUTE_MISSING_COMMANDS",
                        current_model=selection["model"],
                        current_model_reason=selection["reason"],
                        current_tool=None,
                        current_llm_workspace=str(turn_workspace),
                        last_llm_workspace=str(turn_workspace),
                        last_error="finish blocked by command coverage check",
                        last_system_note=f"完了がブロックされました: 要求されたコマンドがまだ実行されていません: {', '.join(missing_commands)}",
                        worker_running=self._worker_running(),
                    )
                    continue
                missing_artifacts = self._missing_expected_artifacts(user_message=recent_user_message)
                if missing_artifacts:
                    self._append_session_event(
                        self.root,
                        session_id,
                        {
                            "type": "system_note",
                            "role": "system",
                            "content": f"完了がブロックされました: 期待される成果物が見つかりません: {', '.join(missing_artifacts)}",
                            "code": "finish_blocked",
                            "reason_code": "missing_expected_artifacts",
                            "details": {"missing_artifacts": list(missing_artifacts)},
                            "turn_id": turn_id,
                            "queue_id": queue_id,
                            "step_index": step_index,
                            "llm_workspace": str(turn_workspace),
                        },
                    )
                    self._record_observer_judgement_note(
                        session_id=session_id,
                        turn_id=turn_id,
                        queue_id=queue_id,
                        step_index=step_index,
                        user_message=recent_user_message,
                        assistant_message=assistant_message,
                        system_decision=f"完了をブロックしました: 期待される成果物が見つかりません: {', '.join(missing_artifacts)}",
                        reason_code="missing_expected_artifacts",
                        prompt_snapshot=prompt,
                        steps=steps,
                    )
                    self._write_runtime_status(
                        status="running",
                        current_role=selection["role"],
                        current_turn_id=turn_id,
                        current_queue_id=queue_id,
                        current_user_message=recent_user_message,
                        current_prompt_preview=prompt[:2000],
                        current_stream_text=f"Finish blocked. Missing artifacts: {', '.join(missing_artifacts)}",
                        current_plan=planning_note,
                        current_phase="EXECUTE_MISSING_COMMANDS",
                        current_model=selection["model"],
                        current_model_reason=selection["reason"],
                        current_tool=None,
                        current_llm_workspace=str(turn_workspace),
                        last_llm_workspace=str(turn_workspace),
                        last_error="finish blocked by expected artifact check",
                        last_system_note=f"完了がブロックされました: 期待される成果物が見つかりません: {', '.join(missing_artifacts)}",
                        worker_running=self._worker_running(),
                    )
                    continue
                final_answer = str(tool_args.get("final_answer") or assistant_message or "Task finished.")
                if str(selection.get("role") or "") == "terminal":
                    synthesized_answer = self._synthesize_terminal_final_answer(
                        goal_text=goal_text,
                        user_message=recent_user_message,
                        steps=steps,
                    )
                    if synthesized_answer:
                        final_answer = synthesized_answer
                grounding_issues = self._grounding_issues(
                    user_message=recent_user_message,
                    final_answer=final_answer,
                    steps=steps,
                )
                if grounding_issues:
                    issue_text = "; ".join(grounding_issues)
                    judge_trace = self._last_grounding_judge_trace
                    judge_decision = str((judge_trace or {}).get("decision") or "unknown")
                    if judge_decision == "ng":
                        finish_reason_code = "grounding_issues"
                    elif judge_decision == "error":
                        finish_reason_code = "judge_error"
                    elif judge_decision in {"invalid_output", "invalid_json", "empty_output"}:
                        finish_reason_code = "judge_invalid_output"
                    else:
                        finish_reason_code = "grounding_issues"
                    if judge_trace:
                        if judge_decision == "ng":
                            judge_note = "根拠判定: NG。judge は最終回答が証拠から逸脱していると判定しました。"
                        elif judge_decision == "error":
                            judge_note = "根拠判定: ERROR。judge 実行中にエラーが発生したため、完了判定に失敗しました。"
                        else:
                            judge_note = "根拠判定: INVALID_OUTPUT。judge が有効な判定JSONを返さなかったため、完了判定に失敗しました。"
                        self._append_session_event(
                            self.root,
                            session_id,
                            {
                                "type": "system_note",
                                "role": "system",
                                "content": judge_note,
                                "code": "grounding_judge",
                                "reason_code": judge_decision,
                                "details": judge_trace,
                                "turn_id": turn_id,
                                "queue_id": queue_id,
                                "step_index": step_index,
                                "llm_workspace": str(turn_workspace),
                            },
                        )
                    self._append_session_event(
                        self.root,
                        session_id,
                        {
                            "type": "system_note",
                            "role": "system",
                            "content": f"完了がブロックされました: {issue_text}",
                            "code": "finish_blocked",
                            "reason_code": finish_reason_code,
                            "details": {"issues": list(grounding_issues), "judge": judge_trace},
                            "turn_id": turn_id,
                            "queue_id": queue_id,
                            "step_index": step_index,
                            "llm_workspace": str(turn_workspace),
                        },
                    )
                    self._record_observer_judgement_note(
                        session_id=session_id,
                        turn_id=turn_id,
                        queue_id=queue_id,
                        step_index=step_index,
                        user_message=recent_user_message,
                        assistant_message=assistant_message,
                        system_decision=f"完了をブロックしました: {issue_text}",
                        reason_code=finish_reason_code,
                        prompt_snapshot=prompt,
                        steps=steps,
                    )
                    self._write_runtime_status(
                        status="running",
                        current_role=selection["role"],
                        current_turn_id=turn_id,
                        current_queue_id=queue_id,
                        current_user_message=recent_user_message,
                        current_prompt_preview=prompt[:2000],
                        current_stream_text=f"Finish blocked. {issue_text}",
                        current_plan=planning_note,
                        current_phase="SYNTHESIZE_FROM_EVIDENCE",
                        current_model=selection["model"],
                        current_model_reason=selection["reason"],
                        current_tool=None,
                        current_llm_workspace=str(turn_workspace),
                        last_llm_workspace=str(turn_workspace),
                        last_error="finish blocked by grounding check",
                        last_system_note=f"完了がブロックされました: {issue_text}",
                        worker_running=self._worker_running(),
                    )
                    continue
                self._append_session_event(
                    self.root,
                    session_id,
                    {
                        "type": "finish",
                        "role": "assistant",
                        "content": final_answer,
                        "model": selection["model"],
                        "model_reason": selection["reason"],
                        "llm_attempt_count": telemetry["attempt_count"],
                        "turn_id": turn_id,
                        "queue_id": queue_id,
                        "step_index": step_index,
                        "llm_workspace": str(turn_workspace),
                    },
                )
                self._write_runtime_status(
                    status="idle",
                    current_role=selection["role"],
                    current_turn_id=None,
                    current_queue_id=None,
                    current_user_message=None,
                    current_prompt_preview=None,
                    current_stream_text="",
                    current_plan=None,
                    current_phase="FINISH",
                    current_model=selection["model"],
                    current_model_reason=selection["reason"],
                    current_tool="finish",
                    current_llm_workspace=None,
                    last_llm_workspace=str(turn_workspace),
                    last_error=None,
                    last_system_note=None,
                    current_started_at=None,
                    current_finished_at=now_iso(),
                    worker_running=self._worker_running(),
                )
                return {"ok": True, "session_id": session_id, "steps": steps, "final_answer": final_answer}
            self._append_session_event(
                self.root,
                session_id,
                {
                    "type": "tool_call",
                    "tool_name": tool_name,
                    "tool_args": tool_args,
                    "model": selection["model"],
                    "model_reason": selection["reason"],
                    "llm_attempt_count": telemetry["attempt_count"],
                    "turn_id": turn_id,
                    "queue_id": queue_id,
                    "step_index": step_index,
                    "llm_workspace": str(turn_workspace),
                },
            )
            if tool_name == "run_command":
                redundant_reason = self._redundant_command_reason(tool_args=tool_args, steps=steps)
                if redundant_reason:
                    previous_result = (steps[-1].get("tool_result") if steps else {}) or {}
                    previous_failed = not bool(previous_result.get("ok"))
                    self._append_session_event(
                        self.root,
                        session_id,
                        {
                            "type": "system_note",
                            "role": "system",
                            "content": redundant_reason,
                            "code": "command_blocked",
                            "reason_code": "repeated_command",
                            "details": {"command": str(tool_args.get("command") or "").strip()},
                            "turn_id": turn_id,
                            "queue_id": queue_id,
                            "step_index": step_index,
                            "llm_workspace": str(turn_workspace),
                        },
                    )
                    self._write_runtime_status(
                        status="running",
                        current_role=selection["role"],
                        current_turn_id=turn_id,
                        current_queue_id=queue_id,
                        current_user_message=recent_user_message,
                        current_prompt_preview=prompt[:2000],
                        current_stream_text=redundant_reason,
                        current_plan=planning_note,
                        current_model=selection["model"],
                        current_model_reason=selection["reason"],
                        current_tool=None,
                        current_llm_workspace=str(turn_workspace),
                        last_llm_workspace=str(turn_workspace),
                        last_error=redundant_reason,
                        last_system_note=redundant_reason,
                        worker_running=self._worker_running(),
                    )
                    if previous_failed:
                        self._record_reflection(
                            session_id=session_id,
                            turn_id=turn_id,
                            queue_id=queue_id,
                            user_message=recent_user_message,
                            reason=redundant_reason,
                            steps=steps,
                        )
                        self._write_runtime_status(
                            status="idle",
                            current_role=selection["role"],
                            current_turn_id=None,
                            current_queue_id=None,
                            current_user_message=None,
                            current_prompt_preview=None,
                            current_stream_text=redundant_reason,
                            current_plan=None,
                            current_phase="FINISH",
                            current_model=selection["model"],
                            current_model_reason=selection["reason"],
                            current_tool=None,
                            current_llm_workspace=None,
                            last_llm_workspace=str(turn_workspace),
                            last_error=redundant_reason,
                            last_system_note=redundant_reason,
                            current_started_at=None,
                            current_finished_at=now_iso(),
                            worker_running=self._worker_running(),
                        )
                        return {
                            "ok": False,
                            "session_id": session_id,
                            "steps": steps,
                            "error": redundant_reason,
                        }
                    continue
            self._write_runtime_status(
                status="running_tool",
                current_role=selection["role"],
                current_turn_id=turn_id,
                current_queue_id=queue_id,
                current_user_message=recent_user_message,
                current_prompt_preview=prompt[:2000],
                current_stream_text=(
                    f"Running command via {tool_args.get('shell') or 'auto'}:\n{tool_args.get('command')}"
                    if tool_name == "run_command"
                    else ""
                ),
                current_plan=planning_note,
                current_phase=current_phase,
                current_model=selection["model"],
                current_model_reason=selection["reason"],
                current_tool=tool_name,
                current_llm_workspace=str(turn_workspace),
                last_llm_workspace=str(turn_workspace),
                current_started_at=read_json(self.paths.runtime_status_path, fallback={}).get("current_started_at") or now_iso(),
                current_finished_at=None,
                worker_running=self._worker_running(),
            )
            try:
                tool_result = self.tools.execute(
                    tool_name,
                    dict(tool_args),
                    on_update=(self._make_tool_stream_updater(
                        selection=selection,
                        turn_id=turn_id,
                        queue_id=queue_id,
                        recent_user_message=recent_user_message,
                        prompt=prompt,
                        tool_name=tool_name,
                    ) if tool_name == "run_command" else None),
                )
            except Exception as exc:
                tool_result = {"ok": False, "tool": tool_name, "error": str(exc)}
            self._append_session_event(
                self.root,
                session_id,
                {
                    "type": "tool_result",
                    "tool_name": tool_name,
                    "content": json.dumps(tool_result, ensure_ascii=False),
                    "ok": bool(tool_result.get("ok")),
                    "turn_id": turn_id,
                    "queue_id": queue_id,
                    "step_index": step_index,
                    "model_reason": selection["reason"],
                    "llm_workspace": str(turn_workspace),
                },
            )
            if tool_name == "run_command":
                self._append_session_event(
                    self.root,
                    session_id,
                    {
                        "type": "system_note",
                        "role": "system",
                        "content": (
                            "run_command の実行結果を受信しました"
                            if bool(tool_result.get("ok"))
                            else f"run_command が失敗しました: {tool_result.get('error') or tool_result.get('stderr') or '不明なエラー'}"
                        ),
                        "turn_id": turn_id,
                        "queue_id": queue_id,
                        "step_index": step_index,
                        "llm_workspace": str(turn_workspace),
                    },
                )
                if not bool(tool_result.get("ok")):
                    self._append_session_event(
                        self.root,
                        session_id,
                        {
                            "type": "system_note",
                            "role": "system",
                            "content": self._failed_command_guardrail(tool_result=tool_result),
                            "code": "command_failed",
                            "reason_code": "recovery_guidance",
                            "turn_id": turn_id,
                            "queue_id": queue_id,
                            "step_index": step_index,
                            "llm_workspace": str(turn_workspace),
                        },
            )
            self.frame_manager.update_from_tool_result(tool_name, dict(tool_args), tool_result)
            steps.append({"tool_name": tool_name, "tool_result": tool_result})
            if int(telemetry.get("attempt_count") or 0) > 0:
                self._maybe_record_observer_note(
                    session_id=session_id,
                    turn_id=turn_id,
                    queue_id=queue_id,
                    step_index=step_index,
                    user_message=recent_user_message,
                    tool_name=tool_name,
                    tool_result=tool_result,
                    steps=steps,
                    prompt_snapshot=prompt,
                    assistant_message=assistant_message,
                )
            self._write_runtime_status(
                status="running",
                current_role=selection["role"],
                current_turn_id=turn_id,
                current_queue_id=queue_id,
                current_user_message=recent_user_message,
                current_prompt_preview=prompt[:2000],
                current_stream_text=(
                    f"Received result from {tool_name}\n{json.dumps(tool_result, ensure_ascii=False)}"
                )[-4000:],
                current_plan=planning_note,
                current_phase=self._current_phase(user_message=recent_user_message, steps=steps),
                current_model=selection["model"],
                current_model_reason=selection["reason"],
                current_tool=tool_name,
                current_llm_workspace=str(turn_workspace),
                last_llm_workspace=str(turn_workspace),
                last_error=None,
                current_finished_at=now_iso(),
                worker_running=self._worker_running(),
            )
            if tool_name == "run_command" and not bool(tool_result.get("ok")):
                error_text = str(tool_result.get("error") or tool_result.get("stderr") or "")
                if "PowerShell requested but 'pwsh' is not installed" in error_text:
                    final_answer = "PowerShell is not available in this environment because 'pwsh' is not installed."
                    self._append_session_event(
                        self.root,
                        session_id,
                        {
                            "type": "finish",
                            "role": "assistant",
                            "content": final_answer,
                            "model": selection["model"],
                            "model_reason": selection["reason"],
                            "llm_attempt_count": telemetry["attempt_count"],
                            "turn_id": turn_id,
                            "queue_id": queue_id,
                            "step_index": step_index,
                        },
                    )
                    self._write_runtime_status(
                        status="idle",
                        current_role=selection["role"],
                        current_turn_id=None,
                        current_queue_id=None,
                        current_user_message=None,
                        current_prompt_preview=None,
                        current_stream_text=error_text,
                        current_plan=None,
                        current_phase="FINISH",
                        current_model=selection["model"],
                        current_model_reason=selection["reason"],
                        current_tool="finish",
                        current_llm_workspace=None,
                        last_llm_workspace=str(turn_workspace),
                        last_error=error_text,
                        last_system_note=error_text,
                        current_started_at=None,
                        current_finished_at=now_iso(),
                        worker_running=self._worker_running(),
                    )
                    self._record_reflection(
                        session_id=session_id,
                        turn_id=turn_id,
                        queue_id=queue_id,
                        user_message=recent_user_message,
                        reason="shell unavailable",
                        steps=steps,
                    )
                    return {"ok": False, "session_id": session_id, "steps": steps, "final_answer": final_answer, "error": error_text}
        self._append_session_event(
            self.root,
            session_id,
            {
                "type": "system_note",
                "role": "system",
                "content": "step limit reached before finish",
                "turn_id": turn_id,
                "queue_id": queue_id,
                "step_index": max_steps,
            },
        )
        self._record_reflection(
            session_id=session_id,
            turn_id=turn_id,
            queue_id=queue_id,
            user_message=recent_user_message,
            reason="step limit reached before finish",
            steps=steps,
        )
        self._write_runtime_status(
            status="idle",
            current_turn_id=None,
            current_queue_id=None,
            current_user_message=None,
            current_prompt_preview=None,
            current_stream_text="",
            current_plan=None,
            current_phase="FINISH",
            current_tool=None,
            current_llm_workspace=None,
            last_llm_workspace=str(turn_workspace),
            last_error="step limit reached",
            last_system_note="step limit reached before finish",
            current_started_at=None,
            current_finished_at=now_iso(),
            worker_running=self._worker_running(),
        )
        return {"ok": False, "session_id": session_id, "steps": steps, "error": "step limit reached"}

    def _make_tool_stream_updater(
        self,
        *,
        selection: dict[str, str],
        turn_id: str,
        queue_id: str,
        recent_user_message: str,
        prompt: str,
        tool_name: str,
    ) -> Any:
        def _update(partial: dict[str, Any]) -> None:
            preview = json.dumps(partial, ensure_ascii=False)[-4000:]
            self._write_runtime_status(
                status="running_tool",
                current_role=selection["role"],
                current_turn_id=turn_id,
                current_queue_id=queue_id,
                current_user_message=recent_user_message,
                current_prompt_preview=prompt[:2000],
                current_stream_text=preview,
                current_model=selection["model"],
                current_model_reason=selection["reason"],
                current_tool=tool_name,
                worker_running=self._worker_running(),
            )
        return _update




































    def _record_reflection(
        self,
        *,
        session_id: str,
        turn_id: str,
        queue_id: str,
        user_message: str,
        reason: str,
        steps: list[dict[str, Any]],
    ) -> None:
        recent_tools = [
            str(step.get("tool_name") or "")
            for step in steps[-3:]
            if str(step.get("tool_name") or "")
        ]
        failure_class = self._classify_failure(reason=reason, steps=steps)
        reflection = (
            f"失敗パターン ({failure_class}): {reason}。 "
            f"ユーザーの依頼: {user_message[:120]}。 "
            f"直近のツール: {', '.join(recent_tools) or 'なし'}。 "
            "次ターンではスコープを絞り込み、事実（tool_result）の実績を重視し、安易な完了を避けるべきです。"
        )
        append_jsonl(
            self.paths.reflections_path,
            {
                "timestamp": now_iso(),
                "session_id": session_id,
                "turn_id": turn_id,
                "queue_id": queue_id,
                "reason": reason,
                "failure_class": failure_class,
                "reflection": reflection,
            },
        )
        self._append_session_event(
            self.root,
            session_id,
            {
                "type": "system_note",
                "role": "system",
                "content": f"reflection: {reflection}",
                "turn_id": turn_id,
                "queue_id": queue_id,
            },
        )
        self._write_runtime_status(last_reflection=reflection, worker_running=self._worker_running(), status=read_json(self.paths.runtime_status_path, fallback={}).get("status") or "idle")


    def _parse_envelope(self, raw_text: str) -> dict[str, Any]:
        raw_text = raw_text.strip()
        if not raw_text:
            return {"tool_name": "finish", "tool_args": {"final_answer": "No model output was produced."}}
        candidate = self._extract_json_object(raw_text)
        if candidate is None:
            return {"assistant_message": raw_text, "tool_name": "finish", "tool_args": {"final_answer": raw_text}}
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            return {"assistant_message": raw_text, "tool_name": "finish", "tool_args": {"final_answer": raw_text}}
        if not isinstance(payload, dict):
            return {"assistant_message": raw_text, "tool_name": "finish", "tool_args": {"final_answer": raw_text}}
        action = str(payload.get("action") or "").strip()
        tool_name = str(payload.get("tool_name") or action or "").strip()
        if tool_name == "final_answer":
            tool_args = payload.get("tool_args") if isinstance(payload.get("tool_args"), dict) else {}
            answer = str(tool_args.get("answer") or tool_args.get("final_answer") or payload.get("answer") or payload.get("assistant_message") or "")
            payload["tool_name"] = "finish"
            payload["tool_args"] = {"final_answer": answer}
            payload["assistant_message"] = str(payload.get("assistant_message") or answer)
        return payload








    def _worker_running(self) -> bool:
        if not self.paths.worker_pid_path.exists():
            return False
        try:
            pid = int(self.paths.worker_pid_path.read_text(encoding="utf-8").strip())
        except ValueError:
            return False
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def _write_runtime_status(
        self,
        *,
        status: str,
        current_role: Any = _UNSET,
        current_turn_id: Any = _UNSET,
        current_queue_id: Any = _UNSET,
        current_user_message: Any = _UNSET,
        current_prompt_preview: Any = _UNSET,
        current_stream_text: Any = _UNSET,
        current_plan: Any = _UNSET,
        current_phase: Any = _UNSET,
        current_started_at: Any = _UNSET,
        current_finished_at: Any = _UNSET,
        current_model: Any = _UNSET,
        current_model_reason: Any = _UNSET,
        current_operation_id: Any = _UNSET,
        current_tool: Any = _UNSET,
        current_llm_workspace: Any = _UNSET,
        last_llm_workspace: Any = _UNSET,
        last_error: Any = _UNSET,
        last_system_note: Any = _UNSET,
        last_reflection: Any = _UNSET,
        last_llm_started_at: Any = _UNSET,
        last_llm_finished_at: Any = _UNSET,
        last_llm_duration_ms: Any = _UNSET,
        last_llm_attempt_count: Any = _UNSET,
        last_llm_raw_preview: Any = _UNSET,
        last_llm_thinking_preview: Any = _UNSET,
        last_llm_parse_issue: Any = _UNSET,
        last_llm_stream_metadata: Any = _UNSET,
        worker_running: bool | None = None,
    ) -> None:
        current = read_json(self.paths.runtime_status_path, fallback={})
        payload = {
            **current,
            "status": status,
            "current_role": current.get("current_role") if current_role is _UNSET else current_role,
            "current_turn_id": current.get("current_turn_id") if current_turn_id is _UNSET else current_turn_id,
            "current_queue_id": current.get("current_queue_id") if current_queue_id is _UNSET else current_queue_id,
            "current_user_message": current.get("current_user_message") if current_user_message is _UNSET else current_user_message,
            "current_prompt_preview": current.get("current_prompt_preview") if current_prompt_preview is _UNSET else current_prompt_preview,
            "current_stream_text": current.get("current_stream_text") if current_stream_text is _UNSET else current_stream_text,
            "current_plan": current.get("current_plan") if current_plan is _UNSET else current_plan,
            "current_phase": current.get("current_phase") if current_phase is _UNSET else current_phase,
            "current_started_at": current.get("current_started_at") if current_started_at is _UNSET else current_started_at,
            "current_finished_at": current.get("current_finished_at") if current_finished_at is _UNSET else current_finished_at,
            "current_model": current.get("current_model") if current_model is _UNSET else current_model,
            "current_model_reason": current.get("current_model_reason") if current_model_reason is _UNSET else current_model_reason,
            "current_operation_id": current.get("current_operation_id") if current_operation_id is _UNSET else current_operation_id,
            "current_tool": current.get("current_tool") if current_tool is _UNSET else current_tool,
            "current_llm_workspace": current.get("current_llm_workspace") if current_llm_workspace is _UNSET else current_llm_workspace,
            "last_llm_workspace": current.get("last_llm_workspace") if last_llm_workspace is _UNSET else last_llm_workspace,
            "last_error": current.get("last_error") if last_error is _UNSET else last_error,
            "last_system_note": current.get("last_system_note") if last_system_note is _UNSET else last_system_note,
            "last_reflection": current.get("last_reflection") if last_reflection is _UNSET else last_reflection,
            "last_llm_started_at": current.get("last_llm_started_at") if last_llm_started_at is _UNSET else last_llm_started_at,
            "last_llm_finished_at": current.get("last_llm_finished_at") if last_llm_finished_at is _UNSET else last_llm_finished_at,
            "last_llm_duration_ms": current.get("last_llm_duration_ms") if last_llm_duration_ms is _UNSET else last_llm_duration_ms,
            "last_llm_attempt_count": current.get("last_llm_attempt_count", 0) if last_llm_attempt_count is _UNSET else last_llm_attempt_count,
            "last_llm_raw_preview": current.get("last_llm_raw_preview") if last_llm_raw_preview is _UNSET else last_llm_raw_preview,
            "last_llm_thinking_preview": current.get("last_llm_thinking_preview") if last_llm_thinking_preview is _UNSET else last_llm_thinking_preview,
            "last_llm_parse_issue": current.get("last_llm_parse_issue") if last_llm_parse_issue is _UNSET else last_llm_parse_issue,
            "last_llm_stream_metadata": current.get("last_llm_stream_metadata") if last_llm_stream_metadata is _UNSET else last_llm_stream_metadata,
            "last_event_at": now_iso(),
            "worker_running": current.get("worker_running") if worker_running is None else worker_running,
        }
        write_json(self.paths.runtime_status_path, payload)

# Helper methods are kept as AgentRuntime attributes so existing tests and
# monkeypatch extension points remain source-compatible after the split.
AgentRuntime._observer_enabled = _observer_enabled
AgentRuntime._maybe_record_observer_note = _maybe_record_observer_note
AgentRuntime._usable_japanese_observer_text = _usable_japanese_observer_text
AgentRuntime._deterministic_step_commentary = _deterministic_step_commentary
AgentRuntime._context_risk_summary = _context_risk_summary
AgentRuntime._record_observer_judgement_note = _record_observer_judgement_note
AgentRuntime._record_observer_llm_output_issue_note = _record_observer_llm_output_issue_note
AgentRuntime._grounding_issues = _grounding_issues
AgentRuntime._semantic_grounding_check = _semantic_grounding_check
AgentRuntime._parse_grounding_judge_payload = _parse_grounding_judge_payload
AgentRuntime.run_terminal_agent = run_terminal_agent
AgentRuntime._resolve_terminal_model = _resolve_terminal_model
AgentRuntime._preferred_shell_from_extra_prompt = _preferred_shell_from_extra_prompt
AgentRuntime._controller_terminal_finish = _controller_terminal_finish
AgentRuntime._terminal_answer_is_direct_evidence = _terminal_answer_is_direct_evidence
AgentRuntime._deterministic_terminal_final_answer = _deterministic_terminal_final_answer
AgentRuntime._synthesize_terminal_final_answer = _synthesize_terminal_final_answer
AgentRuntime._normalize_run_command_evidence = _normalize_run_command_evidence
AgentRuntime._output_preview = _output_preview
AgentRuntime._current_phase = _current_phase
AgentRuntime._deliberation_reasons = _deliberation_reasons
AgentRuntime._system_prompt = _system_prompt
AgentRuntime._output_budget_prompt = _output_budget_prompt
AgentRuntime._build_prompt = _build_prompt
AgentRuntime._render_action_context_events = _render_action_context_events
AgentRuntime._compact_context_text = _compact_context_text
AgentRuntime._build_planning_note = _build_planning_note
AgentRuntime._build_deliberation_note = _build_deliberation_note
AgentRuntime._reflection_prompt_block = _reflection_prompt_block
AgentRuntime._reflection_relevant_to_user = _reflection_relevant_to_user
AgentRuntime._chat_with_repair = _chat_with_repair
AgentRuntime._extract_stream_metadata = _extract_stream_metadata
AgentRuntime._format_llm_stream_text = _format_llm_stream_text
AgentRuntime._tail_stream_text = _tail_stream_text
AgentRuntime._json_repair_prompt = _json_repair_prompt
AgentRuntime._thinking_only_repair_prompt = _thinking_only_repair_prompt
AgentRuntime._classify_llm_parse_issue = _classify_llm_parse_issue
AgentRuntime._looks_like_truncated_json = _looks_like_truncated_json
AgentRuntime._looks_like_structured_envelope = _looks_like_structured_envelope
AgentRuntime._raw_contains_json_object = _raw_contains_json_object
AgentRuntime._extract_json_object = _extract_json_object
AgentRuntime._missing_requested_commands = _missing_requested_commands
AgentRuntime._expected_artifacts = _expected_artifacts
AgentRuntime._missing_expected_artifacts = _missing_expected_artifacts
AgentRuntime._failed_command_guardrail = _failed_command_guardrail
AgentRuntime._redundant_command_reason = _redundant_command_reason
AgentRuntime._extract_requested_commands = _extract_requested_commands
AgentRuntime._classify_failure = _classify_failure


def stop_worker(root: Path) -> dict[str, Any]:
    paths = WorkspacePaths(root)
    if not paths.worker_pid_path.exists():
        write_json(paths.runtime_status_path, {**read_json(paths.runtime_status_path, fallback={}), "worker_running": False})
        return {"ok": True, "stopped": False}
    try:
        pid = int(paths.worker_pid_path.read_text(encoding="utf-8").strip())
    except ValueError:
        paths.worker_pid_path.unlink(missing_ok=True)
        return {"ok": True, "stopped": False}
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass
    return {"ok": True, "stopped": True, "pid": pid}


def worker_command() -> list[str]:
    return [sys.executable, "-u", "-m", "p4_core.cli", "worker"]
