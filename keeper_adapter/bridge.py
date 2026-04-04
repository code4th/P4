from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_P1_ROOT = Path("/Users/satojunichi/.openclaw/workspace/systems/p1")


def p1_root() -> Path:
    return Path(os.environ.get("OPENCLAW_P1_ROOT", DEFAULT_P1_ROOT)).expanduser().resolve()


def reports_dir() -> Path:
    return p1_root() / "state" / "reports" / "daily"


def health_path() -> Path:
    return p1_root() / "state" / "health.json"


def intervene_script() -> Path:
    return p1_root() / "intervene.js"


def read_json(path: Path, fallback: Any = None) -> Any:
    try:
        return json.loads(path.read_text())
    except Exception:
        return fallback


def latest_report_path(kind: str = "glance", date: str | None = None) -> Path | None:
    directory = reports_dir()
    if date:
      candidate = directory / f"{date}-{kind}.json"
      return candidate if candidate.exists() else None
    pattern = f"*-{kind}.json"
    files = sorted(directory.glob(pattern), key=lambda item: item.stat().st_mtime, reverse=True)
    return files[0] if files else None


def latest_report(kind: str = "glance", date: str | None = None) -> dict[str, Any]:
    path = latest_report_path(kind=kind, date=date)
    if not path:
        raise FileNotFoundError(f"no P1 {kind} report found")
    report = read_json(path, {})
    report["_source_path"] = str(path)
    return report


def approvals(date: str | None = None) -> dict[str, Any]:
    report = latest_report(kind="glance", date=date)
    pending = report.get("tuningSummary", {}).get("approvalPending", [])
    if not pending:
        pending = read_json(health_path(), {}).get("approvalPending", [])
    return {
        "kind": "approvals",
        "status": report.get("status", "unknown"),
        "pending": pending,
        "source": report.get("_source_path"),
    }


@dataclass(frozen=True)
class RoutedCommand:
    command: str
    risk_tier: str


def classify_command(command: str) -> RoutedCommand:
    normalized = " ".join(command.strip().split())
    lower = normalized.lower()
    if lower in {"continue", "stop"}:
        return RoutedCommand(command=normalized, risk_tier="bounded_operational")
    if lower.startswith(("priority change", "weight change", "skepticism ", "exploration ", "stability ")):
        return RoutedCommand(command=normalized, risk_tier="bounded_operational")
    if lower.startswith(("approve", "reject", "rollback")):
        return RoutedCommand(command=normalized, risk_tier="approval_required")
    return RoutedCommand(command=normalized, risk_tier="approval_required")


def execute_intervention(command: str) -> dict[str, Any]:
    routed = classify_command(command)
    script = intervene_script()
    result = subprocess.run(
        ["node", str(script), f"--command={routed.command}"],
        capture_output=True,
        text=True,
        check=False,
    )
    payload = parse_json_output(result.stdout)
    return {
        "ok": result.returncode == 0,
        "riskTier": routed.risk_tier,
        "command": routed.command,
        "stdout": payload if payload is not None else result.stdout.strip(),
        "stderr": result.stderr.strip(),
        "exitCode": result.returncode,
    }


def parse_json_output(raw: str) -> Any:
    text = raw.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def keeper_status(date: str | None = None) -> dict[str, Any]:
    report = latest_report(kind="glance", date=date)
    return {
        "kind": "status",
        "status": report.get("status"),
        "mainPoints": report.get("mainPoints", []),
        "recommendedInterventions": report.get("recommendedInterventions", []),
        "trackSummary": report.get("trackSummary", {}),
        "source": report.get("_source_path"),
    }


def keeper_report(date: str | None = None, kind: str = "daily") -> dict[str, Any]:
    report = latest_report(kind=kind, date=date)
    report["kind"] = kind
    return report

