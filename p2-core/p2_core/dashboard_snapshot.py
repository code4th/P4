from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from p2_core.dashboard_context import (
    attempt_history_from_disk,
    build_context_audit,
    build_task_hierarchy,
    build_thought_history,
    recent_attempt_thought_history,
    thought_history_from_hierarchy,
)
from p2_core.dashboard_generation import build_generation_report
from p2_core.dashboard_presenter import (
    clone_reason_from_attempt,
    decision_explanation,
    humanize_response_text,
    validation_summary,
)
from p2_core.dashboard_projection import (
    build_implementation_notes,
    build_operator_insights,
    derive_reasoning_summary,
)
from p2_core.workspace import build_status_snapshot, read_validation_report


def _split_stream_sections(text: str) -> dict[str, str]:
    if not text:
        return {}
    sections: dict[str, list[str]] = {}
    current_label: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip("\n")
        if line.startswith("=====") and line.endswith("====="):
            label = line.strip("=").strip()
            current_label = label
            sections.setdefault(label, [])
            continue
        if current_label is None:
            continue
        sections.setdefault(current_label, []).append(line)
    return {label: "\n".join(lines).strip() for label, lines in sections.items()}


def _session_event_summary(event: dict[str, Any]) -> str:
    action = str(event.get("action") or "unknown")
    result = event.get("result") or {}
    step = event.get("step")
    prefix = f"step {step}: {action}"
    if not isinstance(result, dict):
        return prefix
    if result.get("ok") is False:
        detail = result.get("summary") or result.get("error") or result.get("message") or "失敗"
        return f"{prefix} -> {detail}"
    if action == "read_file":
        return f"{prefix} -> {result.get('relative_path') or result.get('path')}"
    if action == "search_code":
        return f"{prefix} -> match {result.get('count', 0)} 件"
    if action == "apply_patch":
        return f"{prefix} -> +{result.get('added_lines', 0)} / -{result.get('removed_lines', 0)}"
    if action == "run_validation":
        return f"{prefix} -> {'成功' if result.get('passed') else '失敗'}"
    if action == "open_child_frame":
        return f"{prefix} -> {result.get('next_goal') or 'child'}"
    if action == "return_to_parent":
        return f"{prefix} -> 親へ戻る"
    if action == "attempt_started":
        return f"{prefix} -> attempt を開始"
    if action == "attempt_resumed":
        return f"{prefix} -> attempt を再開"
    if action == "runtime_failed":
        return f"{prefix} -> {result.get('reason') or result.get('note') or 'ランタイム異常'}"
    if action == "finish":
        return f"{prefix} -> 完了"
    return prefix


def _event_depth(event: dict[str, Any], frame: dict[str, Any] | None) -> int:
    raw_depth = event.get("frame_depth")
    if raw_depth is None:
        raw_depth = event.get("depth")
    if raw_depth is None and frame:
        raw_depth = frame.get("depth")
    if raw_depth is None:
        frame_id = str(event.get("frame_id") or "")
        match = re.search(r":d(\d+):", frame_id)
        raw_depth = int(match.group(1)) if match else 0
    try:
        return max(0, int(raw_depth))
    except (TypeError, ValueError):
        return 0


def _dump_compact(value: Any) -> str:
    if value is None or value == "" or value == [] or value == {}:
        return ""
    if isinstance(value, str):
        return value.strip()
    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return str(value)


def _event_result_detail(event: dict[str, Any], summary_map: dict[str, str]) -> str:
    step_key = str(event.get("step"))
    summary = str(summary_map.get(step_key) or "").strip()
    result = event.get("result") or {}
    if summary:
        detail = result.get("detail") or result.get("message") or ""
        if detail and str(detail).strip() and str(detail).strip() not in summary:
            return f"{summary}\n{detail}"
        return summary
    return _dump_compact(result)


def _transition_label(
    current_event: dict[str, Any],
    next_event: dict[str, Any] | None,
    hierarchy_by_id: dict[str, dict[str, Any]],
) -> str:
    if not isinstance(next_event, dict):
        return ""
    current_frame_id = str(current_event.get("frame_id") or "")
    next_frame_id = str(next_event.get("frame_id") or "")
    if not current_frame_id or not next_frame_id or current_frame_id == next_frame_id:
        return ""
    current_frame = hierarchy_by_id.get(current_frame_id) or {}
    next_frame = hierarchy_by_id.get(next_frame_id) or {}
    current_depth = _event_depth(current_event, current_frame)
    next_depth = _event_depth(next_event, next_frame)
    if str(next_frame.get("parent_frame_id") or "") == current_frame_id or next_depth > current_depth:
        return "次に子フレームへ移動"
    if str(current_frame.get("parent_frame_id") or "") == next_frame_id or next_depth < current_depth:
        return "次に親フレームへ復帰"
    return "次に別フレームへ遷移"


def build_thought_action_chain(
    session_events: list[dict[str, Any]],
    task_hierarchy: list[dict[str, Any]],
    summary_map: dict[str, str],
) -> list[dict[str, Any]]:
    if not session_events:
        return []
    ordered_events = sorted(
        (event for event in session_events if isinstance(event, dict)),
        key=lambda event: (
            int(event.get("step") or 0),
            str(event.get("timestamp") or ""),
        ),
    )
    hierarchy_by_id = {
        str(frame.get("frame_id")): frame
        for frame in task_hierarchy
        if isinstance(frame, dict) and frame.get("frame_id")
    }
    chain: list[dict[str, Any]] = []
    for index, event in enumerate(ordered_events):
        frame_id = str(event.get("frame_id") or "")
        frame = hierarchy_by_id.get(frame_id) or {}
        depth = _event_depth(event, frame)
        next_event = ordered_events[index + 1] if index + 1 < len(ordered_events) else None
        next_frame_id = str((next_event or {}).get("frame_id") or "")
        next_frame = hierarchy_by_id.get(next_frame_id) or {}
        next_thinking = str((next_event or {}).get("thinking") or "").strip()
        chain.append(
            {
                "index": index + 1,
                "timestamp": event.get("timestamp") or "",
                "frame_id": frame_id,
                "frame_goal": frame.get("goal") or frame.get("frame_goal") or "",
                "parent_frame_id": frame.get("parent_frame_id") or "",
                "depth": depth,
                "step": event.get("step"),
                "thinking": str(event.get("thinking") or "").strip(),
                "action": str(event.get("action") or "unknown"),
                "action_input_text": _dump_compact(event.get("action_input") or {}),
                "result_text": _event_result_detail(event, summary_map),
                "result_ok": None if not isinstance(event.get("result"), dict) else event.get("result", {}).get("ok"),
                "next_frame_id": next_frame_id,
                "next_frame_goal": next_frame.get("goal") or next_frame.get("frame_goal") or "",
                "next_action": str((next_event or {}).get("action") or ""),
                "next_thinking": next_thinking,
                "transition_label": _transition_label(event, next_event, hierarchy_by_id),
            }
        )
    return chain


def _latest_completed_attempt(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    attempts = snapshot.get("recent_attempts") or []
    for attempt in reversed(attempts):
        if attempt.get("status") != "started":
            return attempt
    return None


def _read_attempt_events(root: Path, candidate_id: str | None) -> list[dict[str, Any]]:
    candidate = str(candidate_id or "").strip()
    if not candidate:
        return []
    path = root / "state" / "attempts" / f"{candidate}.events.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _read_latest_available_attempt_events(root: Path, attempts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for attempt in attempts:
        rows = _read_attempt_events(root, attempt.get("candidate_id"))
        if rows:
            return rows
    attempts_dir = root / "state" / "attempts"
    try:
        candidates = sorted(attempts_dir.glob("c*.events.jsonl"), reverse=True)
    except OSError:
        return []
    for path in candidates:
        candidate_id = path.stem.replace(".events", "")
        rows = _read_attempt_events(root, candidate_id)
        if rows:
            return rows
    return []


def _build_latest_context_frame(
    *,
    goal: dict[str, Any],
    runtime: dict[str, Any],
    snapshot: dict[str, Any],
    latest_attempt: dict[str, Any],
    latest_completed_attempt: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    context_source = latest_attempt or latest_completed_attempt or {}
    current_task_stack = snapshot.get("current_task_stack") or runtime.get("current_task_stack") or []
    latest_frame_trace = snapshot.get("latest_frame_trace") or []
    hierarchy_source = current_task_stack or latest_frame_trace
    if hierarchy_source:
        latest_context_frame = hierarchy_source[-1]
    elif snapshot.get("latest_task_frame"):
        latest_context_frame = snapshot.get("latest_task_frame")
    else:
        selected_context_payload = context_source.get("selected_context") or {}
        delta_context = context_source.get("delta_context") or {}
        latest_context_frame = {
            "parent_goal": goal.get("text"),
            "frame_goal": context_source.get("purpose"),
            "target_file": context_source.get("target_file"),
            "search_mode": context_source.get("search_mode"),
            "question_to_answer": selected_context_payload.get("question_to_answer"),
            "commitment": selected_context_payload.get("commitment"),
            "selected_context": list(selected_context_payload.get("selected_context") or []),
            "resolved_context_keys": sorted(list((context_source.get("resolved_context") or {}).keys())),
            "latest_failure": delta_context.get("latest_failure") or {},
            "must_avoid_next": list(delta_context.get("must_avoid_next") or []),
        }
    if latest_context_frame:
        frame_context = latest_context_frame.get("context") or {}
        frame_parent_context = frame_context.get("parent_context") or {}
        frame_local_context = frame_context.get("local_context") or {}
        frame_delta_context = frame_context.get("delta_context") or {}
        latest_context_frame = {
            **latest_context_frame,
            "frame_goal": latest_context_frame.get("frame_goal") or latest_context_frame.get("goal") or "",
            "parent_goal": latest_context_frame.get("parent_goal") or frame_parent_context.get("goal") or "",
            "target_file": latest_context_frame.get("target_file") or frame_local_context.get("target_file") or "",
            "search_mode": latest_context_frame.get("search_mode") or frame_local_context.get("search_mode") or "",
            "question_to_answer": latest_context_frame.get("question_to_answer")
            or frame_parent_context.get("why_this_frame_exists")
            or "",
            "commitment": latest_context_frame.get("commitment") or "",
            "selected_context": list(
                latest_context_frame.get("selected_context") or frame_local_context.get("selected_context") or []
            ),
            "resolved_context_keys": list(
                latest_context_frame.get("resolved_context_keys")
                or frame_local_context.get("resolved_context_keys")
                or []
            ),
            "latest_failure": latest_context_frame.get("latest_failure")
            or frame_delta_context.get("latest_failure")
            or {},
            "must_avoid_next": list(
                latest_context_frame.get("must_avoid_next") or frame_delta_context.get("must_avoid_next") or []
            ),
            "inherited_frame_ids": list(
                latest_context_frame.get("inherited_frame_ids")
                or list(((frame_context.get("inherited_context") or {}).get("ancestor_frame_ids") or []))
            ),
            "inherited_goal_chain": list(
                latest_context_frame.get("inherited_goal_chain")
                or list(((frame_context.get("inherited_context") or {}).get("ancestor_goals") or []))
            ),
            "inherited_tool_result_count": int(
                latest_context_frame.get("inherited_tool_result_count")
                or len(list(((frame_context.get("inherited_context") or {}).get("ancestor_tool_results") or [])))
            ),
            "inherited_findings": list(
                latest_context_frame.get("inherited_findings")
                or list(
                    (((frame_context.get("inherited_context") or {}).get("inherited_working_memory") or {}).get(
                        "learned_findings"
                    ) or [])
                )
            ),
            "local_tool_result_count": int(
                latest_context_frame.get("local_tool_result_count")
                or len(list(frame_context.get("local_tool_results") or []))
            ),
            "local_observed_files": list(
                latest_context_frame.get("local_observed_files")
                or list((frame_context.get("local_working_memory") or {}).get("observed_files") or [])
            ),
            "local_observed_symbols": list(
                latest_context_frame.get("local_observed_symbols")
                or list((frame_context.get("local_working_memory") or {}).get("observed_symbols") or [])
            ),
            "local_learned_findings": list(
                latest_context_frame.get("local_learned_findings")
                or list((frame_context.get("local_working_memory") or {}).get("learned_findings") or [])
            ),
            "local_unresolved_questions": list(
                latest_context_frame.get("local_unresolved_questions")
                or list((frame_context.get("local_working_memory") or {}).get("unresolved_questions") or [])
            ),
            "current_focus": latest_context_frame.get("current_focus")
            or (frame_context.get("local_working_memory") or {}).get("current_focus")
            or "",
            "child_return_count": int(
                latest_context_frame.get("child_return_count")
                or len(list(frame_context.get("child_return_payloads") or []))
            ),
            "child_return_summaries": list(
                latest_context_frame.get("child_return_summaries")
                or [
                    payload.get("summary")
                    for payload in (frame_context.get("child_return_payloads") or [])
                    if isinstance(payload, dict) and payload.get("summary")
                ]
            ),
            "return_payload_summary": latest_context_frame.get("return_payload_summary")
            or ((frame_context.get("return_payload") or {}).get("summary") or ""),
        }
    return latest_context_frame, hierarchy_source, latest_frame_trace


def build_public_snapshot(root: Path) -> dict[str, Any]:
    snapshot = build_status_snapshot(root)
    goal = snapshot.get("goal") or {}
    version = snapshot.get("version") or {}
    runtime = snapshot.get("runtime_status") or {}
    latest_attempt = snapshot.get("latest_attempt") or {}
    latest_validation = snapshot.get("latest_validation") or {}
    latest_retry_validation = snapshot.get("latest_retry_validation") or {}
    raw_recent_history = snapshot.get("recent_history") or []
    recent_attempts = [
        {
            "candidate_id": attempt.get("candidate_id"),
            "status": attempt.get("status"),
            "target_file": attempt.get("target_file"),
            "decision_reason": attempt.get("decision_reason"),
            "created_at": attempt.get("created_at"),
            "frame_trace": attempt.get("frame_trace") or [],
            "validation_summary": validation_summary(read_validation_report(root, attempt.get("candidate_id")))
            if attempt.get("candidate_id") and attempt.get("status") != "started"
            else "",
        }
        for attempt in (snapshot.get("recent_attempts") or [])
    ][::-1]
    recent_history = [
        {
            "timestamp": row.get("timestamp"),
            "step": row.get("step"),
            "outcome": row.get("outcome"),
            "message": row.get("message"),
        }
        for row in (snapshot.get("recent_history") or [])
    ][::-1]
    latest_completed_attempt = _latest_completed_attempt(snapshot)
    latest_completed_validation = None
    latest_completed_validation_summary = ""
    if latest_completed_attempt:
        latest_completed_validation = read_validation_report(root, latest_completed_attempt["candidate_id"])
        latest_completed_validation_summary = validation_summary(latest_completed_validation)
        if not latest_completed_validation_summary:
            latest_completed_validation_summary = str(latest_completed_attempt.get("decision_reason") or "")
    derived_reasoning_summary = derive_reasoning_summary(
        snapshot=snapshot,
        latest_attempt=latest_attempt,
        latest_completed_attempt=latest_completed_attempt,
    )
    current_stream_text = snapshot.get("current_stream_text") or ""
    if not current_stream_text and latest_completed_attempt:
        stream_path = latest_completed_attempt.get("stream_log_path")
        if stream_path:
            try:
                current_stream_text = Path(stream_path).read_text(encoding="utf-8")
            except OSError:
                current_stream_text = ""
    current_stream_sections = _split_stream_sections(current_stream_text)
    current_candidate_id = (
        str((runtime.get("current_candidate_id") or "")).strip()
        or str((latest_attempt or {}).get("candidate_id") or "").strip()
    )
    current_candidate_events = _read_attempt_events(root, current_candidate_id)
    latest_session_events = list(reversed(snapshot.get("latest_session_events") or []))
    thought_action_chain_source = "current_snapshot"
    if not latest_session_events:
        latest_session_events = current_candidate_events
        if latest_session_events:
            thought_action_chain_source = f"candidate:{current_candidate_id}"
    if not latest_session_events:
        latest_session_events = _read_attempt_events(root, (latest_completed_attempt or {}).get("candidate_id"))
        if latest_session_events:
            thought_action_chain_source = f"candidate:{(latest_completed_attempt or {}).get('candidate_id')}"
    if not latest_session_events:
        latest_session_events = _read_latest_available_attempt_events(root, snapshot.get("recent_attempts") or [])
        if latest_session_events:
            first_frame_id = str((latest_session_events[0] or {}).get("frame_id") or "")
            candidate_hint = first_frame_id.split(":")[0] if ":" in first_frame_id else "latest_available"
            thought_action_chain_source = f"fallback:{candidate_hint}"
    latest_prompt_snapshots = list(reversed(snapshot.get("latest_prompt_snapshots") or []))
    latest_prompt_snapshot = snapshot.get("latest_prompt_snapshot") or (
        latest_prompt_snapshots[0] if latest_prompt_snapshots else None
    )
    latest_session_event_summary_map = {
        str(event.get("step")): _session_event_summary(event)
        for event in latest_session_events
        if isinstance(event, dict)
    }
    latest_context_frame, hierarchy_source, latest_frame_trace = _build_latest_context_frame(
        goal=goal,
        runtime=runtime,
        snapshot=snapshot,
        latest_attempt=latest_attempt,
        latest_completed_attempt=latest_completed_attempt,
    )
    task_hierarchy = build_task_hierarchy(hierarchy_source, latest_context_frame)
    thought_history = build_thought_history(
        raw_recent_history,
        candidate_id=(latest_attempt or {}).get("candidate_id") or (latest_completed_attempt or {}).get("candidate_id"),
    )
    if len(thought_history) <= 1:
        recent_attempt_history = recent_attempt_thought_history(snapshot.get("recent_attempts") or [])
        if not recent_attempt_history:
            recent_attempt_history = attempt_history_from_disk(root)
        if recent_attempt_history:
            thought_history = recent_attempt_history
    if not thought_history:
        thought_history = thought_history_from_hierarchy(task_hierarchy)
    thought_action_chain = build_thought_action_chain(
        latest_session_events,
        task_hierarchy,
        latest_session_event_summary_map,
    )
    current_hierarchy_frame = next(
        (frame for frame in reversed(task_hierarchy) if frame.get("is_current")),
        latest_context_frame,
    )
    context_audit = build_context_audit(current_hierarchy_frame)
    generation_report = build_generation_report(root)
    public_snapshot = {
        "generated_at": snapshot.get("generated_at"),
        "goal": {
            "goal_id": goal.get("goal_id"),
            "text": goal.get("text"),
            "status": goal.get("status"),
            "cycle_count": goal.get("cycle_count"),
            "next_focus_index": goal.get("next_focus_index"),
            "last_promoted_candidate_id": goal.get("last_promoted_candidate_id"),
        },
        "version": {
            "active_generation": version.get("active_generation"),
            "active_version_id": version.get("active_version_id"),
        },
        "runtime_status": {
            "status": runtime.get("status"),
            "current_candidate_id": runtime.get("current_candidate_id"),
            "current_task_stack": runtime.get("current_task_stack"),
            "current_runtime_kernel": runtime.get("current_runtime_kernel"),
            "current_action": runtime.get("current_action"),
            "current_action_step": runtime.get("current_action_step"),
            "latest_frame_trace": latest_frame_trace,
            "phase": runtime.get("phase"),
            "phase_started_at": runtime.get("phase_started_at"),
            "last_output_at": runtime.get("last_output_at"),
            "last_event": runtime.get("last_event"),
            "model": runtime.get("model"),
            "thinking_model": runtime.get("thinking_model"),
            "coding_model": runtime.get("coding_model"),
            "exploratory_coding_model": runtime.get("exploratory_coding_model"),
            "stagnation_coding_model": runtime.get("stagnation_coding_model"),
            "last_loop_started_at": runtime.get("last_loop_started_at"),
            "last_loop_finished_at": runtime.get("last_loop_finished_at"),
        },
        "active_generation": version.get("active_generation"),
        "active_version_id": version.get("active_version_id"),
        "latest_reasoning_summary": derived_reasoning_summary,
        "latest_situation_report": snapshot.get("latest_situation_report"),
        "latest_pre_edit_reflection": snapshot.get("latest_pre_edit_reflection"),
        "latest_post_edit_reflection": snapshot.get("latest_post_edit_reflection"),
        "latest_meta_diagnosis": snapshot.get("latest_meta_diagnosis"),
        "latest_search_mode": snapshot.get("latest_search_mode"),
        "latest_reference_index": snapshot.get("latest_reference_index"),
        "latest_selected_context": snapshot.get("latest_selected_context"),
        "latest_resolved_context": snapshot.get("latest_resolved_context"),
        "latest_delta_context": snapshot.get("latest_delta_context"),
        "latest_task_frame": snapshot.get("latest_task_frame"),
        "latest_llm_timings": (latest_attempt or {}).get("llm_timings"),
        "latest_attempted_change": snapshot.get("latest_attempted_change"),
        "latest_runtime_kernel": (latest_attempt or {}).get("runtime_kernel")
        or runtime.get("current_runtime_kernel")
        or "legacy_phase_loop_v1",
        "latest_self_memo": snapshot.get("latest_self_memo"),
        "latest_session_events": latest_session_events,
        "latest_prompt_snapshots": latest_prompt_snapshots,
        "latest_prompt_snapshot": latest_prompt_snapshot,
        "latest_session_event_summary_map": latest_session_event_summary_map,
        "thought_action_chain": thought_action_chain,
        "thought_action_chain_source": thought_action_chain_source,
        "current_candidate_event_count": len(current_candidate_events),
        "current_candidate_has_events": bool(current_candidate_events),
        "current_stream_text": current_stream_text,
        "current_stream_sections": current_stream_sections,
        "llm_timing_trend": snapshot.get("llm_timing_trend"),
        "recent_memos": snapshot.get("recent_memos") or [],
        "system_skills": snapshot.get("system_skills") or [],
        "latest_context_frame": latest_context_frame,
        "task_hierarchy": task_hierarchy,
        "thought_history": thought_history,
        "context_audit": context_audit,
        "generation_report": generation_report,
        "latest_attempt": {
            "candidate_id": latest_attempt.get("candidate_id"),
            "status": latest_attempt.get("status"),
            "target_file": latest_attempt.get("target_file"),
            "decision_reason": latest_attempt.get("decision_reason"),
            "search_mode": latest_attempt.get("search_mode"),
            "selected_coding_model": latest_attempt.get("selected_coding_model"),
            "runtime_kernel": latest_attempt.get("runtime_kernel"),
        }
        if latest_attempt
        else None,
        "latest_validation": {
            "passed": latest_validation.get("passed"),
            "returncode": latest_validation.get("returncode"),
            "duration_ms": latest_validation.get("duration_ms"),
        }
        if latest_validation
        else None,
        "latest_completed_attempt": {
            "candidate_id": latest_completed_attempt.get("candidate_id"),
            "status": latest_completed_attempt.get("status"),
            "target_file": latest_completed_attempt.get("target_file"),
            "decision_reason": latest_completed_attempt.get("decision_reason"),
            "decision_explanation": decision_explanation(
                latest_completed_attempt.get("decision_reason"),
                status=latest_completed_attempt.get("status"),
                validation_summary=latest_completed_validation_summary,
            ),
            "chosen_response": humanize_response_text(
                (latest_completed_attempt.get("situation_report") or {}).get("chosen_response")
            ),
            "validation_summary": latest_completed_validation_summary,
            "selected_context": (latest_completed_attempt.get("selected_context") or {}).get("selected_context"),
            "selected_context_payload": latest_completed_attempt.get("selected_context"),
            "question_to_answer": (latest_completed_attempt.get("selected_context") or {}).get("question_to_answer"),
            "delta_context": latest_completed_attempt.get("delta_context"),
            "clone_reason": clone_reason_from_attempt(latest_completed_attempt),
            "reasoning_summary": latest_completed_attempt.get("reasoning_summary") or derived_reasoning_summary,
            "pre_edit_reflection": latest_completed_attempt.get("pre_edit_reflection"),
            "situation_report": latest_completed_attempt.get("situation_report"),
            "meta_diagnosis": latest_completed_attempt.get("meta_diagnosis"),
            "change_summary": latest_completed_attempt.get("change_summary"),
            "post_edit_reflection": latest_completed_attempt.get("post_edit_reflection"),
            "selected_coding_model": latest_completed_attempt.get("selected_coding_model"),
            "runtime_kernel": latest_completed_attempt.get("runtime_kernel")
            or runtime.get("current_runtime_kernel")
            or "legacy_phase_loop_v1",
            "session_events": latest_session_events,
        }
        if latest_completed_attempt
        else None,
        "latest_retry_validation": {
            "passed": latest_retry_validation.get("passed"),
            "returncode": latest_retry_validation.get("returncode"),
            "duration_ms": latest_retry_validation.get("duration_ms"),
        }
        if latest_retry_validation
        else None,
        "recent_attempts": recent_attempts,
        "recent_history": recent_history,
    }
    public_snapshot["operator_insights"] = build_operator_insights(public_snapshot)
    public_snapshot["implementation_notes"] = build_implementation_notes(snapshot, public_snapshot)
    return public_snapshot
