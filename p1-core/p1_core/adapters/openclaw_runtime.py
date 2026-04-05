from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from typing import Any


def _extract_text(payload: Any) -> str:
    if isinstance(payload, str):
        return payload.strip()
    if isinstance(payload, list):
        for item in payload:
            text = _extract_text(item)
            if text:
                return text
        return ""
    if not isinstance(payload, dict):
        return ""
    for key in ("reply", "text", "message", "output", "content"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for key in ("result", "response", "assistant", "data"):
        text = _extract_text(payload.get(key))
        if text:
            return text
    return ""


@dataclass(slots=True)
class OpenClawAgentTextBackend:
    agent_id: str = "main"
    thinking: str = "minimal"
    timeout_seconds: int = 120
    extra_args: list[str] = field(default_factory=list)

    def generate_text(self, system_prompt: str, user_prompt: str) -> str:
        prompt = f"{system_prompt.strip()}\n\n{user_prompt.strip()}".strip()
        command = [
            "openclaw",
            "agent",
            "--agent",
            self.agent_id,
            "--message",
            prompt,
            "--thinking",
            self.thinking,
            "--timeout",
            str(self.timeout_seconds),
            "--json",
            *self.extra_args,
        ]
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds + 5,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or f"openclaw agent exited with code {completed.returncode}")
        payload = json.loads(completed.stdout)
        text = _extract_text(payload)
        if not text:
            raise RuntimeError("openclaw agent JSON did not contain a reply text")
        return text


@dataclass(slots=True)
class OpenClawNodeActionBackend:
    node_id: str
    command_map: dict[str, str]
    timeout_ms: int = 30000
    invoke_timeout_ms: int = 15000
    extra_args: list[str] = field(default_factory=list)

    def describe(self) -> dict[str, Any]:
        completed = subprocess.run(
            [
                "openclaw",
                "nodes",
                "describe",
                "--node",
                self.node_id,
                "--timeout",
                str(self.timeout_ms),
                "--json",
                *self.extra_args,
            ],
            capture_output=True,
            text=True,
            timeout=max(5, self.timeout_ms // 1000 + 5),
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or f"openclaw nodes describe exited with code {completed.returncode}")
        return json.loads(completed.stdout)

    def run_command(self, *, argv: list[str], cwd: str, timeout_seconds: int) -> dict[str, Any]:
        return self._invoke_mapped(
            "run_command",
            {
                "argv": argv,
                "cwd": cwd,
                "timeoutSeconds": timeout_seconds,
            },
        )

    def read_file(self, *, path: str) -> dict[str, Any]:
        return self._invoke_mapped("read_file", {"path": path})

    def write_file(self, *, path: str, content: str) -> dict[str, Any]:
        return self._invoke_mapped("write_file", {"path": path, "content": content})

    def _invoke_mapped(self, operation: str, params: dict[str, Any]) -> dict[str, Any]:
        command_name = self.command_map.get(operation)
        if not command_name:
            raise RuntimeError(f"openclaw action backend is not configured for {operation}")
        completed = subprocess.run(
            [
                "openclaw",
                "nodes",
                "invoke",
                "--node",
                self.node_id,
                "--command",
                command_name,
                "--params",
                json.dumps(params, ensure_ascii=False),
                "--timeout",
                str(self.timeout_ms),
                "--invoke-timeout",
                str(self.invoke_timeout_ms),
                "--json",
                *self.extra_args,
            ],
            capture_output=True,
            text=True,
            timeout=max(5, self.timeout_ms // 1000 + 5),
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or f"openclaw nodes invoke exited with code {completed.returncode}")
        return json.loads(completed.stdout)
