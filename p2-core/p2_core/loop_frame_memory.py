from __future__ import annotations

import json
from typing import Any

from p2_core.loop_utils import _safe_brief_text


def _append_unique_text(items: list[str], value: str | None, *, max_items: int = 12) -> list[str]:
    text = _safe_brief_text(value, max_chars=200)
    if not text:
        return items
    existing = [entry for entry in items if entry != text]
    existing.append(text)
    return existing[-max_items:]


def _merge_text_lists(left: list[str], right: list[str], *, max_items: int = 16) -> list[str]:
    merged: list[str] = list(left)
    for item in right:
        merged = _append_unique_text(merged, item, max_items=max_items)
    return merged[-max_items:]


def _empty_working_memory() -> dict[str, Any]:
    return {
        "observed_files": [],
        "observed_symbols": [],
        "observed_tests": [],
        "learned_findings": [],
        "prior_hypotheses": [],
        "prior_outcomes": [],
        "failed_hypothesis_reasons": [],
        "what_changed_right_before_failure": [],
        "what_not_to_repeat": [],
        "repeated_failure_patterns": [],
        "focus_candidates": [],
        "current_focus": "",
        "unresolved_questions": [],
        "done_criteria": [],
        "avoid_repeating": [],
    }


def _merge_working_memory(base: dict[str, Any] | None, extra: dict[str, Any] | None) -> dict[str, Any]:
    merged = _empty_working_memory()
    base = dict(base or {})
    extra = dict(extra or {})
    for key in (
        "observed_files",
        "observed_symbols",
        "observed_tests",
        "learned_findings",
        "prior_hypotheses",
        "prior_outcomes",
        "failed_hypothesis_reasons",
        "what_changed_right_before_failure",
        "what_not_to_repeat",
        "repeated_failure_patterns",
        "focus_candidates",
        "unresolved_questions",
        "done_criteria",
        "avoid_repeating",
    ):
        merged[key] = _merge_text_lists(list(base.get(key) or []), list(extra.get(key) or []))
    merged["current_focus"] = _safe_brief_text(extra.get("current_focus") or base.get("current_focus"), max_chars=120)
    return merged


def _frame_local_working_memory(frame: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(frame, dict):
        return _empty_working_memory()
    context = frame.get("context") or {}
    memory = context.get("local_working_memory")
    return json.loads(json.dumps(memory, ensure_ascii=False)) if isinstance(memory, dict) else _empty_working_memory()


def _frame_local_tool_results(frame: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(frame, dict):
        return []
    context = frame.get("context") or {}
    values = context.get("local_tool_results")
    return json.loads(json.dumps(values, ensure_ascii=False)) if isinstance(values, list) else []


def _build_inherited_context(parent_frame: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(parent_frame, dict):
        return {
            "ancestor_frame_ids": [],
            "ancestor_goals": [],
            "inherited_working_memory": _empty_working_memory(),
            "ancestor_tool_results": [],
            "ancestor_return_payloads": [],
        }
    parent_context = dict(parent_frame.get("context") or {})
    inherited = dict(parent_context.get("inherited_context") or {})
    inherited_working_memory = _merge_working_memory(
        inherited.get("inherited_working_memory"),
        parent_context.get("local_working_memory"),
    )
    ancestor_tool_results = list(inherited.get("ancestor_tool_results") or [])
    ancestor_tool_results.extend(_frame_local_tool_results(parent_frame))
    ancestor_return_payloads = list(inherited.get("ancestor_return_payloads") or [])
    parent_return_payload = parent_context.get("return_payload")
    if isinstance(parent_return_payload, dict) and parent_return_payload:
        ancestor_return_payloads.append(json.loads(json.dumps(parent_return_payload, ensure_ascii=False)))
    return {
        "ancestor_frame_ids": list(inherited.get("ancestor_frame_ids") or []) + [parent_frame.get("frame_id")],
        "ancestor_goals": list(inherited.get("ancestor_goals") or []) + [parent_frame.get("goal") or ""],
        "inherited_working_memory": inherited_working_memory,
        "ancestor_tool_results": ancestor_tool_results,
        "ancestor_return_payloads": ancestor_return_payloads,
    }


def _update_frame_working_memory(
    frame: dict[str, Any],
    *,
    action: str,
    action_input: dict[str, Any],
    result: dict[str, Any],
) -> None:
    context = frame.setdefault("context", {})
    memory = _merge_working_memory(context.get("local_working_memory"), {})

    if action == "read_file":
        path = str(result.get("relative_path") or action_input.get("path") or "")
        memory["observed_files"] = _append_unique_text(memory["observed_files"], path)
        if path.startswith("tests/"):
            memory["observed_tests"] = _append_unique_text(memory["observed_tests"], path)
        memory["current_focus"] = _safe_brief_text(path, max_chars=120)
        memory["learned_findings"] = _append_unique_text(memory["learned_findings"], f"{path} を確認した")
        memory["prior_outcomes"] = _append_unique_text(memory["prior_outcomes"], f"{path} を読んで現在状態を観測した")
    elif action == "search_code":
        pattern = str(action_input.get("pattern") or "")
        memory["observed_symbols"] = _append_unique_text(memory["observed_symbols"], pattern)
        memory["focus_candidates"] = _append_unique_text(memory["focus_candidates"], pattern)
        memory["current_focus"] = _safe_brief_text(pattern, max_chars=120)
        matches = int(result.get("match_count") or len(result.get("matches") or []))
        memory["learned_findings"] = _append_unique_text(memory["learned_findings"], f"{pattern} を検索し {matches} 件ヒットした")
        memory["prior_outcomes"] = _append_unique_text(memory["prior_outcomes"], f"{pattern} の出現箇所を {matches} 件観測した")
    elif action == "apply_patch":
        path = str(result.get("relative_path") or action_input.get("path") or "")
        memory["current_focus"] = _safe_brief_text(path, max_chars=120)
        memory["learned_findings"] = _append_unique_text(
            memory["learned_findings"],
            f"{path} に差分を適用した added={result.get('added_lines')} removed={result.get('removed_lines')}",
        )
        memory["prior_hypotheses"] = _append_unique_text(
            memory["prior_hypotheses"],
            f"{path} の最小差分で現在の焦点を改善できるはずだと判断した",
        )
        old_text = _safe_brief_text(action_input.get("old_text"), max_chars=160)
        new_text = _safe_brief_text(action_input.get("new_text"), max_chars=160)
        if path and (old_text or new_text):
            memory["what_changed_right_before_failure"] = _append_unique_text(
                memory["what_changed_right_before_failure"],
                f"{path} で `{old_text}` を `{new_text}` へ変えようとした",
            )
    elif action == "run_validation":
        if result.get("ok") and result.get("passed"):
            memory["learned_findings"] = _append_unique_text(memory["learned_findings"], "validation は成功した")
            memory["done_criteria"] = _append_unique_text(memory["done_criteria"], "直近の変更が validation を通過した")
            memory["avoid_repeating"] = _append_unique_text(memory["avoid_repeating"], "同じ粒度の観測を繰り返さず、次は編集か返却を選ぶ")
            memory["prior_outcomes"] = _append_unique_text(memory["prior_outcomes"], "直近の仮説は validation 成功で支持された")
        else:
            failure = result.get("failure") or {}
            summary = failure.get("summary") or result.get("summary") or "validation failed"
            memory["learned_findings"] = _append_unique_text(memory["learned_findings"], f"validation は失敗した: {summary}")
            memory["unresolved_questions"] = _append_unique_text(memory["unresolved_questions"], summary)
            memory["prior_outcomes"] = _append_unique_text(memory["prior_outcomes"], f"直近の仮説は validation 失敗で崩れた: {summary}")
            memory["failed_hypothesis_reasons"] = _append_unique_text(
                memory["failed_hypothesis_reasons"],
                summary,
            )
            failure_type = _safe_brief_text(
                failure.get("error_type") or result.get("error_type") or summary,
                max_chars=160,
            )
            if failure_type:
                memory["repeated_failure_patterns"] = _append_unique_text(
                    memory["repeated_failure_patterns"],
                    failure_type,
                )
            detail = _safe_brief_text(
                failure.get("detail")
                or failure.get("failure_snippet")
                or (result.get("stderr_excerpt") if isinstance(result, dict) else "")
                or summary,
                max_chars=220,
            )
            if detail:
                memory["what_not_to_repeat"] = _append_unique_text(
                    memory["what_not_to_repeat"],
                    f"同じ失敗を再導入しない: {detail}",
                )
    elif action == "open_child_frame":
        next_goal = str(action_input.get("next_goal") or "")
        memory["focus_candidates"] = _append_unique_text(memory["focus_candidates"], next_goal)
        memory["learned_findings"] = _append_unique_text(memory["learned_findings"], f"子フレーム候補を作成した: {next_goal}")
        memory["prior_hypotheses"] = _append_unique_text(memory["prior_hypotheses"], f"今の問いは広いため、{next_goal} へ局所分解した方がよい")
    elif action == "return_to_parent":
        memory["learned_findings"] = _append_unique_text(memory["learned_findings"], "このフレームの結果を親へ返す判断をした")
        memory["prior_outcomes"] = _append_unique_text(memory["prior_outcomes"], "この階層だけで閉じず、親の判断材料として返す方がよいと判断した")
    elif action == "finish":
        memory["done_criteria"] = _append_unique_text(memory["done_criteria"], "このフレームは完了可能")
        memory["prior_outcomes"] = _append_unique_text(memory["prior_outcomes"], "このフレームの作業単位は完了した")

    context["local_working_memory"] = memory


def _build_return_payload_from_action_input(
    *,
    frame: dict[str, Any],
    action_input: dict[str, Any],
    session_events: list[dict[str, Any]],
    default_summary: str,
) -> dict[str, Any]:
    memory = _frame_local_working_memory(frame)
    local_events = [event for event in session_events if event.get("frame_id") == frame.get("frame_id")]
    raw = dict(action_input.get("return_payload") or {})
    selected_steps = [int(step) for step in raw.get("tool_result_steps") or [] if isinstance(step, int) or str(step).isdigit()]
    if selected_steps:
        selected_tool_results = [event for event in local_events if int(event.get("step") or 0) in selected_steps]
    else:
        selected_tool_results = local_events
    summary = _safe_brief_text(raw.get("summary") or default_summary, max_chars=240)
    payload = {
        "frame_id": frame.get("frame_id"),
        "summary": summary,
        "learned_findings": _merge_text_lists(list(raw.get("learned_findings") or []), list(memory.get("learned_findings") or []), max_items=8),
        "unresolved_questions": _merge_text_lists(list(raw.get("unresolved_questions") or []), list(memory.get("unresolved_questions") or []), max_items=8),
        "current_focus": _safe_brief_text(raw.get("current_focus") or memory.get("current_focus"), max_chars=120),
        "tool_result_steps": [int(event.get("step") or 0) for event in selected_tool_results],
        "selected_tool_results": json.loads(json.dumps(selected_tool_results, ensure_ascii=False)),
    }
    return payload


def _merge_child_return_into_parent(parent_frame: dict[str, Any], return_payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(parent_frame, dict) or not isinstance(return_payload, dict) or not return_payload:
        return parent_frame
    context = parent_frame.setdefault("context", {})
    payloads = list(context.get("child_return_payloads") or [])
    payloads.append(json.loads(json.dumps(return_payload, ensure_ascii=False)))
    context["child_return_payloads"] = payloads[-8:]
    memory = _frame_local_working_memory(parent_frame)
    memory["learned_findings"] = _merge_text_lists(list(memory.get("learned_findings") or []), list(return_payload.get("learned_findings") or []))
    memory["unresolved_questions"] = _merge_text_lists(list(memory.get("unresolved_questions") or []), list(return_payload.get("unresolved_questions") or []))
    memory["focus_candidates"] = _append_unique_text(list(memory.get("focus_candidates") or []), return_payload.get("current_focus"))
    memory["learned_findings"] = _append_unique_text(list(memory.get("learned_findings") or []), f"子フレーム {return_payload.get('frame_id')} から結果を受領: {return_payload.get('summary')}")
    if return_payload.get("current_focus"):
        memory["current_focus"] = _safe_brief_text(return_payload.get("current_focus"), max_chars=120)
    context["local_working_memory"] = memory
    return parent_frame
