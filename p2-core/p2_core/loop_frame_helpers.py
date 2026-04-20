from __future__ import annotations

from pathlib import Path
import json
from typing import Any

from p2_core.frame_language import frame_affordances_payload, frame_transition_capabilities_payload
from p2_core.loop_attempt_meta import _classify_decision_reason
from p2_core.loop_utils import _safe_brief_text
from p2_core.workspace import recent_attempt_reports


def _build_task_frame(
    *,
    candidate_id: str,
    goal: dict[str, Any],
    purpose: str,
    target_file: str,
    search_mode: str,
    selected_context: dict[str, Any],
    resolved_context: dict[str, Any],
    delta_context: dict[str, Any],
    frame_affordances: dict[str, Any],
    system_capabilities: dict[str, Any],
    inherited_context: dict[str, Any] | None = None,
    parent_frame: dict[str, Any] | None = None,
    frame_index: int = 0,
) -> dict[str, Any]:
    depth = int(parent_frame.get("depth", 0) or 0) + 1 if isinstance(parent_frame, dict) else 0
    parent_frame_id = parent_frame.get("frame_id") if isinstance(parent_frame, dict) else None
    return {
        "frame_id": f"{candidate_id}:d{depth}:f{frame_index}",
        "parent_frame_id": parent_frame_id,
        "depth": depth,
        "goal": purpose or goal.get("text") or "",
        "context": {
            "parent_context": {
                "goal": (parent_frame or {}).get("goal") if isinstance(parent_frame, dict) else goal.get("text"),
                "constraints": list(goal.get("acceptance", {}).get("command", []) or []),
                "why_this_frame_exists": selected_context.get("question_to_answer")
                or purpose
                or ((parent_frame or {}).get("continue_or_return") or {}).get("next_goal")
                or goal.get("text"),
            },
            "inherited_context": inherited_context or {},
            "local_context": {
                "target_file": target_file,
                "search_mode": search_mode,
                "selected_context": list(selected_context.get("selected_context") or []),
                "resolved_context_keys": sorted(list(resolved_context.keys())),
                "frame_affordances": frame_affordances,
                "system_capabilities": system_capabilities,
            },
            "local_working_memory": {
                "observed_files": [],
                "observed_symbols": [],
                "observed_tests": [],
                "learned_findings": [],
                "focus_candidates": [],
                "current_focus": "",
                "unresolved_questions": [],
                "done_criteria": [],
                "avoid_repeating": [],
            },
            "local_tool_results": [],
            "child_return_payloads": [],
            "delta_context": delta_context,
            "return_payload": None,
        },
        "transition_decision": {
            "decision": "continue_here",
            "reason": "このフレームの作業を開始したばかりです。",
            "next_goal": "",
        },
        "commitment": selected_context.get("commitment") or "",
        "continue_or_return": {
            "decision": "continue_here",
            "reason": "このフレームの作業を開始したばかりです。",
            "next_goal": "",
        },
        "result": {
            "status": "active",
            "summary": "フレーム実行中",
        },
    }


def _update_task_frame_outcome(
    task_frame: dict[str, Any] | None,
    *,
    status: str,
    summary: str,
    decision: str | None = None,
    reason: str | None = None,
    next_goal: str | None = None,
) -> dict[str, Any] | None:
    if not isinstance(task_frame, dict):
        return task_frame
    task_frame = json.loads(json.dumps(task_frame, ensure_ascii=False))
    task_frame["result"] = {
        "status": status,
        "summary": summary,
    }
    current = task_frame.get("continue_or_return") or {}
    task_frame["transition_decision"] = {
        "decision": decision or current.get("decision") or "continue_here",
        "reason": reason or current.get("reason") or summary,
        "next_goal": next_goal or current.get("next_goal") or "",
    }
    task_frame["continue_or_return"] = {
        "decision": decision or current.get("decision") or "continue_here",
        "reason": reason or current.get("reason") or summary,
        "next_goal": next_goal or current.get("next_goal") or "",
    }
    return task_frame


def _task_stack_summary(task_stack: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for index, frame in enumerate(task_stack or []):
        if not isinstance(frame, dict):
            continue
        context = frame.get("context") or {}
        memory = context.get("local_working_memory") or {}
        continuation = frame.get("continue_or_return") or {}
        result = frame.get("result") or {}
        summary.append(
            {
                "depth": int(frame.get("depth", index) or 0),
                "frame_id": frame.get("frame_id"),
                "goal": frame.get("goal") or "",
                "commitment": frame.get("commitment") or "",
                "current_focus": memory.get("current_focus") or "",
                "decision": continuation.get("decision") or "continue_here",
                "result_status": result.get("status") or "active",
                "result_summary": result.get("summary") or "",
            }
        )
    return summary


def _recent_attempt_observation_bundle(root: Path, *, limit: int = 5) -> list[dict[str, Any]]:
    bundle: list[dict[str, Any]] = []
    for attempt in recent_attempt_reports(root, limit=limit):
        if not isinstance(attempt, dict):
            continue
        selected_context = attempt.get("selected_context") or {}
        delta_context = attempt.get("delta_context") or {}
        action_raw = delta_context.get("action_raw") or {}
        latest_failure = delta_context.get("latest_failure") or {}
        change_summary = attempt.get("change_summary") or {}
        bundle.append(
            {
                "candidate_id": attempt.get("candidate_id"),
                "status": attempt.get("status"),
                "decision_type": _classify_decision_reason(attempt.get("decision_reason")),
                "frame_depth": int(((attempt.get("task_frame") or {}).get("depth")) or 0),
                "frame_transition": ((attempt.get("continue_or_return") or {}).get("decision")) or "continue_here",
                "selected_context": list(selected_context.get("selected_context") or []),
                "commitment": _safe_brief_text(selected_context.get("commitment"), max_chars=160),
                "had_code_change": bool(action_raw.get("diff_excerpt")),
                "changed_lines": list(action_raw.get("changed_line_numbers") or []),
                "failure_file": latest_failure.get("file"),
                "failure_line": latest_failure.get("line"),
                "failure_detail": _safe_brief_text(latest_failure.get("detail"), max_chars=160),
                "change_summary": _safe_brief_text(
                    change_summary.get("summary") if isinstance(change_summary, dict) else change_summary,
                    max_chars=180,
                ),
            }
        )
    return bundle


def _frame_affordances(*, depth: int, max_depth: int, parent_frame: dict[str, Any] | None) -> dict[str, Any]:
    return frame_affordances_payload(
        depth=depth,
        max_depth=max_depth,
        has_parent_frame=isinstance(parent_frame, dict),
    )


def _system_capabilities(
    *,
    depth: int,
    max_depth: int,
    parent_frame: dict[str, Any] | None,
    reference_index: list[dict[str, Any]],
) -> dict[str, Any]:
    skill_ids = [str(entry.get("id")) for entry in reference_index if entry.get("kind") == "skill"]
    memo_ids = [str(entry.get("id")) for entry in reference_index if entry.get("kind") == "memo"]
    return {
        "reference_lookup": {
            "available": True,
            "how_to_use": "参照選択 selected_context に 参照インデックス reference_index の id を書くと、システムが 解決済み参照 resolved_context として内容を渡す。",
            "max_items_per_frame": 3,
            "available_reference_ids": [str(entry.get("id")) for entry in reference_index if entry.get("id")],
        },
        "skill_lookup": {
            "available": True,
            "how_to_use": "参照選択 selected_context に skill:<id> を書くと、システムスキルのヒントを読める。",
            "available_skill_ids": skill_ids,
            "notes": "システムスキルは命令ではなくヒント。今の問題に関係するものだけ読む。",
        },
        "memo_lookup": {
            "available": True,
            "how_to_use": "参照選択 selected_context に memo:<id> を書くと、永続自己メモを読める。",
            "available_memo_ids": memo_ids,
            "notes": "永続自己メモは自分の過去経験の圧縮表現。関連するものだけ参照する。",
        },
        "frame_transitions": {
            **frame_transition_capabilities_payload(),
        },
        "automatic_runtime": {
            "validation_after_generation": True,
            "active_version_protected_until_promotion": True,
            "current_depth": depth,
            "max_depth": max_depth,
            "has_parent_frame": isinstance(parent_frame, dict),
        },
    }
