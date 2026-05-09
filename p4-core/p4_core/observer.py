from __future__ import annotations

import json
import re
import time
from typing import Any

from p4_core.workspace import append_jsonl, append_session_event, now_iso, read_json, read_jsonl


def _observer_enabled(self) -> bool:
    return bool(self.runtime_config.get("observer_enabled"))


def _maybe_record_observer_note(
    self,
    *,
    session_id: str,
    turn_id: str,
    queue_id: str,
    step_index: int,
    user_message: str,
    tool_name: str,
    tool_result: dict[str, Any],
    steps: list[dict[str, Any]],
    prompt_snapshot: str,
    assistant_message: str,
) -> None:
    if not self._observer_enabled():
        return
    model = str(self.runtime_config.get("observer_model") or self.router.models.get("fast") or "fast")
    options = dict(self.runtime_config.get("observer_options") or {"temperature": 0.2, "num_predict": 220})
    step_summary = {
        "tool_name": tool_name,
        "tool_result": tool_result,
        "step_count": len(steps),
        "latest_llm_message": assistant_message[-2000:],
        "context_excerpt": prompt_snapshot[-3000:],
    }
    prompt = (
        "あなたはAIエージェント研究開発のエキスパートであり、P4実験の実況解説者です。"
        "システムとLLMのやりとりを観察し、直近ステップで何が起きたのか、"
        "それがAIエージェントの設計・制御・失敗解析の観点でどう見えるのかを日本語で解説してください。"
        "特に、LLMが失敗しそうな出力をした場合は、なぜそうなったかを、渡されたコンテキスト、直近のLLM応答、"
        "ツール結果の不足、指示の衝突や混入の観点から点検してください。\n"
        "現段階では介入や指示はせず、観測と解説だけを行ってください。\n"
        "次の4点だけを簡潔に書いてください: 1. 起きたこと 2. 失敗要因の仮説 3. コンテキスト点検 4. 次に確認すべきこと。\n\n"
        f"ユーザー依頼: {user_message}\n\n"
        f"直近ステップ:\n{json.dumps(step_summary, ensure_ascii=False)[:6000]}"
    )
    try:
        response = self.llm_backend.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            options=options,
            timeout_seconds=60,
        )
        content = str(response.get("content_text") or response.get("content") or "").strip()
        thinking_text = str(response.get("thinking_text") or "")
        if not self._usable_japanese_observer_text(content):
            content = self._deterministic_step_commentary(
                user_message=user_message,
                tool_name=tool_name,
                tool_result=tool_result,
                step_count=len(steps),
                prompt_snapshot=prompt_snapshot,
                assistant_message=assistant_message,
                raw_observer_output=content or thinking_text,
            )
        append_session_event(
            self.root,
            session_id,
            {
                "type": "observer_note",
                "role": "observer",
                "content": content,
                "model": response.get("model", model),
                "code": "live_commentator",
                "reason_code": "step_commentary",
                "details": {
                    "prompt": prompt,
                    "raw_response": str(response.get("content") or ""),
                    "content_text": str(response.get("content_text") or ""),
                    "thinking_text": thinking_text,
                },
                "turn_id": turn_id,
                "queue_id": queue_id,
                "step_index": step_index,
            },
        )
    except Exception as exc:
        append_session_event(
            self.root,
            session_id,
            {
                "type": "observer_note",
                "role": "observer",
                "content": f"監視者の解説生成に失敗しました: {exc}",
                "code": "live_commentator",
                "reason_code": "observer_error",
                "turn_id": turn_id,
                "queue_id": queue_id,
                "step_index": step_index,
            },
        )


def _usable_japanese_observer_text(self, content: str) -> bool:
    text = str(content or "").strip()
    if not text:
        return False
    if any(
        marker in text
        for marker in (
            "Analyze the",
            "User Request",
            "R&D Insight",
            "**Role:**",
            "**Task:**",
            "Here's a thinking process",
            "Deconstruct the Log",
            "Input Log:",
            "Output Format:",
        )
    ):
        return False
    japanese_chars = sum(1 for char in text if "\u3040" <= char <= "\u30ff" or "\u4e00" <= char <= "\u9fff")
    return japanese_chars >= 8


def _deterministic_step_commentary(
    self,
    *,
    user_message: str,
    tool_name: str,
    tool_result: dict[str, Any],
    step_count: int,
    prompt_snapshot: str = "",
    assistant_message: str = "",
    raw_observer_output: str = "",
) -> str:
    ok = bool(tool_result.get("ok"))
    result_summary = "成功" if ok else "失敗"
    target = str(tool_result.get("path") or tool_result.get("command") or tool_name)
    extra = ""
    if raw_observer_output:
        extra = " なお、解説LLMは日本語本文ではなく思考過程または英語分析を返したため、システム側で日本語解説に置き換えています。"
    context_risks = self._context_risk_summary(prompt_snapshot=prompt_snapshot, assistant_message=assistant_message)
    return (
        f"1. 起きたこと: ユーザー依頼「{user_message[:80]}」に対して、STEP{step_count}で `{tool_name}` を実行し、結果は{result_summary}でした。対象は `{target}` です。\n"
        f"2. 失敗要因の仮説: {context_risks} LLMの発話だけでなく、システムは tool_result を時系列証拠として記録しています。ここではツール結果が次の判断材料になります。{extra}\n"
        f"3. コンテキスト点検: 渡されたコンテキストには直近プロンプト、LLM応答、tool_result が含まれます。LLM応答が予定や未実行コマンドを完了のように扱っていないかを確認する必要があります。\n"
        "4. 次に確認すべきこと: このステップの結果だけで完了できるか、または次のツール実行やシステム判定が必要かを確認します。"
    )


def _context_risk_summary(self, *, prompt_snapshot: str, assistant_message: str) -> str:
    prompt_text = str(prompt_snapshot or "")
    assistant_text = str(assistant_message or "")
    risks: list[str] = []
    if len(prompt_text) > 12000:
        risks.append("プロンプトが長く、重要な制約や証拠が埋もれた可能性があります。")
    if "次のJSONだけ" in prompt_text and "```" in assistant_text:
        risks.append("JSONのみ要求とMarkdownコードブロックが衝突し、形式違反を誘発した可能性があります。")
    if "未実行" in prompt_text or "完了がブロックされました" in prompt_text:
        risks.append("過去のブロック通知が文脈に残り、LLMがエラー説明に引っ張られた可能性があります。")
    if "run_command" in prompt_text and "python3" in assistant_text and "tool_name" not in assistant_text:
        risks.append("LLMが実行すべきコマンドを文章で述べ、ツール呼び出しへ変換できていない可能性があります。")
    if not risks:
        risks.append("明確なコンテキスト異常は見えませんが、証拠不足とタスク未完了を分けて見る必要があります。")
    return " ".join(risks)


def _record_observer_judgement_note(
    self,
    *,
    session_id: str,
    turn_id: str,
    queue_id: str,
    step_index: int,
    user_message: str,
    assistant_message: str,
    system_decision: str,
    reason_code: str,
    prompt_snapshot: str = "",
    steps: list[dict[str, Any]] | None = None,
) -> None:
    if not self._observer_enabled():
        return
    context_risks = self._context_risk_summary(prompt_snapshot=prompt_snapshot, assistant_message=assistant_message)
    evidence_summary = json.dumps((steps or [])[-3:], ensure_ascii=False)[:2000]
    append_session_event(
        self.root,
        session_id,
        {
            "type": "observer_note",
            "role": "observer",
            "content": (
                f"1. 入力とLLM応答: ユーザー依頼は「{user_message[:80]}」です。LLMはSTEP{step_index}で、完了または次の行動に進もうとする回答を出しました。\n"
                f"2. システム判定: {system_decision}\n"
                f"3. 失敗要因の仮説: {context_risks}\n"
                f"4. コンテキスト点検: reason_code は `{reason_code}` です。直近証拠は「{evidence_summary}」です。未実行コマンドやjudge出力不正がある場合、完了を止める判断自体は妥当です。ただし judge がJSON判定を返せなかったケースは、根拠不足そのものではなく判定器出力の問題として分けて見るべきです。"
            ),
            "code": "live_commentator",
            "reason_code": "system_judgement_commentary",
            "details": {
                "assistant_message": assistant_message[:4000],
                "system_decision": system_decision,
                "system_reason_code": reason_code,
                "context_excerpt": prompt_snapshot[-4000:],
                "recent_steps": (steps or [])[-3:],
            },
            "turn_id": turn_id,
            "queue_id": queue_id,
            "step_index": step_index,
        },
    )


def _record_observer_llm_output_issue_note(
    self,
    *,
    session_id: str,
    turn_id: str,
    queue_id: str,
    step_index: int,
    user_message: str,
    assistant_message: str,
    parse_issue: str,
    prompt_snapshot: str,
    steps: list[dict[str, Any]],
) -> None:
    if not self._observer_enabled():
        return
    context_risks = self._context_risk_summary(prompt_snapshot=prompt_snapshot, assistant_message=assistant_message)
    append_session_event(
        self.root,
        session_id,
        {
            "type": "observer_note",
            "role": "observer",
            "content": (
                f"1. 起きたこと: STEP{step_index}でLLM応答は返りましたが、システムが期待する tool_call JSON として解釈できませんでした。parse_issue は `{parse_issue}` です。\n"
                f"2. 失敗要因の仮説: {context_risks}\n"
                f"3. コンテキスト点検: ユーザー依頼は「{user_message[:100]}」です。直近の実行済みステップは {len(steps)} 件で、LLM応答は「{assistant_message[:300]}」から始まっています。文章で計画を述べるだけになっていないか、JSON-only 指示とMarkdown/長文コードが衝突していないかを確認します。\n"
                "4. 次に確認すべきこと: この応答はツール実行に進んでいないため、次のシステム判定で未実行コマンドや証拠不足としてブロックされる可能性があります。"
            ),
            "code": "live_commentator",
            "reason_code": "llm_output_issue_commentary",
            "details": {
                "parse_issue": parse_issue,
                "assistant_message": assistant_message[:4000],
                "context_excerpt": prompt_snapshot[-4000:],
                "recent_steps": steps[-3:],
            },
            "turn_id": turn_id,
            "queue_id": queue_id,
            "step_index": step_index,
        },
    )

