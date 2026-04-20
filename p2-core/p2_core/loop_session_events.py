from __future__ import annotations

import json
from typing import Any, Callable


def _record_session_event(
    *,
    timestamp: str,
    frame: dict[str, Any],
    frame_depth: int,
    step: int,
    action: str,
    action_input: dict[str, Any],
    thinking: str,
    result: dict[str, Any],
    append_event: Callable[[dict[str, Any]], None],
    update_frame_working_memory: Callable[[dict[str, Any], str, dict[str, Any], dict[str, Any]], None],
    attempt_report: dict[str, Any],
) -> None:
    event = {
        "timestamp": timestamp,
        "frame_id": frame.get("frame_id"),
        "frame_depth": frame_depth,
        "step": step,
        "action": action,
        "action_input": action_input,
        "thinking": thinking,
        "result": result,
    }
    append_event(event)
    context = frame.setdefault("context", {})
    local_tool_results = list(context.get("local_tool_results") or [])
    local_tool_results.append(json.loads(json.dumps(event, ensure_ascii=False)))
    context["local_tool_results"] = local_tool_results[-24:]
    update_frame_working_memory(frame, action, action_input, result)
    if attempt_report.get("task_stack") and attempt_report["task_stack"][-1].get("frame_id") == frame.get("frame_id"):
        attempt_report["task_stack"][-1] = json.loads(json.dumps(frame, ensure_ascii=False))
    attempt_report["task_frame"] = json.loads(json.dumps(frame, ensure_ascii=False))
