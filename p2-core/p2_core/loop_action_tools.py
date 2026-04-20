from __future__ import annotations

import difflib
import re
from pathlib import Path, PurePosixPath
from typing import Any

from p2_core.loop_utils import _line_counts, _sanitize_code_context, _sanitize_prompt_text


def _resolve_candidate_path(base: Path, relative_path: str) -> Path:
    normalized = PurePosixPath(relative_path or ".")
    if normalized.is_absolute():
        raise ValueError("absolute path is not allowed")
    path = (base / normalized).resolve()
    if base.resolve() not in {path, *path.parents}:
        raise ValueError("path escaped workspace root")
    return path


def _resolve_read_path(*, root: Path, candidate_path: Path, requested_path: str, target_file: str) -> tuple[Path, str]:
    relative_path = str(requested_path or target_file).strip() or target_file
    if relative_path.startswith(("state/", "logs/", "seed/")):
        resolved = _resolve_candidate_path(root.resolve(), relative_path)
        return resolved, relative_path
    resolved = _resolve_candidate_path(candidate_path.resolve(), relative_path)
    return resolved, relative_path


def _read_file_slice(path: Path, *, start_line: int | None = None, end_line: int | None = None, max_chars: int = 5000) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    start = max(1, int(start_line or 1))
    end = min(len(lines), int(end_line or len(lines)))
    excerpt = "\n".join(lines[start - 1 : end])
    return {
        "path": str(path),
        "start_line": start,
        "end_line": end,
        "line_count": len(lines),
        "excerpt": _sanitize_code_context(excerpt, max_chars=max_chars),
    }


def _search_workspace(candidate_path: Path, pattern: str, *, limit: int = 20) -> dict[str, Any]:
    needle = str(pattern or "").strip()
    if not needle:
        return {"matches": [], "count": 0}
    regex: re.Pattern[str] | None = None
    try:
        regex = re.compile(needle)
    except re.error:
        regex = None
    matches: list[dict[str, Any]] = []
    for path in sorted(candidate_path.rglob("*.py")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for index, line in enumerate(text.splitlines(), start=1):
            hit = bool(regex.search(line)) if regex is not None else needle in line
            if not hit:
                continue
            matches.append(
                {
                    "path": str(path.relative_to(candidate_path)),
                    "line": index,
                    "text": _sanitize_prompt_text(line, max_chars=200),
                }
            )
            if len(matches) >= limit:
                return {"matches": matches, "count": len(matches)}
    return {"matches": matches, "count": len(matches)}


def _apply_structured_patch(
    *,
    before_text: str,
    edits: list[dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    updated = before_text
    applied: list[dict[str, Any]] = []
    for index, edit in enumerate(edits, start=1):
        old_text = str(edit.get("old_text") or "")
        new_text = str(edit.get("new_text") or "")
        if not old_text:
            raise ValueError(f"edit {index} missing old_text")
        occurrences = updated.count(old_text)
        if occurrences != 1:
            raise ValueError(f"edit {index} expected exactly 1 match but found {occurrences}")
        updated = updated.replace(old_text, new_text, 1)
        applied.append(
            {
                "index": index,
                "old_excerpt": _sanitize_prompt_text(old_text, max_chars=160),
                "new_excerpt": _sanitize_prompt_text(new_text, max_chars=160),
            }
        )
    diff_text = "\n".join(
        difflib.unified_diff(
            before_text.splitlines(),
            updated.splitlines(),
            fromfile="before",
            tofile="after",
            lineterm="",
        )
    )
    added_lines, removed_lines = _line_counts(diff_text)
    return (
        updated,
        {
            "applied_edits": applied,
            "added_lines": added_lines,
            "removed_lines": removed_lines,
            "diff_excerpt": _sanitize_prompt_text(diff_text, max_chars=1800),
        },
    )


def _render_target_diff(*, before_text: str, after_text: str, target_file: str) -> str:
    return "\n".join(
        difflib.unified_diff(
            before_text.splitlines(),
            after_text.splitlines(),
            fromfile=f"a/{target_file}",
            tofile=f"b/{target_file}",
            lineterm="",
        )
    )
