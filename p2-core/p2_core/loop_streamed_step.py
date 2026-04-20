from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from p2_core.backend import ModelBackend
from p2_core.loop_phase_runtime import _mark_phase_completed, _mark_phase_started
from p2_core.loop_streaming import _build_prompt_snapshot, _update_llm_timing
from p2_core.workspace import append_jsonl, append_loop_log, notify_dashboard, now_iso


def _run_streamed_step_impl(
    *,
    root: Path,
    phase: str,
    system_prompt: str,
    user_prompt: str,
    banner: str,
    phase_backend: ModelBackend,
    phase_model_name: str,
    step: int | None,
    frame_id: str | None,
    frame_depth: int | None,
    prompt_context: dict[str, Any] | None,
    candidate_id: str,
    target_file: str,
    loop_run_id: str,
    runtime_kernel: str,
    thinking_model: str,
    coding_model: str,
    exploratory_coding_model: str,
    stagnation_coding_model: str,
    prompt_snapshots_path: Path,
    stream_path: Path,
    phase_label: str,
    append_stream_chunk: Callable[[str], None],
    llm_timings: dict[str, Any],
    persist_attempt_report: Callable[[], None],
) -> str:
    phase_started_at = now_iso()
    started = time.monotonic()
    first_chunk_at: float | None = None
    streamed_chars = 0
    prompt_snapshot = _build_prompt_snapshot(
        timestamp=phase_started_at,
        candidate_id=candidate_id,
        loop_run_id=loop_run_id,
        runtime_kernel=runtime_kernel,
        phase=phase,
        banner=banner,
        step=step,
        frame_id=frame_id,
        frame_depth=frame_depth,
        model=phase_model_name,
        thinking_model=thinking_model,
        coding_model=coding_model,
        exploratory_coding_model=exploratory_coding_model,
        stagnation_coding_model=stagnation_coding_model,
        target_file=target_file,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        prompt_context=prompt_context,
    )
    prompt_snapshot_written = False

    def _record_request_payload(request_payload: dict[str, Any]) -> None:
        nonlocal prompt_snapshot_written
        if prompt_snapshot_written:
            return
        prompt_snapshot["request"] = json.loads(json.dumps(request_payload, ensure_ascii=False))
        append_jsonl(prompt_snapshots_path, prompt_snapshot)
        prompt_snapshot_written = True

    with stream_path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n\n===== {phase_label} =====\n")
    _mark_phase_started(
        root=root,
        candidate_id=candidate_id,
        phase=phase,
        phase_started_at=phase_started_at,
        phase_model_name=phase_model_name,
        thinking_model=thinking_model,
        coding_model=coding_model,
        exploratory_coding_model=exploratory_coding_model,
        stagnation_coding_model=stagnation_coding_model,
        stream_path=stream_path,
    )
    print(
        f"[p2-{phase}] candidate={candidate_id} target={target_file} model={phase_model_name} streaming=true",
        flush=True,
    )
    print(f"----- {banner}ここから -----", flush=True)

    def _handler(chunk: str) -> None:
        nonlocal first_chunk_at, streamed_chars
        if not chunk:
            return
        if first_chunk_at is None:
            first_chunk_at = time.monotonic()
        streamed_chars += len(chunk)
        append_stream_chunk(chunk)

    text = phase_backend.generate_text(
        system_prompt,
        user_prompt,
        stream_handler=_handler,
        request_recorder=_record_request_payload,
    )
    if not prompt_snapshot_written:
        append_jsonl(prompt_snapshots_path, prompt_snapshot)
    duration_ms = int((time.monotonic() - started) * 1000)
    _update_llm_timing(
        llm_timings=llm_timings,
        phase=phase,
        started_at=phase_started_at,
        completed_at=now_iso(),
        duration_ms=duration_ms,
        first_output_latency_ms=int((first_chunk_at - started) * 1000) if first_chunk_at is not None else None,
        streamed_chars=streamed_chars,
    )
    persist_attempt_report()
    _mark_phase_completed(
        root=root,
        candidate_id=candidate_id,
        phase=phase,
        phase_started_at=phase_started_at,
        phase_model_name=phase_model_name,
        thinking_model=thinking_model,
        coding_model=coding_model,
        exploratory_coding_model=exploratory_coding_model,
        stagnation_coding_model=stagnation_coding_model,
        stream_path=stream_path,
        has_output=first_chunk_at is not None,
    )
    append_loop_log(
        root,
        f"llm phase completed candidate={candidate_id} phase={phase} duration_ms={duration_ms} "
        f"first_output_latency_ms={llm_timings[phase]['first_output_latency_ms']} "
        f"streamed_chars={streamed_chars}",
    )
    if text:
        print("", flush=True)
    print(f"----- {banner}ここまで -----", flush=True)
    print(f"[p2-{phase}] candidate={candidate_id} completed duration_ms={duration_ms}", flush=True)
    notify_dashboard(root)
    return text
