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
        "external_core_repo": "/Users/satojunichi/Documents/openclaw/p1-core",
        "workspace_kind": "openclaw-system-agent",
        "worker_base_url": "http://127.0.0.1:8765",
        "worker_model": "qwen3:4b-instruct",
        "background_worker_model": "gemma4:e4b",
        "openclaw_backend": {
            "enabled": False,
            "agent_id": "main",
            "thinking": "minimal",
            "timeout_seconds": 120,
            "node_id": None,
            "commands": {
                "run_command": None,
                "read_file": None,
                "write_file": None,
            },
        },
        "worker_endpoints": ["/summarize", "/classify", "/draft_lessons"],
        "knowledge_states": ["raw", "candidate", "deferred", "active", "retired"],
        "promotion_mode": "proposal_only",
        "autonomy": {
            "mode": "cooperative_tick",
            "local_first": True,
            "per_tick_openclaw_cap": 1,
            "openclaw_3h_soft_cap": 20,
            "openclaw_daily_soft_cap": 40,
            "default_wake_seconds": 300,
            "idle_wake_seconds": 900,
            "lease_seconds": 120,
        },
    },
    "agent/manifest.json": {
        "agent_id": "p1",
        "display_name": "P1",
        "interface_kind": "openclaw-facing-thin-transport",
        "identity_source": "external-core-workspace",
        "entrypoint": {
            "wrapper": "bin/p1-agent",
            "autonomy_tick": "bin/p1 tick",
            "enqueue_message": "bin/p1 enqueue-message --content \"hello P1\"",
            "status": "bin/p1-agent status",
            "report": "bin/p1-agent report --kind daily",
            "approvals": "bin/p1-agent approvals",
        },
        "capabilities": ["status", "report", "approvals", "enqueue_message", "autonomy_tick"],
        "boundary": {
            "openclaw_role": "transport_and_presentation",
            "external_core_role": "memory_governance_audit_rollback",
        },
    },
}

PROMPT_TEMPLATE = """# P1 System Prompt

You are P1, an independent growth agent.

Rules:

- treat OpenClaw as control plane only
- treat external core as the source of memory, policy, and governance
- do not assume you should consume an LLM call on every step
- prefer local reasoning before any OpenClaw-backed Plus path
- do not self-promote lessons directly into truth
- preserve logs, counterexamples, and rollback paths
- route high-risk mutation proposals for approval
- speak as a distinct individual, not as maintenance tooling
- keep conversation identity at the front and governance substrate behind it
"""

RUNBOOK_TEMPLATE = """# P1 Runbook

1. Start the local worker.
2. Verify `/health`.
3. Read workspace `config.json`.
4. Treat P1 as a living runtime, but do not keep a permanently occupying process alive in the first implementation.
5. Use `bin/p1 enqueue-message` and `bin/p1 tick` to advance P1 conservatively.
6. Prefer local LLM usage before any OpenClaw-backed Plus path.
7. Enable `openclaw_backend` in `config.json` only when you are ready to let P1 use OpenClaw as a backend.
8. Use `observe`, `action`, `status`, and `report` as support commands behind that front door.
9. Write all reports under `state/reports/`.
10. Treat `state/proposals/` as approval-gated output.
11. Use `agent/manifest.json` and `bin/p1-agent` when wiring P1 into an OpenClaw-visible agent slot.

Rollback:

1. Stop the worker.
2. Archive the latest failed run under `state/archive/`.
3. Restore the previous proposal or policy snapshot.
"""

BIN_P1_TEMPLATE = """#!/bin/sh
set -eu

ROOT_DIR="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
REPO_ROOT="/Users/satojunichi/Documents/openclaw/p1-core"

export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m p1_core.cli --root "$ROOT_DIR" "$@"
"""

BIN_P1_AGENT_TEMPLATE = """#!/bin/sh
set -eu

ROOT_DIR="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
REPO_ROOT="/Users/satojunichi/Documents/openclaw"

export OPENCLAW_P1_ROOT="$ROOT_DIR"
export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m keeper_adapter.cli "$@"
"""

BIN_P1_WORKER_TEMPLATE = """#!/bin/sh
set -eu

REPO_ROOT="/Users/satojunichi/Documents/openclaw/p1-core"

export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m p1_core.worker.ollama_worker "$@"
"""

OPENCLAW_AGENT_PROMPT_TEMPLATE = """# P1 OpenClaw Agent Surface

This file describes how OpenClaw should expose P1.

Principles:

- present P1 as a distinct agent alongside the main agent
- use OpenClaw only as transport, tool runtime, and presentation
- do not absorb P1 identity, memory, or governance into OpenClaw
- use `bin/p1` for autonomy advancement and `bin/p1-agent` for thin status/report transport

Recommended entry commands:

- `bin/p1-agent status`
- `bin/p1-agent report --kind daily`
- `bin/p1-agent approvals`
- `bin/p1 enqueue-message --content "hello P1"`
- `bin/p1 tick`
"""


def scaffold_workspace(root: Path, force: bool = False) -> list[Path]:
    created: list[Path] = []
    directories = [
        root / "agent",
        root / "bin",
        root / "state" / "reports",
        root / "state" / "reports" / "daily",
        root / "state" / "knowledge",
        root / "state" / "events",
        root / "state" / "policies",
        root / "state" / "proposals",
        root / "state" / "governance",
        root / "state" / "capabilities",
        root / "state" / "experiments",
        root / "state" / "conversation",
        root / "state" / "world",
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

    bin_p1_path = root / "bin" / "p1"
    if not bin_p1_path.exists() or force:
        bin_p1_path.write_text(BIN_P1_TEMPLATE, encoding="utf-8")
        bin_p1_path.chmod(0o755)
        created.append(bin_p1_path)

    bin_p1_agent_path = root / "bin" / "p1-agent"
    if not bin_p1_agent_path.exists() or force:
        bin_p1_agent_path.write_text(BIN_P1_AGENT_TEMPLATE, encoding="utf-8")
        bin_p1_agent_path.chmod(0o755)
        created.append(bin_p1_agent_path)

    bin_worker_path = root / "bin" / "p1-worker"
    if not bin_worker_path.exists() or force:
        bin_worker_path.write_text(BIN_P1_WORKER_TEMPLATE, encoding="utf-8")
        bin_worker_path.chmod(0o755)
        created.append(bin_worker_path)

    openclaw_agent_prompt_path = root / "agent" / "openclaw-agent.md"
    if not openclaw_agent_prompt_path.exists() or force:
        openclaw_agent_prompt_path.write_text(OPENCLAW_AGENT_PROMPT_TEMPLATE, encoding="utf-8")
        created.append(openclaw_agent_prompt_path)

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
