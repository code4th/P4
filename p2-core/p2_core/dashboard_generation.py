from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Any

from p2_core.workspace import WorkspacePaths, read_json, read_validation_report


def _format_duration_ms(value: Any) -> str:
    if value in {None, "", "n/a"}:
        return "n/a"
    try:
        seconds = float(value) / 1000.0
    except (TypeError, ValueError):
        return "n/a"
    if seconds >= 10:
        return f"{seconds:.1f} 秒"
    return f"{seconds:.2f} 秒"


def _python_function_ranges(lines: list[str]) -> list[tuple[int, str]]:
    ranges: list[tuple[int, str]] = []
    for index, line in enumerate(lines, start=1):
        stripped = line.lstrip()
        if stripped.startswith("def "):
            name = stripped[4:].split("(", 1)[0].strip()
            if name:
                ranges.append((index, name))
    return ranges


def _function_name_for_line(ranges: list[tuple[int, str]], line_number: int) -> str:
    current = ""
    for start, name in ranges:
        if start > line_number:
            break
        current = name
    return current


def _diff_function_names(diff_text: str, after_lines: list[str]) -> list[str]:
    names: list[str] = []
    ranges = _python_function_ranges(after_lines)
    for line in diff_text.splitlines():
        if not line.startswith("@@"):
            continue
        match = re.search(r"\+(\d+)", line)
        if not match:
            continue
        func = _function_name_for_line(ranges, int(match.group(1)))
        if func and func not in names:
            names.append(func)
    return names


def _diff_excerpt(diff_text: str, *, limit: int = 6) -> list[str]:
    excerpts: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith(("---", "+++", "@@")):
            continue
        if not line.startswith(("+", "-")):
            continue
        if line.startswith("+++") or line.startswith("---"):
            continue
        excerpts.append(line[:220])
        if len(excerpts) >= limit:
            break
    return excerpts


def _validation_result_text(report: dict[str, Any] | None, *, retry: bool = False) -> str:
    if not report:
        return "情報なし"
    label = "再検証" if retry else "検証"
    passed = report.get("passed")
    returncode = report.get("returncode")
    duration = report.get("duration_ms")
    outcome = "成功" if passed else "失敗"
    tail = []
    if returncode is not None:
        tail.append(f"rc={returncode}")
    if duration is not None:
        tail.append(f"{_format_duration_ms(duration)}")
    suffix = f" ({', '.join(tail)})" if tail else ""
    return f"{label}: {outcome}{suffix}"


def _generation_outcome_text(
    attempt: dict[str, Any],
    validation: dict[str, Any] | None,
    retry_validation: dict[str, Any] | None,
) -> str:
    parts = [f"昇格: {attempt.get('candidate_id') or 'n/a'} -> v{int(attempt.get('candidate_generation') or 0):04d}"]
    parts.append(_validation_result_text(validation))
    if retry_validation:
        parts.append(_validation_result_text(retry_validation, retry=True))
    return " / ".join(parts)


def _attempt_outcome_text(
    attempt: dict[str, Any],
    validation: dict[str, Any] | None,
    retry_validation: dict[str, Any] | None,
) -> str:
    status = str(attempt.get("status") or "unknown")
    candidate_id = str(attempt.get("candidate_id") or "n/a")
    decision_reason = str(attempt.get("decision_reason") or "")
    parts = [f"候補: {candidate_id}", f"状態: {status}"]
    if validation:
        parts.append(_validation_result_text(validation))
    if retry_validation:
        parts.append(_validation_result_text(retry_validation, retry=True))
    if decision_reason:
        parts.append(f"理由: {decision_reason}")
    return " / ".join(parts)


def build_generation_report(root: Path, *, limit: int = 32) -> list[dict[str, Any]]:
    paths = WorkspacePaths(root)
    promoted_attempts: dict[int, dict[str, Any]] = {}
    recent_attempts: list[dict[str, Any]] = []
    for attempt_path in sorted(paths.attempts_dir.glob("c*.json")):
        try:
            attempt = read_json(attempt_path, fallback={})
        except (FileNotFoundError, json.JSONDecodeError):
            continue
        if isinstance(attempt, dict):
            recent_attempts.append(attempt)
        if attempt.get("status") != "promoted":
            continue
        try:
            generation = int(attempt.get("candidate_generation") or 0)
        except (TypeError, ValueError):
            continue
        if generation > 1:
            promoted_attempts[generation] = attempt

    entries: list[dict[str, Any]] = []
    for generation in sorted(promoted_attempts.keys(), reverse=True)[:limit]:
        attempt = promoted_attempts[generation]
        candidate_id = str(attempt.get("candidate_id") or "")
        parent_generation = int(attempt.get("parent_generation") or generation - 1)
        target_file = str(attempt.get("target_file") or "agent/goal_logic.py")
        diff_path = Path(str(attempt.get("diff_path") or paths.diff_path(candidate_id)))
        diff_text = ""
        try:
            diff_text = diff_path.read_text(encoding="utf-8")
        except OSError:
            before_path = paths.runtime_versions_dir / f"v{parent_generation:04d}" / target_file
            after_path = paths.runtime_versions_dir / f"v{generation:04d}" / target_file
            try:
                before_lines = before_path.read_text(encoding="utf-8").splitlines()
                after_lines = after_path.read_text(encoding="utf-8").splitlines()
                diff_text = "\n".join(
                    difflib.unified_diff(before_lines, after_lines, fromfile=str(before_path), tofile=str(after_path), n=1)
                )
            except OSError:
                diff_text = ""
        after_lines: list[str] = []
        after_target_path = paths.runtime_versions_dir / f"v{generation:04d}" / target_file
        try:
            after_lines = after_target_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            after_lines = []

        validation = read_validation_report(root, candidate_id)
        retry_validation = read_validation_report(root, candidate_id, retry=True)
        change_summary = attempt.get("change_summary") or {}
        entries.append(
            {
                "generation": generation,
                "version_id": f"v{generation:04d}",
                "candidate_id": candidate_id,
                "target_file": target_file,
                "parent_generation": parent_generation,
                "changed_functions": _diff_function_names(diff_text, after_lines),
                "added_lines": change_summary.get("added_lines"),
                "removed_lines": change_summary.get("removed_lines"),
                "diff_excerpt": _diff_excerpt(diff_text),
                "outcome": _generation_outcome_text(attempt, validation, retry_validation),
                "decision_reason": str(attempt.get("decision_reason") or ""),
                "created_at": str(attempt.get("created_at") or ""),
            }
        )
    if entries:
        return entries

    for attempt in reversed(recent_attempts[-limit:]):
        candidate_id = str(attempt.get("candidate_id") or "")
        if not candidate_id:
            continue
        target_file = str(attempt.get("target_file") or "agent/goal_logic.py")
        validation = read_validation_report(root, candidate_id)
        retry_validation = read_validation_report(root, candidate_id, retry=True)
        change_summary = attempt.get("change_summary") or {}
        generation = int(attempt.get("candidate_generation") or attempt.get("parent_generation") or 0)
        entries.append(
            {
                "generation": generation,
                "version_id": f"v{generation:04d}" if generation > 0 else "v----",
                "candidate_id": candidate_id,
                "target_file": target_file,
                "parent_generation": int(attempt.get("parent_generation") or 0),
                "changed_functions": list(change_summary.get("changed_functions") or []),
                "added_lines": change_summary.get("added_lines"),
                "removed_lines": change_summary.get("removed_lines"),
                "diff_excerpt": [],
                "outcome": _attempt_outcome_text(attempt, validation, retry_validation),
                "decision_reason": str(attempt.get("decision_reason") or ""),
                "created_at": str(attempt.get("created_at") or ""),
            }
        )
    return entries
