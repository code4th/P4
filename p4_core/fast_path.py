from __future__ import annotations

from typing import Any


def _fast_path_envelope(
    self,
    *,
    step_index: int,
    selection: dict[str, str],
    user_message: str,
    extra_prompt: str | None,
    recent_events: list[dict[str, Any]],
    steps: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if str(selection.get("role") or "") != "terminal":
        return None
    if "open_child_frame" in str(user_message or "") or "return_to_parent" in str(user_message or ""):
        return None
    if any(str(event.get("type") or "") == "finish" for event in recent_events[-8:]):
        return None
    missing = self._missing_requested_commands(user_message=user_message, steps=steps)
    if not missing:
        return None
    command = missing[0]
    shell_name = self._preferred_shell_from_extra_prompt(extra_prompt)
    blocked_reason = self._redundant_command_reason(
        tool_args={"command": command, "shell": shell_name},
        steps=steps,
    )
    if blocked_reason is not None:
        return None
    action_note = (
        "高速パス（起動）: 最初に要求されたコマンドを即座に実行します。"
        if step_index == 1
        else "高速パス（継続）: 次の未実行コマンドをモデルに渡さず続けて実行します。"
    )
    return {
        "analysis": action_note,
        "assistant_message": f"Executing '{command}' command...",
        "tool_name": "run_command",
        "tool_args": {"command": command, "shell": shell_name},
    }
