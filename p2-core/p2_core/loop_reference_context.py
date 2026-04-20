from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from p2_core.loop_attempt_meta import _classify_decision_reason, _recent_attempt_reports_with_started
from p2_core.loop_delta import (
    _build_action_result_raw,
    _extract_failure_detail,
    _is_meaningful_failure_detail,
    _read_text_if_exists,
    _validation_failure_summary,
)
from p2_core.loop_utils import _safe_brief_text, _sanitize_code_context, _sanitize_prompt_text
from p2_core.workspace import (
    build_status_snapshot,
    read_attempt_report,
    read_memos,
    read_system_skills,
    read_validation_report,
)


def _build_delta_context(root: Path, *, goal_id: str | None = None) -> dict[str, Any]:
    attempts = _recent_attempt_reports_with_started(root, limit=12)
    if goal_id:
        scoped = [
            attempt
            for attempt in attempts
            if str(attempt.get("goal_id") or "").strip() == str(goal_id)
        ]
        if scoped:
            attempts = scoped
    failed_attempts = [attempt for attempt in attempts if attempt.get("status") in {"rejected", "rolled_back"}]
    local_failures: list[dict[str, Any]] = []
    for attempt in reversed(failed_attempts):
        report = read_validation_report(root, str(attempt.get("candidate_id")), retry=attempt.get("status") == "rolled_back")
        if report is None:
            report = read_validation_report(root, str(attempt.get("candidate_id")))
        detail = _extract_failure_detail(report, target_file=str(attempt.get("target_file") or ""))
        if not _is_meaningful_failure_detail(detail, report):
            continue
        raw_bundle = _build_action_result_raw(root=root, attempt=attempt, report=report, detail=detail)
        local_failures.append(
            {
                "candidate_id": attempt.get("candidate_id"),
                "target_file": attempt.get("target_file"),
                **detail,
                **raw_bundle,
            }
        )
        if len(local_failures) >= 3:
            break
    repeated_pattern = False
    repeated_count = 0
    if len(local_failures) >= 2:
        first = local_failures[0]
        repeated_count = sum(
            1
            for failure in local_failures
            if failure.get("error_type") == first.get("error_type")
            and failure.get("target_file") == first.get("target_file")
            and failure.get("detail") == first.get("detail")
        )
        repeated_pattern = repeated_count >= 2
    latest = local_failures[0] if local_failures else {}
    must_avoid_next: list[str] = []
    if latest.get("error_type") == "SyntaxError":
        if latest.get("line"):
            must_avoid_next.append(f"直近は {latest.get('line')} 行目付近で構文を壊したため、その周辺を大きく崩さない")
        if latest.get("file"):
            must_avoid_next.append(f"直近の構文エラーは {latest.get('file')} で起きたため、同じ場所の編集は最小化する")
        must_avoid_next.append("変更後に unittest の前に構文妥当性を必ず確認する")
    if repeated_pattern:
        must_avoid_next.append("同型失敗が続いているため、前回と同じ編集様式を繰り返さない")
    return {
        "latest_failure": latest,
        "recent_failures": list(reversed(local_failures)),
        "action_raw": latest.get("action_raw") or {},
        "result_raw": latest.get("result_raw") or {},
        "repeated_pattern": repeated_pattern,
        "repeated_count": repeated_count,
        "must_avoid_next": must_avoid_next,
    }


def _build_reference_index(root: Path, *, target_file: str, active_path: Path, goal_id: str | None = None) -> list[dict[str, Any]]:
    del active_path
    snapshot = build_status_snapshot(root, attempt_limit=8, history_limit=8)
    system_skills = read_system_skills(root)
    recent_memos = read_memos(root, limit=4)
    entries: list[dict[str, Any]] = [
        {
            "id": "active_target_file",
            "kind": "file",
            "summary": f"現在の編集対象 {target_file} の全文",
        },
        {
            "id": "tests_context",
            "kind": "tests",
            "summary": "受け入れ条件に関係する tests の内容",
        },
        {
            "id": "current_task_stack",
            "kind": "task_stack",
            "summary": "現在のフレーム階層と継続判断の状態",
        },
    ]
    for skill in system_skills:
        skill_id = str(skill.get("skill_id") or "").strip()
        if not skill_id:
            continue
        entries.append(
            {
                "id": f"skill:{skill_id}",
                "kind": "skill",
                "summary": f"{skill.get('title')}: {_safe_brief_text(skill.get('summary'), max_chars=120)}",
            }
        )
    for memo in reversed(recent_memos):
        memo_id = str(memo.get("memo_id") or "").strip()
        if not memo_id:
            continue
        entries.append(
            {
                "id": f"memo:{memo_id}",
                "kind": "memo",
                "summary": (
                    f"{_safe_brief_text(memo.get('title'), max_chars=64)} / "
                    f"{_safe_brief_text(memo.get('when'), max_chars=80)} / "
                    f"confidence={memo.get('confidence')}"
                ),
            }
        )
    completed_attempts = [attempt for attempt in snapshot.get("recent_attempts", []) if attempt.get("status") != "started"]
    if goal_id:
        scoped = [
            attempt
            for attempt in completed_attempts
            if str(attempt.get("goal_id") or "").strip() == str(goal_id)
        ]
        if scoped:
            completed_attempts = scoped
    for attempt in reversed(completed_attempts[-3:]):
        candidate_id = str(attempt.get("candidate_id"))
        entries.append(
            {
                "id": f"attempt:{candidate_id}",
                "kind": "attempt",
                "summary": (
                    f"{candidate_id} の試行内容、自己診断、継続判断、局所失敗差分"
                    if attempt.get("status") == "rejected"
                    else f"{candidate_id} の試行内容、自己診断、継続判断、変更内容"
                ),
            }
        )
        entries.append(
            {
                "id": f"diff:{candidate_id}",
                "kind": "diff",
                "summary": f"{candidate_id} の差分、変更行、前後スニペット",
            }
        )
        entries.append(
            {
                "id": f"validation:{candidate_id}",
                "kind": "validation",
                "summary": f"{candidate_id} の検証コマンド結果、stderr、失敗位置",
            }
        )
    for attempt in reversed(completed_attempts[-2:]):
        candidate_id = str(attempt.get("candidate_id"))
        if not any(entry["id"] == f"diff:{candidate_id}" for entry in entries):
            entries.append(
                {
                    "id": f"diff:{candidate_id}",
                    "kind": "diff",
                    "summary": f"{candidate_id} の差分、変更行、前後スニペット",
                }
            )
    return entries


def _resolve_selected_context(
    root: Path,
    *,
    selected_context: list[str],
    target_file: str,
    current_content: str,
    active_path: Path,
    read_test_context: Callable[[Path, int], str],
) -> dict[str, Any]:
    resolved: dict[str, Any] = {}
    skills_by_id = {
        str(skill.get("skill_id")): skill
        for skill in read_system_skills(root)
        if isinstance(skill, dict) and str(skill.get("skill_id") or "").strip()
    }
    memos_by_id = {
        str(memo.get("memo_id")): memo
        for memo in read_memos(root, limit=24)
        if isinstance(memo, dict) and str(memo.get("memo_id") or "").strip()
    }
    for ref_id in selected_context:
        if ref_id == "active_target_file":
            resolved[ref_id] = _sanitize_code_context(current_content, max_chars=2400)
            continue
        if ref_id == "tests_context":
            resolved[ref_id] = read_test_context(active_path, 2400)
            continue
        if ref_id == "current_task_stack":
            snapshot = build_status_snapshot(root, attempt_limit=8, history_limit=8)
            resolved[ref_id] = snapshot.get("current_task_stack") or []
            continue
        if ref_id.startswith("skill:"):
            skill_id = ref_id.split(":", 1)[1]
            if skill_id in skills_by_id:
                resolved[ref_id] = skills_by_id[skill_id]
            continue
        if ref_id.startswith("memo:"):
            memo_id = ref_id.split(":", 1)[1]
            if memo_id in memos_by_id:
                resolved[ref_id] = memos_by_id[memo_id]
            continue
        if ref_id.startswith("attempt:"):
            candidate_id = ref_id.split(":", 1)[1]
            attempt = read_attempt_report(root, candidate_id)
            report = read_validation_report(root, candidate_id)
            delta_context = attempt.get("delta_context") or {}
            resolved[ref_id] = {
                "candidate_id": candidate_id,
                "status": attempt.get("status"),
                "decision_type": _classify_decision_reason(attempt.get("decision_reason")),
                "selected_context": (attempt.get("selected_context") or {}).get("selected_context"),
                "commitment": (attempt.get("selected_context") or {}).get("commitment"),
                "pre_edit_reflection": attempt.get("pre_edit_reflection"),
                "situation_report": attempt.get("situation_report"),
                "post_edit_reflection": attempt.get("post_edit_reflection"),
                "continue_or_return": attempt.get("continue_or_return"),
                "task_frame": attempt.get("task_frame"),
                "latest_failure": delta_context.get("latest_failure"),
                "action_raw": delta_context.get("action_raw") or {},
                "result_raw": delta_context.get("result_raw") or {},
                "validation_summary": _validation_failure_summary(report),
            }
            continue
        if ref_id.startswith("diff:"):
            candidate_id = ref_id.split(":", 1)[1]
            attempt = read_attempt_report(root, candidate_id)
            delta_context = attempt.get("delta_context") or {}
            diff_path = attempt.get("diff_path")
            diff_text = ""
            if diff_path and Path(diff_path).exists():
                diff_text = Path(diff_path).read_text(encoding="utf-8")
            resolved[ref_id] = {
                "candidate_id": candidate_id,
                "change_summary": attempt.get("change_summary"),
                "diff_excerpt": _sanitize_prompt_text(diff_text, max_chars=1200),
                "action_raw": delta_context.get("action_raw") or {},
                "latest_failure": delta_context.get("latest_failure") or {},
            }
            continue
        if ref_id.startswith("validation:"):
            candidate_id = ref_id.split(":", 1)[1]
            report = read_validation_report(root, candidate_id)
            detail = _extract_failure_detail(report, target_file=target_file)
            stderr = ""
            stdout = ""
            if report:
                stderr = _read_text_if_exists(Path(report["stderr_path"])) if report.get("stderr_path") else ""
                stdout = _read_text_if_exists(Path(report["stdout_path"])) if report.get("stdout_path") else ""
            resolved[ref_id] = {
                "candidate_id": candidate_id,
                "command": (report or {}).get("command"),
                "passed": (report or {}).get("passed"),
                "returncode": (report or {}).get("returncode"),
                "failure_detail": detail,
                "stderr_excerpt": _sanitize_prompt_text(stderr, max_chars=1200),
                "stdout_excerpt": _sanitize_prompt_text(stdout, max_chars=600),
            }
    return resolved
