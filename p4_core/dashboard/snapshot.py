from __future__ import annotations
import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from p4_core.frames import FrameManager
from p4_core.ollama_client import OllamaChatClient
from p4_core.workspace import WorkspacePaths, active_session_id, read_json, read_jsonl, now_iso

_BENCHMARK_CASE_FALLBACKS: dict[str, dict[str, str]] = {
    "terminal_pwd_short": {"label": "単発パス確認", "phase": "basic-grounding"},
    "terminal_pwd_ls_summary": {"label": "複数コマンド要約", "phase": "multi-step-grounding"},
    "terminal_find_head_agents": {"label": "探索とプレビュー要約", "phase": "file-evidence-synthesis"},
    "terminal_git_status_then_pwd": {"label": "git 文脈確認", "phase": "multi-step-grounding"},
}
_BENCHMARK_PHASE_ORDER: dict[str, int] = {
    "basic-grounding": 0,
    "multi-step-grounding": 1,
    "file-evidence-synthesis": 2,
}

def _benchmark_phase_rank(phase: str) -> int:
    return _BENCHMARK_PHASE_ORDER.get(str(phase or ""), 99)

def _reasoning_model(root: Path) -> str:
    config = read_json(WorkspacePaths(root).config_path, fallback={})
    models = config.get("models", {}) if isinstance(config, dict) else {}
    return str(models.get("reasoning") or "gemma4:26b")

def _available_models(root: Path) -> list[str]:
    config = read_json(WorkspacePaths(root).config_path, fallback={})
    base_url = str(config.get("ollama_base_url") or "http://127.0.0.1:11434") if isinstance(config, dict) else "http://127.0.0.1:11434"
    try:
        payload = OllamaChatClient(base_url=base_url).list_models()
        models = [str(item.get("name") or "").strip() for item in payload.get("models") or []]
        models = [item for item in models if item]
        if models:
            return models
    except Exception:
        pass
    return [_reasoning_model(root)]

def _duration_ms(started_at: str, finished_at: str) -> int | None:
    try:
        start = datetime.fromisoformat(started_at)
        finish = datetime.fromisoformat(finished_at)
    except ValueError:
        return None
    return max(0, int((finish - start).total_seconds() * 1000))

def _parse_timestamp(value: str | None) -> datetime | None:
    if not value: return None
    try: return datetime.fromisoformat(value)
    except ValueError: return None

def _activity_updates(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [
        {
            "timestamp": row.get("timestamp"),
            "message": row.get("content") or "",
            "status": row.get("status") or "info",
        }
        for row in events
        if row.get("type") == "activity_update"
    ][-20:]
    rows.reverse()
    return rows

def _operation_rows(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered_ids: list[str] = []
    operations: dict[str, dict[str, Any]] = {}
    current_scan_op_id = None

    for row in events:
        event_type = row.get("type")
        if event_type == "operation":
            operation_id = str(row.get("operation_id") or row.get("event_id") or uuid.uuid4().hex)
            if operation_id not in operations:
                ordered_ids.append(operation_id)
                operations[operation_id] = {
                    "operation_id": operation_id,
                    "title": "",
                    "detail": "",
                    "status": "running",
                    "started_at": None,
                    "finished_at": None,
                    "duration_ms": None,
                    "output_preview": "",
                    "trace_preview": "",
                    "flow_preview": "",
                    "flow_steps": [],
                    "timestamp": row.get("timestamp"),
                    "last_event_at": row.get("timestamp"),
                    "max_step_index": 0,
                }
            current = operations[operation_id]
            for key in ("title", "detail", "status", "started_at", "finished_at", "duration_ms", "output_preview", "trace_preview", "flow_preview", "flow_steps"):
                value = row.get(key)
                if value is not None and value != "":
                    current[key] = value
            current["timestamp"] = row.get("timestamp") or current.get("timestamp")
            current["last_event_at"] = row.get("timestamp") or current.get("last_event_at")
            if current.get("status") == "running":
                current_scan_op_id = operation_id
            else:
                current_scan_op_id = None
        elif current_scan_op_id:
            current = operations[current_scan_op_id]
            current["last_event_at"] = row.get("timestamp") or current.get("last_event_at")
            if row.get("turn_id") and not current.get("turn_id"):
                current["turn_id"] = str(row.get("turn_id") or "")
            if row.get("queue_id") and not current.get("queue_id"):
                current["queue_id"] = str(row.get("queue_id") or "")
            step_idx = row.get("step_index")
            if step_idx is not None:
                current["max_step_index"] = max(current.get("max_step_index", 0), int(step_idx))

    rows = [operations[op_id] for op_id in ordered_ids][-20:]
    rows.reverse()
    return rows

def _normalize_operation_rows(runtime: dict[str, Any], operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    runtime_status = str(runtime.get("status") or "")
    worker_running = bool(runtime.get("worker_running", False))
    now = now_iso()
    chrono = list(reversed(operations))
    for i, current in enumerate(chrono):
        if str(current.get("status") or "") != "running":
            continue
        is_active_in_runtime = False
        current_op_id_in_runtime = str(runtime.get("current_operation_id") or "")
        if runtime_status.startswith("running"):
            current_turn_id = str(runtime.get("current_turn_id") or "")
            current_started_at = str(runtime.get("current_started_at") or "")
            if current_op_id_in_runtime and str(current.get("operation_id") or "") == current_op_id_in_runtime:
                is_active_in_runtime = True
            elif current_turn_id and str(current.get("turn_id") or "") == current_turn_id:
                is_active_in_runtime = True
            elif current_started_at and str(current.get("started_at") or "") == current_started_at:
                is_active_in_runtime = True
        started_at_str = str(current.get("started_at") or "")
        last_event_at_str = str(current.get("last_event_at") or "")
        age_seconds = 9999
        if started_at_str:
            try:
                started_dt = _parse_timestamp(started_at_str)
                now_dt = _parse_timestamp(now)
                age_seconds = (now_dt - started_dt).total_seconds()
            except Exception: pass
        activity_age_seconds = 9999
        if last_event_at_str:
            try:
                last_dt = _parse_timestamp(last_event_at_str)
                now_dt = _parse_timestamp(now)
                activity_age_seconds = (now_dt - last_dt).total_seconds()
            except Exception: pass
        if is_active_in_runtime and (worker_running or activity_age_seconds < 120):
            continue
        if age_seconds < 60: continue
        if activity_age_seconds < 120 and worker_running: continue
        if current_op_id_in_runtime and current_op_id_in_runtime != str(current.get("operation_id")): pass
        elif worker_running and activity_age_seconds < 300: continue
        current["status"] = "failed"
        next_op_start = None
        if i + 1 < len(chrono):
            next_op_start = chrono[i+1].get("started_at")
        runtime_hint = str(runtime.get("last_event_at") or "")
        if not current.get("finished_at"):
            current["finished_at"] = next_op_start or runtime_hint or now
        if not current.get("duration_ms") and current.get("started_at") and current.get("finished_at"):
            current["duration_ms"] = _duration_ms(str(current.get("started_at") or ""), str(current.get("finished_at") or ""))
        detail = str(current.get("detail") or "")
        if "normalized from stale" not in detail:
            reason = "another run is active" if runtime_status.startswith("running") else "runtime is idle and last activity was too long ago"
            current["detail"] = (detail + f"\n\n[normalized from stale running operation because {reason}]").strip()
    return list(reversed(chrono))

def _trace_preview_for_operation(operation: dict[str, Any], events: list[dict[str, Any]]) -> str:
    started_at = _parse_timestamp(str(operation.get("started_at") or ""))
    finished_at = _parse_timestamp(str(operation.get("finished_at") or ""))
    if started_at is None: return ""
    trace_lines: list[str] = []
    for row in events:
        row_time = _parse_timestamp(str(row.get("timestamp") or ""))
        if row_time is None or row_time < started_at: continue
        if finished_at is not None and row_time > finished_at + timedelta(seconds=2): continue
        row_type = str(row.get("type") or "")
        if row_type not in {"user_message", "assistant_message", "tool_call", "tool_result", "finish", "system_note", "planning_note", "observer_note", "activity_update", "frame_opened", "frame_returned", "child_return"}: continue
        if row_type == "user_message":
            trace_lines.append(f"[user_message] {row.get('content') or ''}")
        elif row_type == "assistant_message":
            trace_lines.append(f"[assistant_message] {row.get('content') or ''}")
        elif row_type == "activity_update":
            trace_lines.append(f"[activity_update] {row.get('content') or ''}")
        elif row_type == "tool_call":
            trace_lines.append(f"[tool_call] {row.get('tool_name') or ''} args={json.dumps(row.get('tool_args') or {}, ensure_ascii=False)}")
        elif row_type == "tool_result":
            trace_lines.append(f"[tool_result] {row.get('tool_name') or ''} -> {row.get('content') or ''}")
        elif row_type == "finish":
            trace_lines.append(f"[finish] {row.get('content') or ''}")
    return "\n\n".join(trace_lines)[-16000:]

def _event_in_operation_window(row: dict[str, Any], operation: dict[str, Any]) -> bool:
    op_id = str(operation.get("operation_id") or "")
    row_op_id = str(row.get("operation_id") or "")
    if op_id and row_op_id and op_id == row_op_id: return True
    op_turn_id = str(operation.get("turn_id") or "")
    row_turn_id = str(row.get("turn_id") or "")
    if op_turn_id and row_turn_id and op_turn_id == row_turn_id: return True
    row_time = _parse_timestamp(str(row.get("timestamp") or ""))
    started_at = _parse_timestamp(str(operation.get("started_at") or ""))
    finished_at = _parse_timestamp(str(operation.get("finished_at") or ""))
    if row_time is None or started_at is None or row_time < started_at: return False
    if finished_at is not None and row_time > finished_at + timedelta(seconds=2): return False
    return True

def _flow_steps_for_operation(operation: dict[str, Any], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Import locally to avoid circular dependency
    from p4_core.dashboard.templates import _phase_for_flow_step
    grouped: dict[int, dict[str, Any]] = {}
    order: list[int] = []
    frame_depth_by_id: dict[str, int] = {}
    frame_stack: list[str] = []
    for row in events:
        row_type = str(row.get("type") or "")
        if row_type not in {"user_message", "assistant_message", "tool_call", "tool_result", "finish", "system_note", "planning_note", "observer_note", "activity_update", "frame_opened", "frame_returned", "child_return"}: continue
        if not _event_in_operation_window(row, operation): continue
        item_frame_id = frame_stack[-1] if frame_stack else ""
        item_frame_depth = len(frame_stack)
        step_index = int(row.get("step_index") or 0)
        if step_index not in grouped:
            order.append(step_index)
            grouped[step_index] = {
                "step_index": step_index,
                "title": "Input" if step_index == 0 else f"Step {step_index}",
                "items": [],
            }
        if row_type == "tool_call":
            item = {
                "label": row_type,
                "tool_name": str(row.get("tool_name") or ""),
                "content": f"{row.get('tool_name') or ''} args={json.dumps(row.get('tool_args') or {}, ensure_ascii=False)}",
                "tool_args": row.get("tool_args") or {},
            }
        elif row_type == "tool_result":
            parsed_payload: dict[str, Any] | None = None
            try:
                loaded = json.loads(str(row.get("content") or ""))
                if isinstance(loaded, dict): parsed_payload = loaded
            except json.JSONDecodeError: pass
            item = {
                "label": row_type,
                "tool_name": str(row.get("tool_name") or ""),
                "content": str(row.get("content") or ""),
                "parsed_payload": parsed_payload,
            }
        elif row_type in {"system_note", "planning_note", "observer_note", "activity_update"}:
            item = {
                "label": row_type,
                "content": str(row.get("content") or ""),
                "code": str(row.get("code") or ""),
                "reason_code": str(row.get("reason_code") or ""),
                "details": row.get("details") or {},
            }
        elif row_type in {"frame_opened", "frame_returned", "child_return"}:
            item = {
                "label": row_type,
                "content": str(row.get("content") or ""),
                "frame_id": str(row.get("frame_id") or ""),
                "parent_frame_id": str(row.get("parent_frame_id") or ""),
                "child_frame_id": str(row.get("child_frame_id") or ""),
                "goal": str(row.get("goal") or ""),
                "return_payload": row.get("return_payload") or {},
            }
        else:
            item = {"label": row_type, "content": str(row.get("content") or "")}
        if row_type == "frame_opened":
            parent_id = str(row.get("parent_frame_id") or "")
            frame_id = str(row.get("frame_id") or "")
            parent_depth = frame_depth_by_id.get(parent_id, 0) if parent_id else 0
            item_frame_id = frame_id
            item_frame_depth = parent_depth + 1
            if frame_id:
                frame_depth_by_id[frame_id] = item_frame_depth
                frame_stack.append(frame_id)
        elif row_type == "frame_returned":
            returned_id = str(row.get("frame_id") or "")
            item_frame_id = returned_id or item_frame_id
            item_frame_depth = frame_depth_by_id.get(returned_id, item_frame_depth)
        elif row_type == "child_return":
            returned_id = str(row.get("child_frame_id") or "")
            item_frame_id = returned_id or item_frame_id
            item_frame_depth = frame_depth_by_id.get(returned_id, item_frame_depth)
            if returned_id:
                while frame_stack and frame_stack[-1] != returned_id:
                    frame_stack.pop()
                if frame_stack and frame_stack[-1] == returned_id:
                    frame_stack.pop()
        item["frame_id_for_display"] = item_frame_id
        item["frame_depth"] = item_frame_depth
        if row_type == "observer_note":
            grouped[step_index]["items"] = [
                existing
                for existing in grouped[step_index]["items"]
                if str(existing.get("label") or "") != "observer_note"
            ]
        grouped[step_index]["items"].append(item)
    rows = [grouped[index] for index in order]
    for row in rows:
        row["phase"] = _phase_for_flow_step(row)
    return rows

def _latest_blocked_reason(flow_steps: list[dict[str, Any]]) -> str | None:
    latest_block: str | None = None
    latest_finish_seen = False
    for step in flow_steps:
        for item in step.get("items") or []:
            label = str(item.get("label") or "")
            content = str(item.get("content") or "")
            code = str(item.get("code") or "")
            if label == "finish":
                latest_finish_seen = True
            elif label == "system_note" and (code == "finish_blocked" or "完了がブロックされました" in content):
                latest_block = content
                latest_finish_seen = False
    if latest_block and not latest_finish_seen:
        return latest_block
    return None

def _commentator_notes(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    notes: list[dict[str, Any]] = []
    for row in reversed(events):
        if str(row.get("type") or "") != "observer_note":
            continue
        notes.append(
            {
                "timestamp": str(row.get("timestamp") or ""),
                "content": str(row.get("content") or ""),
                "step_index": row.get("step_index"),
                "code": str(row.get("code") or ""),
                "reason_code": str(row.get("reason_code") or ""),
                "model": str(row.get("model") or ""),
            }
        )
        if len(notes) >= 5:
            break
    return notes

def build_snapshot(root: Path) -> dict[str, Any]:
    paths = WorkspacePaths(root)
    session_id = active_session_id(root)
    runtime = read_json(paths.runtime_status_path, fallback={})
    benchmark = read_json(paths.benchmark_status_path, fallback={})
    session = read_json(paths.session_meta_path(session_id), fallback={})
    events = read_jsonl(paths.session_events_path(session_id), limit=120)
    transcript = [row for row in events if row.get("type") in {"user_message", "assistant_message"}][-40:]
    transcript.reverse()
    operations = _normalize_operation_rows(runtime, _operation_rows(events))
    if operations:
        for index, operation in enumerate(operations):
            if index == 0 and str(operation.get("status") or "") == "running":
                operation["output_preview"] = str(runtime.get("current_stream_text") or operation.get("output_preview") or "")
            trace_preview = _trace_preview_for_operation(operation, events)
            if trace_preview:
                operation["trace_preview"] = trace_preview
                operation["flow_preview"] = trace_preview
            operation["flow_steps"] = _flow_steps_for_operation(operation, events)
            blocked_reason = _latest_blocked_reason(operation["flow_steps"])
            if blocked_reason:
                operation["blocked_reason"] = blocked_reason
                if str(operation.get("status") or "") != "running" or not bool(runtime.get("worker_running", False)):
                    operation["status"] = "blocked"
                if not operation.get("output_preview") or str(operation.get("status") or "") == "blocked":
                    operation["output_preview"] = blocked_reason
            operation_assistant = next((row for row in events if row.get("type") in {"assistant_message", "finish"} and _event_in_operation_window(row, operation)), None)
            if operation_assistant is not None:
                operation["transcript_preview"] = str(operation_assistant.get("content") or "")
            if str(operation.get("status") or "") != "running":
                latest_tool_result = next((row for row in reversed(events) if row.get("type") == "tool_result" and _event_in_operation_window(row, operation)), None)
                if latest_tool_result is not None and str(operation.get("status") or "") != "blocked":
                    operation["output_preview"] = str(latest_tool_result.get("content") or operation.get("output_preview") or "")
    return {
        "root": str(Path(root).expanduser().resolve()),
        "model": _reasoning_model(root),
        "available_models": _available_models(root),
        "runtime": runtime,
        "benchmark": benchmark,
        "session": session,
        "recent_transcript": transcript,
        "recent_updates": _activity_updates(events),
        "recent_operations": operations,
        "commentator_notes": _commentator_notes(events),
        "frames": FrameManager(root).snapshot(),
    }
