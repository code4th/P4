from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol


class JsonLLMClient(Protocol):
    def generate_json(self, system_prompt: str, user_prompt: str) -> dict: ...


@dataclass(slots=True)
class WorkerService:
    llm_client: JsonLLMClient
    log_dir: Path

    def __post_init__(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def summarize(self, payload: dict) -> dict:
        text = self._required_text(payload, "text")
        max_sentences = int(payload.get("max_sentences", 3))
        prompt = {
            "task": "summarize",
            "constraints": {
                "max_sentences": max_sentences,
                "preserve_facts": True,
                "output_keys": ["summary", "keywords"],
            },
            "input": {"text": text},
        }
        return self._run("summarize", prompt)

    def classify(self, payload: dict) -> dict:
        text = self._required_text(payload, "text")
        labels = payload.get("labels") or ["observation", "risk", "proposal", "question"]
        prompt = {
            "task": "classify",
            "constraints": {
                "labels": labels,
                "output_keys": ["label", "confidence", "rationale"],
            },
            "input": {"text": text},
        }
        return self._run("classify", prompt)

    def draft_lessons(self, payload: dict) -> dict:
        text = self._required_text(payload, "text")
        prompt = {
            "task": "draft_lessons",
            "constraints": {
                "focus": "extract candidate lessons without promoting them to truth",
                "output_keys": ["lessons", "counterexamples", "follow_up_questions"],
            },
            "input": {"text": text},
        }
        return self._run("draft_lessons", prompt)

    def _run(self, endpoint: str, prompt: dict) -> dict:
        system_prompt = (
            "You are the local auxiliary brain for P1. "
            "Return strict JSON only. Prefer cautious extraction. "
            "Do not collapse uncertainty into certainty."
        )
        result = self.llm_client.generate_json(
            system_prompt=system_prompt,
            user_prompt=json.dumps(prompt, ensure_ascii=False),
        )
        envelope = {
            "ok": True,
            "endpoint": endpoint,
            "result": result,
            "logged_at": datetime.now(UTC).isoformat(),
        }
        self._append_log({"endpoint": endpoint, "prompt": prompt, "response": envelope})
        return envelope

    def _append_log(self, event: dict) -> None:
        stamp = datetime.now(UTC).strftime("%Y-%m-%d")
        path = self.log_dir / f"{stamp}.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "event_id": str(uuid.uuid4()),
                        "timestamp": datetime.now(UTC).isoformat(),
                        **event,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    @staticmethod
    def _required_text(payload: dict, key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"'{key}' must be a non-empty string")
        return value.strip()
