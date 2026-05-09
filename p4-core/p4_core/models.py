from __future__ import annotations

from typing import Any


CODE_KEYWORDS = {
    "code",
    "python",
    "function",
    "class",
    "bug",
    "fix",
    "implement",
    "refactor",
    "file",
    "module",
    "test",
    "コード",
    "実装",
    "修正",
    "関数",
    "ファイル",
    "テスト",
}
TERMINAL_KEYWORDS = {
    "command",
    "shell",
    "terminal",
    "run",
    "build",
    "install",
    "execute",
    "log",
    "stdout",
    "stderr",
    "コマンド",
    "シェル",
    "ターミナル",
    "実行",
    "ビルド",
    "インストール",
    "ログ",
}
FAST_KEYWORDS = {
    "summary",
    "summarize",
    "status",
    "quick",
    "brief",
    "what",
    "why",
    "explain",
    "短く",
    "要約",
    "一言",
    "簡潔",
    "説明",
    "なぜ",
    "何",
    "挨拶",
}


class ModelRouter:
    def __init__(self, models: dict[str, str]) -> None:
        self.models = {key: self._normalize_model_name(value) for key, value in dict(models).items()}

    @staticmethod
    def _normalize_model_name(model_name: str) -> str:
        clean = str(model_name or "").strip()
        if not clean:
            return clean
        if ":" in clean:
            return clean
        if clean in {"gemma4:26b"}:
            return clean
        return f"{clean}:latest"

    def select_model(
        self,
        *,
        goal_text: str,
        pending_message: str,
        recent_events: list[dict[str, Any]],
        current_phase: str | None = None,
    ) -> dict[str, str]:
        haystack = f"{goal_text}\n{pending_message}".lower()
        last_tool = ""
        for event in reversed(recent_events):
            if event.get("type") == "tool_call":
                last_tool = str(event.get("tool_name") or "")
                break

        if current_phase == "DELIBERATE":
            if last_tool in {"read_file", "search_code", "write_file", "append_file", "replace_text"} or any(token in haystack for token in CODE_KEYWORDS):
                return {
                    "role": "coding",
                    "model": self.models["coding"],
                    "reason": "deliberation phase in code/file context",
                }
            if last_tool == "run_command" or any(token in haystack for token in TERMINAL_KEYWORDS):
                return {
                    "role": "terminal",
                    "model": self.models["terminal"],
                    "reason": "deliberation phase in terminal context",
                }
            return {
                "role": "reasoning",
                "model": self.models["reasoning"],
                "reason": "deliberation phase triggered by process stagnation",
            }

        if last_tool == "run_command" or any(token in haystack for token in TERMINAL_KEYWORDS):
            return {
                "role": "terminal",
                "model": self.models["terminal"],
                "reason": "terminal-oriented request or recent command execution",
            }
        if last_tool in {"read_file", "search_code", "write_file"} or any(token in haystack for token in CODE_KEYWORDS):
            return {
                "role": "coding",
                "model": self.models["coding"],
                "reason": "code/file-oriented request or recent file tool usage",
            }
        if any(token in haystack for token in FAST_KEYWORDS):
            return {
                "role": "fast",
                "model": self.models["fast"],
                "reason": "lightweight explanatory turn",
            }
        return {
            "role": "reasoning",
            "model": self.models["reasoning"],
            "reason": "default reasoning path for ambiguous or broad work",
        }

    def terminal_fallback_chain(self, preferred: str | None = None) -> list[str]:
        candidates = [
            self._normalize_model_name(str(preferred or "")),
            self.models.get("terminal", ""),
            self.models.get("coding", ""),
            self.models.get("fast", ""),
            self.models.get("reasoning", ""),
        ]
        deduped: list[str] = []
        for item in candidates:
            if item and item not in deduped:
                deduped.append(item)
        return deduped

    def consultant_model(self, *, current_model: str | None = None, purpose: str = "plan_acceptance") -> dict[str, str]:
        """Choose a second-opinion model with a different role from the actor.

        Design intent: the actor proposes an action; a consultant only reviews
        semantic fit. Prefer the strongest general model when it differs from
        the actor, otherwise use a lightweight skeptical reviewer.
        """
        del purpose
        current = self._normalize_model_name(str(current_model or ""))
        candidates = [
            ("reasoning", self.models.get("reasoning", "")),
            ("fast", self.models.get("fast", "")),
            ("coding", self.models.get("coding", "")),
            ("terminal", self.models.get("terminal", "")),
        ]
        for role, model in candidates:
            normalized = self._normalize_model_name(model)
            if normalized and normalized != current:
                return {
                    "role": role,
                    "model": normalized,
                    "reason": "semantic plan acceptance consultant",
                }
        return {
            "role": "reasoning",
            "model": current or self.models.get("reasoning", ""),
            "reason": "semantic plan acceptance consultant",
        }
