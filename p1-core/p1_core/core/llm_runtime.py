from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol


class TextLLMBackend(Protocol):
    def generate_text(self, system_prompt: str, user_prompt: str) -> str: ...


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class DisabledOpenClawLLMBackend:
    def generate_text(self, system_prompt: str, user_prompt: str) -> str:
        raise RuntimeError("OpenClaw-backed LLM backend is not configured for this runtime")


@dataclass(slots=True)
class LLMUsageStore:
    root: Path

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text(json.dumps(self.default_state(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @property
    def path(self) -> Path:
        return self.root / "llm-usage.json"

    def default_state(self) -> dict:
        return {
            "updated_at": _now_iso(),
            "openclaw_calls": [],
            "local_calls": [],
            "last_openclaw_call_at": None,
            "last_local_call_at": None,
        }

    def read(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def write(self, payload: dict) -> dict:
        payload["updated_at"] = _now_iso()
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return payload

    def record_call(self, backend: str, now: datetime | None = None) -> dict:
        current = self.read()
        moment = now or datetime.now(UTC)
        key = "openclaw_calls" if backend == "openclaw" else "local_calls"
        current[key] = self._prune([*current.get(key, []), moment.isoformat()], window=timedelta(days=1), now=moment)
        current[f"last_{backend}_call_at"] = moment.isoformat()
        return self.write(current)

    @staticmethod
    def _prune(rows: list[str], *, window: timedelta, now: datetime) -> list[str]:
        kept: list[str] = []
        for value in rows:
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError:
                continue
            if now - parsed <= window:
                kept.append(parsed.isoformat())
        return kept

    def counts(self, now: datetime | None = None) -> dict[str, int]:
        moment = now or datetime.now(UTC)
        current = self.read()

        def count_within(rows: list[str], window: timedelta) -> int:
            total = 0
            for value in rows:
                try:
                    parsed = datetime.fromisoformat(value)
                except ValueError:
                    continue
                if moment - parsed <= window:
                    total += 1
            return total

        return {
            "openclaw_3h": count_within(current.get("openclaw_calls", []), timedelta(hours=3)),
            "openclaw_daily": count_within(current.get("openclaw_calls", []), timedelta(days=1)),
            "local_daily": count_within(current.get("local_calls", []), timedelta(days=1)),
        }


@dataclass(slots=True)
class LLMRouter:
    local_backend: TextLLMBackend
    usage_store: LLMUsageStore
    openclaw_backend: TextLLMBackend | None = None
    openclaw_3h_soft_cap: int = 20
    openclaw_daily_soft_cap: int = 40
    per_tick_openclaw_cap: int = 1
    local_first: bool = True
    openclaw_cooldown_seconds: int = 900
    local_failure_allows_openclaw: bool = False
    tick_openclaw_calls: int = field(default=0, init=False)

    def route_text(self, system_prompt: str, user_prompt: str, *, allow_openclaw: bool = True) -> dict[str, str]:
        local_error: Exception | None = None
        if self.local_first:
            try:
                text = self.local_backend.generate_text(system_prompt, user_prompt)
                self.usage_store.record_call("local")
                return {"backend": "local", "text": text}
            except Exception as exc:
                local_error = exc
                if not allow_openclaw or not self.local_failure_allows_openclaw:
                    raise

        if allow_openclaw and self.can_use_openclaw():
            backend = self.openclaw_backend or DisabledOpenClawLLMBackend()
            text = backend.generate_text(system_prompt, user_prompt)
            self.usage_store.record_call("openclaw")
            self.tick_openclaw_calls += 1
            return {"backend": "openclaw", "text": text}

        if local_error is not None:
            raise local_error

        text = self.local_backend.generate_text(system_prompt, user_prompt)
        self.usage_store.record_call("local")
        return {"backend": "local", "text": text}

    def can_use_openclaw(self, now: datetime | None = None) -> bool:
        moment = now or datetime.now(UTC)
        if self.tick_openclaw_calls >= self.per_tick_openclaw_cap:
            return False
        counts = self.usage_store.counts(now=moment)
        if counts["openclaw_3h"] >= self.openclaw_3h_soft_cap:
            return False
        if counts["openclaw_daily"] >= self.openclaw_daily_soft_cap:
            return False
        usage = self.usage_store.read()
        last_call_at = usage.get("last_openclaw_call_at")
        if last_call_at:
            try:
                last = datetime.fromisoformat(last_call_at)
            except ValueError:
                last = None
            if last and moment - last < timedelta(seconds=self.openclaw_cooldown_seconds):
                return False
        return self.openclaw_backend is not None
