from __future__ import annotations


def stdout_looks_like_user_visible_result(stdout: str) -> bool:
    text = str(stdout or "").strip()
    if not text:
        return False
    lower = text.lower()
    notification_markers = [
        "created successfully",
        "saved successfully",
        "written successfully",
        "generated successfully",
        "file created",
    ]
    if any(marker in lower for marker in notification_markers) and len(text.splitlines()) <= 3:
        return False
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) >= 3:
        return True
    visual_chars = set("#|+-*/\\_[]{}<>█▓▒░.•SG")
    visual_count = sum(1 for char in text if char in visual_chars)
    if visual_count >= 8:
        return True
    return bool(text)


def visible_result_sanity_issues(*, user_message: str, stdout: str) -> list[str]:
    """Return obvious, deterministic issues in a displayed artifact.

    This is not a domain validator. It only catches low-information visible
    output that should not be promoted to final when the user asked for a
    generated artifact to be displayed.
    """
    request = str(user_message or "").lower()
    visible_artifact_markers = [
        "迷路",
        "図",
        "diagram",
        "chart",
        "table",
        "表",
        "board",
        "map",
        "grid",
    ]
    if not any(marker in request for marker in visible_artifact_markers):
        return []
    text = str(stdout or "").strip("\n")
    if not text.strip():
        return ["stdout is empty"]
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    if len(lines) < 8:
        return []
    unique_lines = set(lines)
    if len(unique_lines) <= 2 and len(lines) >= 8:
        return [
            "visible artifact output has only "
            f"{len(unique_lines)} unique non-empty line patterns across {len(lines)} lines"
        ]
    return []
