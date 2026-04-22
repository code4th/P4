from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from p4_core.workspace import append_jsonl, append_session_event, now_iso, read_json, read_jsonl


def _missing_requested_commands(self, *, user_message: str, steps: list[dict[str, Any]]) -> list[str]:
    requested = self._extract_requested_commands(user_message)
    if not requested:
        return []
    executed = " ; ".join(
        str(((step.get("tool_result") or {}).get("command") or ""))
        for step in steps
        if step.get("tool_name") == "run_command"
    ).lower()
    missing = [command for command in requested if command.lower() not in executed]
    return missing


def _expected_artifacts(self, text: str) -> list[str]:
    raw = str(text or "")
    if not raw.strip():
        return []
    candidates: list[str] = []
    for match in re.findall(r"`([^`]+)`", raw):
        candidate = str(match).strip()
        if "/" in candidate or "." in Path(candidate).name:
            candidates.append(candidate)
    for match in re.findall(r"\b([A-Za-z0-9_.-]+\.[A-Za-z0-9_.-]+)\b", raw):
        candidates.append(str(match).strip())
    deduped: list[str] = []
    for candidate in candidates:
        normalized = candidate.lstrip("./")
        if normalized and normalized not in deduped:
            deduped.append(normalized)
    return deduped


def _missing_expected_artifacts(self, *, user_message: str) -> list[str]:
    expected = self._expected_artifacts(user_message)
    if not expected:
        return []
    missing: list[str] = []
    for rel in expected:
        candidate = (self.execution_root / rel).resolve()
        try:
            if os.path.commonpath([str(self.execution_root), str(candidate)]) != str(self.execution_root):
                continue
        except ValueError:
            continue
        if not candidate.exists():
            missing.append(rel)
    return missing


def _failed_command_guardrail(self, *, tool_result: dict[str, Any]) -> str:
    command = str(tool_result.get("command") or "").strip()
    returncode = tool_result.get("returncode")
    stderr = str(tool_result.get("stderr") or tool_result.get("error") or "").strip()
    stderr_preview = stderr[:240] if stderr else "no stderr"
    return (
        f"リカバリモード：直前のコマンド `{command}` が失敗しました（終了コード={returncode}）。"
        "同じ引数での再試行は避け、エラー理由を分析した上でアプローチを変更してください。"
        f" エラー詳細：{stderr_preview}"
    )


def _redundant_command_reason(self, *, tool_args: dict[str, Any], steps: list[dict[str, Any]]) -> str | None:
    command = str(tool_args.get("command") or "").strip()
    shell_name = str(tool_args.get("shell") or "")
    if not command or not steps:
        return None
    previous = steps[-1]
    if previous.get("tool_name") != "run_command":
        return None
    payload = previous.get("tool_result") or {}
    if not isinstance(payload, dict):
        return None
    previous_command = str(payload.get("command") or "").strip()
    previous_shell = str(payload.get("shell") or "")
    same_command = previous_command == command and (not shell_name or previous_shell == shell_name)
    if same_command and bool(payload.get("ok")):
        return f"重複コマンドの実行をブロックしました: {command}。既存の tool_result を使用するか、別のステップを選択してください。"
    if same_command and not bool(payload.get("ok")):
        return f"失敗した重複コマンドの実行をブロックしました: {command}。引数を変更するか、新しい証拠を収集してから再試行してください。"
    return None


def _extract_requested_commands(self, text: str) -> list[str]:
    raw_text = str(text or "")
    if "open_child_frame" in raw_text or "return_to_parent" in raw_text:
        raw_text = re.sub(r"goal\s*(?:は|=|:)\s*[\"'`][^\"'`]+[\"'`]", "", raw_text)
        raw_text = re.sub(r"context_summary\s*(?:は|=|:)\s*[\"'`][^\"'`]+[\"'`]", "", raw_text)
    known_heads = {"pwd", "ls", "rg", "find", "cat", "sed", "head", "tail", "python", "python3", "pytest", "git", "$psversiontable.psversion.tostring()"}
    patterns = [
        r"([A-Za-z0-9_.:/$-]+(?:\s+[A-Za-z0-9_.$:/=-]+)*)\s*を実行",
        r"`([^`]+)`",
        r"'([^']+)'",
        r"\"([^\"]+)\"",
    ]
    commands: list[str] = []
    fragments = re.split(r"[、。\n]|その後|続けて|そして|then|and then", raw_text)
    for fragment in fragments:
        clean = str(fragment).strip()
        if not clean:
            continue
        for pattern in patterns:
            for match in re.findall(pattern, clean):
                candidate = str(match).strip()
                if not candidate:
                    continue
                head = candidate.split()[0].lower()
                if head in known_heads:
                    commands.append(candidate)
        tokens = clean.split()
        for index, token in enumerate(tokens):
            head = token.lower()
            if head not in known_heads:
                continue
            candidate = token
            if head == "git" and index + 1 < len(tokens):
                candidate = f"{token} {tokens[index + 1]}"
            commands.append(candidate)
    deduped: list[str] = []
    for command in commands:
        if command not in deduped:
            deduped.append(command)
    return deduped


def _classify_failure(
    self,
    *,
    reason: str,
    steps: list[dict[str, Any]],
) -> str:
    clean = str(reason or "").lower()
    if "shell unavailable" in clean or "pwsh" in clean:
        return "shell_unavailable"
    if "expected artifact" in clean:
        return "missing_expected_artifact"
    if "unsupported detail" in clean:
        return "unsupported_detail"
    if "requested commands not yet executed" in clean or "command coverage" in clean:
        return "missing_command_coverage"
    if "step limit reached" in clean or "before finish" in clean or "missing finish" in clean:
        return "missing_finish"
    if "repeated failed command blocked" in clean or "repeated command blocked" in clean:
        return "repeated_command"
    recent_tools = [str(step.get("tool_name") or "") for step in steps[-3:]]
    if recent_tools.count("run_command") >= 2:
        return "repeated_command"
    return "generic_failure"
