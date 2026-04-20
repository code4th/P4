from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any

from p2_core.loop_delta import _validation_failure_summary
from p2_core.loop_utils import _safe_brief_text, _sanitize_prompt_text
from p2_core.workspace import (
    WorkspacePaths,
    append_jsonl,
    append_loop_log,
    build_status_snapshot,
    now_iso,
    read_json,
    read_jsonl_rows,
    read_validation_report,
    write_json,
)


def _load_latest_failed_summary(root: Path) -> str | None:
    snapshot = build_status_snapshot(root)
    for attempt in reversed(snapshot.get("recent_attempts", [])):
        if attempt.get("status") not in {"rejected", "rolled_back", "failed"}:
            continue
        report = read_validation_report(root, attempt["candidate_id"])
        summary = _validation_failure_summary(report)
        if summary:
            return summary
        decision_reason = attempt.get("decision_reason")
        if decision_reason:
            return _sanitize_prompt_text(str(decision_reason), max_chars=160)
    return None


def _recent_attempt_reports_with_started(root: Path, *, limit: int = 12) -> list[dict[str, Any]]:
    paths = WorkspacePaths(root)
    reports: list[dict[str, Any]] = []
    for path in sorted(paths.attempts_dir.glob("c*.json")):
        try:
            reports.append(read_json(path))
        except (FileNotFoundError, json.JSONDecodeError):
            continue
    return reports[-limit:]


def _attempt_is_terminal(status: Any) -> bool:
    return str(status or "").strip() in {"promoted", "rejected", "rolled_back", "failed", "completed"}


def _mark_attempt_failed(
    root: Path,
    *,
    candidate_id: str,
    reason: str,
    phase: str | None = None,
    note: str | None = None,
) -> dict[str, Any] | None:
    paths = WorkspacePaths(root)
    attempt_path = paths.attempt_report_path(candidate_id)
    if not attempt_path.exists():
        return None
    attempt = read_json(attempt_path, fallback={})
    if not isinstance(attempt, dict):
        return None
    if _attempt_is_terminal(attempt.get("status")):
        return attempt
    reason_text = _safe_brief_text(reason, max_chars=240)
    if phase:
        reason_text = f"{phase} 中に異常終了: {reason_text}"
    attempt["status"] = "failed"
    attempt["decision_reason"] = reason_text
    attempt["finished_at"] = now_iso()
    attempt["failure_summary"] = note or reason_text
    write_json(attempt_path, attempt)
    events_path = paths.session_events_path(candidate_id)
    existing_events = read_jsonl_rows(events_path)
    max_step = 0
    for event in existing_events:
        if not isinstance(event, dict):
            continue
        try:
            max_step = max(max_step, int(event.get("step", 0) or 0))
        except (TypeError, ValueError):
            continue
    append_jsonl(
        events_path,
        {
            "timestamp": now_iso(),
            "frame_id": str((attempt.get("task_frame") or {}).get("frame_id") or f"{candidate_id}:d0:f0"),
            "frame_depth": int((attempt.get("task_frame") or {}).get("depth", 0) or 0),
            "step": max_step + 1,
            "action": "runtime_failed",
            "action_input": {},
            "thinking": "ランタイム要因で attempt を継続できないため、失敗として回収します。",
            "result": {
                "ok": False,
                "reason": reason_text,
                "phase": phase or "",
                "note": note or "",
            },
        },
    )
    append_loop_log(root, f"attempt marked failed candidate={candidate_id} reason={reason_text}")
    return attempt


def _close_stale_started_attempts(root: Path, *, preserve_candidate_id: str | None = None) -> list[str]:
    paths = WorkspacePaths(root)
    closed: list[str] = []
    for attempt_path in sorted(paths.attempts_dir.glob("c*.json")):
        attempt = read_json(attempt_path, fallback={})
        if not isinstance(attempt, dict):
            continue
        candidate_id = str(attempt.get("candidate_id") or "").strip()
        if not candidate_id or candidate_id == preserve_candidate_id:
            continue
        if str(attempt.get("status") or "").strip() != "started":
            continue
        updated = _mark_attempt_failed(
            root,
            candidate_id=candidate_id,
            phase="previous_run",
            reason="前回の run-loop が attempt 完了前に終了しました。",
            note="古い started attempt を回収しました。",
        )
        if updated:
            closed.append(candidate_id)
    return closed


def _latest_resumable_started_attempt(root: Path, *, goal_id: str | None = None) -> dict[str, Any] | None:
    paths = WorkspacePaths(root)
    latest: dict[str, Any] | None = None
    for attempt_path in sorted(paths.attempts_dir.glob("c*.json")):
        attempt = read_json(attempt_path, fallback={})
        if not isinstance(attempt, dict):
            continue
        if str(attempt.get("status") or "").strip() != "started":
            continue
        if goal_id:
            if str(attempt.get("goal_id") or "").strip() != str(goal_id):
                continue
        candidate_id = str(attempt.get("candidate_id") or "").strip()
        target_file = str(attempt.get("target_file") or "").strip()
        if not candidate_id or not target_file:
            continue
        candidate_target = paths.runtime_candidates_dir / candidate_id / target_file
        if not candidate_target.exists():
            continue
        latest = attempt
    return latest


def _classify_decision_reason(reason: str | None) -> str:
    text = str(reason or "").strip()
    if not text:
        return "unknown"
    lowered = text.lower()
    if "validation failed" in lowered:
        return "validation_failed"
    if "candidate did not change" in lowered:
        return "no_change"
    if "低価値" in text:
        return "low_value"
    if "protected" in lowered:
        return "protected_path"
    if "timed out" in lowered:
        return "runtime_timeout"
    if "異常終了" in text or "終了しました" in text:
        return "runtime_error"
    if "retry failed" in lowered or "rolled back" in lowered:
        return "retry_failed"
    return "other"


def _attempt_max_frame_depth(attempt: dict[str, Any]) -> int:
    depths: list[int] = []
    task_frame = attempt.get("task_frame")
    if isinstance(task_frame, dict):
        try:
            depths.append(int(task_frame.get("depth", 0) or 0))
        except (TypeError, ValueError):
            pass
    for frame in attempt.get("task_stack") or []:
        if not isinstance(frame, dict):
            continue
        try:
            depths.append(int(frame.get("depth", 0) or 0))
        except (TypeError, ValueError):
            continue
    return max(depths) if depths else 0


def _summarize_meta_diagnosis(root: Path) -> dict[str, Any]:
    attempts = _recent_attempt_reports_with_started(root, limit=14)
    recent_completed = [attempt for attempt in attempts if _attempt_is_terminal(attempt.get("status"))]
    recent_started = [attempt for attempt in attempts if attempt.get("status") == "started"]
    reason_counter = Counter(_classify_decision_reason(attempt.get("decision_reason")) for attempt in recent_completed)
    target_counter = Counter(
        _safe_brief_text(attempt.get("target_file") or "", max_chars=160)
        for attempt in attempts
        if attempt.get("target_file")
    )
    since_last_promotion = 0
    for attempt in reversed(recent_completed):
        if attempt.get("status") == "promoted":
            break
        since_last_promotion += 1
    else:
        since_last_promotion = len(recent_completed)

    stagnation_score = 0
    if since_last_promotion >= 3:
        stagnation_score += 2
    if reason_counter.get("validation_failed", 0) >= 2:
        stagnation_score += 2
    if reason_counter.get("no_change", 0) + reason_counter.get("low_value", 0) >= 2:
        stagnation_score += 2
    if len(recent_started) >= 2:
        stagnation_score += 2
    if len(target_counter) == 1 and target_counter:
        stagnation_score += 1

    recent_outcomes = [
        {
            "candidate_id": _safe_brief_text(attempt.get("candidate_id"), max_chars=32),
            "status": _safe_brief_text(attempt.get("status"), max_chars=32),
            "decision_type": _classify_decision_reason(attempt.get("decision_reason")),
            "target_file": _safe_brief_text(attempt.get("target_file"), max_chars=160),
        }
        for attempt in recent_completed[-6:]
    ]
    validation_summaries: list[str] = []
    for attempt in reversed(recent_completed):
        if attempt.get("status") != "rejected":
            continue
        report = read_validation_report(root, str(attempt.get("candidate_id")))
        summary = _validation_failure_summary(report)
        if summary:
            validation_summaries.append(summary)
        if len(validation_summaries) >= 3:
            break

    recent_selected_coding_models: list[str] = []
    for attempt in reversed(attempts):
        model_name = str(attempt.get("selected_coding_model") or "").strip()
        if model_name:
            recent_selected_coding_models.append(model_name)

    frame_transition_counter = Counter(
        str(((attempt.get("continue_or_return") or {}).get("decision")) or "continue_here")
        for attempt in recent_completed
    )
    recent_frame_depths = [
        {
            "candidate_id": _safe_brief_text(attempt.get("candidate_id"), max_chars=32),
            "max_depth": _attempt_max_frame_depth(attempt),
            "transition": str(((attempt.get("continue_or_return") or {}).get("decision")) or "continue_here"),
        }
        for attempt in recent_completed[-6:]
    ]
    flat_frame_streak = 0
    for attempt in reversed(recent_completed):
        if _attempt_max_frame_depth(attempt) == 0:
            flat_frame_streak += 1
            continue
        break

    if stagnation_score >= 5:
        status = "stagnating"
    elif stagnation_score >= 2:
        status = "watch"
    else:
        status = "normal"

    no_change_like = reason_counter.get("no_change", 0) + reason_counter.get("low_value", 0)
    if status == "normal":
        search_mode = "direct_improvement"
    elif len(recent_started) >= 2 or reason_counter.get("validation_failed", 0) >= 2:
        search_mode = "constraint_probe"
    elif no_change_like >= 2 and len(target_counter) == 1:
        search_mode = "reframe"
    else:
        search_mode = "constraint_probe"

    return {
        "status": status,
        "search_mode": search_mode,
        "observation_bundle": {
            "stagnation_score": stagnation_score,
            "since_last_promotion": since_last_promotion,
            "decision_reason_histogram": dict(reason_counter),
            "unfinished_started_attempts": len(recent_started),
            "target_histogram": dict(target_counter),
            "recent_frame_transition_histogram": dict(frame_transition_counter),
            "recent_frame_depths": recent_frame_depths,
            "flat_frame_streak": flat_frame_streak,
            "recent_completed_outcomes": recent_outcomes,
            "recent_validation_summaries": list(reversed(validation_summaries)),
            "recent_selected_coding_models": recent_selected_coding_models[:6],
            "last_selected_coding_model": recent_selected_coding_models[0] if recent_selected_coding_models else None,
        },
    }
