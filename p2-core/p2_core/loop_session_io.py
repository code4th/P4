from __future__ import annotations

from pathlib import Path


def _append_action_raw_output(*, raw_model_output_path: Path, step: int, raw_text: str) -> None:
    with raw_model_output_path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n\n===== ACTION STEP {step} =====\n{raw_text}\n")
