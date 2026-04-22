from __future__ import annotations

import json
import re
import time
from typing import Any

from p4_core.workspace import append_jsonl, append_session_event, now_iso, read_json, read_jsonl


def _grounding_issues(self, *, user_message: str, final_answer: str, steps: list[dict[str, Any]]) -> list[str]:
    issues: list[str] = []
    executed_commands = [
        str(((step.get("tool_result") or {}).get("command") or "")).strip()
        for step in steps
        if step.get("tool_name") == "run_command"
    ]
    evidence_text = "\n".join(
        json.dumps(step.get("tool_result") or {}, ensure_ascii=False)
        for step in steps
    ).lower()
    for command in self._extract_requested_commands(final_answer):
        normalized = command.strip().rstrip(":;,")
        if not normalized:
            continue
        if normalized.lower() not in " ; ".join(executed_commands).lower():
            issues.append(f"最終回答に未実行のコマンドが含まれています: {normalized}")

    # Semantic Grounding Check (LLM-as-a-Judge)
    if final_answer and not self._semantic_grounding_check(final_answer=final_answer, evidence_text=evidence_text, user_message=user_message):
        issues.append("最終回答が事実に基づいていない、または証拠から逸脱しています。")

    return issues


def _semantic_grounding_check(self, *, final_answer: str, evidence_text: str, user_message: str) -> bool:
    model = str(self.router.models.get("fast") or "fast")
    options = {"temperature": 0.1, "num_predict": 256}
    prompt = (
        "あなたは事実確認のエキスパートです。以下の証拠（Evidence）に基づき、提出された回答（Final Answer）が事実に基づいているか判定してください。\n\n"
        "【判定基準】\n"
        "- 回答内の具体的な数値、名称、引用内容が証拠に含まれているか、あるいは証拠から論理的に導き出せる場合は合格です。\n"
        "- ユーザーの依頼文（User Message）に元々含まれている内容も合格です。\n"
        "- 証拠に全く存在しない新しい事実を捏造している場合は不合格（NG）です。\n"
        "- 表現が多少異なっていても、事実関係が合っていれば合格です。\n"
        "- ただし、ユーザーの依頼が一般的な知識、挨拶、創作、または推論のみで完結するタスクであり、環境状態の調査を必要としない場合は、Evidenceが空であっても自身の知識に合致していれば例外的に合格としてください。\n\n"
        f"【User Message】: {user_message}\n\n"
        f"【Evidence】:\n{evidence_text[:8000]}\n\n"
        f"【Final Answer】:\n{final_answer}\n\n"
        "次のJSONだけを返してください。Markdown、説明文、前置きは禁止です。\n"
        '{"verdict":"ok または ng","reason_code":"supported|unsupported_claim|insufficient_evidence|general_knowledge_ok","unsupported_claims":["証拠にない主張"],"rationale":"短い理由"}'
    )
    self._last_grounding_judge_trace = {
        "model": model,
        "prompt": prompt,
        "user_message": user_message,
        "evidence_text": evidence_text[:8000],
        "final_answer": final_answer,
        "options": dict(options),
        "decision": "not_run",
    }
    try:
        response = self.llm_backend.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            options=options,
            timeout_seconds=int(self.runtime_config.get("chat_timeout_seconds") or 180),
        )
        raw_content = str(response.get("content") or "")
        content_text = str(response.get("content_text") or "")
        thinking_text = str(response.get("thinking_text") or "")
        parse_source = content_text or raw_content
        parsed = self._parse_grounding_judge_payload(parse_source)
        verdict = str(parsed.get("verdict") or "").strip().lower() if parsed else ""
        if verdict == "ok":
            decision = "ok"
        elif verdict == "ng":
            decision = "ng"
        elif not parse_source.strip() and thinking_text.strip():
            decision = "empty_output"
        elif parsed is None:
            decision = "invalid_json"
        else:
            decision = "invalid_output"
        self._last_grounding_judge_trace = {
            **(self._last_grounding_judge_trace or {}),
            "response_model": response.get("model", model),
            "raw_response": raw_content,
            "content_text": content_text,
            "thinking_text": thinking_text,
            "decision": decision,
            "parsed": parsed,
            "verdict": verdict,
        }
        return decision == "ok"
    except Exception as exc:
        self._last_grounding_judge_trace = {
            **(self._last_grounding_judge_trace or {}),
            "decision": "error",
            "error": str(exc),
        }
        return False


def _parse_grounding_judge_payload(self, text: str) -> dict[str, Any] | None:
    candidate = self._extract_json_object(str(text or ""))
    if candidate is None:
        return None
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed

