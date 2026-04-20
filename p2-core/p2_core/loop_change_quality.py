from __future__ import annotations

import ast
import difflib
from pathlib import Path
from typing import Any

from p2_core.loop_attempt_meta import _attempt_is_terminal, _classify_decision_reason
from p2_core.loop_delta import _validation_failure_summary
from p2_core.loop_utils import _line_counts, _safe_brief_text
from p2_core.workspace import build_status_snapshot, read_validation_report


class _ConstantNeutralizer(ast.NodeTransformer):
    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        return ast.copy_location(ast.Constant(value="__P2_CONST__"), node)


def _normalized_ast_signature(text: str) -> str | None:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    normalized = _ConstantNeutralizer().visit(tree)
    ast.fix_missing_locations(normalized)
    return ast.dump(normalized, include_attributes=False)


def _normalize_summary_text(text: str) -> str:
    return " ".join(text.split())


def _recent_attempt_memory(root: Path, *, limit: int = 4) -> str:
    snapshot = build_status_snapshot(root, attempt_limit=max(limit + 2, 6), history_limit=6)
    attempts = snapshot.get("recent_attempts", [])
    rows: list[str] = []
    for attempt in reversed(attempts):
        if not _attempt_is_terminal(attempt.get("status")):
            continue
        candidate_id = str(attempt.get("candidate_id"))
        validation = read_validation_report(root, candidate_id)
        validation_summary = _validation_failure_summary(validation)
        if validation and validation.get("passed"):
            validation_text = "validation=pass"
        elif validation_summary:
            validation_text = validation_summary
        else:
            validation_text = "validation=no-record"
        decision_type = _classify_decision_reason(attempt.get("decision_reason"))
        target_file = _safe_brief_text(attempt.get("target_file") or "unknown", max_chars=160)
        search_mode = _safe_brief_text(attempt.get("search_mode") or "unknown", max_chars=80)
        rows.append(
            f"- {candidate_id} status={attempt.get('status')} target={target_file} "
            f"search_mode={search_mode} "
            f"decision_type={decision_type} {validation_text}"
        )
        if len(rows) >= limit:
            break
    if not rows:
        return "まだ有効な試行履歴はありません。"
    return "\n".join(rows)


def _low_value_change_reason(
    *,
    root: Path,
    candidate_id: str,
    before_text: str,
    after_text: str,
    diff_text: str,
    change_summary: dict[str, Any] | None,
) -> str | None:
    added, removed = _line_counts(diff_text)
    total_changed_lines = added + removed
    before_signature = _normalized_ast_signature(before_text)
    after_signature = _normalized_ast_signature(after_text)
    if before_signature and after_signature and before_signature == after_signature and total_changed_lines <= 4:
        return "低価値な変更として拒否しました: 定数や文言の微修正だけで、構造的な改善が見当たりません。"

    current_summary = _normalize_summary_text(str((change_summary or {}).get("summary") or ""))
    if not current_summary:
        return None
    snapshot = build_status_snapshot(root, attempt_limit=8, history_limit=6)
    for recent in reversed(snapshot.get("recent_attempts", [])):
        if recent.get("candidate_id") == candidate_id:
            continue
        if not _attempt_is_terminal(recent.get("status")):
            continue
        recent_summary = _normalize_summary_text(str((recent.get("change_summary") or {}).get("summary") or ""))
        if not recent_summary:
            continue
        similarity = difflib.SequenceMatcher(a=current_summary, b=recent_summary).ratio()
        if similarity >= 0.9 and total_changed_lines <= 6:
            return (
                "低価値な変更として拒否しました: "
                f"直近の {recent.get('candidate_id')} と改善要約が近すぎます。別の改善方向を選んでください。"
            )
    return None
