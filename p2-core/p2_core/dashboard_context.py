from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from p2_core.frame_language import FRAME_DECISION_LABELS


def build_task_hierarchy(
    current_task_stack: list[dict[str, Any]] | None,
    latest_context_frame: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    frames = list(current_task_stack or [])
    if not frames and latest_context_frame:
        frames = [latest_context_frame]

    hierarchy: list[dict[str, Any]] = []
    current_frame_id = ""
    if frames:
        current_frame_id = str((frames[-1] or {}).get("frame_id") or f"frame-{len(frames) - 1}")

    for index, frame in enumerate(frames):
        if not isinstance(frame, dict):
            continue
        context = frame.get("context") or {}
        local_context = context.get("local_context") or {}
        parent_context = context.get("parent_context") or {}
        inherited_context = context.get("inherited_context") or {}
        local_memory = context.get("local_working_memory") or {}
        local_tool_results = list(context.get("local_tool_results") or [])
        delta_context = context.get("delta_context") or {}
        continuation = frame.get("continue_or_return") or {}
        result = frame.get("result") or {}
        child_return_payloads = list(context.get("child_return_payloads") or [])
        return_payload = context.get("return_payload") or {}
        frame_id = str(frame.get("frame_id") or f"frame-{index}")
        proposed_next_goal = ""
        proposed_child_goals: list[str] = []
        for tool_result in reversed(local_tool_results):
            if not isinstance(tool_result, dict):
                continue
            if str(tool_result.get("action") or "") != "invalid_response":
                continue
            action_input = tool_result.get("action_input") or {}
            result_payload = tool_result.get("result") or {}
            proposed_action = str(
                result_payload.get("proposed_action")
                or action_input.get("proposed_action")
                or ""
            )
            if proposed_action != "open_child_frame":
                continue
            proposed_payload = (
                result_payload.get("proposed_action_input")
                or action_input.get("proposed_action_input")
                or {}
            )
            if not isinstance(proposed_payload, dict):
                proposed_payload = {}
            proposed_next_goal = str(proposed_payload.get("next_goal") or "")
            raw_child_goals = proposed_payload.get("child_goals") or []
            if isinstance(raw_child_goals, list):
                proposed_child_goals = [str(item) for item in raw_child_goals if str(item).strip()]
            if proposed_next_goal and proposed_next_goal not in proposed_child_goals:
                proposed_child_goals.insert(0, proposed_next_goal)
            break
        hierarchy.append(
            {
                "frame_id": frame_id,
                "parent_frame_id": frame.get("parent_frame_id"),
                "depth": int(frame.get("depth", index) or 0),
                "goal": frame.get("goal") or frame.get("frame_goal") or "",
                "parent_goal": parent_context.get("goal") or frame.get("parent_goal") or "",
                "target_file": local_context.get("target_file") or frame.get("target_file") or "",
                "search_mode": local_context.get("search_mode") or frame.get("search_mode") or "",
                "question_to_answer": parent_context.get("why_this_frame_exists") or frame.get("question_to_answer") or "",
                "commitment": frame.get("commitment") or "",
                "selected_context": list(local_context.get("selected_context") or frame.get("selected_context") or []),
                "resolved_context_keys": list(
                    local_context.get("resolved_context_keys") or frame.get("resolved_context_keys") or []
                ),
                "inherited_frame_ids": list(inherited_context.get("ancestor_frame_ids") or []),
                "inherited_goal_chain": list(inherited_context.get("ancestor_goals") or []),
                "inherited_tool_result_count": len(list(inherited_context.get("ancestor_tool_results") or [])),
                "inherited_findings": list(
                    ((inherited_context.get("inherited_working_memory") or {}).get("learned_findings") or [])
                ),
                "local_tool_result_count": len(list(context.get("local_tool_results") or [])),
                "local_observed_files": list(local_memory.get("observed_files") or []),
                "local_observed_symbols": list(local_memory.get("observed_symbols") or []),
                "local_learned_findings": list(local_memory.get("learned_findings") or []),
                "local_unresolved_questions": list(local_memory.get("unresolved_questions") or []),
                "current_focus": local_memory.get("current_focus") or "",
                "child_return_count": len(child_return_payloads),
                "child_return_summaries": [
                    payload.get("summary") for payload in child_return_payloads if isinstance(payload, dict) and payload.get("summary")
                ],
                "return_payload_summary": return_payload.get("summary") or "",
                "proposed_next_goal": proposed_next_goal,
                "proposed_child_goals": proposed_child_goals,
                "latest_failure": delta_context.get("latest_failure") or frame.get("latest_failure") or {},
                "must_avoid_next": list(delta_context.get("must_avoid_next") or frame.get("must_avoid_next") or []),
                "decision": continuation.get("decision") or "continue_here",
                "decision_label": FRAME_DECISION_LABELS.get(
                    continuation.get("decision") or "continue_here",
                    continuation.get("decision") or "continue_here",
                ),
                "decision_reason": continuation.get("reason") or "",
                "next_goal": continuation.get("next_goal") or "",
                "result_status": result.get("status") or frame.get("status") or "active",
                "result_summary": result.get("summary") or "",
                "is_current": frame_id == current_frame_id or index == len(frames) - 1,
            }
        )
    return hierarchy


def build_thought_history(raw_history: list[dict[str, Any]], *, candidate_id: str | None) -> list[dict[str, Any]]:
    if not candidate_id:
        return []
    recursing_re = re.compile(
        r"(?P<parent>c\d+:d\d+:f\d+)\s+->\s+child goal(?:\[\d+/\d+\])?:\s+(?P<goal>.+)"
    )
    returned_re = re.compile(r"(?P<parent>c\d+:d\d+:f\d+)\s+<=\s+(?P<child>c\d+:d\d+:f\d+)")
    entries: list[dict[str, Any]] = []
    for row in raw_history:
        if str(row.get("candidate_id") or "") != candidate_id:
            continue
        step = str(row.get("step") or "")
        message = str(row.get("message") or "")
        timestamp = row.get("timestamp")
        if step == "task_frame_recursing":
            match = recursing_re.search(message)
            if not match:
                continue
            parent = match.group("parent")
            goal = match.group("goal").strip()
            depth_match = re.search(r":d(\d+):", parent)
            parent_depth = int(depth_match.group(1)) if depth_match else 0
            entries.append(
                {
                    "timestamp": timestamp,
                    "type": "open_child_frame",
                    "frame_id": parent,
                    "depth": parent_depth + 1,
                    "label": goal,
                    "message": message,
                }
            )
        elif step == "task_frame_child_returned":
            match = returned_re.search(message)
            if not match:
                continue
            child = match.group("child")
            depth_match = re.search(r":d(\d+):", child)
            child_depth = int(depth_match.group(1)) if depth_match else 0
            entries.append(
                {
                    "timestamp": timestamp,
                    "type": "return_to_parent",
                    "frame_id": child,
                    "depth": child_depth,
                    "label": "親フレームへ返却",
                    "message": message,
                }
            )
    return list(reversed(entries))


def thought_history_from_hierarchy(task_hierarchy: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for frame in task_hierarchy:
        entries.append(
            {
                "timestamp": "",
                "type": "frame_context",
                "frame_id": frame.get("frame_id"),
                "depth": int(frame.get("depth") or 0),
                "label": frame.get("goal") or frame.get("frame_id") or "frame",
                "message": frame.get("decision_label") or frame.get("decision") or frame.get("result_status") or "",
            }
        )
        for goal in list(frame.get("proposed_child_goals") or []):
            entries.append(
                {
                    "timestamp": "",
                    "type": "proposed_open_child_frame",
                    "frame_id": frame.get("frame_id"),
                    "depth": int(frame.get("depth") or 0) + 1,
                    "label": str(goal),
                    "message": "モデルは child goal として提案したが、runtime ではまだ確定していません。",
                }
            )
    return entries


def _summarize_frame_for_history(
    frame: dict[str, Any],
    *,
    candidate_id: str,
    timestamp: str,
    fallback_status: str = "",
    attempt: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(frame, dict):
        return None
    depth = int(frame.get("depth") or 0)
    context = frame.get("context") or {}
    local_memory = context.get("local_working_memory") or {}
    continuation = frame.get("continue_or_return") or frame.get("transition_decision") or {}
    result = frame.get("result") or {}
    return_payload = frame.get("return_payload") or context.get("return_payload") or {}
    latest_failure = (context.get("delta_context") or {}).get("latest_failure") or {}
    attempt = attempt or {}

    learned_findings = list(local_memory.get("learned_findings") or [])
    unresolved = list(local_memory.get("unresolved_questions") or [])
    reason = (
        continuation.get("reason")
        or result.get("reason")
        or frame.get("decision_reason")
        or attempt.get("decision_reason")
        or ""
    )
    summary = (
        return_payload.get("summary")
        or result.get("summary")
        or attempt.get("validation_summary")
        or frame.get("goal")
        or frame.get("frame_goal")
        or frame.get("frame_id")
        or "frame"
    )
    outcome = result.get("status") or frame.get("status") or continuation.get("decision") or fallback_status or "active"
    current_focus = return_payload.get("current_focus") or local_memory.get("current_focus") or ""
    finding = learned_findings[-1] if learned_findings else ""
    unresolved_text = unresolved[0] if unresolved else ""
    if latest_failure:
        unresolved_text = unresolved_text or str(latest_failure.get("summary") or latest_failure.get("detail") or "")
    if not unresolved_text and str(attempt.get("status") or "") in {"failed", "rejected", "rolled_back"}:
        unresolved_text = str(attempt.get("decision_reason") or "")
    if not finding and attempt.get("validation_summary"):
        finding = str(attempt.get("validation_summary") or "")
    return {
        "timestamp": timestamp,
        "type": "recent_attempt_frame",
        "frame_id": frame.get("frame_id"),
        "depth": depth,
        "label": f"{candidate_id}: {frame.get('goal') or frame.get('frame_goal') or frame.get('frame_id') or 'frame'}",
        "message": FRAME_DECISION_LABELS.get(str(continuation.get("decision") or ""), str(continuation.get("decision") or "")) or outcome,
        "summary": summary,
        "reason": reason,
        "finding": finding,
        "unresolved": unresolved_text,
        "focus": current_focus,
        "outcome": outcome,
    }


def recent_attempt_thought_history(recent_attempts: list[dict[str, Any]], *, limit: int = 24) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for attempt in recent_attempts:
        candidate_id = str(attempt.get("candidate_id") or "")
        trace = attempt.get("frame_trace") or attempt.get("task_stack") or []
        if not trace and attempt.get("task_frame"):
            trace = [attempt.get("task_frame")]
        if not candidate_id or not isinstance(trace, list):
            continue
        for frame in trace:
            entry = _summarize_frame_for_history(
                frame,
                candidate_id=candidate_id,
                timestamp=str(attempt.get("created_at") or ""),
                fallback_status=str(attempt.get("status") or ""),
                attempt=attempt,
            )
            if entry:
                entries.append(entry)
    return entries[:limit]


def attempt_history_from_disk(root: Path, *, limit: int = 24) -> list[dict[str, Any]]:
    attempts_dir = root / "state" / "attempts"
    rows: list[tuple[str, list[dict[str, Any]], str, str]] = []
    try:
        attempt_paths = sorted(attempts_dir.glob("c*.json"), reverse=True)
    except OSError:
        return []
    for path in attempt_paths:
        try:
            attempt = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        candidate_id = str(attempt.get("candidate_id") or path.stem)
        trace = attempt.get("frame_trace") or attempt.get("task_stack") or []
        if not trace and attempt.get("task_frame"):
            trace = [attempt.get("task_frame")]
        if not isinstance(trace, list) or not trace:
            continue
        created_at = str(attempt.get("created_at") or "")
        rows.append((candidate_id, trace, created_at, str(attempt.get("status") or "")))
    entries: list[dict[str, Any]] = []
    for candidate_id, trace, created_at, status in rows:
        for frame in trace:
            entry = _summarize_frame_for_history(
                frame,
                candidate_id=candidate_id,
                timestamp=created_at,
                fallback_status=status,
                attempt=attempt,
            )
            if entry:
                entries.append(entry)
                if len(entries) >= limit:
                    return entries
    return entries


def _context_audit_status(status: str) -> str:
    labels = {
        "ok": "OK",
        "root": "root",
        "pending": "保留",
        "none": "なし",
        "missing": "不足",
    }
    return labels.get(status, status)


def build_context_audit(frame: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(frame, dict):
        return {"summary": "まだ監査対象のフレームはありません。", "checks": []}

    depth = int(frame.get("depth", 0) or 0)
    inherited_frames = list(frame.get("inherited_frame_ids") or [])
    inherited_findings = list(frame.get("inherited_findings") or [])
    inherited_tool_result_count = int(frame.get("inherited_tool_result_count") or 0)
    local_observed_files = list(frame.get("local_observed_files") or [])
    local_observed_symbols = list(frame.get("local_observed_symbols") or [])
    local_learned_findings = list(frame.get("local_learned_findings") or [])
    local_tool_result_count = int(frame.get("local_tool_result_count") or 0)
    child_return_summaries = list(frame.get("child_return_summaries") or [])
    return_payload_summary = str(frame.get("return_payload_summary") or "")
    decision = str(frame.get("decision") or "continue_here")
    decision_label = str(frame.get("decision_label") or decision)
    current_focus = str(frame.get("current_focus") or "")

    if depth == 0:
        inheritance_check = {
            "label": "上位コンテキスト継承",
            "status": "root",
            "status_label": _context_audit_status("root"),
            "detail": "最上位フレームです。継承元フレームはありません。",
        }
    else:
        inherited_ok = bool(inherited_frames or inherited_findings or inherited_tool_result_count)
        inheritance_check = {
            "label": "上位コンテキスト継承",
            "status": "ok" if inherited_ok else "missing",
            "status_label": _context_audit_status("ok" if inherited_ok else "missing"),
            "detail": (
                f"継承元フレーム {len(inherited_frames)} 件 / "
                f"継承知見 {len(inherited_findings)} 件 / "
                f"継承 tool result {inherited_tool_result_count} 件"
            ),
        }

    local_ok = bool(local_observed_files or local_observed_symbols or local_learned_findings or local_tool_result_count or current_focus)
    local_check = {
        "label": "ローカル作業メモ",
        "status": "ok" if local_ok else "missing",
        "status_label": _context_audit_status("ok" if local_ok else "missing"),
        "detail": (
            f"観測ファイル {len(local_observed_files)} 件 / "
            f"観測記号 {len(local_observed_symbols)} 件 / "
            f"ローカル知見 {len(local_learned_findings)} 件 / "
            f"ローカル tool result {local_tool_result_count} 件"
            + (f" / 現在フォーカス {current_focus}" if current_focus else "")
        ),
    }

    child_return_check = {
        "label": "子フレーム返却",
        "status": "ok" if child_return_summaries else "none",
        "status_label": _context_audit_status("ok" if child_return_summaries else "none"),
        "detail": " / ".join(child_return_summaries[:3]) if child_return_summaries else "まだ子フレーム返却はありません。",
    }

    return_payload_check = {
        "label": "親へ返す要約",
        "status": "ok" if return_payload_summary else "pending",
        "status_label": _context_audit_status("ok" if return_payload_summary else "pending"),
        "detail": return_payload_summary or "このフレームの return_payload はまだ未設定です。",
    }

    separation_check = {
        "label": "遷移判断と返却要約の分離",
        "status": "ok" if decision else "missing",
        "status_label": _context_audit_status("ok" if decision else "missing"),
        "detail": (
            f"遷移判断は {decision_label}。"
            + (
                f" return_payload 要約は「{return_payload_summary}」です。"
                if return_payload_summary
                else " return_payload 要約はまだ未設定です。"
            )
        ),
    }

    return {
        "summary": f"{frame.get('frame_id') or 'frame'} depth={depth}",
        "checks": [
            inheritance_check,
            local_check,
            child_return_check,
            return_payload_check,
            separation_check,
        ],
    }
