from __future__ import annotations

import hashlib
from typing import Any


def _extract_code_block(text: str) -> str:
    marker = "```"
    start = text.find(marker)
    if start == -1:
        return text.strip()
    remainder = text[start + len(marker) :]
    newline = remainder.find("\n")
    if newline != -1:
        remainder = remainder[newline + 1 :]
    end = remainder.find(marker)
    if end == -1:
        return remainder.strip()
    return remainder[:end].strip()


def _sanitize_prompt_text(text: str, *, max_chars: int = 600) -> str:
    cleaned = text.replace("```", "'''").replace("\x00", "")
    cleaned = "".join(ch for ch in cleaned if ch == "\n" or ch == "\t" or ord(ch) >= 32)
    cleaned = cleaned.strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 3].rstrip() + "..."


def _sanitize_code_context(text: str, *, max_chars: int = 8000) -> str:
    return _sanitize_prompt_text(text, max_chars=max_chars)


def _safe_brief_text(value: Any, *, max_chars: int = 120) -> str:
    return _sanitize_prompt_text(str(value or ""), max_chars=max_chars)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _line_counts(diff_text: str) -> tuple[int, int]:
    added = 0
    removed = 0
    for line in diff_text.splitlines():
        if line.startswith("+++ ") or line.startswith("--- "):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return added, removed


def _changed_paths_from_diff(diff_text: str) -> list[str]:
    paths: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            paths.append(line[len("+++ b/") :].strip())
    return paths
