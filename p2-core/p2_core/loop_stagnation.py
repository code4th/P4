from __future__ import annotations


def _advance_stagnation_state(
    *,
    marker: str,
    made_progress: bool,
    no_progress_streak: int,
    repeated_marker_streak: int,
    last_stagnation_marker: str,
) -> tuple[int, int, str]:
    next_no_progress_streak = 0 if made_progress else no_progress_streak + 1
    next_repeated_marker_streak = repeated_marker_streak
    next_last_stagnation_marker = last_stagnation_marker
    if not marker:
        return next_no_progress_streak, 0, ""
    if marker == last_stagnation_marker:
        next_repeated_marker_streak += 1
    else:
        next_last_stagnation_marker = marker
        next_repeated_marker_streak = 1
    return next_no_progress_streak, next_repeated_marker_streak, next_last_stagnation_marker


def _is_stagnation_threshold_exceeded(
    *,
    repeated_marker_streak: int,
    no_progress_streak: int,
    repeated_marker_threshold: int = 3,
    no_progress_threshold: int = 6,
) -> bool:
    return repeated_marker_streak >= repeated_marker_threshold or no_progress_streak >= no_progress_threshold
