from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from p2_core.loop_utils import _safe_brief_text, _sanitize_prompt_text
from p2_core.workspace import WorkspacePaths


def _validation_failure_summary(report: dict[str, Any] | None) -> str | None:
    if not report:
        return None
    stderr = Path(report["stderr_path"]).read_text(encoding="utf-8") if report.get("stderr_path") else ""
    stdout = Path(report["stdout_path"]).read_text(encoding="utf-8") if report.get("stdout_path") else ""
    combined = "\n".join(part for part in [stderr.strip(), stdout.strip()] if part).strip()
    if combined:
        if "SyntaxError" in combined:
            return "validation failed: SyntaxError"
        if "ImportError" in combined or "ModuleNotFoundError" in combined:
            return "validation failed: import error"
        if "AssertionError" in combined:
            return "validation failed: assertion error"
        if "TimeoutExpired" in combined or "timed out" in combined.lower():
            return "validation failed: timeout"
        return "validation failed: generic error"
    return report.get("message")


def _choose_trace_location(stderr: str, *, target_file: str | None = None) -> tuple[str, int | None]:
    matches = re.findall(r'File "([^"]+)", line (\d+)', stderr)
    if not matches:
        return "", None
    ranked: list[tuple[int, str, int]] = []
    target_suffix = str(target_file or "").strip()
    for index, (file_path, line_text) in enumerate(matches):
        score = 0
        normalized = file_path.replace("\\", "/")
        if target_suffix and normalized.endswith(target_suffix):
            score += 100
        if "/runtime/candidates/" in normalized or "/runtime/versions/" in normalized:
            score += 50
        if "/agent/" in normalized:
            score += 25
        if "unittest/loader.py" in normalized:
            score -= 100
        if "importlib" in normalized:
            score -= 20
        ranked.append((score - index, file_path, int(line_text)))
    ranked.sort(reverse=True)
    _, best_file, best_line = ranked[0]
    return best_file, best_line


def _extract_failure_detail(report: dict[str, Any] | None, *, target_file: str | None = None) -> dict[str, Any]:
    if not report:
        return {}
    stderr = Path(report["stderr_path"]).read_text(encoding="utf-8") if report.get("stderr_path") else ""
    detail: dict[str, Any] = {
        "summary": _validation_failure_summary(report) or "",
        "error_type": "",
        "file": "",
        "line": None,
        "detail": "",
    }
    if "SyntaxError:" in stderr:
        detail["error_type"] = "SyntaxError"
        file_path, line_no = _choose_trace_location(stderr, target_file=target_file)
        if file_path:
            detail["file"] = file_path
            detail["line"] = line_no
        detail_match = re.search(r"SyntaxError:\s*(.+)", stderr)
        if detail_match:
            detail["detail"] = detail_match.group(1).strip()
    elif "ImportError:" in stderr:
        detail["error_type"] = "ImportError"
        file_path, line_no = _choose_trace_location(stderr, target_file=target_file)
        if file_path:
            detail["file"] = file_path
            detail["line"] = line_no
        detail_match = re.search(r"ImportError:\s*(.+)", stderr)
        if detail_match:
            detail["detail"] = detail_match.group(1).strip()
    elif "NameError:" in stderr:
        detail["error_type"] = "NameError"
        file_path, line_no = _choose_trace_location(stderr, target_file=target_file)
        if file_path:
            detail["file"] = file_path
            detail["line"] = line_no
        detail_match = re.search(r"NameError:\s*(.+)", stderr)
        if detail_match:
            detail["detail"] = detail_match.group(1).strip()
    return detail


def _read_text_if_exists(path: Path | None) -> str:
    if path is None or not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _extract_line_snippet(text: str, *, line: int | None, radius: int = 2, max_chars: int = 800) -> str:
    if not text:
        return ""
    lines = text.splitlines()
    if not lines:
        return ""
    if not line or line < 1:
        start = 0
        end = min(len(lines), radius * 2 + 1)
    else:
        start = max(0, line - 1 - radius)
        end = min(len(lines), line + radius)
    snippet = "\n".join(f"{index + 1}: {lines[index]}" for index in range(start, end))
    return snippet[:max_chars]


def _extract_changed_line_numbers(diff_text: str, *, limit: int = 8) -> list[int]:
    line_numbers: list[int] = []
    current_new_line: int | None = None
    for raw_line in diff_text.splitlines():
        header = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", raw_line)
        if header:
            current_new_line = int(header.group(1))
            continue
        if current_new_line is None:
            continue
        if raw_line.startswith("+++ ") or raw_line.startswith("--- "):
            continue
        if raw_line.startswith("+"):
            line_numbers.append(current_new_line)
            current_new_line += 1
        elif raw_line.startswith("-"):
            continue
        else:
            current_new_line += 1
        if len(line_numbers) >= limit:
            break
    return line_numbers


def _excerpt_diff(diff_text: str, *, max_lines: int = 30, max_chars: int = 1600) -> str:
    if not diff_text:
        return ""
    lines = diff_text.splitlines()
    excerpt = "\n".join(lines[:max_lines])
    return excerpt[:max_chars]


def _build_action_result_raw(
    *,
    root: Path,
    attempt: dict[str, Any],
    report: dict[str, Any] | None,
    detail: dict[str, Any],
    before_text_override: str | None = None,
    after_text_override: str | None = None,
    diff_text_override: str | None = None,
) -> dict[str, Any]:
    paths = WorkspacePaths(root)
    candidate_id = str(attempt.get("candidate_id") or "")
    target_file = str(attempt.get("target_file") or "")
    diff_path = Path(str(attempt.get("diff_path") or paths.diff_path(candidate_id)))
    diff_text = diff_text_override if diff_text_override is not None else _read_text_if_exists(diff_path)
    changed_line_numbers = _extract_changed_line_numbers(diff_text)

    before_text = before_text_override or ""
    if not before_text:
        parent_generation = attempt.get("parent_generation")
        if isinstance(parent_generation, int):
            before_path = paths.runtime_versions_dir / f"v{parent_generation:04d}" / target_file
            before_text = _read_text_if_exists(before_path)
    candidate_path = paths.runtime_candidates_dir / candidate_id / target_file
    after_text = after_text_override if after_text_override is not None else _read_text_if_exists(candidate_path)

    failure_file = str(detail.get("file") or "")
    failure_line = detail.get("line")
    failure_text = _read_text_if_exists(Path(failure_file)) if failure_file else ""
    if not failure_text:
        failure_text = after_text

    stderr_text = ""
    stdout_text = ""
    if report:
        stderr_text = _read_text_if_exists(Path(str(report.get("stderr_path") or "")))
        stdout_text = _read_text_if_exists(Path(str(report.get("stdout_path") or "")))

    action_raw = {
        "candidate_id": candidate_id,
        "target_file": target_file,
        "changed_line_numbers": changed_line_numbers,
        "diff_excerpt": _sanitize_prompt_text(_excerpt_diff(diff_text), max_chars=1600),
        "before_snippet": _sanitize_prompt_text(
            _extract_line_snippet(before_text, line=changed_line_numbers[0] if changed_line_numbers else None),
            max_chars=800,
        ),
        "after_snippet": _sanitize_prompt_text(
            _extract_line_snippet(after_text, line=changed_line_numbers[0] if changed_line_numbers else None),
            max_chars=800,
        ),
    }
    result_raw = {
        "command": list(report.get("command") or []) if report else [],
        "returncode": report.get("returncode") if report else None,
        "failure_file": failure_file,
        "failure_line": failure_line,
        "failure_detail": _sanitize_prompt_text(str(detail.get("detail") or ""), max_chars=240),
        "failure_snippet": _sanitize_prompt_text(_extract_line_snippet(failure_text, line=failure_line), max_chars=800),
        "stderr_excerpt": _sanitize_prompt_text(stderr_text, max_chars=1200),
        "stdout_excerpt": _sanitize_prompt_text(stdout_text, max_chars=800),
    }
    return {
        "action_raw": action_raw,
        "result_raw": result_raw,
    }


def _is_meaningful_failure_detail(detail: dict[str, Any], report: dict[str, Any] | None = None) -> bool:
    if not isinstance(detail, dict) or not detail:
        return False
    returncode = (report or {}).get("returncode")
    summary = str(detail.get("summary") or "")
    error_type = str(detail.get("error_type") or "")
    file_path = str(detail.get("file") or "")
    line = detail.get("line")
    detail_text = str(detail.get("detail") or "")
    if error_type or file_path or line or detail_text:
        return True
    if returncode not in (None, 0):
        return True
    if summary and summary != "validation failed: generic error":
        return True
    return False


def _delta_context_for_prompt(delta_context: dict[str, Any] | None) -> dict[str, Any]:
    source = dict(delta_context or {})

    def _trim_failure(item: dict[str, Any]) -> dict[str, Any]:
        action_raw = dict(item.get("action_raw") or {})
        result_raw = dict(item.get("result_raw") or {})
        return {
            "candidate_id": item.get("candidate_id"),
            "target_file": item.get("target_file"),
            "summary": item.get("summary"),
            "error_type": item.get("error_type"),
            "file": item.get("file"),
            "line": item.get("line"),
            "detail": item.get("detail"),
            "action_raw": {
                "candidate_id": action_raw.get("candidate_id"),
                "target_file": action_raw.get("target_file"),
                "changed_line_numbers": list(action_raw.get("changed_line_numbers") or []),
                "diff_excerpt": action_raw.get("diff_excerpt"),
                "before_snippet": action_raw.get("before_snippet"),
                "after_snippet": action_raw.get("after_snippet"),
            },
            "result_raw": {
                "command": list(result_raw.get("command") or []),
                "returncode": result_raw.get("returncode"),
                "failure_file": result_raw.get("failure_file"),
                "failure_line": result_raw.get("failure_line"),
                "failure_detail": result_raw.get("failure_detail"),
                "failure_snippet": result_raw.get("failure_snippet"),
            },
        }

    latest_failure = source.get("latest_failure") or {}
    recent_failures = source.get("recent_failures") or []
    return {
        "latest_failure": _trim_failure(latest_failure) if latest_failure else {},
        "recent_failures": [_trim_failure(item) for item in recent_failures[:3] if isinstance(item, dict)],
        "action_raw": dict(source.get("action_raw") or {}),
        "result_raw": {
            "command": list((source.get("result_raw") or {}).get("command") or []),
            "returncode": (source.get("result_raw") or {}).get("returncode"),
            "failure_file": (source.get("result_raw") or {}).get("failure_file"),
            "failure_line": (source.get("result_raw") or {}).get("failure_line"),
            "failure_detail": (source.get("result_raw") or {}).get("failure_detail"),
            "failure_snippet": (source.get("result_raw") or {}).get("failure_snippet"),
        },
        "repeated_pattern": bool(source.get("repeated_pattern")),
        "repeated_count": int(source.get("repeated_count") or 0),
        "must_avoid_next": list(source.get("must_avoid_next") or []),
    }


def _clear_delta_context_after_success(delta_context: dict[str, Any] | None) -> dict[str, Any]:
    source = dict(delta_context or {})
    recent_failures = [item for item in list(source.get("recent_failures") or []) if isinstance(item, dict)]
    return {
        "latest_failure": {},
        "recent_failures": recent_failures[:3],
        "action_raw": {},
        "result_raw": {},
        "repeated_pattern": False,
        "repeated_count": 0,
        "must_avoid_next": [],
    }

def _build_frame_delta_context(
    *,
    root: Path,
    attempt: dict[str, Any],
    previous_delta_context: dict[str, Any] | None,
    before_text: str,
    after_text: str,
    diff_text: str,
    detail: dict[str, Any],
    report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    latest_failure = {
        "candidate_id": attempt.get("candidate_id"),
        "target_file": attempt.get("target_file"),
        **detail,
        **_build_action_result_raw(
            root=root,
            attempt=attempt,
            report=report,
            detail=detail,
            before_text_override=before_text,
            after_text_override=after_text,
            diff_text_override=diff_text,
        ),
    }
    recent_failures = [latest_failure]
    for previous in list((previous_delta_context or {}).get("recent_failures") or []):
        if not isinstance(previous, dict):
            continue
        if previous.get("candidate_id") == latest_failure.get("candidate_id") and previous.get("detail") == latest_failure.get("detail"):
            continue
        recent_failures.append(previous)
        if len(recent_failures) >= 3:
            break
    repeated_count = sum(
        1
        for failure in recent_failures
        if failure.get("error_type") == latest_failure.get("error_type")
        and failure.get("target_file") == latest_failure.get("target_file")
        and failure.get("detail") == latest_failure.get("detail")
    )
    repeated_pattern = repeated_count >= 2
    must_avoid_next = list((previous_delta_context or {}).get("must_avoid_next") or [])
    if latest_failure.get("error_type") == "SyntaxError":
        if latest_failure.get("line"):
            must_avoid_next.append(f"直近は {latest_failure.get('line')} 行目付近で構文を壊した")
        if latest_failure.get("file"):
            must_avoid_next.append(f"直近の失敗位置は {latest_failure.get('file')}")
    elif latest_failure.get("error_type") == "ChildFrameRequest":
        must_avoid_next.append("子フレーム要求だけで終わらせず、観測か編集の実行結果を返す")
    elif latest_failure.get("error_type") == "NoChange":
        must_avoid_next.append("差分のない提案を繰り返さない")
    elif latest_failure.get("error_type") == "LowValueChange":
        must_avoid_next.append("表層的な文言変更だけで終わらせない")
    elif latest_failure.get("error_type") == "ProtectedPath":
        must_avoid_next.append("保護対象には触れない")
    if repeated_pattern:
        must_avoid_next.append("同型失敗が続いているため、同じ変更様式を避ける")
        if latest_failure.get("error_type") == "ChildFrameRequest":
            must_avoid_next.append("同じ child_goals の再分解を続けず、まず read_file/search_code を行う")
        if latest_failure.get("error_type") == "NoChange":
            must_avoid_next.append("次は必ず差分を伴う最小変更か、編集前の追加観測を行う")
    unique_must_avoid = []
    for item in must_avoid_next:
        text = _safe_brief_text(item, max_chars=240)
        if text and text not in unique_must_avoid:
            unique_must_avoid.append(text)
    return {
        "latest_failure": latest_failure,
        "recent_failures": recent_failures[:3],
        "action_raw": latest_failure.get("action_raw") or {},
        "result_raw": latest_failure.get("result_raw") or {},
        "repeated_pattern": repeated_pattern,
        "repeated_count": repeated_count,
        "must_avoid_next": unique_must_avoid[:6],
    }
