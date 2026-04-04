from __future__ import annotations

import argparse
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path


DEFAULT_OPENCLAW_HOME = Path("/Users/satojunichi/.openclaw")
DEFAULT_WORKSPACE_ROOT = Path("/Users/satojunichi/.openclaw/workspace/systems/p1")
DEFAULT_AGENT_NAME = "p1"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _backup_path(config_path: Path, agent_name: str) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return config_path.with_name(f"{config_path.name}.bak-{agent_name}-{stamp}")


def apply_patch(
    *,
    config_path: Path,
    workspace_root: Path,
    agent_name: str = DEFAULT_AGENT_NAME,
    backup: bool = True,
) -> dict:
    entry_path = workspace_root / "agent" / "openclaw-config-agent-entry.json"
    config = _read_json(config_path)
    entry = _read_json(entry_path)
    agents = config.setdefault("agents", {}).setdefault("list", [])

    if any(item.get("id") == agent_name for item in agents):
        return {
            "ok": True,
            "changed": False,
            "reason": f"agent '{agent_name}' is already registered",
            "configPath": str(config_path),
        }

    backup_path = None
    if backup:
        backup_path = _backup_path(config_path, agent_name)
        shutil.copy2(config_path, backup_path)

    agents.append(entry)
    _write_json(config_path, config)
    return {
        "ok": True,
        "changed": True,
        "configPath": str(config_path),
        "entryPath": str(entry_path),
        "backupPath": str(backup_path) if backup_path else None,
    }


def rollback_patch(*, config_path: Path, agent_name: str = DEFAULT_AGENT_NAME, backup: bool = True) -> dict:
    config = _read_json(config_path)
    agents = config.setdefault("agents", {}).setdefault("list", [])
    filtered = [item for item in agents if item.get("id") != agent_name]
    if len(filtered) == len(agents):
        return {
            "ok": True,
            "changed": False,
            "reason": f"agent '{agent_name}' was not registered",
            "configPath": str(config_path),
        }

    backup_path = None
    if backup:
        backup_path = _backup_path(config_path, f"{agent_name}-rollback")
        shutil.copy2(config_path, backup_path)

    config["agents"]["list"] = filtered
    _write_json(config_path, config)
    return {
        "ok": True,
        "changed": True,
        "configPath": str(config_path),
        "backupPath": str(backup_path) if backup_path else None,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply or rollback the generated OpenClaw config patch for P1")
    parser.add_argument("--config-path", default=str(DEFAULT_OPENCLAW_HOME / "openclaw.json"))
    parser.add_argument("--workspace-root", default=str(DEFAULT_WORKSPACE_ROOT))
    parser.add_argument("--agent-name", default=DEFAULT_AGENT_NAME)
    parser.add_argument("--rollback", action="store_true")
    parser.add_argument("--no-backup", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.rollback:
        payload = rollback_patch(
            config_path=Path(args.config_path).expanduser(),
            agent_name=args.agent_name,
            backup=not args.no_backup,
        )
    else:
        payload = apply_patch(
            config_path=Path(args.config_path).expanduser(),
            workspace_root=Path(args.workspace_root).expanduser(),
            agent_name=args.agent_name,
            backup=not args.no_backup,
        )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
