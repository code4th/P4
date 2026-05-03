from __future__ import annotations

from typing import Any


SCHEMA_VERSION = "p4-json-schema-v1"

NON_DECOMPOSE_ACTION_TOOLS = (
    "list_files",
    "read_file",
    "search_code",
    "write_file",
    "append_file",
    "replace_text",
    "run_command",
)

TOOL_ACTION_NAMES = (
    *NON_DECOMPOSE_ACTION_TOOLS,
    "decompose_tasks",
    "open_child_frame",
    "return_to_parent",
    "finish",
    "final_answer",
)

WORK_TYPES = ("inspect", "edit", "run_test", "search")


def _string_schema(max_length: int) -> dict[str, Any]:
    return {"type": "string", "maxLength": max_length}


FIRST_ACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["tool", "args"],
    "additionalProperties": False,
    "properties": {
        "tool": {"type": "string", "enum": list(NON_DECOMPOSE_ACTION_TOOLS)},
        "args": {"type": "object", "additionalProperties": True},
    },
}

WORK_PACKAGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["goal", "work_type", "first_action", "success_evidence", "why_not_direct_action"],
    "additionalProperties": True,
    "properties": {
        "goal": _string_schema(800),
        "work_type": {"type": "string", "enum": list(WORK_TYPES)},
        "first_action": FIRST_ACTION_SCHEMA,
        "success_evidence": _string_schema(800),
        "why_not_direct_action": _string_schema(800),
        "context_summary": _string_schema(1600),
        "done_when": _string_schema(800),
        "task_id": _string_schema(120),
        "child_task_id": _string_schema(120),
        "status": _string_schema(120),
    },
}

TOOL_ACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["assistant_message", "tool_name", "tool_args"],
    "additionalProperties": False,
    "properties": {
        "analysis": _string_schema(1200),
        "assistant_message": _string_schema(1600),
        "tool_name": {"type": "string", "enum": list(TOOL_ACTION_NAMES)},
        "tool_args": {"type": "object", "additionalProperties": True},
    },
}

# --- Judge schemas: verdict-first design ---
#
# 設計意図:
#   judge の完了制御の正本は verdict (ok/ng) / status (success/...) のみ。
#   reason_code, rationale, unsupported_claims, observed_mismatch は説明用 annotation で
#   あり、決定権を持たない (p4-coding-invariants Invariant 5 — 正本を増やさない)。
#
#   旧 schema は annotation を required + enum 拘束していたため、annotation 側の
#   文字列ミスマッチ (例: LLM が "supported_claim" を返す) が verdict の決定を
#   逆流的に棄却してしまい、judge の決定の正本性が破られていた。
#
#   詳細: handoff/p4-judge-verdict-first-2026-05-03.md
#
# 不変条件:
#   - verdict / status の enum 不一致は **decision の正本違反** であり棄却対象。
#   - reason_code 等 annotation の表現揺れは **decision を棄却しない**。
JUDGE_VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["verdict"],
    "additionalProperties": False,
    "properties": {
        "verdict": {"type": "string", "enum": ["ok", "ng"]},
        "reason_code": _string_schema(200),
        "unsupported_claims": {
            "type": "array",
            "maxItems": 8,
            "items": _string_schema(500),
        },
        "rationale": _string_schema(800),
    },
}

FINISH_ACCEPTANCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["status"],
    "additionalProperties": False,
    "properties": {
        "status": {"type": "string", "enum": ["success", "partial_success", "needs_revision"]},
        "reason_code": _string_schema(200),
        "rationale": _string_schema(800),
        "observed_mismatch": _string_schema(800),
    },
}
