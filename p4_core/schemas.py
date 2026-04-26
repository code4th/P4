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

JUDGE_VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["verdict", "reason_code", "unsupported_claims", "rationale"],
    "additionalProperties": False,
    "properties": {
        "verdict": {"type": "string", "enum": ["ok", "ng"]},
        "reason_code": {
            "type": "string",
            "enum": ["supported", "unsupported_claim", "insufficient_evidence", "general_knowledge_ok"],
        },
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
    "required": ["status", "reason_code", "rationale", "observed_mismatch"],
    "additionalProperties": False,
    "properties": {
        "status": {"type": "string", "enum": ["success", "partial_success", "needs_revision"]},
        "reason_code": {
            "type": "string",
            "enum": ["supported", "obvious_mismatch", "insufficient_semantic_evidence"],
        },
        "rationale": _string_schema(800),
        "observed_mismatch": _string_schema(800),
    },
}
