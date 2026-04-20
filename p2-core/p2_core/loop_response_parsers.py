from __future__ import annotations

import json
from typing import Any

from p2_core.backend import extract_first_json_object
from p2_core.loop_utils import (
    _extract_code_block,
    _safe_brief_text,
    _sanitize_prompt_text,
)


def _parse_model_response(raw_text: str) -> dict[str, Any]:
    parsed: dict[str, Any] | None = None
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        try:
            parsed = extract_first_json_object(raw_text)
        except json.JSONDecodeError:
            parsed = None
    if parsed is None:
        return {
            "reasoning_summary": {
                "problem_statement": "Model response was not valid JSON.",
                "diagnosis": "Fallback parser extracted code content directly.",
                "edit_intent": "Try the raw response as the full replacement.",
                "why_this_file": "The kernel selected the editable zone.",
                "expected_effect": "The replacement may still satisfy the tests.",
                "validation_hypothesis": "The returned code might compile and pass validation.",
                "next_if_fail": "Use the validation failure summary in the next attempt.",
            },
            "situation_report": {
                "known": ["モデル応答が JSON として解釈できませんでした。"],
                "suspected": ["フォーマット崩れか、指示理解の失敗が起きています。"],
                "unknown": ["モデルがどの内部状態で応答したかは不明です。"],
                "chosen_response": "raw response を全文置換候補として扱い、検証結果で次回を調整します。",
            },
            "post_edit_reflection": {
                "did_i_actually_change_behavior": "編集応答の構造化に失敗したため、不明です。",
                "how_is_this_different_from_recent_failures": "raw response をそのまま候補として扱っています。",
                "why_this_is_not_another_no_change": "差分が出るかどうかは未確認です。",
                "remaining_risk": "フォーマット崩れのため、同種失敗を繰り返す可能性があります。",
            },
            "continue_or_return": {
                "decision": "continue_here",
                "reason": "フォーマット崩れのため、このフレームで状況を立て直す必要があります。",
                "next_goal": "まず有効な JSON 形式で候補を返す",
                "child_goals": [],
            },
            "change_summary": "Fallback path: raw model response treated as file content.",
            "revised_file_content": _extract_code_block(raw_text) + "\n",
            "self_memo": {
                "title": "",
                "when": "",
                "tactic": "",
                "why": "",
                "confidence": 0.0,
                "tags": [],
            },
        }

    reasoning = parsed.get("reasoning_summary")
    if not isinstance(reasoning, dict):
        reasoning = {}
    normalized_reasoning = {
        key: str(reasoning.get(key, "")).strip()
        for key in [
            "problem_statement",
            "diagnosis",
            "edit_intent",
            "why_this_file",
            "expected_effect",
            "validation_hypothesis",
            "next_if_fail",
        ]
    }

    situation = parsed.get("situation_report")
    if not isinstance(situation, dict):
        situation = {}
    normalized_situation = {
        "known": [str(item).strip() for item in (situation.get("known") or []) if str(item).strip()][:5],
        "suspected": [str(item).strip() for item in (situation.get("suspected") or []) if str(item).strip()][:5],
        "unknown": [str(item).strip() for item in (situation.get("unknown") or []) if str(item).strip()][:5],
        "chosen_response": str(situation.get("chosen_response", "")).strip(),
    }
    if not normalized_situation["known"] and normalized_reasoning["diagnosis"]:
        normalized_situation["known"] = [normalized_reasoning["diagnosis"]]
    if not normalized_situation["suspected"] and normalized_reasoning["problem_statement"]:
        normalized_situation["suspected"] = [normalized_reasoning["problem_statement"]]
    if not normalized_situation["unknown"]:
        normalized_situation["unknown"] = ["未知要因は明示されませんでした。"]
    if not normalized_situation["chosen_response"]:
        normalized_situation["chosen_response"] = normalized_reasoning["edit_intent"] or "小さな自己改善を試行する。"

    reflection = parsed.get("post_edit_reflection")
    if not isinstance(reflection, dict):
        reflection = {}
    normalized_post_reflection = {
        "did_i_actually_change_behavior": str(reflection.get("did_i_actually_change_behavior", "")).strip(),
        "how_is_this_different_from_recent_failures": str(
            reflection.get("how_is_this_different_from_recent_failures", "")
        ).strip(),
        "why_this_is_not_another_no_change": str(reflection.get("why_this_is_not_another_no_change", "")).strip(),
        "remaining_risk": str(reflection.get("remaining_risk", "")).strip(),
    }
    if not normalized_post_reflection["did_i_actually_change_behavior"]:
        normalized_post_reflection["did_i_actually_change_behavior"] = (
            normalized_reasoning["edit_intent"] or "今回の行動変化は明示されませんでした。"
        )
    if not normalized_post_reflection["how_is_this_different_from_recent_failures"]:
        normalized_post_reflection["how_is_this_different_from_recent_failures"] = (
            normalized_situation["chosen_response"] or "前回との違いは明示されませんでした。"
        )
    if not normalized_post_reflection["why_this_is_not_another_no_change"]:
        normalized_post_reflection["why_this_is_not_another_no_change"] = "差分と検証で確認します。"
    if not normalized_post_reflection["remaining_risk"]:
        normalized_post_reflection["remaining_risk"] = normalized_reasoning["next_if_fail"] or "残余リスクは未記載です。"

    continuation = parsed.get("continue_or_return")
    continuation_explicit = isinstance(continuation, dict)
    if not isinstance(continuation, dict):
        continuation = {}
    decision = str(continuation.get("decision", "")).strip()
    if decision not in {"continue_here", "open_child_frame", "return_to_parent", "escalate_to_top"}:
        decision = "continue_here"
    normalized_continuation = {
        "decision": decision,
        "reason": _sanitize_prompt_text(str(continuation.get("reason", "")).strip(), max_chars=240),
        "next_goal": _sanitize_prompt_text(str(continuation.get("next_goal", "")).strip(), max_chars=240),
        "child_goals": [],
    }
    raw_child_goals = continuation.get("child_goals")
    if not isinstance(raw_child_goals, list):
        raw_child_goals = continuation.get("next_goals")
    if not isinstance(raw_child_goals, list):
        raw_child_goals = continuation.get("decomposed_goals")
    normalized_child_goals: list[str] = []
    if isinstance(raw_child_goals, list):
        for item in raw_child_goals:
            goal_text = _sanitize_prompt_text(str(item or "").strip(), max_chars=240)
            if goal_text and goal_text not in normalized_child_goals:
                normalized_child_goals.append(goal_text)
    if not normalized_continuation["reason"]:
        normalized_continuation["reason"] = (
            normalized_post_reflection["remaining_risk"] or normalized_situation["chosen_response"] or "この階層で継続判断を維持します。"
        )
    if not normalized_continuation["next_goal"]:
        normalized_continuation["next_goal"] = normalized_reasoning["next_if_fail"] or normalized_reasoning["edit_intent"]
    if normalized_continuation["decision"] == "open_child_frame":
        if normalized_continuation["next_goal"] and normalized_continuation["next_goal"] not in normalized_child_goals:
            normalized_child_goals.insert(0, normalized_continuation["next_goal"])
        if not normalized_child_goals and normalized_continuation["next_goal"]:
            normalized_child_goals = [normalized_continuation["next_goal"]]
        if not normalized_continuation["next_goal"] and normalized_child_goals:
            normalized_continuation["next_goal"] = normalized_child_goals[0]
    normalized_continuation["child_goals"] = normalized_child_goals[:6]

    revised = parsed.get("revised_file_content")
    if not isinstance(revised, str) or not revised.strip():
        revised = _extract_code_block(raw_text)
    change_summary = parsed.get("change_summary")
    if isinstance(change_summary, dict):
        candidate_parts = [
            str(change_summary.get("summary", "")).strip(),
            str(change_summary.get("description", "")).strip(),
            str(change_summary.get("rationale", "")).strip(),
        ]
        change_summary = " / ".join(part for part in candidate_parts if part)
    if not isinstance(change_summary, str) or not change_summary.strip():
        change_summary = "Model did not provide a textual change summary."

    self_memo = parsed.get("self_memo")
    if not isinstance(self_memo, dict):
        self_memo = {}
    try:
        confidence = float(self_memo.get("confidence", 0) or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    normalized_self_memo = {
        "title": _sanitize_prompt_text(str(self_memo.get("title", "")).strip(), max_chars=120),
        "when": _sanitize_prompt_text(str(self_memo.get("when", "")).strip(), max_chars=200),
        "tactic": _sanitize_prompt_text(str(self_memo.get("tactic", "")).strip(), max_chars=200),
        "why": _sanitize_prompt_text(str(self_memo.get("why", "")).strip(), max_chars=200),
        "confidence": confidence,
        "tags": [
            _sanitize_prompt_text(str(item).strip(), max_chars=48)
            for item in (self_memo.get("tags") or [])
            if _sanitize_prompt_text(str(item).strip(), max_chars=48)
        ][:6],
    }
    return {
        "reasoning_summary": normalized_reasoning,
        "situation_report": normalized_situation,
        "post_edit_reflection": normalized_post_reflection,
        "continue_or_return": normalized_continuation,
        "continue_or_return_explicit": continuation_explicit,
        "change_summary": change_summary.strip(),
        "revised_file_content": revised.rstrip() + "\n",
        "self_memo": normalized_self_memo,
    }


def _parse_reflection_response(raw_text: str) -> dict[str, str]:
    fallback = _parse_model_response(raw_text)
    parsed: dict[str, Any] | None = None
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        try:
            parsed = extract_first_json_object(raw_text)
        except json.JSONDecodeError:
            parsed = None
    if not isinstance(parsed, dict):
        parsed = {}
    if isinstance(parsed.get("pre_edit_reflection"), dict):
        parsed = parsed["pre_edit_reflection"]
    result = {
        "what_i_tried": _sanitize_prompt_text(str(parsed.get("what_i_tried", "")).strip(), max_chars=240),
        "what_kept_happening": _sanitize_prompt_text(str(parsed.get("what_kept_happening", "")).strip(), max_chars=240),
        "what_this_suggests_about_my_search": _sanitize_prompt_text(
            str(parsed.get("what_this_suggests_about_my_search", "")).strip(),
            max_chars=240,
        ),
        "what_i_might_be_missing": _sanitize_prompt_text(
            str(parsed.get("what_i_might_be_missing", "")).strip(),
            max_chars=240,
        ),
        "what_must_be_different_this_time": _sanitize_prompt_text(
            str(parsed.get("what_must_be_different_this_time", "")).strip(),
            max_chars=240,
        ),
    }
    if not result["what_i_tried"]:
        result["what_i_tried"] = fallback["reasoning_summary"].get("edit_intent") or "直近の意図を十分に再構成できませんでした。"
    if not result["what_kept_happening"]:
        result["what_kept_happening"] = fallback["reasoning_summary"].get("problem_statement") or "繰り返し現象を十分に再構成できませんでした。"
    if not result["what_this_suggests_about_my_search"]:
        result["what_this_suggests_about_my_search"] = fallback["reasoning_summary"].get("diagnosis") or "探索様式の問題を十分に再構成できませんでした。"
    if not result["what_i_might_be_missing"]:
        result["what_i_might_be_missing"] = "自分の見方に偏りがあり、観測と行動が結びついていない可能性があります。"
    if not result["what_must_be_different_this_time"]:
        result["what_must_be_different_this_time"] = (
            fallback["situation_report"].get("chosen_response") or "今回は前回と違う具体的なコード変更を必ず作ります。"
        )
    return result


def _parse_reference_selection_response(raw_text: str, *, allowed_ids: set[str]) -> dict[str, Any]:
    parsed: dict[str, Any] | None = None
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        try:
            parsed = extract_first_json_object(raw_text)
        except json.JSONDecodeError:
            parsed = None
    if not isinstance(parsed, dict):
        parsed = {}
    selected = parsed.get("selected_context")
    if not isinstance(selected, list):
        selected = []
    normalized_selected = []
    for item in selected:
        candidate = _safe_brief_text(item, max_chars=64)
        if candidate in allowed_ids and candidate not in normalized_selected:
            normalized_selected.append(candidate)
    return {
        "question_to_answer": _sanitize_prompt_text(str(parsed.get("question_to_answer", "")).strip(), max_chars=240),
        "selected_context": normalized_selected[:3],
        "commitment": _sanitize_prompt_text(str(parsed.get("commitment", "")).strip(), max_chars=240),
    }
