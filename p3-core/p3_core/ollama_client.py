from __future__ import annotations

import json
from typing import Any, Iterator
from urllib import error, request


class OllamaChatClient:
    def __init__(self, *, base_url: str = "http://127.0.0.1:11434") -> None:
        self.base_url = base_url.rstrip("/")

    def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        options: dict[str, Any] | None = None,
        timeout_seconds: int = 180,
    ) -> dict[str, Any]:
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
        }
        if options:
            payload["options"] = options
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(
            f"{self.base_url}/api/chat",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except error.URLError as exc:
            raise RuntimeError(f"failed to reach Ollama at {self.base_url}: {exc}") from exc
        message = body.get("message") or {}
        content = str(message.get("content") or "")
        thinking = str(message.get("thinking") or "")
        if content and thinking:
            merged = f"{thinking}\n{content}"
        else:
            merged = content or thinking
        return {
            "model": body.get("model", model),
            "content": merged,
            "content_text": content,
            "thinking_text": thinking,
            "raw": body,
        }

    def chat_stream(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        options: dict[str, Any] | None = None,
        timeout_seconds: int = 180,
    ) -> list[dict[str, Any]]:
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        if options:
            payload["options"] = options
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(
            f"{self.base_url}/api/chat",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        chunks: list[dict[str, Any]] = []
        try:
            with request.urlopen(req, timeout=timeout_seconds) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8").strip()
                    if not line:
                        continue
                    chunks.append(json.loads(line))
        except error.URLError as exc:
            raise RuntimeError(f"failed to reach Ollama at {self.base_url}: {exc}") from exc
        return chunks

    def iter_chat_stream(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        options: dict[str, Any] | None = None,
        timeout_seconds: int = 180,
    ) -> Iterator[dict[str, Any]]:
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        if options:
            payload["options"] = options
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(
            f"{self.base_url}/api/chat",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=timeout_seconds) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8").strip()
                    if not line:
                        continue
                    yield json.loads(line)
        except error.URLError as exc:
            raise RuntimeError(f"failed to reach Ollama at {self.base_url}: {exc}") from exc

    def iter_generate_stream(
        self,
        *,
        model: str,
        prompt: str,
        options: dict[str, Any] | None = None,
        system: str | None = None,
        timeout_seconds: int = 180,
    ) -> Iterator[dict[str, Any]]:
        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": True,
        }
        if options:
            payload["options"] = options
        if system:
            payload["system"] = system
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(
            f"{self.base_url}/api/generate",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=timeout_seconds) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8").strip()
                    if not line:
                        continue
                    yield json.loads(line)
        except error.URLError as exc:
            raise RuntimeError(f"failed to reach Ollama at {self.base_url}: {exc}") from exc

    def list_models(self) -> dict[str, Any]:
        req = request.Request(
            f"{self.base_url}/api/tags",
            headers={"Content-Type": "application/json"},
            method="GET",
        )
        try:
            with request.urlopen(req, timeout=30) as response:
                body = json.loads(response.read().decode("utf-8"))
        except error.URLError as exc:
            raise RuntimeError(f"failed to reach Ollama at {self.base_url}: {exc}") from exc
        models = body.get("models") or []
        return {
            "ok": True,
            "base_url": self.base_url,
            "models": [
                {
                    "name": item.get("name"),
                    "size": item.get("size"),
                    "modified_at": item.get("modified_at"),
                }
                for item in models
            ],
        }
