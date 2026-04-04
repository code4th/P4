from __future__ import annotations

import json
from dataclasses import dataclass
from urllib import error, request


class OllamaError(RuntimeError):
    pass


@dataclass(slots=True)
class OllamaClient:
    model: str
    base_url: str = "http://127.0.0.1:11434"
    timeout_seconds: float = 60.0

    def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
        payload = {
            "model": self.model,
            "stream": False,
            "format": "json",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(
            f"{self.base_url}/api/chat",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except error.URLError as exc:
            raise OllamaError(f"failed to reach ollama: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise OllamaError("ollama returned invalid json envelope") from exc

        content = raw.get("message", {}).get("content", "")
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise OllamaError("ollama returned non-json content") from exc
