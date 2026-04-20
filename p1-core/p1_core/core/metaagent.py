from __future__ import annotations

import json
import re
import subprocess
import shutil
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Sequence
from urllib import error, request

from p1_core.worker.ollama_client import OllamaClient


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _extract_code(text: str) -> str:
    fenced = re.search(r"```(?:python)?\n(.*?)```", text, flags=re.DOTALL)
    if fenced:
        return fenced.group(1).strip() + "\n"
    return text.strip() + "\n"


def _compile_if_python(path: Path, code: str) -> None:
    if path.suffix != ".py":
        return
    compile(code, str(path), "exec")


def _load_openclaw_gateway_config(config_path: Path) -> tuple[str, str]:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    gateway = payload.get("gateway", {})
    port = int(gateway.get("port", 18789))
    token = str(gateway.get("auth", {}).get("token", ""))
    return f"http://127.0.0.1:{port}", token


@dataclass(slots=True)
class OpenAICompatibleClient:
    model: str
    base_url: str
    api_key: str
    timeout_seconds: float = 60.0

    def _chat(self, system_prompt: str, user_prompt: str) -> dict:
        payload = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(
            f"{self.base_url}/v1/chat/completions",
            data=data,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.URLError as exc:
            raise RuntimeError(f"failed to reach OpenAI-compatible backend: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError("OpenAI-compatible backend returned invalid JSON") from exc

    def generate_text(self, system_prompt: str, user_prompt: str) -> str:
        raw = self._chat(system_prompt, user_prompt)
        content = raw.get("choices", [{}])[0].get("message", {}).get("content")
        if not isinstance(content, str):
            raise RuntimeError("OpenAI-compatible backend returned non-text content")
        return content.strip()


@dataclass(slots=True)
class MetaAgentRunResult:
    target: str
    model: str
    success: bool
    message: str
    backup_path: str | None = None
    test_stdout: str | None = None
    test_stderr: str | None = None


@dataclass(slots=True)
class SelfRepairMetaAgent:
    root: Path
    model: str
    base_url: str = "http://127.0.0.1:11434"
    backend: str = "ollama"
    openclaw_config_path: Path | None = None
    test_command: Sequence[str] | None = None
    timeout_seconds: float = 60.0
    max_attempts: int = 2

    def __post_init__(self) -> None:
        self.root = self.root.expanduser()
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    @property
    def history_path(self) -> Path:
        return self.root / "state" / "metaagent" / "generation_history.json"

    @property
    def backup_dir(self) -> Path:
        return self.root / "state" / "metaagent" / "backups"

    def _read_history(self) -> dict:
        if not self.history_path.exists():
            return {"history": []}
        return json.loads(self.history_path.read_text(encoding="utf-8"))

    def _write_history(self, payload: dict) -> None:
        self.history_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _append_history(self, entry: dict) -> None:
        history = self._read_history()
        history.setdefault("history", []).append(entry)
        history["updated_at"] = _now_iso()
        self._write_history(history)

    def _history_entry(
        self,
        *,
        target: Path,
        purpose: str,
        constraints: str,
        success: bool,
        message: str,
        backup_path: Path | None = None,
        test_stdout: str | None = None,
        test_stderr: str | None = None,
        candidate_length: int | None = None,
        original_length: int | None = None,
    ) -> dict[str, object]:
        return {
            "timestamp": _now_iso(),
            "target": str(target),
            "target_name": target.name,
            "model": self.model,
            "base_url": self.base_url,
            "backend": self.backend,
            "timeout_seconds": self.timeout_seconds,
            "max_attempts": self.max_attempts,
            "test_command": list(self.test_command) if self.test_command else None,
            "purpose": purpose,
            "constraints": constraints,
            "success": success,
            "message": message,
            "backup_path": str(backup_path) if backup_path else None,
            "candidate_length": candidate_length,
            "original_length": original_length,
            "test_stdout": test_stdout,
            "test_stderr": test_stderr,
        }

    def _client(self):
        if self.backend == "ollama":
            return OllamaClient(model=self.model, base_url=self.base_url, timeout_seconds=self.timeout_seconds)
        if self.backend == "openclaw":
            config_path = (self.openclaw_config_path or Path("~/.openclaw/openclaw.json")).expanduser()
            base_url, api_key = _load_openclaw_gateway_config(config_path)
            return OpenAICompatibleClient(
                model=self.model,
                base_url=base_url,
                api_key=api_key,
                timeout_seconds=self.timeout_seconds,
            )
        raise ValueError(f"unsupported backend: {self.backend}")

    def propose(
        self,
        target: Path,
        *,
        purpose: str,
        constraints: str,
        previous_error: str | None = None,
    ) -> str:
        source = target.read_text(encoding="utf-8")
        system_prompt = (
            "You are a conservative self-repair agent. "
            "Return only the full revised file content, with no explanation. "
            "Preserve all imports, public behavior, and safety constraints unless the prompt explicitly allows changing them. "
            "Make the smallest change that addresses the stated problem."
        )
        error_hint = ""
        if previous_error:
            error_hint = (
                "\n\nPrevious attempt failed validation with this error:\n"
                f"{previous_error}\n"
                "Produce a corrected full file content only."
            )
        user_prompt = (
            f"Purpose:\n{purpose}\n\n"
            f"Constraints:\n{constraints}\n\n"
            f"Target path: {target}\n\n"
            f"Current file content:\n{source}\n"
            f"{error_hint}"
        )
        client = self._client()
        return client.generate_text(system_prompt=system_prompt, user_prompt=user_prompt)

    def run(self, target: Path, *, purpose: str, constraints: str) -> MetaAgentRunResult:
        target = target.expanduser()
        original = target.read_text(encoding="utf-8")
        backup_path: Path | None = None
        test_stdout = None
        test_stderr = None
        last_error: str | None = None
        for attempt in range(1, max(self.max_attempts, 1) + 1):
            candidate = None
            try:
                proposed = self.propose(
                    target,
                    purpose=purpose,
                    constraints=constraints,
                    previous_error=last_error,
                )
                candidate = _extract_code(proposed)
                _compile_if_python(target, candidate)
                backup_name = f"{target.name}.{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}.bak"
                backup_path = self.backup_dir / backup_name
                shutil.copyfile(target, backup_path)
                target.write_text(candidate, encoding="utf-8")
                if self.test_command:
                    env = os.environ.copy()
                    env["PYTHONPATH"] = "."
                    test_proc = subprocess.run(
                        list(self.test_command),
                        capture_output=True,
                        text=True,
                        cwd=self.root,
                        env=env,
                        timeout=self.timeout_seconds,
                    )
                    test_stdout = test_proc.stdout
                    test_stderr = test_proc.stderr
                    if test_proc.returncode != 0:
                        raise RuntimeError(
                            "test command failed after applying the proposed change; restored original file"
                        )

                self._append_history(
                    self._history_entry(
                        target=target,
                        purpose=purpose,
                        constraints=constraints,
                        success=True,
                        message="applied proposed file revision",
                        backup_path=backup_path,
                        test_stdout=test_stdout,
                        test_stderr=test_stderr,
                        candidate_length=len(candidate),
                        original_length=len(original),
                    )
                )
                return MetaAgentRunResult(
                    target=str(target),
                    model=self.model,
                    success=True,
                    message="applied proposed file revision",
                    backup_path=str(backup_path),
                    test_stdout=test_stdout,
                    test_stderr=test_stderr,
                )
            except Exception as exc:
                last_error = str(exc)
                if target.exists():
                    try:
                        target.write_text(original, encoding="utf-8")
                    except Exception:
                        pass
                if attempt >= max(self.max_attempts, 1):
                    self._append_history(
                        self._history_entry(
                            target=target,
                            purpose=purpose,
                            constraints=constraints,
                            success=False,
                            message=str(exc),
                            backup_path=backup_path,
                            test_stdout=test_stdout,
                            test_stderr=test_stderr,
                            candidate_length=len(candidate) if candidate is not None else None,
                            original_length=len(original),
                        )
                    )
                    return MetaAgentRunResult(
                        target=str(target),
                        model=self.model,
                        success=False,
                        message=str(exc),
                        backup_path=str(backup_path) if backup_path else None,
                        test_stdout=test_stdout,
                        test_stderr=test_stderr,
                    )
        raise RuntimeError("self-repair loop exhausted unexpectedly")


def load_recent_history(root: Path, *, limit: int = 5) -> list[dict[str, object]]:
    history_path = root.expanduser() / "state" / "metaagent" / "generation_history.json"
    if not history_path.exists():
        return []
    try:
        payload = json.loads(history_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    rows = payload.get("history", [])
    if not isinstance(rows, list):
        return []
    recent: list[dict[str, object]] = []
    for row in rows[-limit:]:
        if isinstance(row, dict):
            recent.append(row)
    return recent
