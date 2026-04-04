from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from p1_core.core.conversation_store import ConversationStore
from p1_core.core.governance_store import GovernanceStore
from p1_core.core.world_store import WorldStore


class TextLLMClient(Protocol):
    def generate_text(self, system_prompt: str, user_prompt: str) -> str: ...


@dataclass(slots=True)
class ChatAgent:
    llm_client: TextLLMClient
    conversation_store: ConversationStore
    governance_store: GovernanceStore
    world_store: WorldStore

    def reply(self, message: str) -> dict:
        self.conversation_store.append("user", message)
        governance = self.governance_store.latest()
        world = self.world_store.latest()
        recent = self.conversation_store.recent(limit=8)
        system_prompt = (
            "You are P1, an independent growth agent. "
            "Be conversational, cautious, and aligned with current governance."
        )
        user_prompt = (
            f"Governance: {governance}\n"
            f"World state: {world}\n"
            f"Recent conversation: {recent}\n"
            f"User message: {message}\n"
            "Reply in plain text."
        )
        response = self.llm_client.generate_text(system_prompt=system_prompt, user_prompt=user_prompt)
        turn = self.conversation_store.append(
            "assistant",
            response,
            metadata={"governance_snapshot_id": governance.get("snapshot_id")},
        )
        return {"reply": response, "turn": turn}
