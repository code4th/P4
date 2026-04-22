from __future__ import annotations

import json
import re
import time
from typing import Any

from p4_core.config_defaults import DEFAULT_TOOL_CONTENT_CHUNK_BYTES
from p4_core.workspace import append_jsonl, append_session_event, now_iso, read_json, read_jsonl


def _chat_with_repair(self, *, role: str, model: str, prompt: str, session_id: str | None = None) -> dict[str, Any]:
    timeout_seconds = int(self.runtime_config.get("chat_timeout_seconds") or 180)
    retry_limit = int(self.runtime_config.get("json_retry_limit") or 0)
    thinking_only_repair_limit = int(self.runtime_config.get("thinking_only_repair_limit") if self.runtime_config.get("thinking_only_repair_limit") is not None else 1)
    options = dict(self.ollama_options.get(role, {}))
    started_at = time.time()
    started_iso = now_iso()
    last_content = ""
    last_thinking = ""
    last_display = ""
    attempt_count = 0
    stream_metadata: dict[str, Any] = {}

    # Action/tool mode gets a curated prompt from _build_prompt. Replaying the
    # full chat history here reintroduces stale failures and unrelated tasks.
    del session_id
    messages = [
        {"role": "system", "content": self._system_prompt()},
        {"role": "user", "content": prompt},
    ]
    normal_repairs_used = 0
    thinking_repairs_used = 0
    while True:
        attempt_count += 1
        stream_metadata = {}
        if hasattr(self.llm_backend, "iter_chat_stream"):
            chunks = self.llm_backend.iter_chat_stream(
                model=model,
                messages=messages,
                options=options,
                timeout_seconds=timeout_seconds,
            )
            content_parts: list[str] = []
            thinking_parts: list[str] = []
            for chunk in chunks:
                stream_metadata = self._extract_stream_metadata(chunk, previous=stream_metadata)
                message = chunk.get("message") or {}
                delta_content = str(message.get("content") or "")
                delta_thinking = str(message.get("thinking") or "")
                if not delta_content and not delta_thinking:
                    continue
                if delta_thinking:
                    thinking_parts.append(delta_thinking)
                if delta_content:
                    content_parts.append(delta_content)
                current_stream = self._format_llm_stream_text(
                    thinking_text="".join(thinking_parts),
                    content_text="".join(content_parts),
                )
                self._write_runtime_status(
                    status="running",
                    current_role=role,
                    current_model=model,
                    current_stream_text=self._tail_stream_text(current_stream, limit=4000),
                    worker_running=self._worker_running(),
                )
            last_content = "".join(content_parts)
            last_thinking = "".join(thinking_parts)
            last_display = self._format_llm_stream_text(thinking_text=last_thinking, content_text=last_content)
        elif hasattr(self.llm_backend, "chat_stream"):
            # Some fake/test backends expose chat_stream as a buffered list.
            # Real Ollama streaming should use iter_chat_stream above so live
            # output is updated while tokens arrive.
            chunks = self.llm_backend.chat_stream(
                model=model,
                messages=messages,
                options=options,
                timeout_seconds=timeout_seconds,
            )
            content_parts: list[str] = []
            thinking_parts: list[str] = []
            for chunk in chunks:
                stream_metadata = self._extract_stream_metadata(chunk, previous=stream_metadata)
                message = chunk.get("message") or {}
                delta_content = str(message.get("content") or "")
                delta_thinking = str(message.get("thinking") or "")
                if not delta_content and not delta_thinking:
                    continue
                if delta_thinking:
                    thinking_parts.append(delta_thinking)
                if delta_content:
                    content_parts.append(delta_content)
                current_stream = self._format_llm_stream_text(
                    thinking_text="".join(thinking_parts),
                    content_text="".join(content_parts),
                )
                self._write_runtime_status(
                    status="running",
                    current_role=role,
                    current_model=model,
                    current_stream_text=self._tail_stream_text(current_stream, limit=4000),
                    worker_running=self._worker_running(),
                )
            last_content = "".join(content_parts)
            last_thinking = "".join(thinking_parts)
            last_display = self._format_llm_stream_text(thinking_text=last_thinking, content_text=last_content)
        else:
            response = self.llm_backend.chat(
                model=model,
                messages=messages,
                options=options,
                timeout_seconds=timeout_seconds,
            )
            last_content = str(response.get("content_text") or response.get("content") or "")
            last_thinking = str(response.get("thinking_text") or response.get("thinking") or "")
            last_display = self._format_llm_stream_text(thinking_text=last_thinking, content_text=last_content)
            stream_metadata = self._extract_stream_metadata(response.get("raw") or {}, previous={})
            self._write_runtime_status(
                status="running",
                current_role=role,
                current_model=model,
                current_stream_text=self._tail_stream_text(last_display, limit=4000),
                worker_running=self._worker_running(),
            )
        envelope = self._parse_envelope(last_content)
        if self._raw_contains_json_object(last_content) and self._looks_like_structured_envelope(envelope):
            finished_at = time.time()
            finished_iso = now_iso()
            self._write_runtime_status(
                status="running",
                current_role=role,
                current_model=model,
                last_llm_started_at=started_iso,
                last_llm_finished_at=finished_iso,
                last_llm_duration_ms=int((finished_at - started_at) * 1000),
                last_llm_attempt_count=attempt_count,
                last_llm_raw_preview=last_content[:500],
                last_llm_thinking_preview=last_thinking[:500],
                last_llm_parse_issue=None,
                last_llm_stream_metadata=stream_metadata,
                current_stream_text=self._tail_stream_text(last_display, limit=4000),
            )
            return {
                "envelope": envelope,
                "attempt_count": attempt_count,
                "raw_text": last_content,
                "thinking_text": last_thinking,
                "combined_text": last_display,
                "parse_issue": "",
                "stream_metadata": stream_metadata,
            }
        fallback = self._parse_envelope(last_content)
        parse_issue = self._classify_llm_parse_issue(
            raw_text=last_content,
            thinking_text=last_thinking,
            envelope=fallback,
            stream_metadata=stream_metadata,
        )
        if parse_issue == "thinking_only_output" and thinking_repairs_used < thinking_only_repair_limit:
            thinking_repairs_used += 1
            messages = [
                {"role": "system", "content": self._system_prompt()},
                {"role": "user", "content": prompt},
                {
                    "role": "user",
                    "content": self._thinking_only_repair_prompt(thinking_text=last_thinking, stream_metadata=stream_metadata),
                },
            ]
            continue
        if normal_repairs_used >= retry_limit:
            break
        normal_repairs_used += 1
        messages = [
            {"role": "system", "content": self._system_prompt()},
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": last_content},
            {
                "role": "user",
                "content": self._json_repair_prompt(parse_target_text=last_content, stream_metadata=stream_metadata),
            },
        ]
    finished_at = time.time()
    finished_iso = now_iso()
    fallback = self._parse_envelope(last_content)
    parse_issue = self._classify_llm_parse_issue(
        raw_text=last_content,
        thinking_text=last_thinking,
        envelope=fallback,
        stream_metadata=stream_metadata,
    )
    self._write_runtime_status(
        status="running",
        current_role=role,
        current_model=model,
        last_llm_started_at=started_iso,
        last_llm_finished_at=finished_iso,
        last_llm_duration_ms=int((finished_at - started_at) * 1000),
        last_llm_attempt_count=attempt_count,
        last_llm_raw_preview=last_content[:500],
        last_llm_thinking_preview=last_thinking[:500],
        last_llm_parse_issue=parse_issue,
        last_llm_stream_metadata=stream_metadata,
        current_stream_text=self._tail_stream_text(last_display, limit=4000),
        last_error=f"llm response did not follow json contract: {parse_issue}",
    )
    return {
        "envelope": fallback,
        "attempt_count": attempt_count,
        "raw_text": last_content,
        "thinking_text": last_thinking,
        "combined_text": last_display,
        "parse_issue": parse_issue,
        "stream_metadata": stream_metadata,
    }


def _format_llm_stream_text(self, *, thinking_text: str, content_text: str) -> str:
    thinking = str(thinking_text or "")
    content = str(content_text or "")
    if thinking and content:
        return f"[thinking]\n{thinking}\n\n[content]\n{content}"
    if thinking:
        return f"[thinking]\n{thinking}"
    return content


def _tail_stream_text(self, text: str, *, limit: int) -> str:
    value = str(text or "")
    if len(value) <= limit:
        return value
    tail = value[-limit:]
    if value.startswith("[thinking]") and not tail.startswith("[thinking]"):
        return "[thinking]\n... [live output truncated]\n" + tail
    if value.startswith("[content]") and not tail.startswith("[content]"):
        return "[content]\n... [live output truncated]\n" + tail
    return tail


def _json_repair_prompt(self, *, parse_target_text: str, stream_metadata: dict[str, Any]) -> str:
    del parse_target_text
    chunk_bytes = int(getattr(self, "tool_content_chunk_bytes", DEFAULT_TOOL_CONTENT_CHUNK_BYTES) or DEFAULT_TOOL_CONTENT_CHUNK_BYTES)
    done_reason = str((stream_metadata or {}).get("done_reason") or "")
    length_note = (
        "The previous response hit the generation length limit. Do not continue the previous text. "
        if done_reason.lower() in {"length", "max_tokens", "num_predict"}
        else ""
    )
    return (
        "Your previous response was not valid JSON for the required schema. "
        f"{length_note}"
        "Return exactly one JSON object in assistant content only, with keys "
        "analysis, assistant_message, tool_name, tool_args. Do not put prose, Markdown, "
        "code fences, or hidden reasoning in the visible content. "
        "If you are writing a file, return only one tool call that can complete within this response. "
        f"write_file and append_file tool_args.content are limited to {chunk_bytes} UTF-8 bytes per step. "
        "If the file is longer, include only the next line-boundary chunk now and continue with append_file in a later step. "
        "For existing files, use replace_text with an exact unique old_text copied from read_file output. "
        "Always close the JSON object."
    )


def _thinking_only_repair_prompt(self, *, thinking_text: str, stream_metadata: dict[str, Any]) -> str:
    del stream_metadata
    thinking_preview = str(thinking_text or "")[:1200]
    return (
        "Your previous response put useful text only in hidden thinking/reasoning, and assistant content was empty. "
        "P4 cannot treat hidden thinking as an action or final answer. "
        "Return exactly one JSON object in visible assistant content only, with keys "
        "analysis, assistant_message, tool_name, tool_args. "
        "Do not include Markdown, code fences, prose outside JSON, or hidden reasoning in content. "
        "If the user only needs a conversational answer and no tool is needed, use "
        "{\"tool_name\":\"final_answer\",\"tool_args\":{\"answer\":\"...\"}}. "
        "If a tool is needed, choose exactly one concrete tool call. "
        f"Previous hidden-thinking preview for diagnosis only, not to copy verbatim: {thinking_preview}"
    )


def _extract_stream_metadata(self, chunk: dict[str, Any], *, previous: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(previous or {})
    if not isinstance(chunk, dict):
        return metadata
    for key in (
        "done",
        "done_reason",
        "total_duration",
        "load_duration",
        "prompt_eval_count",
        "prompt_eval_duration",
        "eval_count",
        "eval_duration",
    ):
        if key in chunk:
            metadata[key] = chunk.get(key)
    return metadata


def _classify_llm_parse_issue(
    self,
    *,
    raw_text: str,
    thinking_text: str = "",
    envelope: dict[str, Any],
    stream_metadata: dict[str, Any],
) -> str:
    raw = str(raw_text or "")
    if not raw.strip():
        if str(thinking_text or "").strip():
            return "thinking_only_output"
        return "empty_output"
    done_reason = str((stream_metadata or {}).get("done_reason") or "").lower()
    if done_reason in {"length", "max_tokens", "num_predict"}:
        return "length_truncated"
    if self._looks_like_truncated_json(raw):
        return "length_truncated"
    if "{" not in raw:
        return "missing_json_object"
    candidate = self._extract_json_object(raw.strip())
    if candidate is None:
        return "json_parse_error"
    try:
        json.loads(candidate)
    except json.JSONDecodeError:
        return "json_parse_error"
    if not self._looks_like_structured_envelope(envelope):
        return "invalid_tool_envelope"
    return "json_contract_not_confirmed"


def _looks_like_truncated_json(self, raw_text: str) -> bool:
    raw = str(raw_text or "").strip()
    if not raw or "{" not in raw:
        return False
    if self._extract_json_object(raw) is not None:
        return False
    open_braces = raw.count("{") - raw.count("}")
    open_brackets = raw.count("[") - raw.count("]")
    quote_count = raw.count('"') - raw.count('\\"')
    if open_braces > 0 or open_brackets > 0:
        return True
    if quote_count % 2 == 1:
        return True
    return raw.endswith(("\\", ",", ":", "{", "["))


def _looks_like_structured_envelope(self, envelope: dict[str, Any]) -> bool:
    return isinstance(envelope, dict) and "tool_name" in envelope and "tool_args" in envelope


def _raw_contains_json_object(self, raw_text: str) -> bool:
    return self._extract_json_object(raw_text.strip()) is not None


def _extract_json_object(self, text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for index in range(start, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None
