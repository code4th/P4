from __future__ import annotations

from pathlib import Path

from p2_core.workspace import now_iso, update_runtime_status


def _mark_action_runtime_event(
    *,
    root: Path,
    candidate_id: str,
    runtime_kernel: str,
    action: str,
    step: int,
    last_event: str,
) -> None:
    update_runtime_status(
        root,
        status="running",
        current_candidate_id=candidate_id,
        current_runtime_kernel=runtime_kernel,
        current_action=action,
        current_action_step=step,
        phase="acting",
        worker_heartbeat_at=now_iso(),
        last_event=last_event,
    )
