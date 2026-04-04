from __future__ import annotations

import json
from dataclasses import dataclass
from urllib import error, request


class OllamaError(RuntimeError):
    pass


def _extract_first_json_object(text: str) -> dict:
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


@dataclass(slots=True)
class OllamaClient:
    model: str
    base_url: str = "http://127.0.0.1:11434"
    timeout_seconds: float = 60.0

    def _chat(self, system_prompt: str, user_prompt: str, *, json_mode: bool) -> dict:
        payload = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if json_mode:
            payload["format"] = "json"
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

        return raw

    def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
        raw = self._chat(system_prompt, user_prompt, json_mode=True)

        content = raw.get("message", {}).get("content", "")
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            try:
                return _extract_first_json_object(content)
            except json.JSONDecodeError as fallback_exc:
                raise OllamaError("ollama returned non-json content") from fallback_exc

    def generate_text(self, system_prompt: str, user_prompt: str) -> str:
        raw = self._chat(system_prompt, user_prompt, json_mode=False)
        content = raw.get("message", {}).get("content")
        if not isinstance(content, str):
            raise OllamaError("ollama returned non-text content")
        return content.strip()
