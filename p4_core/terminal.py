from __future__ import annotations

import json
import re
import time
from typing import Any

from p4_core.workspace import active_session_id, append_jsonl, append_session_event, enqueue_message, now_iso, read_json, read_jsonl


def run_terminal_agent(
    self,
    content: str,
    *,
    model: str,
    shell_name: str,
    session_id: str | None = None,
) -> dict[str, Any]:
    session_id = session_id or active_session_id(self.root)
    if self._is_runtime_identity_query(content):
        result = self._answer_runtime_identity_query(content, session_id=session_id)
        answer = str(result.get("answer") or "")
        return {
            "ok": True,
            "route": "runtime_identity",
            "run": {
                "ok": True,
                "processed": 1,
                "last_result": {
                    "ok": True,
                    "route": "runtime_identity",
                    "final_answer": answer,
                    "evidence": result.get("evidence") or {},
                },
                "pending_queue": 0,
                "runtime_identity": result,
            },
            "shell": shell_name,
            "execution_root": str(self.base_execution_root),
            "model": "",
        }
    payload = enqueue_message(self.root, content, session_id=session_id)
    terminal_model = self._resolve_terminal_model(model)
    run_result = self.run_until_idle(
        max_work_items=1,
        selection_override={
            "role": "terminal",
            "model": terminal_model,
            "reason": f"terminal agent mode via {shell_name}",
        },
        extra_prompt=(
            "You are in terminal agent mode. Prefer terminal commands when they are the most direct path. "
            "Use exactly one command per run_command step. Do not chain commands with &&, ||, ;, or newlines. "
            f"For run_command, prefer shell='{shell_name}'. "
            "The runtime assigns a dedicated LLM workspace for this turn. Use relative paths inside that workspace. "
            "If run_command reports that the requested shell is unavailable, stop immediately and explain that shell is unavailable. "
            "You may also use list_files, read_file, search_code, and write_file when they are simpler than shell."
        ),
    )
    payload["run"] = {
        **run_result,
        "shell": shell_name,
        "execution_root": str(self.base_execution_root),
        "model": terminal_model,
    }
    return payload


def _resolve_terminal_model(self, preferred_model: str) -> str:
    available: set[str] = set()
    if hasattr(self.llm_backend, "list_models"):
        try:
            payload = self.llm_backend.list_models()
            available = {
                str(item.get("name") or "").strip()
                for item in payload.get("models") or []
                if str(item.get("name") or "").strip()
            }
        except Exception:
            available = set()
    for candidate in self.router.terminal_fallback_chain(preferred_model):
        if not available or candidate in available:
            return candidate
    return self.router.models.get("terminal") or preferred_model


def _preferred_shell_from_extra_prompt(self, extra_prompt: str | None) -> str:
    text = str(extra_prompt or "")
    match = re.search(r"shell='([^']+)'", text)
    if match:
        return str(match.group(1) or "auto")
    return "auto"


def _controller_terminal_finish(
    self,
    *,
    selection: dict[str, str],
    goal_text: str,
    user_message: str,
    steps: list[dict[str, Any]],
) -> str | None:
    if str(selection.get("role") or "") != "terminal":
        return None
    if self._missing_requested_commands(user_message=user_message, steps=steps):
        return None
    if not any(str(step.get("tool_name") or "") == "run_command" for step in steps):
        return None
    final_answer = self._deterministic_terminal_final_answer(
        goal_text=goal_text,
        user_message=user_message,
        steps=steps,
    )
    if not final_answer:
        return None
    evidence_text = "\n".join(
        json.dumps(step.get("tool_result") or {}, ensure_ascii=False)
        for step in steps
    ).lower()
    if self._terminal_answer_is_direct_evidence(final_answer=final_answer, steps=steps):
        return final_answer
    if not self._semantic_grounding_check(final_answer=final_answer, evidence_text=evidence_text, user_message=user_message):
        return None
    return final_answer


def _terminal_answer_is_direct_evidence(self, *, final_answer: str, steps: list[dict[str, Any]]) -> bool:
    if not final_answer:
        return False
    for step in steps:
        if str(step.get("tool_name") or "") != "run_command":
            continue
        payload = step.get("tool_result") or {}
        if not bool(payload.get("ok")):
            continue
        stdout = str(payload.get("stdout") or "").strip()
        command = str(payload.get("command") or "").strip()
        stdout_preview = self._output_preview(stdout, max_lines=12, max_chars=700)
        if stdout and stdout[:200] in final_answer:
            return True
        if stdout_preview and stdout_preview in final_answer:
            return True
        if command and command in final_answer and stdout:
            return True
    return False


def _deterministic_terminal_final_answer(self, *, goal_text: str, user_message: str, steps: list[dict[str, Any]]) -> str | None:
    evidence = self._normalize_run_command_evidence(steps)
    if not evidence:
        return None
    goal = (goal_text or user_message).strip()
    parts: list[str] = []
    for row in evidence[-4:]:
        command = str(row.get("command") or "").strip()
        stdout_preview = self._output_preview(str(row.get("stdout") or ""), max_lines=12, max_chars=700)
        stderr_preview = self._output_preview(str(row.get("stderr") or ""), max_lines=2, max_chars=160)
        if bool(row.get("ok")):
            if stdout_preview:
                parts.append(f"{command}: {stdout_preview}")
            else:
                cwd = str(row.get("cwd") or "").strip()
                suffix = f" cwd={cwd}" if cwd else "success"
                parts.append(f"{command}: {suffix}".strip())
        else:
            failure_detail = stderr_preview or f"returncode={row.get('returncode')}"
            parts.append(f"{command}: failed ({failure_detail})")
    if not parts:
        return None
    if len(parts) == 1:
        first = parts[0]
        if ":" in first:
            _, detail = first.split(":", 1)
            return detail.strip()[:1200]
    if len(parts) == 2:
        return " / ".join(parts)[:1200]
    lead = f"{goal}: " if goal else ""
    return (lead + " / ".join(parts))[:1200]


def _synthesize_terminal_final_answer(self, *, goal_text: str, user_message: str, steps: list[dict[str, Any]]) -> str | None:
    deterministic = self._deterministic_terminal_final_answer(
        goal_text=goal_text,
        user_message=user_message,
        steps=steps,
    )
    if deterministic:
        return deterministic
    evidence = self._normalize_run_command_evidence(steps)
    if not evidence:
        return None
    model = str(self.router.models.get("fast") or self.config.get("models", {}).get("fast") or "glm-4.7-flash:latest")
    options = dict(self.ollama_options.get("fast", {}))
    prompt = (
        "あなたはターミナル実行結果の要約を担当します。\n"
        "証拠（evidence）に基づいた短い回答をプレーンテキストで返してください。\n"
        "ユーザーの目標と以下の証拠のみを使用してください。\n"
        "証拠に存在しない事実は絶対に含めないでください。\n"
        "証拠が不足している場合は、その旨を簡潔に伝えてください。\n\n"
        f"ユーザーの目標:\n{(goal_text or user_message).strip()}\n\n"
        f"正規化された証拠:\n{json.dumps(evidence, ensure_ascii=False, indent=2)}\n"
    )
    try:
        reply = self.llm_backend.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            options=options,
            timeout_seconds=int(self.runtime_config.get("chat_timeout_seconds") or 180),
        )
        text = str(reply.get("content") or "").strip()
        return text or self._deterministic_terminal_final_answer(
            goal_text=goal_text,
            user_message=user_message,
            steps=steps,
        )
    except Exception:
        return self._deterministic_terminal_final_answer(
            goal_text=goal_text,
            user_message=user_message,
            steps=steps,
        )


def _normalize_run_command_evidence(self, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for step in steps:
        if str(step.get("tool_name") or "") != "run_command":
            continue
        payload = step.get("tool_result") or {}
        if not isinstance(payload, dict):
            continue
        rows.append(
            {
                "command": str(payload.get("command") or ""),
                "ok": bool(payload.get("ok")),
                "returncode": payload.get("returncode"),
                "cwd": str(payload.get("cwd") or ""),
                "stdout": str(payload.get("stdout") or "")[-1500:],
                "stderr": str(payload.get("stderr") or "")[-800:],
            }
        )
    return rows


def _output_preview(self, text: str, *, max_lines: int, max_chars: int) -> str:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    if not lines:
        return ""
    if len(lines) <= max_lines:
        selected = lines
    else:
        if max_lines <= 1:
            selected = [lines[0]]
        else:
            selected = []
            last_index = len(lines) - 1
            for offset in range(max_lines):
                raw_index = round((last_index * offset) / (max_lines - 1))
                item = lines[raw_index]
                if not selected or selected[-1] != item:
                    selected.append(item)
    preview = " / ".join(selected)
    return preview[:max_chars].strip()
