from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from p2_core.workspace import notify_dashboard, now_iso, update_runtime_status, write_json


def _persist_attempt_runtime_state(
    *,
    root: Path,
    attempt_report_path: Path,
    attempt_report: dict[str, Any],
    candidate_id: str,
    runtime_kernel: str,
    last_event: str | None = None,
    phase: str | None = None,
) -> None:
    write_json(attempt_report_path, attempt_report)
    update_kwargs: dict[str, Any] = {
        "status": "running",
        "current_candidate_id": candidate_id,
        "worker_heartbeat_at": now_iso(),
        "current_task_stack": json.loads(json.dumps(attempt_report.get("task_stack") or [], ensure_ascii=False)) or None,
        "current_runtime_kernel": runtime_kernel,
    }
    if last_event is not None:
        update_kwargs["last_event"] = last_event
    if phase is not None:
        update_kwargs["phase"] = phase
    update_runtime_status(root, **update_kwargs)
    notify_dashboard(root)
