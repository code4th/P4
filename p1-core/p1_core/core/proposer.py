from __future__ import annotations


class Proposer:
    def propose(self, observation: str) -> dict[str, object]:
        return {
            "observation": observation,
            "proposal_type": "operational_rule_adjustment",
        }
