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
