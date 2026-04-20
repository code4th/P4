from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any


def purpose_brief(purpose: dict[str, Any]) -> str:
    statement = str(purpose.get("statement") or "").strip()
    objectives = purpose.get("root_objectives") or []
    objectives_text = "; ".join(str(item) for item in objectives if str(item).strip())
    if objectives_text and statement:
        return f"{statement}\nroot_objectives: {objectives_text}"
    if statement:
        return statement
    return "unassigned"


def build_purpose_initiative_payload(
    *,
    state: dict[str, Any],
    moment: datetime,
    reason: str,
    recent_heartbeat: str | None,
) -> tuple[dict[str, Any], str]:
    prompt = {
        "task": "purpose_initiative",
        "constraints": {
            "output_keys": [
                "problem_statement",
                "diagnosis",
                "candidates",
                "selected_candidate",
                "selection_reason",
                "why",
                "expected_effect",
                "risk_level",
                "note",
            ],
            "must_not_require_external_learning": True,
            "must_be_small_and_safe": True,
            "must_not_change_master_purpose": True,
            "must_be_internal_only": True,
            "must_not_suggest_personal_or_social_actions": True,
            "candidate_count": 3,
        },
        "context": {
            "purpose": purpose_brief(state.get("purpose", {})),
            "reason": reason,
            "current_focus": state.get("current_focus"),
            "recent_heartbeat": recent_heartbeat,
            "inbox_queued": state.get("inbox_queued", 0),
            "action_queued": state.get("action_queued", 0),
            "task_pending": state.get("task_pending", 0),
            "task_deferred": state.get("task_deferred", 0),
            "task_in_progress": state.get("task_in_progress", 0),
            "last_tick_summary": state.get("last_tick_summary"),
            "last_heartbeat_at": state.get("last_heartbeat_at"),
            "previous_problem_statement": state.get("previous_problem_statement"),
            "previous_selected_candidate": state.get("previous_selected_candidate"),
            "previous_selection_reason": state.get("previous_selection_reason"),
        },
        "meta": {
            "moment": moment.isoformat(),
            "mode": state.get("mode"),
        },
    }
    system_prompt = (
        "You are P1's internal thought process. "
        "Read the master purpose from the provided context. "
        "Return strict JSON only. "
        "First identify the actual current problem in the P1 situation. "
        "Then think in terms of alternatives: generate 3 tiny, safe, INTERNAL ways to address that problem, "
        "compare them directly by evaluating their concreteness, repetition risk, and diagnostic value, "
        "and select the best one. "
        "Do not mention external learning as a requirement. "
        "Do not change the master purpose. "
        "Do not suggest contacting people, sending messages, browsing, or any personal/social action. "
        "Prefer concrete but small internal next steps such as reviewing purpose alignment, preparing a plan, "
        "or identifying a safe internal improvement candidate. "
        "Include a problem_statement and diagnosis, candidate ids, short summaries, next steps, why each candidate is chosen or rejected, "
        "and a final selected_candidate with selection_reason. "
        "If recent thought was shallow or repetitive, explicitly state the cause (e.g., 'repeated use of same phrase', 'lack of comparison'), "
        "then choose a different internal strategy that breaks the repetition and adds diagnostic depth."
    )
    return prompt, system_prompt


def fallback_purpose_candidates() -> list[dict[str, Any]]:
    return [
        {
            "id": "review",
            "summary": "Identify why the current P1 behavior is too shallow.",
            "problem": "P1 is repeatedly recording purpose confirmation without forming a deeper internal diagnosis.",
            "next_step": {
                "kind": "self_diagnosis",
                "scope": "internal",
                "description": "Identify the current gap between the desired thinking behavior and the actual initiative pattern.",
                "status": "queued",
            },
            "why": "The current issue is not action safety but lack of real diagnosis.",
            "expected_effect": "Reveal what prevents deeper thought and what must change internally.",
            "risk_level": "low",
        },
        {
            "id": "map_gap",
            "summary": "Map the reasons the system keeps producing shallow initiative.",
            "problem": "The thought loop has been producing one-step purpose reviews instead of actual comparison and selection.",
            "next_step": {
                "kind": "thought_gap_mapping",
                "scope": "internal",
                "description": "Map the internal reasons the current thought loop does not yet show genuine deliberation.",
                "status": "queued",
            },
            "why": "A specific diagnosis is needed before any repair or action.",
            "expected_effect": "Surface the smallest repairable cause of shallow thinking.",
            "risk_level": "low",
        },
        {
            "id": "compare",
            "summary": "Compare three internal interpretations of what P1 should think about next.",
            "problem": "P1 needs to choose among competing internal interpretations instead of defaulting to the first safe phrase.",
            "next_step": {
                "kind": "thought_comparison",
                "scope": "internal",
                "description": "Generate and compare three internal interpretations of the most important next thought.",
                "status": "queued",
            },
            "why": "Comparison is the missing step between a prompt and real thinking.",
            "expected_effect": "Make the next tick show a real choice rather than a default phrase.",
            "risk_level": "low",
        },
    ]
