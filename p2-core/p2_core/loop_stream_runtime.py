from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from p2_core.workspace import notify_dashboard, now_iso, update_runtime_status


def _append_stream_chunk(
    *,
    root: Path,
    stream_path: Path,
    candidate_id: str,
    phase: str,
    chunk: str,
    emit_chunk: Callable[[str], None],
    last_notify_at: float,
    notify_interval_sec: float = 0.25,
) -> float:
    if not chunk:
        return last_notify_at
    with stream_path.open("a", encoding="utf-8") as handle:
        handle.write(chunk)
    update_runtime_status(
        root,
        status="running",
        current_candidate_id=candidate_id,
        phase=phase,
        worker_heartbeat_at=now_iso(),
        last_output_at=now_iso(),
        current_stream_path=str(stream_path),
        last_event=f"{phase}_stream",
    )
    emit_chunk(chunk)
    now_mono = time.monotonic()
    if now_mono - last_notify_at >= notify_interval_sec:
        notify_dashboard(root)
        return now_mono
    return last_notify_at
