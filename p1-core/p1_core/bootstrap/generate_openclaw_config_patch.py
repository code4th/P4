from __future__ import annotations

import argparse
import json
from pathlib import Path


DEFAULT_OPENCLAW_HOME = Path("/Users/satojunichi/.openclaw")
DEFAULT_WORKSPACE_ROOT = Path("/Users/satojunichi/.openclaw/workspace/systems/p1")
DEFAULT_AGENT_NAME = "p1"


def build_agent_entry(*, agent_name: str, workspace_root: Path, openclaw_home: Path) -> dict:
    return {
        "id": agent_name,
        "name": agent_name,
        "workspace": str(workspace_root),
        "agentDir": str(openclaw_home / "agents" / agent_name / "agent"),
        "model": "openai-codex/gpt-5.4-mini",
        "tools": {
            "profile": "full",
            "deny": ["subagents"],
        },
    }


def generate_patch(
    *,
    openclaw_home: Path,
    workspace_root: Path,
    agent_name: str = DEFAULT_AGENT_NAME,
) -> list[Path]:
    patch_dir = workspace_root / "agent"
    patch_dir.mkdir(parents=True, exist_ok=True)

    target = patch_dir / "openclaw-config-agent-entry.json"
    target.write_text(
        json.dumps(
            build_agent_entry(
                agent_name=agent_name,
                workspace_root=workspace_root,
                openclaw_home=openclaw_home,
            ),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    instructions = patch_dir / "openclaw-config-apply.md"
    instructions.write_text(
        "\n".join(
            [
                "# OpenClaw Config Patch",
                "",
                "Add the following entry to `~/.openclaw/openclaw.json` under `agents.list`.",
                "",
                f"- Source entry: `{target}`",
                f"- Agent slot scaffold: `{openclaw_home / 'agents' / agent_name}`",
                f"- Workspace entrypoint: `{workspace_root / 'bin' / 'p1-agent'}`",
                f"- Workspace manifest: `{workspace_root / 'agent' / 'manifest.json'}`",
                "",
                "Do not move P1 memory, governance, or rollback into OpenClaw config.",
                "Only register the minimal agent slot in `openclaw.json` and keep P1 identity details in the external workspace.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return [target, instructions]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a safe OpenClaw config patch for P1 agent registration")
    parser.add_argument("--openclaw-home", default=str(DEFAULT_OPENCLAW_HOME))
    parser.add_argument("--workspace-root", default=str(DEFAULT_WORKSPACE_ROOT))
    parser.add_argument("--agent-name", default=DEFAULT_AGENT_NAME)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    created = generate_patch(
        openclaw_home=Path(args.openclaw_home).expanduser(),
        workspace_root=Path(args.workspace_root).expanduser(),
        agent_name=args.agent_name,
    )
    print(json.dumps({"ok": True, "created": [str(path) for path in created]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
