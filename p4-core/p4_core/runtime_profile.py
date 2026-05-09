from __future__ import annotations

import re
from typing import Any


RUNTIME_PROFILE: dict[str, str] = {
    "name": "P4",
    "kind": "ローカルエージェントランタイム",
}


def runtime_identity_answer() -> str:
    return f"私は{RUNTIME_PROFILE['name']}、{RUNTIME_PROFILE['kind']}です。"


def runtime_profile_evidence() -> dict[str, Any]:
    return {
        "evidence_type": "runtime_profile",
        "source": "p4_core.runtime_profile",
        "profile": dict(RUNTIME_PROFILE),
        "answer": runtime_identity_answer(),
    }


def is_runtime_identity_query(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    normalized = re.sub(r"\s+", "", value.lower())
    identity_markers = (
        "お名前",
        "名前は",
        "あなたは誰",
        "君は誰",
        "何者",
        "whoareyou",
        "yourname",
    )
    return any(marker in normalized for marker in identity_markers)
