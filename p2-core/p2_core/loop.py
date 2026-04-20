from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Any

from p2_core.backend import BackendError, ModelBackend
from p2_core.agent_runtime import AgentRuntime, Frame as RuntimeFrame, RuntimeValidationError
from p2_core.loop_frame_memory import (
    _append_unique_text,
    _build_inherited_context,
    _build_return_payload_from_action_input,
    _empty_working_memory,
    _frame_local_tool_results,
    _frame_local_working_memory,
    _merge_child_return_into_parent,
    _merge_text_lists,
    _merge_working_memory,
    _update_frame_working_memory,
)
from p2_core.loop_utils import (
    _changed_paths_from_diff,
    _line_counts,
    _safe_brief_text,
    _sanitize_code_context,
    _sanitize_prompt_text,
    _sha256,
)
from p2_core.loop_delta import (
    _build_frame_delta_context,
    _choose_trace_location,
    _clear_delta_context_after_success,
    _delta_context_for_prompt,
    _extract_failure_detail,
    _extract_line_snippet,
    _is_meaningful_failure_detail,
    _validation_failure_summary,
)
from p2_core.loop_prompts import (
    _build_prompts,
    _build_reference_selection_prompts,
    _build_reflection_prompts,
    _build_session_action_prompts,
)
from p2_core.loop_response_parsers import (
    _parse_model_response,
    _parse_reference_selection_response,
    _parse_reflection_response,
)
from p2_core.loop_runtime_helpers import (
    _default_backend,
    _emit_model_chunk,
    _emit_reflection_chunk,
    _phase_label,
    _run_validation,
)
from p2_core.loop_change_quality import _low_value_change_reason, _recent_attempt_memory
from p2_core.loop_frame_helpers import (
    _build_task_frame,
    _frame_affordances,
    _recent_attempt_observation_bundle,
    _system_capabilities,
    _task_stack_summary,
    _update_task_frame_outcome,
)
from p2_core.loop_reference_context import _build_delta_context, _build_reference_index, _resolve_selected_context
from p2_core.loop_attempt_meta import (
    _attempt_is_terminal,
    _close_stale_started_attempts,
    _latest_resumable_started_attempt,
    _load_latest_failed_summary,
    _mark_attempt_failed,
    _summarize_meta_diagnosis,
)
from p2_core.loop_attempt_report import _build_attempt_report
from p2_core.loop_attempt_runtime import _persist_attempt_runtime_state
from p2_core.loop_frame_runtime import _apply_closed_frame_state, _set_current_frame_state
from p2_core.loop_runtime_init import _prepare_runtime_backends
from p2_core.loop_model_selection import _choose_coding_model
from p2_core.loop_stream_runtime import _append_stream_chunk
from p2_core.loop_streamed_step import _run_streamed_step_impl
from p2_core.loop_stagnation import _advance_stagnation_state, _is_stagnation_threshold_exceeded
from p2_core.loop_session_events import _record_session_event
from p2_core.loop_session_actions import (
    _action_input_signature,
    _build_frame_judgment_state,
    _parse_session_action_response,
    _stagnation_event_marker,
)
from p2_core.loop_action_runtime import _mark_action_runtime_event
from p2_core.loop_session_io import _append_action_raw_output
from p2_core.loop_action_tools import (
    _apply_structured_patch,
    _read_file_slice,
    _render_target_diff,
    _resolve_candidate_path,
    _resolve_read_path,
    _search_workspace,
)
from p2_core.terminology import MEMORY_NAMES, RUNTIME_NAMES
from p2_core.workspace import (
    WorkspacePaths,
    advance_goal_after_promotion,
    advance_goal_focus,
    append_jsonl,
    append_history,
    append_loop_log,
    append_memo,
    build_status_snapshot,
    copytree_clean,
    dequeue_queue_item,
    history_event,
    make_loop_run_id,
    notify_dashboard,
    now_iso,
    read_attempt_report,
    read_json,
    read_jsonl_rows,
    read_memos,
    sync_self_model_payload,
    read_validation_report,
    tail_text,
    update_runtime_status,
    write_json,
)

def _clone_reason(*, parent_generation: int, candidate_id: str, target_file: str) -> str:
    active_version_id = f"v{parent_generation:04d}"
    return (
        f"{RUNTIME_NAMES['active_version']['formal_name']} {active_version_id} を "
        f"{RUNTIME_NAMES['candidate_version']['formal_name']} {candidate_id} として分離し、"
        f"{target_file} の変更を現行コードへ直接当てずに検証するため。"
    )


def _is_protected_path(target_file: str, immutable_paths: list[str]) -> bool:
    target = PurePosixPath(target_file)
    for value in immutable_paths:
        candidate = PurePosixPath(value)
        if str(value).endswith("/"):
            if candidate in target.parents or target == candidate:
                return True
            continue
        if target == candidate:
            return True
    return False


def _select_target_file(active_path: Path, self_model: dict[str, Any]) -> str:
    for candidate in self_model.get("editable_zones", []):
        if (active_path / candidate).exists():
            return str(candidate)
    raise FileNotFoundError("no editable zone found in active version")


def _append_session_event(root: Path, candidate_id: str, payload: dict[str, Any]) -> None:
    append_jsonl(WorkspacePaths(root).session_events_path(candidate_id), payload)


def _append_lifecycle_session_event(
    root: Path,
    *,
    candidate_id: str,
    action: str,
    thinking: str,
    result: dict[str, Any],
    task_frame: dict[str, Any] | None = None,
) -> None:
    frame_id = (
        str((task_frame or {}).get("frame_id") or "").strip()
        or f"{candidate_id}:d0:f0"
    )
    raw_depth = (task_frame or {}).get("depth", 0)
    try:
        frame_depth = int(raw_depth or 0)
    except (TypeError, ValueError):
        frame_depth = 0
    _append_session_event(
        root,
        candidate_id,
        {
            "timestamp": now_iso(),
            "frame_id": frame_id,
            "frame_depth": frame_depth,
            "step": 0,
            "action": action,
            "action_input": {},
            "thinking": thinking,
            "result": result,
        },
    )


def _normalize_finish_payload(payload: dict[str, Any], *, current_text: str) -> dict[str, Any]:
    candidate = dict(payload)
    candidate["revised_file_content"] = current_text
    return _parse_model_response(json.dumps(candidate, ensure_ascii=False))





def _read_test_context(active_path: Path, *, max_chars: int = 8000) -> str:
    blocks: list[str] = []
    for path in sorted((active_path / "tests").rglob("*.py")):
        relative = path.relative_to(active_path)
        blocks.append(f"FILE: {relative}\n```python\n{_sanitize_code_context(path.read_text(encoding='utf-8'), max_chars=max_chars)}\n```")
    context = "\n\n".join(blocks)
    return context[:max_chars] if context else "No tests were found."


def _maybe_persist_self_memo(
    root: Path,
    *,
    attempt: dict[str, Any],
    goal_id: str | None,
) -> dict[str, Any] | None:
    if attempt.get("persisted_memo_id"):
        return None
    self_memo = attempt.get("self_memo") or {}
    title = _sanitize_prompt_text(str(self_memo.get("title", "")).strip(), max_chars=120)
    when = _sanitize_prompt_text(str(self_memo.get("when", "")).strip(), max_chars=200)
    tactic = _sanitize_prompt_text(str(self_memo.get("tactic", "")).strip(), max_chars=200)
    why = _sanitize_prompt_text(str(self_memo.get("why", "")).strip(), max_chars=200)
    if not any([title, when, tactic, why]):
        return None
    try:
        confidence = float(self_memo.get("confidence", 0) or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    if confidence < 0.35:
        return None
    existing = read_memos(root, limit=24)
    for memo in reversed(existing):
        if (
            str(memo.get("title") or "") == title
            and str(memo.get("when") or "") == when
            and str(memo.get("tactic") or "") == tactic
        ):
            attempt["persisted_memo_id"] = memo.get("memo_id")
            return memo
    latest_failure = (attempt.get("delta_context") or {}).get("latest_failure") or {}
    tags = []
    for item in list(self_memo.get("tags") or []):
        normalized = _sanitize_prompt_text(str(item).strip(), max_chars=48)
        if normalized and normalized not in tags:
            tags.append(normalized)
    for inferred in [
        str(attempt.get("search_mode") or "").strip(),
        str(attempt.get("status") or "").strip(),
        str(latest_failure.get("error_type") or "").strip(),
    ]:
        if inferred and inferred not in tags:
            tags.append(inferred)
    saved = append_memo(
        root,
        {
            "goal_id": goal_id,
            "source_candidate_id": attempt.get("candidate_id"),
            "target_file": attempt.get("target_file"),
            "status": attempt.get("status"),
            "decision_reason": attempt.get("decision_reason"),
            "title": title,
            "when": when,
            "tactic": tactic,
            "why": why,
            "confidence": confidence,
            "tags": tags[:8],
            "evidence": {
                "candidate_id": attempt.get("candidate_id"),
                "error_type": latest_failure.get("error_type"),
                "failure_detail": latest_failure.get("detail"),
            },
        },
    )
    attempt["persisted_memo_id"] = saved.get("memo_id")
    return saved


def _next_candidate_id(version: dict[str, Any]) -> str:
    last_candidate_id = version.get("last_candidate_id")
    if isinstance(last_candidate_id, str) and last_candidate_id.startswith("c"):
        return f"c{int(last_candidate_id[1:]) + 1:04d}"
    return "c0001"


def _next_version_id(parent_generation: int) -> str:
    return f"v{parent_generation + 1:04d}"


def _build_change_summary(before_text: str, after_text: str, diff_text: str, model_summary: str) -> dict[str, Any]:
    added, removed = _line_counts(diff_text)
    return {
        "summary": model_summary,
        "added_lines": added,
        "removed_lines": removed,
        "before_sha256": _sha256(before_text),
        "after_sha256": _sha256(after_text),
    }


def _build_runtime_validation_frame(
    *,
    frame: dict[str, Any],
    frame_goal: str,
    parent_frame: dict[str, Any] | None,
    session_events: list[dict[str, Any]],
    last_validation_report: dict[str, Any] | None,
) -> RuntimeFrame:
    observations = [
        {"action": event.get("action"), "input": event.get("action_input"), "result": event.get("result")}
        for event in session_events
        if str(event.get("action") or "") in {"read_file", "search_code"}
    ]
    patches = [
        {"input": event.get("action_input"), "result": event.get("result")}
        for event in session_events
        if str(event.get("action") or "") == "apply_patch"
    ]
    validations: list[dict[str, Any]] = []
    for event in session_events:
        if str(event.get("action") or "") != "run_validation":
            continue
        result = dict(event.get("result") or {})
        signature = _safe_brief_text(
            result.get("summary")
            or (result.get("failure") or {}).get("summary")
            or result.get("error")
            or "",
            max_chars=240,
        )
        validations.append({"passed": bool(result.get("passed")), "signature": signature, "result": result})
    child_return_payloads = list((frame.get("context") or {}).get("child_return_payloads") or [])
    return RuntimeFrame(
        frame_id=str(frame.get("frame_id") or "frame"),
        parent_frame_id=str(parent_frame.get("frame_id") or "") if isinstance(parent_frame, dict) else None,
        goal=str(frame_goal or frame.get("goal") or ""),
        status="active",
        child_goals=list((frame.get("context") or {}).get("child_goals") or []),
        current_child_index=int((frame.get("context") or {}).get("current_child_index") or 0),
        child_results=[{"return_payload": payload} for payload in child_return_payloads if isinstance(payload, dict)],
        context=dict(frame.get("context") or {}),
        last_action=str(frame.get("last_action") or ""),
        last_validation_result=dict(last_validation_report or {}),
        observations=observations,
        patches_applied=patches,
        validations_run=validations,
        validation_success=bool(last_validation_report and last_validation_report.get("passed")),
        known_open_questions=list(((frame.get("context") or {}).get("local_working_memory") or {}).get("unresolved_questions") or []),
    )


def _validate_session_action_with_runtime(
    *,
    payload: dict[str, Any],
    runtime_frame: RuntimeFrame,
) -> dict[str, Any]:
    bridge_action = str(payload.get("action") or "")
    bridge_input = dict(payload.get("action_input") or {})
    if bridge_action == "return_to_parent":
        return_payload = dict(bridge_input.get("return_payload") or {})
        if "status" not in return_payload:
            return_payload["status"] = "done"
        bridge_input["return_payload"] = return_payload
        bridge_action = "continue_or_return"
    bridge_payload = {
        "thinking": str(payload.get("thinking") or ""),
        "action": bridge_action,
        "action_input": bridge_input,
    }
    runtime = AgentRuntime(
        goal=runtime_frame.goal,
        root_frame_id=runtime_frame.frame_id,
        frames={runtime_frame.frame_id: runtime_frame},
        active_frame_id=runtime_frame.frame_id,
    )
    normalized = runtime.validate_llm_output(bridge_payload)
    runtime.validate_action(runtime_frame, normalized)
    normalized_action = str(normalized.get("action") or "")
    normalized_input = dict(normalized.get("action_input") or {})
    if normalized_action == "continue_or_return":
        normalized_action = "return_to_parent"
    return {
        "thinking": str(normalized.get("thinking") or ""),
        "action": normalized_action,
        "action_input": normalized_input,
        "raw_payload": dict(payload.get("raw_payload") or {}),
        "mode": "action",
    }


def run_loop(
    root: Path,
    *,
    model: str,
    thinking_model: str | None = None,
    coding_model: str | None = None,
    exploratory_coding_model: str | None = None,
    stagnation_coding_model: str | None = None,
    max_iterations: int = 1,
    backend: ModelBackend | None = None,
) -> dict[str, Any]:
    root = root.expanduser()
    paths = WorkspacePaths(root)
    runtime_backends = _prepare_runtime_backends(
        model=model,
        thinking_model=thinking_model,
        coding_model=coding_model,
        exploratory_coding_model=exploratory_coding_model,
        stagnation_coding_model=stagnation_coding_model,
        backend=backend,
        backend_factory=_default_backend,
    )
    thinking_model = str(runtime_backends["thinking_model"])
    coding_model = str(runtime_backends["coding_model"])
    exploratory_coding_model = str(runtime_backends["exploratory_coding_model"])
    stagnation_coding_model = str(runtime_backends["stagnation_coding_model"])
    backend = runtime_backends["backend"]
    thinking_backend = runtime_backends["thinking_backend"]
    default_coding_backend = runtime_backends["default_coding_backend"]
    exploratory_coding_backend = runtime_backends["exploratory_coding_backend"]
    stagnation_coding_backend = runtime_backends["stagnation_coding_backend"]
    loop_run_id = make_loop_run_id()
    snapshot = build_status_snapshot(root)
    goal = snapshot["goal"]
    version = snapshot["version"]
    runtime_status_payload = read_json(paths.runtime_status_path, fallback={})
    goal_preflight = dict(runtime_status_payload.get("goal_preflight") or {})
    current_generation = int(version.get("active_generation", 1))
    append_loop_log(root, f"run-loop started loop_run_id={loop_run_id} model={model}")
    update_runtime_status(
        root,
        status="running",
        active_loop_run_id=loop_run_id,
        current_candidate_id=None,
        current_task_stack=None,
        current_runtime_kernel=None,
        current_action=None,
        current_action_step=None,
        phase="planning",
        phase_started_at=now_iso(),
        current_stream_path=None,
        model=model,
        thinking_model=thinking_model,
        coding_model=coding_model,
        exploratory_coding_model=exploratory_coding_model,
        stagnation_coding_model=stagnation_coding_model,
        last_loop_started_at=now_iso(),
        last_error=None,
        last_event="loop_started",
        worker_heartbeat_at=now_iso(),
    )
    notify_dashboard(root)

    queue_item = dequeue_queue_item(root)
    if goal.get("status") == "paused" and queue_item is None:
        update_runtime_status(
            root,
            status="idle",
            active_loop_run_id=None,
            current_candidate_id=None,
            current_task_stack=None,
            current_runtime_kernel=None,
            current_action=None,
            current_action_step=None,
            phase=None,
            phase_started_at=None,
            current_stream_path=None,
            last_loop_finished_at=now_iso(),
            last_event="goal_paused",
            worker_heartbeat_at=now_iso(),
        )
        notify_dashboard(root)
        return {
            "loop_run_id": loop_run_id,
            "status": "goal_paused",
            "generation": current_generation,
            "goal_status": "paused",
            "iterations": [],
        }

    if goal_preflight and not bool(goal_preflight.get("ok")):
        message = "; ".join(goal_preflight.get("errors") or []) or "goal preflight failed"
        update_runtime_status(
            root,
            status="blocked",
            active_loop_run_id=None,
            current_candidate_id=None,
            current_task_stack=None,
            current_runtime_kernel=None,
            current_action=None,
            current_action_step=None,
            phase="blocked",
            phase_started_at=now_iso(),
            current_stream_path=None,
            last_loop_finished_at=now_iso(),
            last_error=message,
            last_event="goal_preflight_blocked",
            worker_heartbeat_at=now_iso(),
        )
        notify_dashboard(root)
        return {
            "loop_run_id": loop_run_id,
            "status": "blocked",
            "generation": current_generation,
            "goal_status": "blocked",
            "reason": message,
            "iterations": [],
        }

    resumable_started_attempt = _latest_resumable_started_attempt(
        root,
        goal_id=str(goal.get("goal_id") or ""),
    )
    closed_stale_attempts = _close_stale_started_attempts(
        root,
        preserve_candidate_id=str((resumable_started_attempt or {}).get("candidate_id") or "") or None,
    )
    if closed_stale_attempts:
        append_loop_log(root, f"recovered stale started attempts={','.join(closed_stale_attempts)}")
        notify_dashboard(root)

    previous_failure_summary = _load_latest_failed_summary(root)
    iterations: list[dict[str, Any]] = []
    latest_candidate_id: str | None = None
    try:
        for _ in range(max(1, max_iterations)):
            goal = read_json(paths.goal_path)
            version = read_json(paths.version_path)
            sync_self_model_payload(root)
            self_model = read_json(paths.self_model_path)
            runtime_kernel = str(self_model.get("runtime_kernel") or "legacy_phase_loop_v1").strip() or "legacy_phase_loop_v1"
            current_generation = int(version.get("active_generation", 1))
            active_path = Path(version["active_path"])
            resumable_attempt = (
                _latest_resumable_started_attempt(root, goal_id=str(goal.get("goal_id") or ""))
                if runtime_kernel == "session_action_loop_v1"
                and not bool(read_json(paths.runtime_status_path, fallback={}).get("goal_reset_pending"))
                else None
            )
            current_queue_item = queue_item if queue_item is not None else (
                None if resumable_attempt is not None else dequeue_queue_item(root)
            )
            queue_item = None
            command = list(self_model.get("default_validation_command", []))
            if not command:
                raise ValueError("default_validation_command is missing")
            resuming_attempt = resumable_attempt is not None
            if resuming_attempt:
                attempt_report = json.loads(json.dumps(resumable_attempt, ensure_ascii=False))
                candidate_id = str(attempt_report.get("candidate_id") or "").strip()
                target_file = str(attempt_report.get("target_file") or _select_target_file(active_path, self_model)).strip()
                latest_candidate_id = candidate_id
                candidate_generation = int(attempt_report.get("candidate_generation", current_generation + 1) or (current_generation + 1))
                candidate_path = paths.runtime_candidates_dir / candidate_id
                parent_generation = int(attempt_report.get("parent_generation", current_generation) or current_generation)
                base_active_path = paths.runtime_versions_dir / f"v{parent_generation:04d}"
                target_active_path = base_active_path / target_file
                if not target_active_path.exists():
                    target_active_path = active_path / target_file
                target_candidate_path = candidate_path / target_file
                before_text = target_active_path.read_text(encoding="utf-8")
                goal["last_attempt_at"] = now_iso()
                write_json(paths.goal_path, goal)
                attempt_report["loop_run_id"] = loop_run_id
                attempt_report["resumed_at"] = now_iso()
                attempt_report["resume_count"] = int(attempt_report.get("resume_count", 0) or 0) + 1
                attempt_report["meta_diagnosis"] = _summarize_meta_diagnosis(root)
                attempt_report["search_mode"] = str(
                    attempt_report.get("search_mode")
                    or (attempt_report["meta_diagnosis"].get("search_mode") if isinstance(attempt_report.get("meta_diagnosis"), dict) else "")
                    or "direct_improvement"
                )
                write_json(paths.attempt_report_path(candidate_id), attempt_report)
                update_runtime_status(
                    root,
                    status="running",
                    current_candidate_id=candidate_id,
                    current_task_stack=json.loads(json.dumps(attempt_report.get("task_stack") or [], ensure_ascii=False)) or None,
                    current_runtime_kernel=runtime_kernel,
                    current_action=None,
                    current_action_step=None,
                    worker_heartbeat_at=now_iso(),
                    last_event="attempt_resumed",
                )
                append_history(
                    root,
                    history_event(
                        step="attempt_resumed",
                        outcome="running",
                        message=f"{candidate_id} を途中状態から再開",
                        goal_id=goal.get("goal_id"),
                        generation=current_generation,
                        candidate_id=candidate_id,
                        loop_run_id=loop_run_id,
                    ),
                )
                append_loop_log(root, f"resuming started attempt candidate={candidate_id}")
            else:
                target_file = _select_target_file(active_path, self_model)
                candidate_id = _next_candidate_id(version)
                latest_candidate_id = candidate_id
                candidate_generation = current_generation + 1
                candidate_path = paths.runtime_candidates_dir / candidate_id
                version["last_candidate_id"] = candidate_id
                version["updated_at"] = now_iso()
                write_json(paths.version_path, version)

                update_runtime_status(
                    root,
                    status="running",
                    current_candidate_id=candidate_id,
                    current_task_stack=None,
                    current_runtime_kernel=runtime_kernel,
                    current_action=None,
                    current_action_step=None,
                    worker_heartbeat_at=now_iso(),
                    last_event="candidate_started",
                )
                copytree_clean(active_path, candidate_path)
                target_active_path = active_path / target_file
                target_candidate_path = candidate_path / target_file
                before_text = target_active_path.read_text(encoding="utf-8")
                goal["last_attempt_at"] = now_iso()
                write_json(paths.goal_path, goal)

                attempt_report = _build_attempt_report(
                    candidate_id=candidate_id,
                    loop_run_id=loop_run_id,
                    parent_generation=current_generation,
                    candidate_generation=candidate_generation,
                    target_file=target_file,
                    clone_reason=_clone_reason(
                        parent_generation=current_generation,
                        candidate_id=candidate_id,
                        target_file=target_file,
                    ),
                    purpose=current_queue_item.get("goal_text")
                    if isinstance(current_queue_item, dict) and current_queue_item.get("goal_text")
                    else goal.get("text"),
                    runtime_kernel=runtime_kernel,
                    meta_diagnosis=_summarize_meta_diagnosis(root),
                    created_at=now_iso(),
                    paths=paths,
                    goal_id=str(goal.get("goal_id") or ""),
                )
                attempt_report["search_mode"] = str(attempt_report["meta_diagnosis"].get("search_mode") or "direct_improvement")
            selected_coding_model = _choose_coding_model(
                meta_diagnosis=attempt_report.get("meta_diagnosis") or {},
                default_model=coding_model,
                exploratory_model=exploratory_coding_model,
                stagnation_model=stagnation_coding_model,
            )
            selected_coding_model = str(attempt_report.get("selected_coding_model") or selected_coding_model)
            if selected_coding_model == stagnation_coding_model:
                selected_coding_backend = stagnation_coding_backend
            elif selected_coding_model == exploratory_coding_model:
                selected_coding_backend = exploratory_coding_backend
            else:
                selected_coding_backend = default_coding_backend
            attempt_report["delta_context"] = attempt_report.get("delta_context") or _build_delta_context(
                root,
                goal_id=str(goal.get("goal_id") or ""),
            )
            attempt_report["reference_index"] = attempt_report.get("reference_index") or _build_reference_index(
                root,
                target_file=target_file,
                active_path=active_path,
                goal_id=str(goal.get("goal_id") or ""),
            )
            attempt_report["thinking_model"] = thinking_model
            attempt_report["coding_model_default"] = coding_model
            attempt_report["coding_model_exploratory"] = exploratory_coding_model
            attempt_report["coding_model_stagnation"] = stagnation_coding_model
            attempt_report["selected_coding_model"] = selected_coding_model
            attempt_report["require_initial_observation"] = bool(
                read_json(paths.runtime_status_path, fallback={}).get("goal_reset_pending")
            )
            if not resuming_attempt:
                paths.stream_log_path(candidate_id).write_text("", encoding="utf-8")
                paths.session_events_path(candidate_id).write_text("", encoding="utf-8")
                paths.prompt_snapshots_path(candidate_id).write_text("", encoding="utf-8")
            write_json(paths.attempt_report_path(candidate_id), attempt_report)
            _append_lifecycle_session_event(
                root,
                candidate_id=candidate_id,
                action="attempt_resumed" if resuming_attempt else "attempt_started",
                thinking=(
                    "既存 attempt を再開し、前回の状態から続きを実行します。"
                    if resuming_attempt
                    else "新しい attempt を開始し、目的達成に向けた最初の action へ進みます。"
                ),
                result={
                    "ok": True,
                    "runtime_kernel": runtime_kernel,
                    "search_mode": str(attempt_report.get("search_mode") or ""),
                    "resuming": bool(resuming_attempt),
                },
                task_frame=attempt_report.get("task_frame") if isinstance(attempt_report.get("task_frame"), dict) else None,
            )
            if not resuming_attempt:
                append_history(
                    root,
                    history_event(
                        step="attempt_started",
                        outcome="running",
                        message=(
                            f"{RUNTIME_NAMES['candidate_version']['formal_name']} {candidate_id} を "
                            f"世代 {current_generation} の{RUNTIME_NAMES['active_version']['formal_name']}から分離して開始 "
                            f"search_mode={attempt_report['search_mode']}"
                        ),
                        goal_id=goal.get("goal_id"),
                        generation=current_generation,
                        candidate_id=candidate_id,
                        loop_run_id=loop_run_id,
                    ),
                )
            if attempt_report["search_mode"] != "direct_improvement":
                append_loop_log(root, f"search_mode escalated candidate={candidate_id} mode={attempt_report['search_mode']}")
            notify_dashboard(root)

            last_notify_at = 0.0

            def _append_stream(phase: str, chunk: str) -> None:
                nonlocal last_notify_at
                last_notify_at = _append_stream_chunk(
                    root=root,
                    stream_path=paths.stream_log_path(candidate_id),
                    candidate_id=candidate_id,
                    phase=phase,
                    chunk=chunk,
                    emit_chunk=_emit_model_chunk,
                    last_notify_at=last_notify_at,
                )

            def _run_streamed_step(
                *,
                phase: str,
                system_prompt: str,
                user_prompt: str,
                banner: str,
                phase_backend: ModelBackend,
                phase_model_name: str,
                step: int | None = None,
                frame_id: str | None = None,
                frame_depth: int | None = None,
                prompt_context: dict[str, Any] | None = None,
            ) -> str:
                return _run_streamed_step_impl(
                    root=root,
                    phase=phase,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    banner=banner,
                    phase_backend=phase_backend,
                    phase_model_name=phase_model_name,
                    step=step,
                    frame_id=frame_id,
                    frame_depth=frame_depth,
                    prompt_context=prompt_context,
                    candidate_id=candidate_id,
                    target_file=target_file,
                    loop_run_id=loop_run_id,
                    runtime_kernel=runtime_kernel,
                    thinking_model=thinking_model,
                    coding_model=coding_model,
                    exploratory_coding_model=exploratory_coding_model,
                    stagnation_coding_model=stagnation_coding_model,
                    prompt_snapshots_path=paths.prompt_snapshots_path(candidate_id),
                    stream_path=paths.stream_log_path(candidate_id),
                    phase_label=_phase_label(phase),
                    append_stream_chunk=lambda chunk: _append_stream(phase, chunk),
                    llm_timings=attempt_report["llm_timings"],
                    persist_attempt_report=lambda: write_json(paths.attempt_report_path(candidate_id), attempt_report),
                )

            frame_counter = 0
            max_frame_depth = int(self_model.get("max_frame_depth", 3) or 3)
            max_action_steps = max(1, int(self_model.get("max_action_steps", 8) or 8))

            def _persist_attempt_runtime(*, last_event: str | None = None, phase: str | None = None) -> None:
                _persist_attempt_runtime_state(
                    root=root,
                    attempt_report_path=paths.attempt_report_path(candidate_id),
                    attempt_report=attempt_report,
                    candidate_id=candidate_id,
                    runtime_kernel=runtime_kernel,
                    last_event=last_event,
                    phase=phase,
                )

            def _set_current_frame(frame: dict[str, Any], *, last_event: str) -> None:
                _set_current_frame_state(attempt_report, frame)
                _persist_attempt_runtime(last_event=last_event)

            def _close_current_frame(
                *,
                frame: dict[str, Any],
                status: str,
                summary: str,
                decision: str,
                reason: str,
                next_goal: str,
            ) -> dict[str, Any]:
                closed = _update_task_frame_outcome(
                    frame,
                    status=status,
                    summary=summary,
                    decision=decision,
                    reason=reason,
                    next_goal=next_goal,
                )
                _apply_closed_frame_state(attempt_report, closed)
                _persist_attempt_runtime(last_event="task_frame_closed")
                return closed

            def _child_goals_from_payload(
                payload: dict[str, Any] | None,
                *,
                fallback_goal: str,
            ) -> list[str]:
                source = payload if isinstance(payload, dict) else {}
                normalized: list[str] = []
                next_goal = _safe_brief_text(source.get("next_goal") or "", max_chars=240)
                if next_goal:
                    normalized.append(next_goal)
                raw_child_goals = source.get("child_goals")
                if isinstance(raw_child_goals, list):
                    for item in raw_child_goals:
                        goal_text = _safe_brief_text(item or "", max_chars=240)
                        if goal_text and goal_text not in normalized:
                            normalized.append(goal_text)
                if not normalized:
                    fallback = _safe_brief_text(fallback_goal or "", max_chars=240)
                    if fallback:
                        normalized.append(fallback)
                return normalized[:6]

            def _execute_session_frame(
                *,
                current_text: str,
                working_text_seed: str | None = None,
                frame_goal: str,
                frame_delta_context: dict[str, Any],
                parent_frame: dict[str, Any] | None = None,
                resumed_frame: dict[str, Any] | None = None,
            ) -> dict[str, Any]:
                nonlocal frame_counter
                resume_mode = isinstance(resumed_frame, dict) and bool(resumed_frame)
                if resume_mode:
                    frame = json.loads(json.dumps(resumed_frame, ensure_ascii=False))
                    depth = int(frame.get("depth", 0) or 0)
                else:
                    frame_counter += 1
                    depth = int(parent_frame.get("depth", 0) or 0) + 1 if isinstance(parent_frame, dict) else 0
                frame_affordances = _frame_affordances(
                    depth=depth,
                    max_depth=max_frame_depth,
                    parent_frame=parent_frame,
                )
                system_capabilities = _system_capabilities(
                    depth=depth,
                    max_depth=max_frame_depth,
                    parent_frame=parent_frame,
                    reference_index=[],
                )
                selected_context = {
                    "question_to_answer": "次に進むために、どの小さな action を 1 つ実行するべきか。",
                    "selected_context": [],
                    "commitment": "小さな action とその結果を見て次を決める。",
                }
                resolved_context = {
                    "runtime_kernel": runtime_kernel,
                    "session_events_path": str(paths.session_events_path(candidate_id)),
                    "tool_style": "session_action_result_loop",
                }
                if not resume_mode:
                    frame = _build_task_frame(
                        candidate_id=candidate_id,
                        goal=goal,
                        purpose=frame_goal,
                        target_file=target_file,
                        search_mode=attempt_report["search_mode"],
                        selected_context=selected_context,
                        resolved_context=resolved_context,
                        delta_context=frame_delta_context,
                        frame_affordances=frame_affordances,
                        system_capabilities=system_capabilities,
                        inherited_context=_build_inherited_context(parent_frame),
                        parent_frame=parent_frame,
                        frame_index=frame_counter,
                    )
                else:
                    selected_context = attempt_report.get("selected_context") or selected_context
                    resolved_context = attempt_report.get("resolved_context") or resolved_context
                attempt_report["selected_context"] = selected_context
                attempt_report["resolved_context"] = resolved_context
                attempt_report["delta_context"] = frame_delta_context
                attempt_report["frame_affordances"] = frame_affordances
                attempt_report["system_capabilities"] = system_capabilities
                if not resume_mode:
                    attempt_report["task_stack"].append(frame)
                    attempt_report["task_frame"] = frame
                    _persist_attempt_runtime(last_event="task_frame_opened", phase="acting")
                else:
                    if not attempt_report.get("task_stack"):
                        attempt_report["task_stack"] = [frame]
                    attempt_report["task_frame"] = frame
                    _persist_attempt_runtime(last_event="task_frame_resumed", phase="acting")

                working_text = working_text_seed if isinstance(working_text_seed, str) else current_text
                session_events_at_resume = read_jsonl_rows(paths.session_events_path(candidate_id))
                last_validation_report: dict[str, Any] | None = read_validation_report(root, candidate_id)
                local_delta_context = frame_delta_context
                target_changed_in_frame = working_text != current_text
                last_validation_step = 0
                last_apply_patch_step = 0
                applied_patch_count = 0
                successful_validation_count = 0
                resume_step = 0
                frame_id = str(frame.get("frame_id") or "")
                for event in session_events_at_resume:
                    if str(event.get("frame_id") or "") != frame_id:
                        continue
                    try:
                        event_step = int(event.get("step", 0) or 0)
                    except (TypeError, ValueError):
                        event_step = 0
                    resume_step = max(resume_step, event_step)
                    action_name = str(event.get("action") or "")
                    result_payload = event.get("result") or {}
                    if action_name == "apply_patch" and isinstance(result_payload, dict) and result_payload.get("ok"):
                        applied_patch_count += 1
                        last_apply_patch_step = max(last_apply_patch_step, event_step)
                    if action_name == "run_validation" and isinstance(result_payload, dict) and result_payload.get("passed"):
                        successful_validation_count += 1
                        last_validation_step = max(last_validation_step, event_step)
                dirty_since_validation = target_changed_in_frame and last_apply_patch_step > last_validation_step
                no_progress_streak = 0
                repeated_marker_streak = 0
                last_stagnation_marker = ""
                require_initial_observation = bool(attempt_report.get("require_initial_observation"))
                last_failed_validation_diff_sha: str | None = None
                last_failed_validation_patch_count: int | None = None

                def _record_event(step: int, action: str, action_input: dict[str, Any], thinking: str, result: dict[str, Any]) -> None:
                    _record_session_event(
                        timestamp=now_iso(),
                        frame=frame,
                        frame_depth=depth,
                        step=step,
                        action=action,
                        action_input=action_input,
                        thinking=thinking,
                        result=result,
                        append_event=lambda event: _append_session_event(root, candidate_id, event),
                        update_frame_working_memory=lambda target_frame, a, a_input, r: _update_frame_working_memory(
                            target_frame,
                            action=a,
                            action_input=a_input,
                            result=r,
                        ),
                        attempt_report=attempt_report,
                    )
                    _persist_attempt_runtime(last_event="session_event_recorded", phase="acting")

                def _append_raw_output(step: int, raw_text: str) -> None:
                    _append_action_raw_output(
                        raw_model_output_path=Path(attempt_report["raw_model_output_path"]),
                        step=step,
                        raw_text=raw_text,
                    )

                def _update_stagnation_state(marker: str, *, made_progress: bool) -> None:
                    nonlocal no_progress_streak, repeated_marker_streak, last_stagnation_marker
                    (
                        no_progress_streak,
                        repeated_marker_streak,
                        last_stagnation_marker,
                    ) = _advance_stagnation_state(
                        marker=marker,
                        made_progress=made_progress,
                        no_progress_streak=no_progress_streak,
                        repeated_marker_streak=repeated_marker_streak,
                        last_stagnation_marker=last_stagnation_marker,
                    )

                def _check_stagnation() -> dict[str, Any] | None:
                    if not _is_stagnation_threshold_exceeded(
                        repeated_marker_streak=repeated_marker_streak,
                        no_progress_streak=no_progress_streak,
                    ):
                        return None
                    attempt_report["status"] = "rejected"
                    attempt_report["decision_reason"] = "session action loop stagnated without material progress"
                    _close_current_frame(
                        frame=frame,
                        status="rejected",
                        summary="同じ種類の行動や前進しない試行が続いたため、このフレームを打ち切ります。",
                        decision="return_to_parent",
                        reason="観測や変更が停滞し、この階層では新しい前進が生まれていません。",
                        next_goal="同型反復を止める別の仮説や切り分けに切り替える",
                    )
                    return {
                        "status": "rejected",
                        "reason": attempt_report["decision_reason"],
                        "outcome": "stagnated",
                        "validation_report": last_validation_report,
                    }

                def _finalize_action_step(
                    *,
                    step: int,
                    action: str,
                    action_input: dict[str, Any],
                    thinking: str,
                    result: dict[str, Any],
                    made_progress: bool,
                    marker: str | None = None,
                    runtime_event: str = "action_completed",
                    persist_event: str | None = None,
                    check_stagnation: bool = True,
                ) -> dict[str, Any] | None:
                    _record_event(step, action, action_input, thinking, result)
                    _update_stagnation_state(
                        marker or _stagnation_event_marker(action, action_input, result),
                        made_progress=made_progress,
                    )
                    if persist_event:
                        _persist_attempt_runtime(last_event=persist_event, phase="acting")
                    _mark_action_runtime_event(
                        root=root,
                        candidate_id=candidate_id,
                        runtime_kernel=runtime_kernel,
                        action=action,
                        step=step,
                        last_event=runtime_event,
                    )
                    if not check_stagnation:
                        return None
                    return _check_stagnation()

                def _handle_open_child_frame(
                    *,
                    step: int,
                    action: str,
                    action_input: dict[str, Any],
                    thinking: str,
                    frame_goal_value: str,
                    session_events: list[dict[str, Any]],
                ) -> None:
                    nonlocal frame
                    nonlocal working_text
                    nonlocal target_changed_in_frame
                    nonlocal last_validation_report
                    nonlocal successful_validation_count
                    nonlocal dirty_since_validation
                    nonlocal local_delta_context

                    reason = _safe_brief_text(action_input.get("reason") or "下位問題へ分解します。", max_chars=240)
                    child_goals = _child_goals_from_payload(
                        action_input,
                        fallback_goal=frame_goal_value,
                    )
                    next_goal = child_goals[0]
                    local_diff_text = _render_target_diff(
                        before_text=current_text,
                        after_text=working_text,
                        target_file=target_file,
                    )
                    delegate_delta = local_delta_context
                    if not delegate_delta or not delegate_delta.get("latest_failure"):
                        delegate_delta = _build_frame_delta_context(
                            root=root,
                            attempt=attempt_report,
                            previous_delta_context=local_delta_context,
                            before_text=current_text,
                            after_text=working_text,
                            diff_text=local_diff_text,
                            detail={
                                "summary": reason,
                                "error_type": "ChildFrameRequest",
                                "file": str(target_candidate_path),
                                "line": None,
                                "detail": " / ".join(child_goals),
                            },
                            report=last_validation_report,
                        )
                    _finalize_action_step(
                        step=step,
                        action=action,
                        action_input=action_input,
                        thinking=thinking,
                        result={
                            "ok": True,
                            "delegated": True,
                            "next_goal": next_goal,
                            "child_goals": child_goals,
                            "reason": reason,
                        },
                        made_progress=True,
                        marker="open_child_frame",
                        check_stagnation=False,
                    )
                    frame_after_delegate = _update_task_frame_outcome(
                        frame,
                        status="delegating",
                        summary="この階層の goal をより局所化するため、下位フレームへ委譲します。",
                        decision="open_child_frame",
                        reason=reason,
                        next_goal=next_goal,
                    )
                    _set_current_frame(frame_after_delegate, last_event="task_frame_recursing")
                    received_validated_material = False
                    child_count = len(child_goals)
                    for index, child_goal in enumerate(child_goals, start=1):
                        append_history(
                            root,
                            history_event(
                                step="task_frame_recursing",
                                outcome="delegated",
                                message=f"{frame_after_delegate.get('frame_id')} -> child goal[{index}/{child_count}]: {child_goal}",
                                goal_id=goal.get("goal_id"),
                                generation=current_generation,
                                candidate_id=candidate_id,
                                loop_run_id=loop_run_id,
                            ),
                        )
                        child_result = _execute_session_frame(
                            current_text=working_text,
                            frame_goal=child_goal,
                            frame_delta_context=local_delta_context,
                            parent_frame=frame_after_delegate,
                        )
                        current_parent_frame = attempt_report.get("task_frame")
                        if not isinstance(current_parent_frame, dict):
                            current_parent_frame = frame_after_delegate
                        frame = json.loads(json.dumps(current_parent_frame, ensure_ascii=False))
                        return_payload = child_result.get("return_payload")
                        if isinstance(return_payload, dict) and return_payload:
                            frame = _merge_child_return_into_parent(frame, return_payload)
                        child_validation = child_result.get("validation_report")
                        child_has_validated_material = bool(
                            isinstance(child_validation, dict)
                            and child_validation.get("passed")
                            and isinstance(child_result.get("text"), str)
                        )
                        if child_has_validated_material:
                            received_validated_material = True
                            working_text = str(child_result.get("text") or working_text)
                            target_changed_in_frame = working_text != current_text
                            last_validation_report = child_validation
                            successful_validation_count = max(successful_validation_count, 1)
                            dirty_since_validation = False
                        if isinstance(child_result.get("delta_context"), dict) and child_result.get("delta_context"):
                            local_delta_context = dict(child_result["delta_context"])
                            attempt_report["delta_context"] = local_delta_context
                        _set_current_frame(frame, last_event="task_frame_child_returned")
                        append_history(
                            root,
                            history_event(
                                step="task_frame_child_returned",
                                outcome=child_result.get("status") or "unknown",
                                message=f"{frame.get('frame_id')} <= {((return_payload or {}).get('frame_id')) or 'child'} [{index}/{child_count}]",
                                goal_id=goal.get("goal_id"),
                                generation=current_generation,
                                candidate_id=candidate_id,
                                loop_run_id=loop_run_id,
                            ),
                        )
                    frame = _update_task_frame_outcome(
                        frame,
                        status="active",
                        summary=(
                            "子フレーム群の結果を統合し、この階層で次を判断します。"
                            if received_validated_material
                            else "子フレーム群から返却を受け、この階層で判断を継続します。"
                        ),
                        decision="continue_here",
                        reason=(
                            "child_goals を順次実行し、前進材料を統合しました。"
                            if received_validated_material
                            else "child_goals を順次実行し、返却材料を統合しました。"
                        ),
                        next_goal=frame_goal_value,
                    )
                    _set_current_frame(frame, last_event="task_frame_updated")

                def _handle_return_to_parent(
                    *,
                    step: int,
                    action: str,
                    action_input: dict[str, Any],
                    thinking: str,
                    frame_goal_value: str,
                    session_events: list[dict[str, Any]],
                ) -> dict[str, Any]:
                    reason = _safe_brief_text(action_input.get("reason") or "このフレームでは前進しません。", max_chars=240)
                    next_goal = _safe_brief_text(action_input.get("next_goal") or frame_goal_value, max_chars=240)
                    return_payload = _build_return_payload_from_action_input(
                        frame=frame,
                        action_input=action_input,
                        session_events=session_events,
                        default_summary=reason,
                    )
                    frame.setdefault("context", {})["return_payload"] = json.loads(
                        json.dumps(return_payload, ensure_ascii=False)
                    )
                    _finalize_action_step(
                        step=step,
                        action=action,
                        action_input=action_input,
                        thinking=thinking,
                        result={
                            "ok": True,
                            "returned": True,
                            "reason": reason,
                            "next_goal": next_goal,
                            "return_payload": return_payload,
                        },
                        made_progress=True,
                        marker="return_to_parent",
                        check_stagnation=False,
                    )
                    _close_current_frame(
                        frame=frame,
                        status="returned",
                        summary="このフレームを閉じて親へ戻ります。",
                        decision="return_to_parent",
                        reason=reason,
                        next_goal=next_goal,
                    )
                    return {
                        "status": "returned",
                        "reason": reason,
                        "outcome": "return_to_parent",
                        "validation_report": last_validation_report,
                        "return_payload": return_payload,
                        "delta_context": local_delta_context,
                        "text": working_text,
                    }

                def _handle_finish_action(
                    *,
                    step: int,
                    action: str,
                    action_input: dict[str, Any],
                    thinking: str,
                    frame_completion_decision: str,
                    frame_completion_next_goal: str,
                    session_events: list[dict[str, Any]],
                ) -> dict[str, Any] | None:
                    if not last_validation_report or not last_validation_report.get("passed"):
                        result = {
                            "ok": False,
                            "error": "finish_requires_successful_validation",
                            "message": "finish の前に成功した run_validation が必要です。",
                        }
                        stagnation_result = _finalize_action_step(
                            step=step,
                            action=action,
                            action_input=action_input,
                            thinking=thinking,
                            result=result,
                            made_progress=False,
                        )
                        return stagnation_result

                    local_diff_text = _render_target_diff(
                        before_text=current_text,
                        after_text=working_text,
                        target_file=target_file,
                    )
                    if not local_diff_text.strip():
                        attempt_report["decision_reason"] = "candidate did not change the target file"
                        attempt_report["status"] = "rejected"
                        _record_event(
                            step,
                            action,
                            action_input,
                            thinking,
                            {"ok": False, "error": "no_change"},
                        )
                        _update_stagnation_state(
                            _stagnation_event_marker(action, action_input, {"ok": False, "error": "no_change"}),
                            made_progress=False,
                        )
                        _close_current_frame(
                            frame=frame,
                            status="rejected",
                            summary="差分が存在しなかったため、この階層では前進できませんでした。",
                            decision="return_to_parent",
                            reason="説明は変わったが行動が変わっていません。",
                            next_goal="分解や対象の切り方を上位で見直す",
                        )
                        return {
                            "status": "rejected",
                            "reason": attempt_report["decision_reason"],
                            "outcome": "no_change",
                            "validation_report": last_validation_report,
                        }

                    finish_payload = _normalize_finish_payload(parsed_action["raw_payload"], current_text=working_text)
                    return_payload = _build_return_payload_from_action_input(
                        frame=frame,
                        action_input=action_input,
                        session_events=session_events,
                        default_summary=finish_payload["change_summary"],
                    )
                    frame.setdefault("context", {})["return_payload"] = json.loads(
                        json.dumps(return_payload, ensure_ascii=False)
                    )
                    attempt_report["reasoning_summary"] = finish_payload["reasoning_summary"]
                    attempt_report["situation_report"] = finish_payload["situation_report"]
                    attempt_report["post_edit_reflection"] = finish_payload["post_edit_reflection"]
                    attempt_report["self_memo"] = finish_payload["self_memo"]
                    attempt_report["continue_or_return"] = {
                        "decision": frame_completion_decision,
                        "reason": "成功した検証を得たため、このフレームの結果を親へ返せます。 "
                        if parent_frame is not None
                        else "成功した検証を得たため、このフレームを完了できます。",
                        "next_goal": frame_completion_next_goal,
                        "child_goals": [],
                    }
                    attempt_report["change_summary"] = _build_change_summary(
                        current_text,
                        working_text,
                        local_diff_text,
                        finish_payload["change_summary"],
                    )
                    _finalize_action_step(
                        step=step,
                        action=action,
                        action_input=action_input,
                        thinking=thinking,
                        result={
                            "ok": True,
                            "finished": True,
                            "change_summary": finish_payload["change_summary"],
                            "return_payload": return_payload,
                        },
                        made_progress=True,
                        marker="finish",
                        check_stagnation=False,
                    )
                    _close_current_frame(
                        frame=frame,
                        status="completed",
                        summary="この階層の変更は検証を通過しました。",
                        decision=frame_completion_decision,
                        reason="この階層の局所目標を満たし、親へ結果を返せます。"
                        if parent_frame is not None
                        else "この階層の局所目標を満たしました。",
                        next_goal=frame_completion_next_goal,
                    )
                    return {
                        "status": "validated",
                        "text": working_text,
                        "validation_report": last_validation_report,
                        "local_change_summary": finish_payload["change_summary"],
                        "return_payload": return_payload,
                        "delta_context": local_delta_context,
                    }

                step = resume_step
                while True:
                    step += 1
                    if step > max_action_steps:
                        attempt_report["status"] = "rejected"
                        attempt_report["decision_reason"] = f"session action loop exceeded max_action_steps={max_action_steps}"
                        _record_event(
                            step,
                            "step_limit",
                            {"max_action_steps": max_action_steps},
                            f"1 フレームあたりの上限 {max_action_steps} 手に達したため、この階層を打ち切ります。",
                            {"ok": False, "error": "max_action_steps_exceeded"},
                        )
                        _close_current_frame(
                            frame=frame,
                            status="rejected",
                            summary="許可された action 手数を使い切ったため、この階層を打ち切ります。",
                            decision="return_to_parent",
                            reason="手数制限内で前進がまとまらず、同じ階層での継続効率が低下しました。",
                            next_goal="より小さい局所ゴールへ分解するか、観測対象を絞り直す",
                        )
                        return {
                            "status": "rejected",
                            "reason": attempt_report["decision_reason"],
                            "outcome": "max_action_steps_exceeded",
                            "validation_report": last_validation_report,
                        }
                    session_events = read_jsonl_rows(paths.session_events_path(candidate_id))
                    frame_state = _build_frame_judgment_state(
                        applied_patch_count=applied_patch_count,
                        target_changed_in_frame=target_changed_in_frame,
                        dirty_since_validation=dirty_since_validation,
                        last_validation_report=last_validation_report,
                        successful_validation_count=successful_validation_count,
                        delta_context=local_delta_context,
                        has_parent_frame=parent_frame is not None,
                    )
                    system_prompt, user_prompt = _build_session_action_prompts(
                        goal=goal,
                        frame_goal=frame_goal,
                        frame=frame,
                        target_file=target_file,
                        current_content=working_text,
                        validation_command=command,
                        immutable_paths=list(self_model.get("immutable_paths", [])),
                        delta_context=local_delta_context,
                        task_stack_summary=_task_stack_summary(attempt_report.get("task_stack")),
                        session_events=session_events,
                        frame_state=frame_state,
                        frame_affordances=frame_affordances,
                        system_capabilities=system_capabilities,
                    )
                    raw_text = _run_streamed_step(
                        phase="acting",
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                        banner="action loop",
                        phase_backend=selected_coding_backend,
                        phase_model_name=selected_coding_model,
                        step=step,
                        frame_id=str(frame.get("frame_id") or ""),
                        frame_depth=depth,
                        prompt_context={
                            "goal": {"goal_id": goal.get("goal_id"), "text": goal.get("text")},
                            "frame_goal": frame_goal,
                            "target_file": target_file,
                            "validation_command": list(command),
                            "immutable_paths": list(self_model.get("immutable_paths", [])),
                            "task_stack_summary": _task_stack_summary(attempt_report.get("task_stack")),
                            "frame_state": frame_state,
                            "frame_context": frame.get("context") or {},
                            "frame_affordances": frame_affordances,
                            "system_capabilities": system_capabilities,
                            "delta_context": _delta_context_for_prompt(local_delta_context),
                            "current_content_sha256": _sha256(working_text),
                            "session_event_count": len(session_events),
                        },
                    )
                    _append_raw_output(step, raw_text)
                    parsed_action = _parse_session_action_response(
                        raw_text,
                        target_file=target_file,
                        parse_legacy=_parse_model_response,
                    )
                    if parsed_action["mode"] != "action":
                        proposed_action = str(parsed_action.get("action") or "")
                        proposed_input = dict(parsed_action.get("action_input") or {})
                        _mark_action_runtime_event(
                            root=root,
                            candidate_id=candidate_id,
                            runtime_kernel=runtime_kernel,
                            action="invalid_response",
                            step=step,
                            last_event="action_invalid",
                        )
                        stagnation_result = _finalize_action_step(
                            step=step,
                            action="invalid_response",
                            action_input={
                                "proposed_action": proposed_action,
                                "proposed_action_input": proposed_input,
                            },
                            thinking="session kernel では action JSON が必要です。",
                            result={
                                "ok": False,
                                "error": "legacy_or_invalid_payload",
                                "proposed_action": proposed_action,
                                "proposed_action_input": proposed_input,
                            },
                            made_progress=False,
                            runtime_event="action_invalid",
                        )
                        if stagnation_result is not None:
                            return stagnation_result
                        continue

                    proposed_action = str(parsed_action.get("action") or "")
                    if require_initial_observation and proposed_action not in {"read_file", "search_code"}:
                        _mark_action_runtime_event(
                            root=root,
                            candidate_id=candidate_id,
                            runtime_kernel=runtime_kernel,
                            action="invalid_response",
                            step=step,
                            last_event="action_invalid",
                        )
                        stagnation_result = _finalize_action_step(
                            step=step,
                            action="invalid_response",
                            action_input={
                                "proposed_action": proposed_action,
                                "proposed_action_input": dict(parsed_action.get("action_input") or {}),
                            },
                            thinking="goal 変更直後の初手は read_file または search_code です。",
                            result={
                                "ok": False,
                                "error": "goal_reset_requires_initial_observation",
                                "allowed_actions": ["read_file", "search_code"],
                                "proposed_action": proposed_action,
                            },
                            made_progress=False,
                            runtime_event="action_invalid",
                        )
                        if stagnation_result is not None:
                            return stagnation_result
                        continue

                    session_events = read_jsonl_rows(paths.session_events_path(candidate_id))
                    runtime_frame = _build_runtime_validation_frame(
                        frame=frame,
                        frame_goal=frame_goal,
                        parent_frame=parent_frame,
                        session_events=session_events,
                        last_validation_report=last_validation_report,
                    )
                    try:
                        parsed_action = _validate_session_action_with_runtime(
                            payload=parsed_action,
                            runtime_frame=runtime_frame,
                        )
                    except RuntimeValidationError as exc:
                        proposed_action = str(parsed_action.get("action") or "")
                        proposed_input = dict(parsed_action.get("action_input") or {})
                        _mark_action_runtime_event(
                            root=root,
                            candidate_id=candidate_id,
                            runtime_kernel=runtime_kernel,
                            action="invalid_response",
                            step=step,
                            last_event="action_invalid",
                        )
                        stagnation_result = _finalize_action_step(
                            step=step,
                            action="invalid_response",
                            action_input={
                                "proposed_action": proposed_action,
                                "proposed_action_input": proposed_input,
                            },
                            thinking=f"runtime validator rejected action: {exc}",
                            result={
                                "ok": False,
                                "error": "runtime_validation_failed",
                                "proposed_action": proposed_action,
                                "proposed_action_input": proposed_input,
                            },
                            made_progress=False,
                            runtime_event="action_invalid",
                        )
                        if stagnation_result is not None:
                            return stagnation_result
                        continue

                    action = str(parsed_action["action"])
                    action_input = dict(parsed_action.get("action_input") or {})
                    thinking = str(parsed_action.get("thinking") or "")
                    _mark_action_runtime_event(
                        root=root,
                        candidate_id=candidate_id,
                        runtime_kernel=runtime_kernel,
                        action=action,
                        step=step,
                        last_event="action_selected",
                    )
                    notify_dashboard(root)

                    try:
                        frame_completion_decision = "return_to_parent" if parent_frame is not None else "continue_here"
                        frame_completion_next_goal = "親フレームが次を判断する" if parent_frame is not None else "次の改善単位を選ぶ"

                        if action == "read_file":
                            read_path, relative_path = _resolve_read_path(
                                root=root,
                                candidate_path=candidate_path,
                                requested_path=str(action_input.get("path") or target_file),
                                target_file=target_file,
                            )
                            result = {
                                "ok": True,
                                "relative_path": relative_path,
                                **_read_file_slice(
                                    read_path,
                                    start_line=action_input.get("start_line"),
                                    end_line=action_input.get("end_line"),
                                ),
                            }
                            stagnation_result = _finalize_action_step(
                                step=step,
                                action=action,
                                action_input=action_input,
                                thinking=thinking,
                                result=result,
                                made_progress=False,
                            )
                            if stagnation_result is not None:
                                return stagnation_result
                            if require_initial_observation and result.get("ok"):
                                require_initial_observation = False
                                attempt_report["require_initial_observation"] = False
                                update_runtime_status(
                                    root,
                                    goal_reset_pending=False,
                                    last_event="goal_reset_initial_observation_completed",
                                )
                            continue

                        if action == "search_code":
                            result = {
                                "ok": True,
                                **_search_workspace(candidate_path, str(action_input.get("pattern") or "")),
                            }
                            stagnation_result = _finalize_action_step(
                                step=step,
                                action=action,
                                action_input=action_input,
                                thinking=thinking,
                                result=result,
                                made_progress=False,
                            )
                            if stagnation_result is not None:
                                return stagnation_result
                            if require_initial_observation and result.get("ok"):
                                require_initial_observation = False
                                attempt_report["require_initial_observation"] = False
                                update_runtime_status(
                                    root,
                                    goal_reset_pending=False,
                                    last_event="goal_reset_initial_observation_completed",
                                )
                            continue

                        if action == "apply_patch":
                            requested_path = str(action_input.get("path") or target_file).strip() or target_file
                            edit_path = _resolve_candidate_path(candidate_path.resolve(), requested_path)
                            relative_path = str(edit_path.relative_to(candidate_path))
                            if _is_protected_path(relative_path, list(self_model.get("immutable_paths", []))):
                                raise ValueError(f"protected path: {relative_path}")
                            before_patch = edit_path.read_text(encoding="utf-8")
                            edits = action_input.get("edits")
                            if not isinstance(edits, list) or not edits:
                                raise ValueError("apply_patch requires a non-empty edits list")
                            updated_text, patch_result = _apply_structured_patch(
                                before_text=before_patch,
                                edits=[dict(edit) for edit in edits if isinstance(edit, dict)],
                            )
                            edit_path.write_text(updated_text, encoding="utf-8")
                            if relative_path == target_file:
                                working_text = updated_text
                                target_changed_in_frame = working_text != current_text
                                dirty_since_validation = True
                                applied_patch_count += 1
                            result = {
                                "ok": True,
                                "relative_path": relative_path,
                                **patch_result,
                            }
                            material_change = bool(
                                int(result.get("added_lines", 0) or 0) > 0
                                or int(result.get("removed_lines", 0) or 0) > 0
                            )
                            stagnation_result = _finalize_action_step(
                                step=step,
                                action=action,
                                action_input=action_input,
                                thinking=thinking,
                                result=result,
                                made_progress=material_change,
                            )
                            if stagnation_result is not None:
                                return stagnation_result
                            continue

                        if action == "run_validation":
                            local_diff_text = _render_target_diff(
                                before_text=current_text,
                                after_text=working_text,
                                target_file=target_file,
                            )
                            local_diff_sha = _sha256(local_diff_text)
                            if (
                                last_validation_report
                                and not last_validation_report.get("passed")
                                and last_failed_validation_diff_sha
                                and last_failed_validation_patch_count is not None
                                and last_failed_validation_diff_sha == local_diff_sha
                                and applied_patch_count == last_failed_validation_patch_count
                            ):
                                result = {
                                    "ok": False,
                                    "passed": False,
                                    "error": "run_validation_repeated_without_diff_change",
                                    "message": "validation 失敗後に同一差分のまま run_validation は再実行できません。",
                                }
                                stagnation_result = _finalize_action_step(
                                    step=step,
                                    action=action,
                                    action_input=action_input,
                                    thinking=thinking,
                                    result=result,
                                    made_progress=False,
                                    persist_event="validation_rejected_no_diff_change",
                                )
                                if stagnation_result is not None:
                                    return stagnation_result
                                continue
                            last_validation_report = _run_validation(
                                root=root,
                                candidate_id=candidate_id,
                                command=command,
                                cwd=candidate_path,
                            )
                            if last_validation_report["passed"]:
                                successful_validation_count += 1
                                dirty_since_validation = False
                                local_delta_context = _clear_delta_context_after_success(local_delta_context)
                                attempt_report["delta_context"] = local_delta_context
                                last_failed_validation_diff_sha = None
                                last_failed_validation_patch_count = None
                                result = {
                                    "ok": True,
                                    "passed": True,
                                    "returncode": last_validation_report["returncode"],
                                    "duration_ms": last_validation_report["duration_ms"],
                                    "validated_work_unit_ready": bool(target_changed_in_frame),
                                }
                            else:
                                failure_detail = _extract_failure_detail(last_validation_report, target_file=target_file) or {
                                    "summary": "validation failed",
                                    "error_type": "ValidationError",
                                    "file": str(target_candidate_path),
                                    "line": None,
                                    "detail": "検証コマンドが失敗しました。",
                                }
                                local_delta_context = _build_frame_delta_context(
                                    root=root,
                                    attempt=attempt_report,
                                    previous_delta_context=local_delta_context,
                                    before_text=current_text,
                                    after_text=working_text,
                                    diff_text=local_diff_text,
                                    detail=failure_detail,
                                    report=last_validation_report,
                                )
                                attempt_report["delta_context"] = local_delta_context
                                last_failed_validation_diff_sha = local_diff_sha
                                last_failed_validation_patch_count = applied_patch_count
                                result = {
                                    "ok": False,
                                    "passed": False,
                                    "returncode": last_validation_report["returncode"],
                                    "duration_ms": last_validation_report["duration_ms"],
                                    "failure": failure_detail,
                                    "summary": _validation_failure_summary(last_validation_report),
                                    "repeated_pattern": local_delta_context.get("repeated_pattern"),
                                }
                            stagnation_result = _finalize_action_step(
                                step=step,
                                action=action,
                                action_input=action_input,
                                thinking=thinking,
                                result=result,
                                made_progress=bool(result.get("passed")),
                                persist_event="validation_finished",
                            )
                            if stagnation_result is not None:
                                return stagnation_result
                            continue

                        if action == "open_child_frame":
                            _handle_open_child_frame(
                                step=step,
                                action=action,
                                action_input=action_input,
                                thinking=thinking,
                                frame_goal_value=frame_goal,
                                session_events=session_events,
                            )
                            continue

                        if action == "return_to_parent":
                            return _handle_return_to_parent(
                                step=step,
                                action=action,
                                action_input=action_input,
                                thinking=thinking,
                                frame_goal_value=frame_goal,
                                session_events=session_events,
                            )

                        if action == "finish":
                            finish_result = _handle_finish_action(
                                step=step,
                                action=action,
                                action_input=action_input,
                                thinking=thinking,
                                frame_completion_decision=frame_completion_decision,
                                frame_completion_next_goal=frame_completion_next_goal,
                                session_events=session_events,
                            )
                            if finish_result is not None:
                                return finish_result
                            continue

                    except Exception as exc:
                        error_result = {"ok": False, "error": str(exc)}
                        stagnation_result = _finalize_action_step(
                            step=step,
                            action=action,
                            action_input=action_input,
                            thinking=thinking,
                            result=error_result,
                            made_progress=False,
                            runtime_event="action_failed",
                        )
                        if stagnation_result is not None:
                            return stagnation_result
                        continue

            def _execute_frame(
                *,
                current_text: str,
                working_text_seed: str | None = None,
                frame_goal: str,
                frame_delta_context: dict[str, Any],
                parent_frame: dict[str, Any] | None = None,
                resumed_frame: dict[str, Any] | None = None,
            ) -> dict[str, Any]:
                if runtime_kernel == "session_action_loop_v1":
                    return _execute_session_frame(
                        current_text=current_text,
                        working_text_seed=working_text_seed,
                        frame_goal=frame_goal,
                        frame_delta_context=frame_delta_context,
                        parent_frame=parent_frame,
                        resumed_frame=resumed_frame,
                    )
                nonlocal frame_counter
                frame_counter += 1
                depth = int(parent_frame.get("depth", 0) or 0) + 1 if isinstance(parent_frame, dict) else 0
                recent_attempt_bundle = _recent_attempt_observation_bundle(root, limit=5)
                local_reference_index = _build_reference_index(
                    root,
                    target_file=target_file,
                    active_path=active_path,
                    goal_id=str(goal.get("goal_id") or ""),
                )
                frame_affordances = _frame_affordances(
                    depth=depth,
                    max_depth=max_frame_depth,
                    parent_frame=parent_frame,
                )
                system_capabilities = _system_capabilities(
                    depth=depth,
                    max_depth=max_frame_depth,
                    parent_frame=parent_frame,
                    reference_index=local_reference_index,
                )
                selection_system_prompt, selection_user_prompt = _build_reference_selection_prompts(
                    goal=goal,
                    frame_goal=frame_goal,
                    candidate_id=candidate_id,
                    target_file=target_file,
                    search_mode=attempt_report["search_mode"],
                    reference_index=local_reference_index,
                    delta_context=frame_delta_context,
                    task_stack_summary=_task_stack_summary(attempt_report.get("task_stack")),
                    recent_attempt_bundle=recent_attempt_bundle,
                    frame_affordances=frame_affordances,
                    system_capabilities=system_capabilities,
                )
                selection_text = _run_streamed_step(
                    phase="context_selecting",
                    system_prompt=selection_system_prompt,
                    user_prompt=selection_user_prompt,
                    banner="参照要求",
                    phase_backend=thinking_backend,
                    phase_model_name=thinking_model,
                    prompt_context={
                        "goal": {"goal_id": goal.get("goal_id"), "text": goal.get("text")},
                        "frame_goal": frame_goal,
                        "target_file": target_file,
                        "search_mode": attempt_report["search_mode"],
                        "reference_index_size": len(local_reference_index),
                        "task_stack_summary": _task_stack_summary(attempt_report.get("task_stack")),
                        "delta_context": _delta_context_for_prompt(frame_delta_context),
                        "frame_affordances": frame_affordances,
                        "system_capabilities": system_capabilities,
                    },
                )
                allowed_ids = {str(entry.get("id")) for entry in local_reference_index if entry.get("id")}
                selected_context = _parse_reference_selection_response(selection_text, allowed_ids=allowed_ids)
                resolved_context = _resolve_selected_context(
                    root,
                    selected_context=selected_context["selected_context"],
                    target_file=target_file,
                    current_content=current_text,
                    active_path=active_path,
                    read_test_context=lambda path, max_chars: _read_test_context(path, max_chars=max_chars),
                )
                frame = _build_task_frame(
                    candidate_id=candidate_id,
                    goal=goal,
                    purpose=frame_goal,
                    target_file=target_file,
                    search_mode=attempt_report["search_mode"],
                    selected_context=selected_context,
                    resolved_context=resolved_context,
                    delta_context=frame_delta_context,
                    frame_affordances=frame_affordances,
                    system_capabilities=system_capabilities,
                    parent_frame=parent_frame,
                    frame_index=frame_counter,
                )
                attempt_report["reference_index"] = local_reference_index
                attempt_report["selected_context"] = selected_context
                attempt_report["resolved_context"] = resolved_context
                attempt_report["delta_context"] = frame_delta_context
                attempt_report["frame_affordances"] = frame_affordances
                attempt_report["system_capabilities"] = system_capabilities
                attempt_report["recent_attempt_bundle"] = recent_attempt_bundle
                attempt_report["task_stack"].append(frame)
                attempt_report["task_frame"] = frame
                _persist_attempt_runtime(last_event="task_frame_opened", phase="context_selecting")

                reflection_system_prompt, reflection_user_prompt = _build_reflection_prompts(
                    goal=goal,
                    frame_goal=frame_goal,
                    target_file=target_file,
                    parent_generation=current_generation,
                    candidate_id=candidate_id,
                    attempt_memory=_recent_attempt_memory(root),
                    meta_diagnosis=attempt_report["meta_diagnosis"],
                    search_mode=attempt_report["search_mode"],
                    previous_failure_summary=previous_failure_summary,
                    resolved_context=resolved_context,
                    delta_context=frame_delta_context,
                    task_stack_summary=_task_stack_summary(attempt_report.get("task_stack")),
                    recent_attempt_bundle=recent_attempt_bundle,
                    frame_affordances=frame_affordances,
                    system_capabilities=system_capabilities,
                )
                system_prompt, user_prompt = _build_prompts(
                    goal=goal,
                    frame_goal=frame_goal,
                    target_file=target_file,
                    current_content=current_text,
                    parent_generation=current_generation,
                    candidate_id=candidate_id,
                    validation_summary=previous_failure_summary,
                    attempt_memory=_recent_attempt_memory(root),
                    test_context=_read_test_context(active_path),
                    meta_diagnosis=attempt_report["meta_diagnosis"],
                    search_mode=attempt_report["search_mode"],
                    resolved_context=resolved_context,
                    delta_context=frame_delta_context,
                    task_stack_summary=_task_stack_summary(attempt_report.get("task_stack")),
                    recent_attempt_bundle=recent_attempt_bundle,
                    frame_affordances=frame_affordances,
                    system_capabilities=system_capabilities,
                )
                reflection_text = _run_streamed_step(
                    phase="reflecting",
                    system_prompt=reflection_system_prompt,
                    user_prompt=reflection_user_prompt,
                    banner="自己診断",
                    phase_backend=thinking_backend,
                    phase_model_name=thinking_model,
                    frame_id=str(frame.get("frame_id") or ""),
                    frame_depth=depth,
                    prompt_context={
                        "goal": {"goal_id": goal.get("goal_id"), "text": goal.get("text")},
                        "frame_goal": frame_goal,
                        "target_file": target_file,
                        "search_mode": attempt_report["search_mode"],
                        "selected_context": attempt_report.get("selected_context"),
                        "resolved_context_keys": sorted((attempt_report.get("resolved_context") or {}).keys()),
                        "task_stack_summary": _task_stack_summary(attempt_report.get("task_stack")),
                        "delta_context": _delta_context_for_prompt(frame_delta_context),
                        "frame_affordances": frame_affordances,
                        "system_capabilities": system_capabilities,
                        "previous_failure_summary": previous_failure_summary,
                    },
                )
                attempt_report["pre_edit_reflection"] = _parse_reflection_response(reflection_text)
                _persist_attempt_runtime(last_event="reflection_updated", phase="reflecting")

                augmented_user_prompt = (
                    f"直前の自己診断:\n{json.dumps(attempt_report['pre_edit_reflection'], ensure_ascii=False, indent=2)}\n\n"
                    "必須条件:\n"
                    "- 上の自己診断で述べた『what_must_be_different_this_time』を満たしてください。\n"
                    "- その条件を満たせない案は出さないでください。\n\n"
                    f"{user_prompt}"
                )
                raw_text = _run_streamed_step(
                    phase="generating",
                    system_prompt=system_prompt,
                    user_prompt=augmented_user_prompt,
                    banner="モデル出力",
                    phase_backend=selected_coding_backend,
                    phase_model_name=selected_coding_model,
                    frame_id=str(frame.get("frame_id") or ""),
                    frame_depth=depth,
                    prompt_context={
                        "goal": {"goal_id": goal.get("goal_id"), "text": goal.get("text")},
                        "frame_goal": frame_goal,
                        "target_file": target_file,
                        "search_mode": attempt_report["search_mode"],
                        "selected_context": attempt_report.get("selected_context"),
                        "resolved_context_keys": sorted((attempt_report.get("resolved_context") or {}).keys()),
                        "task_stack_summary": _task_stack_summary(attempt_report.get("task_stack")),
                        "delta_context": _delta_context_for_prompt(frame_delta_context),
                        "frame_affordances": frame_affordances,
                        "system_capabilities": system_capabilities,
                        "pre_edit_reflection": attempt_report.get("pre_edit_reflection"),
                        "current_content_sha256": _sha256(current_text),
                    },
                )
                paths.raw_model_output_path(candidate_id).write_text(raw_text, encoding="utf-8")
                parsed = _parse_model_response(raw_text)
                after_text = parsed["revised_file_content"]
                target_candidate_path.write_text(after_text, encoding="utf-8")
                local_diff_text = _render_target_diff(
                    before_text=current_text,
                    after_text=after_text,
                    target_file=target_file,
                )
                paths.diff_path(candidate_id).write_text(
                    local_diff_text + ("\n" if local_diff_text else ""),
                    encoding="utf-8",
                )
                attempt_report["reasoning_summary"] = parsed["reasoning_summary"]
                attempt_report["situation_report"] = parsed["situation_report"]
                attempt_report["post_edit_reflection"] = parsed["post_edit_reflection"]
                attempt_report["self_memo"] = parsed["self_memo"]
                attempt_report["continue_or_return"] = parsed["continue_or_return"]
                attempt_report["change_summary"] = _build_change_summary(
                    current_text,
                    after_text,
                    local_diff_text,
                    parsed["change_summary"],
                )
                frame = _update_task_frame_outcome(
                    frame,
                    status="candidate_generated",
                    summary="コード候補を生成し、検証前の状態に入りました。",
                    decision=parsed["continue_or_return"]["decision"],
                    reason=parsed["continue_or_return"]["reason"],
                    next_goal=parsed["continue_or_return"]["next_goal"],
                )
                _set_current_frame(frame, last_event="task_frame_updated")

                def _maybe_recurse(
                    *,
                    next_text: str,
                    local_delta_context: dict[str, Any],
                    allow_legacy_continue: bool = False,
                ) -> dict[str, Any] | None:
                    def _repeated_failure(error_type: str) -> bool:
                        recent = [
                            item
                            for item in list(local_delta_context.get("recent_failures") or [])
                            if isinstance(item, dict) and str(item.get("error_type") or "") == error_type
                        ]
                        return len(recent) >= 2

                    if depth >= max_frame_depth:
                        return None
                    if _repeated_failure("ChildFrameRequest") or _repeated_failure("NoChange"):
                        return None
                    if not parsed.get("continue_or_return_explicit"):
                        return None
                    decision = parsed["continue_or_return"]["decision"]
                    if decision == "open_child_frame":
                        should_recurse = True
                    elif (
                        allow_legacy_continue
                        and decision == "continue_here"
                        and (
                            parsed["continue_or_return"]["next_goal"]
                            or (parsed["continue_or_return"].get("child_goals") or [])
                        )
                    ):
                        should_recurse = True
                    else:
                        should_recurse = False
                    if not should_recurse:
                        return None
                    child_goals = _child_goals_from_payload(
                        parsed["continue_or_return"],
                        fallback_goal=selected_context.get("commitment") or frame_goal,
                    )
                    frame_after_delegate = _update_task_frame_outcome(
                        frame,
                        status="delegating",
                        summary="この階層の goal をより局所化するため、下位フレームへ委譲します。",
                        decision="open_child_frame",
                        reason=parsed["continue_or_return"]["reason"],
                        next_goal=child_goals[0],
                    )
                    _set_current_frame(frame_after_delegate, last_event="task_frame_recursing")
                    child_count = len(child_goals)
                    child_results: list[dict[str, Any]] = []
                    latest_validated_result: dict[str, Any] | None = None
                    for index, child_goal in enumerate(child_goals, start=1):
                        _set_current_frame(frame_after_delegate, last_event="task_frame_recursing")
                        append_history(
                            root,
                            history_event(
                                step="task_frame_recursing",
                                outcome="delegated",
                                message=f"{frame_after_delegate.get('frame_id')} -> child goal[{index}/{child_count}]: {child_goal}",
                                goal_id=goal.get("goal_id"),
                                generation=current_generation,
                                candidate_id=candidate_id,
                                loop_run_id=loop_run_id,
                            ),
                        )
                        child_result = _execute_frame(
                            current_text=next_text,
                            frame_goal=child_goal,
                            frame_delta_context=local_delta_context,
                            parent_frame=frame_after_delegate,
                        )
                        child_results.append(child_result)
                        if child_result.get("status") == "validated":
                            latest_validated_result = child_result
                            if isinstance(child_result.get("text"), str):
                                next_text = str(child_result["text"])
                        if isinstance(child_result.get("delta_context"), dict) and child_result.get("delta_context"):
                            local_delta_context = dict(child_result["delta_context"])
                        append_history(
                            root,
                            history_event(
                                step="task_frame_child_returned",
                                outcome=child_result.get("status") or "unknown",
                                message=f"{frame_after_delegate.get('frame_id')} <= child [{index}/{child_count}]",
                                goal_id=goal.get("goal_id"),
                                generation=current_generation,
                                candidate_id=candidate_id,
                                loop_run_id=loop_run_id,
                            ),
                        )
                    if latest_validated_result is not None:
                        _close_current_frame(
                            frame=frame_after_delegate,
                            status="completed",
                            summary="下位フレーム群が局所修復に成功しました。",
                            decision="continue_here",
                            reason="child_goals を順次実行し、有効な修復結果を得られました。",
                            next_goal="次の改善単位を選ぶ",
                        )
                        return latest_validated_result
                    fallback_result = child_results[-1] if child_results else {"status": "rejected", "reason": "no child result"}
                    _close_current_frame(
                        frame=frame_after_delegate,
                        status="rejected",
                        summary="下位フレーム群でも前進できませんでした。",
                        decision="return_to_parent",
                        reason=fallback_result.get("reason") or "下位フレームの結果を持って上位へ戻ります。",
                        next_goal=child_goals[0] if child_goals else frame_goal,
                    )
                    return fallback_result

                if parsed.get("continue_or_return_explicit") and parsed["continue_or_return"]["decision"] == "open_child_frame":
                    child_request_delta = _build_frame_delta_context(
                        root=root,
                        attempt=attempt_report,
                        previous_delta_context=frame_delta_context,
                        before_text=current_text,
                        after_text=after_text,
                        diff_text=local_diff_text,
                        detail={
                            "summary": "model requested child frame",
                            "error_type": "ChildFrameRequest",
                            "file": str(target_candidate_path),
                            "line": None,
                            "detail": " / ".join(
                                _child_goals_from_payload(
                                    parsed["continue_or_return"],
                                    fallback_goal=frame_goal,
                                )
                            ),
                        },
                        report=None,
                    )
                    attempt_report["delta_context"] = child_request_delta
                    maybe_child = _maybe_recurse(next_text=after_text, local_delta_context=child_request_delta)
                    if maybe_child is not None:
                        return maybe_child
                    if child_request_delta.get("repeated_pattern"):
                        attempt_report["decision_reason"] = "repeated child frame request without progress"
                        _close_current_frame(
                            frame=frame,
                            status="rejected",
                            summary="子フレーム要求が連続し、この階層で実観測や実編集に進めていません。",
                            decision="return_to_parent",
                            reason="再分解の反復を止め、観測または計画見直しへ戻します。",
                            next_goal="対象コードを直接観測して原因を切り分ける",
                        )
                        return {
                            "status": "rejected",
                            "reason": attempt_report["decision_reason"],
                            "outcome": "repeated_child_frame_request",
                        }

                if not local_diff_text.strip():
                    attempt_report["decision_reason"] = "candidate did not change the target file"
                    attempt_report["status"] = "rejected"
                    no_change_delta = _build_frame_delta_context(
                        root=root,
                        attempt=attempt_report,
                        previous_delta_context=frame_delta_context,
                        before_text=current_text,
                        after_text=after_text,
                        diff_text=local_diff_text,
                        detail={
                            "summary": "candidate did not change the target file",
                            "error_type": "NoChange",
                            "file": str(target_candidate_path),
                            "line": None,
                            "detail": "差分が生成されませんでした。",
                        },
                        report=None,
                    )
                    attempt_report["delta_context"] = no_change_delta
                    maybe = _maybe_recurse(
                        next_text=current_text,
                        local_delta_context=no_change_delta,
                        allow_legacy_continue=True,
                    )
                    if maybe is not None:
                        return maybe
                    if no_change_delta.get("repeated_pattern"):
                        attempt_report["decision_reason"] = "repeated no-change without progress"
                    _close_current_frame(
                        frame=frame,
                        status="rejected",
                        summary="差分が存在しなかったため、この階層では前進できませんでした。",
                        decision="return_to_parent",
                        reason="説明は変わったが行動が変わっていません。",
                        next_goal="分解や対象の切り方を上位で見直す",
                    )
                    return {
                        "status": "rejected",
                        "reason": attempt_report["decision_reason"],
                        "outcome": "no_change",
                    }

                low_value_reason = _low_value_change_reason(
                    root=root,
                    candidate_id=candidate_id,
                    before_text=current_text,
                    after_text=after_text,
                    diff_text=local_diff_text,
                    change_summary=attempt_report["change_summary"],
                )
                if low_value_reason:
                    attempt_report["decision_reason"] = low_value_reason
                    attempt_report["status"] = "rejected"
                    low_value_delta = _build_frame_delta_context(
                        root=root,
                        attempt=attempt_report,
                        previous_delta_context=frame_delta_context,
                        before_text=current_text,
                        after_text=after_text,
                        diff_text=local_diff_text,
                        detail={
                            "summary": low_value_reason,
                            "error_type": "LowValueChange",
                            "file": str(target_candidate_path),
                            "line": None,
                            "detail": low_value_reason,
                        },
                        report=None,
                    )
                    attempt_report["delta_context"] = low_value_delta
                    maybe = _maybe_recurse(
                        next_text=after_text,
                        local_delta_context=low_value_delta,
                        allow_legacy_continue=True,
                    )
                    if maybe is not None:
                        return maybe
                    _close_current_frame(
                        frame=frame,
                        status="rejected",
                        summary="低価値な変更として却下されました。",
                        decision="return_to_parent",
                        reason="この階層の変更粒度が浅く、上位で目的分解を見直す必要があります。",
                        next_goal="より構造的な変更単位を選び直す",
                    )
                    return {
                        "status": "rejected",
                        "reason": attempt_report["decision_reason"],
                        "outcome": "low_value_change",
                    }

                changed_paths = _changed_paths_from_diff(local_diff_text)
                protected_diff = next(
                    (
                        changed
                        for changed in changed_paths
                        if _is_protected_path(changed, list(self_model.get("immutable_paths", [])))
                    ),
                    None,
                )
                if protected_diff:
                    attempt_report["decision_reason"] = f"candidate touched protected path: {protected_diff}"
                    attempt_report["status"] = "rejected"
                    protected_delta = _build_frame_delta_context(
                        root=root,
                        attempt=attempt_report,
                        previous_delta_context=frame_delta_context,
                        before_text=current_text,
                        after_text=after_text,
                        diff_text=local_diff_text,
                        detail={
                            "summary": attempt_report["decision_reason"],
                            "error_type": "ProtectedPath",
                            "file": protected_diff,
                            "line": None,
                            "detail": attempt_report["decision_reason"],
                        },
                        report=None,
                    )
                    attempt_report["delta_context"] = protected_delta
                    _close_current_frame(
                        frame=frame,
                        status="rejected",
                        summary="保護対象に触れたため、この階層の実行は不正でした。",
                        decision="return_to_parent",
                        reason="この階層のスコープ設定が広すぎます。",
                        next_goal="触れてよい範囲で再分解する",
                    )
                    return {
                        "status": "rejected",
                        "reason": attempt_report["decision_reason"],
                        "outcome": "protected_diff",
                    }

                append_history(
                    root,
                    history_event(
                        step="candidate_generated",
                        outcome="completed",
                        message=f"candidate {candidate_id} proposed a replacement for {target_file}",
                        goal_id=goal.get("goal_id"),
                        generation=current_generation,
                        candidate_id=candidate_id,
                        loop_run_id=loop_run_id,
                    ),
                )
                update_runtime_status(
                    root,
                    status="running",
                    current_candidate_id=candidate_id,
                    phase="validating",
                    phase_started_at=now_iso(),
                    worker_heartbeat_at=now_iso(),
                    current_stream_path=str(paths.stream_log_path(candidate_id)),
                    last_event="validation_started",
                )
                validation_report = _run_validation(
                    root=root,
                    candidate_id=candidate_id,
                    command=command,
                    cwd=candidate_path,
                )
                if not validation_report["passed"]:
                    attempt_report["decision_reason"] = "validation failed"
                    attempt_report["status"] = "rejected"
                    failure_detail = _extract_failure_detail(validation_report, target_file=target_file) or {
                        "summary": "validation failed",
                        "error_type": "ValidationError",
                        "file": str(target_candidate_path),
                        "line": None,
                        "detail": "検証コマンドが失敗しました。",
                    }
                    validation_delta = _build_frame_delta_context(
                        root=root,
                        attempt=attempt_report,
                        previous_delta_context=frame_delta_context,
                        before_text=current_text,
                        after_text=after_text,
                        diff_text=local_diff_text,
                        detail=failure_detail,
                        report=validation_report,
                    )
                    attempt_report["delta_context"] = validation_delta
                    maybe = _maybe_recurse(
                        next_text=after_text,
                        local_delta_context=validation_delta,
                        allow_legacy_continue=True,
                    )
                    if maybe is not None:
                        return maybe
                    decision = "return_to_parent" if validation_delta.get("repeated_pattern") else "continue_here"
                    _close_current_frame(
                        frame=frame,
                        status="rejected",
                        summary="検証に失敗しました。",
                        decision=decision,
                        reason=(
                            "同型失敗が続いているため、上位で分解や対象を見直すべきです。"
                            if decision == "return_to_parent"
                            else "この階層で局所修正を続ける余地があります。"
                        ),
                        next_goal=(
                            "上位に戻って別の切り方を選ぶ"
                            if decision == "return_to_parent"
                            else "失敗差分を踏まえてもう一段小さく直す"
                        ),
                    )
                    return {
                        "status": "rejected",
                        "reason": attempt_report["decision_reason"],
                        "outcome": "validation_failed",
                        "validation_report": validation_report,
                    }

                attempt_report["delta_context"] = _clear_delta_context_after_success(attempt_report.get("delta_context"))
                _close_current_frame(
                    frame=frame,
                    status="completed",
                    summary="この階層の変更は検証を通過しました。",
                    decision="continue_here",
                    reason="この階層の局所目標を満たしました。",
                    next_goal="次の改善単位を選ぶ",
                )
                return {
                    "status": "validated",
                    "text": after_text,
                    "validation_report": validation_report,
                    "local_change_summary": parsed["change_summary"],
                }

            if _is_protected_path(target_file, list(self_model.get("immutable_paths", []))):
                attempt_report["status"] = "rejected"
                attempt_report["decision_reason"] = f"target file is protected: {target_file}"
                _maybe_persist_self_memo(root, attempt=attempt_report, goal_id=goal.get("goal_id"))
                write_json(paths.attempt_report_path(candidate_id), attempt_report)
                append_history(
                    root,
                    history_event(
                        step="attempt_rejected",
                        outcome="protected_path",
                        message=attempt_report["decision_reason"],
                        goal_id=goal.get("goal_id"),
                        generation=current_generation,
                        candidate_id=candidate_id,
                        loop_run_id=loop_run_id,
                    ),
                )
                iterations.append(
                    {
                        "candidate_id": candidate_id,
                        "target_file": target_file,
                        "validation_passed": False,
                        "decision": "rejected",
                        "reason": attempt_report["decision_reason"],
                    }
                )
                previous_failure_summary = attempt_report["decision_reason"]
                continue

            resume_frame = attempt_report.get("task_frame") if resuming_attempt else None
            candidate_working_text = target_candidate_path.read_text(encoding="utf-8")
            try:
                frame_result = _execute_frame(
                    current_text=before_text,
                    working_text_seed=candidate_working_text,
                    frame_goal=str(attempt_report["purpose"] or ""),
                    frame_delta_context=attempt_report["delta_context"],
                    parent_frame=None,
                    resumed_frame=resume_frame if isinstance(resume_frame, dict) else None,
                )
            except BackendError as exc:
                update_runtime_status(
                    root,
                    status="error",
                    active_loop_run_id=None,
                    current_candidate_id=candidate_id,
                    last_loop_finished_at=now_iso(),
                    last_error=str(exc),
                    last_event="backend_error",
                    worker_heartbeat_at=now_iso(),
                )
                append_history(
                    root,
                    history_event(
                        step="backend_error",
                        outcome="failed",
                        message=str(exc),
                        goal_id=goal.get("goal_id"),
                        generation=current_generation,
                        candidate_id=candidate_id,
                        loop_run_id=loop_run_id,
                    ),
                )
                notify_dashboard(root)
                raise

            if frame_result["status"] != "validated":
                _maybe_persist_self_memo(root, attempt=attempt_report, goal_id=goal.get("goal_id"))
                write_json(paths.attempt_report_path(candidate_id), attempt_report)
                outcome = frame_result.get("outcome") or "rejected"
                if outcome in {"no_change", "low_value_change"}:
                    updated_goal = advance_goal_focus(goal)
                    write_json(paths.goal_path, updated_goal)
                    append_history(
                        root,
                        history_event(
                            step="focus_rotated",
                            outcome=outcome,
                            message=f"rotated focus after {outcome} candidate {candidate_id}",
                            goal_id=updated_goal.get("goal_id"),
                            generation=current_generation,
                            candidate_id=candidate_id,
                            loop_run_id=loop_run_id,
                        ),
                    )
                append_history(
                    root,
                    history_event(
                        step="attempt_rejected" if outcome != "validation_failed" else "validation",
                        outcome=outcome if outcome != "validation_failed" else "failed",
                        message=attempt_report["decision_reason"] or "candidate failed",
                        goal_id=goal.get("goal_id"),
                        generation=current_generation,
                        candidate_id=candidate_id,
                        loop_run_id=loop_run_id,
                    ),
                )
                iterations.append(
                    {
                        "candidate_id": candidate_id,
                        "target_file": target_file,
                        "validation_passed": False,
                        "decision": "rejected",
                        "reason": attempt_report["decision_reason"],
                    }
                )
                if frame_result.get("validation_report"):
                    previous_failure_summary = _validation_failure_summary(frame_result["validation_report"])
                else:
                    previous_failure_summary = attempt_report["decision_reason"]
                notify_dashboard(root)
                continue

            final_text = str(frame_result.get("text") or before_text)
            target_candidate_path.write_text(final_text, encoding="utf-8")
            diff_text = _render_target_diff(
                before_text=before_text,
                after_text=final_text,
                target_file=target_file,
            )
            paths.diff_path(candidate_id).write_text(diff_text + ("\n" if diff_text else ""), encoding="utf-8")
            final_change_summary = attempt_report.get("change_summary") or {}
            final_change_text = str(final_change_summary.get("summary") or frame_result.get("local_change_summary") or "")
            attempt_report["change_summary"] = _build_change_summary(before_text, final_text, diff_text, final_change_text)
            write_json(paths.attempt_report_path(candidate_id), attempt_report)

            new_version_id = _next_version_id(current_generation)
            new_active_path = paths.runtime_versions_dir / new_version_id
            copytree_clean(candidate_path, new_active_path)
            previous_version_payload = dict(version)
            version["active_generation"] = candidate_generation
            version["active_version_id"] = new_version_id
            version["active_path"] = str(new_active_path.resolve())
            version["last_candidate_id"] = candidate_id
            version["updated_at"] = now_iso()
            write_json(paths.version_path, version)
            update_runtime_status(
                root,
                status="running",
                current_candidate_id=candidate_id,
                phase="promoting",
                phase_started_at=now_iso(),
                worker_heartbeat_at=now_iso(),
                current_stream_path=str(paths.stream_log_path(candidate_id)),
                last_event="promotion_started",
            )
            append_history(
                root,
                history_event(
                    step="promotion",
                    outcome="applied",
                    message=f"candidate {candidate_id} promoted to {new_version_id}",
                    goal_id=goal.get("goal_id"),
                    generation=candidate_generation,
                    candidate_id=candidate_id,
                    loop_run_id=loop_run_id,
                ),
            )

            retry_report = _run_validation(
                root=root,
                candidate_id=candidate_id,
                command=command,
                cwd=new_active_path,
                retry=True,
            )
            attempt_report["promoted_version_id"] = new_version_id
            if retry_report["passed"]:
                attempt_report["delta_context"] = _clear_delta_context_after_success(attempt_report.get("delta_context"))
                updated_goal = advance_goal_after_promotion(goal, candidate_id=candidate_id)
                attempt_report["status"] = "promoted"
                attempt_report["decision_reason"] = "candidate validated and active goal retry passed"
                _maybe_persist_self_memo(root, attempt=attempt_report, goal_id=goal.get("goal_id"))
                write_json(paths.attempt_report_path(candidate_id), attempt_report)
                append_history(
                    root,
                    history_event(
                        step="active_retry",
                        outcome="passed",
                        message=f"{RUNTIME_NAMES['active_version']['formal_name']} {new_version_id} が昇格後の受け入れ条件を通過",
                        goal_id=goal.get("goal_id"),
                        generation=candidate_generation,
                        candidate_id=candidate_id,
                        loop_run_id=loop_run_id,
                    ),
                )
                write_json(paths.goal_path, updated_goal)
                append_history(
                    root,
                    history_event(
                        step="goal_continued",
                        outcome="active",
                        message=f"continuous self-improvement cycle advanced to {updated_goal.get('cycle_count')}",
                        goal_id=updated_goal.get("goal_id"),
                        generation=candidate_generation,
                        candidate_id=candidate_id,
                        loop_run_id=loop_run_id,
                    ),
                )
                append_loop_log(
                    root,
                    f"continuous goal advanced cycle_count={updated_goal.get('cycle_count')} candidate={candidate_id}",
                )
                iterations.append(
                    {
                        "candidate_id": candidate_id,
                        "target_file": target_file,
                        "validation_passed": True,
                        "decision": "promoted",
                        "reason": attempt_report["decision_reason"],
                        "generation": candidate_generation,
                        "cycle_count": updated_goal.get("cycle_count"),
                        "focus_area": (
                            list(updated_goal.get("focus_areas", []))[int(updated_goal.get("next_focus_index", 0)) - 1]
                            if updated_goal.get("focus_areas")
                            else None
                        ),
                    }
                )
                previous_failure_summary = None
                notify_dashboard(root)
                break

            write_json(paths.version_path, previous_version_payload)
            attempt_report["status"] = "rolled_back"
            attempt_report["decision_reason"] = "post-promotion goal retry failed"
            _maybe_persist_self_memo(root, attempt=attempt_report, goal_id=goal.get("goal_id"))
            write_json(paths.attempt_report_path(candidate_id), attempt_report)
            append_history(
                root,
                history_event(
                    step="rollback",
                    outcome="applied",
                    message=f"rolled back promotion of {candidate_id} after failed goal retry",
                    goal_id=goal.get("goal_id"),
                    generation=current_generation,
                    candidate_id=candidate_id,
                    loop_run_id=loop_run_id,
                ),
            )
            iterations.append(
                {
                    "candidate_id": candidate_id,
                    "target_file": target_file,
                    "validation_passed": True,
                    "decision": "rolled_back",
                    "reason": attempt_report["decision_reason"],
                }
            )
            previous_failure_summary = _validation_failure_summary(retry_report)
            notify_dashboard(root)
            queue_item = None

        final_snapshot = build_status_snapshot(root)
        update_runtime_status(
            root,
            status="idle",
            active_loop_run_id=None,
            current_candidate_id=None,
            current_task_stack=None,
            current_runtime_kernel=None,
            current_action=None,
            current_action_step=None,
            phase=None,
            phase_started_at=None,
            current_stream_path=None,
            last_loop_finished_at=now_iso(),
            last_event="loop_completed",
            worker_heartbeat_at=now_iso(),
        )
        append_loop_log(
            root,
            "run-loop completed "
            f"loop_run_id={loop_run_id} goal_status={final_snapshot['goal'].get('status')} latest_candidate={latest_candidate_id}",
        )
        notify_dashboard(root)
        return {
            "loop_run_id": loop_run_id,
            "status": "completed",
            "generation": final_snapshot["active_generation"],
            "goal_status": final_snapshot["goal"].get("status"),
            "iterations": iterations,
            "latest_attempt_id": latest_candidate_id,
            "selected_file": iterations[-1]["target_file"] if iterations else None,
        }
    except Exception as exc:
        phase = str(read_json(paths.runtime_status_path, fallback={}).get("phase") or "").strip() or None
        if latest_candidate_id:
            _mark_attempt_failed(
                root,
                candidate_id=latest_candidate_id,
                phase=phase,
                reason=str(exc),
                note="run-loop 中に attempt が異常終了しました。",
            )
        update_runtime_status(
            root,
            status="error",
            active_loop_run_id=None,
            current_candidate_id=latest_candidate_id,
            current_task_stack=None,
            current_runtime_kernel=None,
            current_action=None,
            current_action_step=None,
            phase=None,
            phase_started_at=None,
            current_stream_path=None,
            last_loop_finished_at=now_iso(),
            last_error=str(exc),
            last_event="loop_error",
            worker_heartbeat_at=now_iso(),
        )
        append_loop_log(root, f"run-loop failed loop_run_id={loop_run_id} error={exc}")
        append_history(
            root,
            history_event(
                step="loop_error",
                outcome="failed",
                message=str(exc),
                goal_id=goal.get("goal_id"),
                generation=current_generation,
                candidate_id=latest_candidate_id,
                loop_run_id=loop_run_id,
            ),
        )
        notify_dashboard(root)
        raise


def show_attempt(root: Path, candidate_id: str) -> dict[str, Any]:
    root = root.expanduser()
    attempt = read_attempt_report(root, candidate_id)
    paths = WorkspacePaths(root)
    validation = read_validation_report(root, candidate_id)
    retry_validation = read_validation_report(root, candidate_id, retry=True)
    raw_model_output = None
    diff_text = None
    stream_log = None
    prompt_snapshots = None
    raw_path = attempt.get("raw_model_output_path")
    diff_path = attempt.get("diff_path")
    stream_path = attempt.get("stream_log_path")
    session_events_path = attempt.get("session_events_path")
    prompt_snapshots_path = attempt.get("prompt_snapshots_path")
    if raw_path and Path(raw_path).exists():
        raw_model_output = Path(raw_path).read_text(encoding="utf-8")
    if diff_path and Path(diff_path).exists():
        diff_text = Path(diff_path).read_text(encoding="utf-8")
    if stream_path and Path(stream_path).exists():
        stream_log = Path(stream_path).read_text(encoding="utf-8")
    if not prompt_snapshots_path and attempt.get("candidate_id"):
        prompt_snapshots_path = str(paths.prompt_snapshots_path(str(attempt["candidate_id"])))
    session_events_all = read_jsonl_rows(Path(str(session_events_path)), limit=200) if session_events_path else None
    system_actions = {"attempt_started", "attempt_resumed", "runtime_failed"}
    session_events = None
    if isinstance(session_events_all, list):
        session_events = [
            event
            for event in session_events_all
            if str((event or {}).get("action") or "") not in system_actions
        ]
    if prompt_snapshots_path:
        prompt_snapshots = read_jsonl_rows(Path(str(prompt_snapshots_path)), limit=200)
    payload = {
        "attempt": attempt,
        "raw_model_output": raw_model_output,
        "stream_log": stream_log,
        "diff": diff_text,
        "session_events": session_events,
        "session_events_all": session_events_all,
        "prompt_snapshots": prompt_snapshots,
        "validation": validation,
        "retry_validation": retry_validation,
    }
    if validation and validation.get("stdout_path") and Path(validation["stdout_path"]).exists():
        payload["validation_stdout"] = Path(validation["stdout_path"]).read_text(encoding="utf-8")
        payload["validation_stderr"] = Path(validation["stderr_path"]).read_text(encoding="utf-8")
    if retry_validation and retry_validation.get("stdout_path") and Path(retry_validation["stdout_path"]).exists():
        payload["retry_validation_stdout"] = Path(retry_validation["stdout_path"]).read_text(encoding="utf-8")
        payload["retry_validation_stderr"] = Path(retry_validation["stderr_path"]).read_text(encoding="utf-8")
    return payload
