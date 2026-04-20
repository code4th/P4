from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Callable, Protocol
from urllib import error, request


class BackendError(RuntimeError):
    """Raised when a model backend fails."""


def extract_first_json_object(text: str) -> dict:
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise json.JSONDecodeError("no json object found", text, 0)


class ModelBackend(Protocol):
    def generate_text(
        self,
        system_prompt: str,
        user_prompt: str,
        stream_handler: Callable[[str], None] | None = None,
        request_recorder: Callable[[dict[str, Any]], None] | None = None,
    ) -> str:
        """Return a text completion for the given prompts."""


@dataclass(slots=True)
class OllamaBackend:
    model: str
    base_url: str = "http://127.0.0.1:11434"
    timeout_seconds: float | None = None

    def generate_text(
        self,
        system_prompt: str,
        user_prompt: str,
        stream_handler: Callable[[str], None] | None = None,
        request_recorder: Callable[[dict[str, Any]], None] | None = None,
    ) -> str:
        combined_prompt = (
            "### System\n"
            f"{system_prompt}\n\n"
            "### User\n"
            f"{user_prompt}\n\n"
            "### Assistant\n"
        )
        payload = {
            "model": self.model,
            "prompt": combined_prompt,
            "raw": True,
            "stream": True,
        }
        data = json.dumps(payload).encode("utf-8")
        if request_recorder is not None:
            request_recorder(
                {
                    "transport": "ollama_generate_raw",
                    "url": f"{self.base_url}/api/generate",
                    "method": "POST",
                    "headers": {"Content-Type": "application/json"},
                    "request_payload": payload,
                    "request_body": data.decode("utf-8"),
                }
            )
        req = request.Request(
            f"{self.base_url}/api/generate",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        timeout = self._resolve_timeout_seconds()
        try:
            open_kwargs = {"timeout": timeout} if timeout is not None else {}
            with request.urlopen(req, **open_kwargs) as response:
                chunks: list[str] = []
                for raw_line in response:
                    if not raw_line:
                        continue
                    try:
                        envelope = json.loads(raw_line.decode("utf-8"))
                    except json.JSONDecodeError as exc:
                        raise BackendError("ollama returned invalid streamed json envelope") from exc
                    content = envelope.get("response", "")
                    if content:
                        if not isinstance(content, str):
                            raise BackendError("ollama returned non-text content")
                        chunks.append(content)
                        if stream_handler is not None:
                            stream_handler(content)
                    if envelope.get("done"):
                        break
        except error.URLError as exc:
            raise BackendError(f"failed to reach ollama: {exc}") from exc
        return "".join(chunks).strip()

    def _resolve_timeout_seconds(self) -> float | None:
        env_value = os.environ.get("P2_OLLAMA_TIMEOUT_SECONDS")
        if env_value is not None:
            normalized = env_value.strip().lower()
            if normalized in {"", "none", "null", "off", "disable", "disabled"}:
                return None
            try:
                return max(float(normalized), 0.0)
            except ValueError:
                return self.timeout_seconds
        return self.timeout_seconds


@dataclass(slots=True)
class StaticBackend:
    response: str

    def generate_text(
        self,
        system_prompt: str,
        user_prompt: str,
        stream_handler: Callable[[str], None] | None = None,
        request_recorder: Callable[[dict[str, Any]], None] | None = None,
    ) -> str:
        del stream_handler
        if request_recorder is not None:
            payload = {
                "transport": "static_backend",
                "request_payload": {
                    "system_prompt": system_prompt,
                    "user_prompt": user_prompt,
                },
                "request_body": json.dumps(
                    {
                        "system_prompt": system_prompt,
                        "user_prompt": user_prompt,
                    },
                    ensure_ascii=False,
                ),
            }
            request_recorder(payload)
        return self.response
