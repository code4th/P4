from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


DEFAULT_OPENCLAW_HOME = Path("/Users/satojunichi/.openclaw")
DEFAULT_AGENT_NAME = "p1"
DEFAULT_SOURCE_AGENT = "main"
DEFAULT_WORKSPACE_ROOT = Path("/Users/satojunichi/.openclaw/workspace/systems/p1")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def install_openclaw_agent(
    *,
    openclaw_home: Path,
    workspace_root: Path,
    agent_name: str = DEFAULT_AGENT_NAME,
    source_agent: str = DEFAULT_SOURCE_AGENT,
    force: bool = False,
) -> list[Path]:
    created: list[Path] = []
    target_root = openclaw_home / "agents" / agent_name
    source_root = openclaw_home / "agents" / source_agent
    agent_dir = target_root / "agent"
    sessions_dir = target_root / "sessions"

    agent_dir.mkdir(parents=True, exist_ok=True)
    sessions_dir.mkdir(parents=True, exist_ok=True)
    created.extend([agent_dir, sessions_dir])

    source_models = source_root / "agent" / "models.json"
    source_auth = source_root / "agent" / "auth-profiles.json"
    target_models = agent_dir / "models.json"
    target_auth = agent_dir / "auth-profiles.json"

    if not source_models.exists():
        raise FileNotFoundError(
            f"missing source models.json for OpenClaw agent '{source_agent}' under {source_models}"
        )
    if not source_auth.exists():
        raise FileNotFoundError(
            f"missing source auth-profiles.json for OpenClaw agent '{source_agent}' under {source_auth}"
        )

    if not target_models.exists() or force:
        shutil.copy2(source_models, target_models)
        created.append(target_models)
    if not target_auth.exists() or force:
        shutil.copy2(source_auth, target_auth)
        created.append(target_auth)

    manifest = _read_json(workspace_root / "agent" / "manifest.json")
    profile = _read_json(workspace_root / "profile.json")
    target_entry = agent_dir / "p1-openclaw-entry.json"
    if not target_entry.exists() or force:
        _write_json(
            target_entry,
            {
                "agent_name": agent_name,
                "display_name": profile.get("display_name", "P1"),
                "workspace_root": str(workspace_root),
                "workspace_agent_manifest": str(workspace_root / "agent" / "manifest.json"),
                "workspace_agent_prompt": str(workspace_root / "agent" / "openclaw-agent.md"),
                "transport_entrypoint": str(workspace_root / "bin" / "p1-agent"),
                "identity_source": "workspace_external_core",
                "manifest": manifest,
            },
        )
        created.append(target_entry)

    target_sessions = sessions_dir / "sessions.json"
    if not target_sessions.exists() or force:
        _write_json(
            target_sessions,
            {
                f"agent:{agent_name}:main": {
                    "status": "ready",
                    "workspaceDir": str(workspace_root),
                    "transportEntrypoint": str(workspace_root / "bin" / "p1-agent"),
                    "interfaceKind": "openclaw-facing-thin-transport",
                }
            },
        )
        created.append(target_sessions)

    return created


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install a P1 OpenClaw agent slot scaffold")
    parser.add_argument("--openclaw-home", default=str(DEFAULT_OPENCLAW_HOME))
    parser.add_argument("--workspace-root", default=str(DEFAULT_WORKSPACE_ROOT))
    parser.add_argument("--agent-name", default=DEFAULT_AGENT_NAME)
    parser.add_argument("--source-agent", default=DEFAULT_SOURCE_AGENT)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    created = install_openclaw_agent(
        openclaw_home=Path(args.openclaw_home).expanduser(),
        workspace_root=Path(args.workspace_root).expanduser(),
        agent_name=args.agent_name,
        source_agent=args.source_agent,
        force=args.force,
    )
    print(
        json.dumps(
            {
                "ok": True,
                "agent_name": args.agent_name,
                "created": [str(path) for path in created],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
