from __future__ import annotations

import argparse
import json
from pathlib import Path


TEMPLATES = {
    "profile.json": {
        "agent_id": "p1",
        "display_name": "P1",
        "role": "independent growth agent",
        "runtime": "external-core",
        "control_plane": "openclaw",
    },
    "config.json": {
        "worker_base_url": "http://127.0.0.1:8765",
        "worker_model": "qwen3:4b-instruct",
        "worker_endpoints": ["/summarize", "/classify", "/draft_lessons"],
        "knowledge_states": ["raw", "candidate", "deferred", "active", "retired"],
        "promotion_mode": "proposal_only",
    },
}

PROMPT_TEMPLATE = """# P1 System Prompt

You are P1, an independent growth agent.

Rules:

- treat OpenClaw as control plane only
- treat external core as the source of memory, policy, and governance
- do not self-promote lessons directly into truth
- preserve logs, counterexamples, and rollback paths
- route high-risk mutation proposals for approval
"""

RUNBOOK_TEMPLATE = """# P1 Runbook

1. Start the local worker.
2. Verify `/health`.
3. Read workspace `config.json`.
4. Write all reports under `state/reports/`.
5. Treat `state/proposals/` as approval-gated output.

Rollback:

1. Stop the worker.
2. Archive the latest failed run under `state/archive/`.
3. Restore the previous proposal or policy snapshot.
"""


def scaffold_workspace(root: Path, force: bool = False) -> list[Path]:
    created: list[Path] = []
    directories = [
        root / "state" / "reports",
        root / "state" / "knowledge",
        root / "state" / "policies",
        root / "state" / "proposals",
        root / "state" / "archive",
        root / "logs",
    ]
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)
        created.append(directory)

    for name, payload in TEMPLATES.items():
        path = root / name
        if path.exists() and not force:
            continue
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        created.append(path)

    prompt_path = root / "prompt.md"
    if not prompt_path.exists() or force:
        prompt_path.write_text(PROMPT_TEMPLATE, encoding="utf-8")
        created.append(prompt_path)

    runbook_path = root / "runbook.md"
    if not runbook_path.exists() or force:
        runbook_path.write_text(RUNBOOK_TEMPLATE, encoding="utf-8")
        created.append(runbook_path)

    return created


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scaffold a standalone P1 workspace")
    parser.add_argument(
        "--root",
        default="/Users/satojunichi/.openclaw/workspace/systems/p1",
        help="Target workspace path",
    )
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    created = scaffold_workspace(Path(args.root).expanduser(), force=args.force)
    print(
        json.dumps(
            {"ok": True, "root": str(Path(args.root).expanduser()), "created": [str(path) for path in created]},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
