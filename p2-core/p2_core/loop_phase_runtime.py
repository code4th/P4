from __future__ import annotations

from pathlib import Path

from p2_core.workspace import notify_dashboard, now_iso, update_runtime_status


def _mark_phase_started(
    *,
    root: Path,
    candidate_id: str,
    phase: str,
    phase_started_at: str,
    phase_model_name: str,
    thinking_model: str,
    coding_model: str,
    exploratory_coding_model: str,
    stagnation_coding_model: str,
    stream_path: Path,
) -> None:
    update_runtime_status(
        root,
        status="running",
        current_candidate_id=candidate_id,
        phase=phase,
        phase_started_at=phase_started_at,
        model=phase_model_name,
        thinking_model=thinking_model,
        coding_model=coding_model,
        exploratory_coding_model=exploratory_coding_model,
        stagnation_coding_model=stagnation_coding_model,
        worker_heartbeat_at=now_iso(),
        last_output_at=None,
        current_stream_path=str(stream_path),
        last_event=f"{phase}_started",
    )
    notify_dashboard(root)


def _mark_phase_completed(
    *,
    root: Path,
    candidate_id: str,
    phase: str,
    phase_started_at: str,
    phase_model_name: str,
    thinking_model: str,
    coding_model: str,
    exploratory_coding_model: str,
    stagnation_coding_model: str,
    stream_path: Path,
    has_output: bool,
) -> None:
    update_runtime_status(
        root,
        status="running",
        current_candidate_id=candidate_id,
        phase=phase,
        phase_started_at=phase_started_at,
        model=phase_model_name,
        thinking_model=thinking_model,
        coding_model=coding_model,
        exploratory_coding_model=exploratory_coding_model,
        stagnation_coding_model=stagnation_coding_model,
        worker_heartbeat_at=now_iso(),
        last_output_at=now_iso() if has_output else None,
        current_stream_path=str(stream_path),
        last_event=f"{phase}_completed",
    )
