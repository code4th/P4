from __future__ import annotations

import json
from typing import Any

from p2_core.loop_utils import _sha256


def _build_prompt_snapshot(
    *,
    timestamp: str,
    candidate_id: str,
    loop_run_id: str,
    runtime_kernel: str,
    phase: str,
    banner: str,
    step: int | None,
    frame_id: str | None,
    frame_depth: int | None,
    model: str,
    thinking_model: str,
    coding_model: str,
    exploratory_coding_model: str,
    stagnation_coding_model: str,
    target_file: str,
    system_prompt: str,
    user_prompt: str,
    prompt_context: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "timestamp": timestamp,
        "candidate_id": candidate_id,
        "loop_run_id": loop_run_id,
        "runtime_kernel": runtime_kernel,
        "phase": phase,
        "banner": banner,
        "step": step,
        "frame_id": frame_id,
        "frame_depth": frame_depth,
        "model": model,
        "thinking_model": thinking_model,
        "coding_model": coding_model,
        "exploratory_coding_model": exploratory_coding_model,
        "stagnation_coding_model": stagnation_coding_model,
        "target_file": target_file,
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "system_prompt_sha256": _sha256(system_prompt),
        "user_prompt_sha256": _sha256(user_prompt),
        "prompt_context": json.loads(json.dumps(prompt_context, ensure_ascii=False))
        if isinstance(prompt_context, dict)
        else None,
    }


def _update_llm_timing(
    *,
    llm_timings: dict[str, Any],
    phase: str,
    started_at: str,
    completed_at: str,
    duration_ms: int,
    first_output_latency_ms: int | None,
    streamed_chars: int,
) -> None:
    llm_timings[phase] = {
        "started_at": started_at,
        "completed_at": completed_at,
        "duration_ms": duration_ms,
        "first_output_latency_ms": first_output_latency_ms,
        "streamed_chars": streamed_chars,
    }
    llm_timings["total_duration_ms"] = sum(
        int(step.get("duration_ms") or 0)
        for key, step in llm_timings.items()
        if isinstance(step, dict) and key != "total_duration_ms"
    )
