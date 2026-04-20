from __future__ import annotations

import json
from typing import Any


def _set_current_frame_state(attempt_report: dict[str, Any], frame: dict[str, Any]) -> None:
    if attempt_report.get("task_stack"):
        attempt_report["task_stack"][-1] = frame
    else:
        attempt_report["task_stack"] = [frame]
    attempt_report["task_frame"] = frame


def _apply_closed_frame_state(attempt_report: dict[str, Any], closed_frame: dict[str, Any]) -> dict[str, Any]:
    if attempt_report.get("task_stack"):
        attempt_report["task_stack"][-1] = closed_frame
        attempt_report["task_stack"].pop()
    if attempt_report.get("task_stack"):
        attempt_report["task_frame"] = attempt_report["task_stack"][-1]
    else:
        attempt_report["task_frame"] = closed_frame
    attempt_report["frame_trace"].append(json.loads(json.dumps(closed_frame, ensure_ascii=False)))
    return closed_frame
