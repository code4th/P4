from __future__ import annotations

import json
from typing import Any, Callable

from p2_core.backend import extract_first_json_object
from p2_core.loop_utils import _safe_brief_text, _sanitize_prompt_text


def _build_frame_judgment_state(
    *,
    applied_patch_count: int,
    target_changed_in_frame: bool,
    dirty_since_validation: bool,
    last_validation_report: dict[str, Any] | None,
    successful_validation_count: int,
    delta_context: dict[str, Any],
    has_parent_frame: bool,
) -> dict[str, Any]:
    latest_failure = dict(delta_context.get("latest_failure") or {})
    last_validation_passed = bool(last_validation_report and last_validation_report.get("passed"))
    can_finish_this_frame = bool(target_changed_in_frame and last_validation_passed and not dirty_since_validation)

    return_materials: list[str] = []
    if can_finish_this_frame:
        return_materials.append("このフレームで加えた変更は直近の validation を通過しており、その後の未検証変更はありません。")
    if latest_failure:
        failure_summary = _safe_brief_text(
            latest_failure.get("summary")
            or latest_failure.get("detail")
            or "validation failure の局所原因が抽出されています。",
            max_chars=180,
        )
        return_materials.append(f"局所 failure 情報を親へ返せます: {failure_summary}")

    decompose_materials: list[str] = []
    if delta_context.get("repeated_pattern"):
        decompose_materials.append("同種の失敗パターンが繰り返されています。")
    if latest_failure and not last_validation_passed:
        decompose_materials.append("失敗箇所が局所化されており、さらに狭い下位問題へ分解できます。")

    continue_materials: list[str] = []
    if not target_changed_in_frame:
        continue_materials.append("まだ対象ファイルに差分がありません。")
    if dirty_since_validation:
        continue_materials.append("未検証の変更が残っています。")
    if not last_validation_report:
        continue_materials.append("まだ validation を実行していません。")
    if not continue_materials and not return_materials and not decompose_materials:
        continue_materials.append("このフレームの問いに対する材料がまだ足りません。")

    return {
        "facts": {
            "applied_patch_count": applied_patch_count,
            "target_changed_in_frame": target_changed_in_frame,
            "dirty_since_validation": dirty_since_validation,
            "last_validation_passed": last_validation_passed,
            "successful_validation_count": successful_validation_count,
            "has_latest_failure": bool(latest_failure),
            "repeated_failure_pattern": bool(delta_context.get("repeated_pattern")),
            "has_parent_frame": has_parent_frame,
        },
        "judgment_materials": {
            "can_finish_this_frame": can_finish_this_frame,
            "can_return_with_material": bool(return_materials),
            "should_consider_decomposition": bool(decompose_materials),
            "return_materials": return_materials,
            "decomposition_materials": decompose_materials,
            "continue_here_materials": continue_materials,
        },
        "note": "これは命令ではなく判断材料です。open_child_frame / continue_or_return / finish / 直接継続（read/search/apply/validate）の選択は自分で行ってください。",
    }


def _action_input_signature(action_input: dict[str, Any]) -> str:
    try:
        return json.dumps(action_input, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return str(action_input)


def _stagnation_event_marker(action: str, action_input: dict[str, Any], result: dict[str, Any]) -> str:
    if action == "apply_patch":
        added = int(result.get("added_lines") or 0)
        removed = int(result.get("removed_lines") or 0)
        if added == 0 and removed == 0:
            return f"apply_patch:no_change:{_action_input_signature(action_input)}"
        return ""
    if action == "run_validation":
        if result.get("ok") is False or result.get("passed") is False:
            failure = result.get("failure") or {}
            summary = str(failure.get("summary") or result.get("summary") or "validation_failed")
            return f"run_validation:failed:{summary}"
        return ""
    if action in {"read_file", "search_code"}:
        return f"{action}:{_action_input_signature(action_input)}"
    if action == "invalid_response":
        return "invalid_response"
    return ""


def _parse_session_action_response(
    raw_text: str,
    *,
    target_file: str,
    parse_legacy: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    parsed: dict[str, Any] | None = None
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        try:
            parsed = extract_first_json_object(raw_text)
        except json.JSONDecodeError:
            parsed = None
    if isinstance(parsed, dict) and ("revised_file_content" in parsed or "reasoning_summary" in parsed):
        return {"mode": "legacy", "legacy": parse_legacy(raw_text) if parse_legacy is not None else None}
    if not isinstance(parsed, dict):
        return {
            "mode": "action",
            "thinking": "モデル応答が JSON として解釈できなかったため、対象ファイルを再読して足場を作る。",
            "action": "read_file",
            "action_input": {"path": target_file},
        }
    action = str(parsed.get("action") or "").strip()
    if action == "continue_or_return":
        action = "return_to_parent"
    if action not in {
        "read_file",
        "search_code",
        "apply_patch",
        "run_validation",
        "open_child_frame",
        "return_to_parent",
        "finish",
    }:
        action = "read_file"
    action_input = parsed.get("action_input")
    if not isinstance(action_input, dict):
        action_input = {}
    return {
        "mode": "action",
        "thinking": _sanitize_prompt_text(str(parsed.get("thinking") or "").strip(), max_chars=1000),
        "action": action,
        "action_input": action_input,
        "raw_payload": parsed,
    }
