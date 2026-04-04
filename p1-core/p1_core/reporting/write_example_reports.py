from __future__ import annotations

import argparse
import json
from pathlib import Path

from .report_writer import ReportWriter


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write example P1 reports into a workspace")
    parser.add_argument(
        "--root",
        default="/Users/satojunichi/.openclaw/workspace/systems/p1",
        help="Target P1 workspace root",
    )
    parser.add_argument("--date", default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    writer = ReportWriter(Path(args.root).expanduser())
    glance = writer.write_glance(
        status="bootstrapping",
        main_points=[
            "external core is active",
            "local worker is available for bounded cognition",
        ],
        recommended_interventions=[
            "keep OpenClaw as thin control plane",
            "promote only proposal snapshots",
        ],
        track_summary={
            "worker": "healthy",
            "governance_mode": "proposal_only",
        },
        approval_pending=[
            {"type": "policy_change", "id": "proposal:bootstrap-example"},
        ],
        date=args.date,
    )
    daily = writer.write_daily(
        status="bootstrapping",
        summary="P1 external core generated initial workspace and report contract.",
        sections=[
            {
                "title": "Boundary",
                "points": [
                    "OpenClaw remains transport and execution only",
                    "growth policy remains outside OpenClaw",
                ],
            }
        ],
        proposals=[
            {
                "id": "proposal:bootstrap-example",
                "summary": "connect daily report production to live bridge",
                "state": "pending_approval",
            }
        ],
        date=args.date,
    )
    health = writer.write_health(
        status="bootstrapping",
        approval_pending=[{"type": "policy_change", "id": "proposal:bootstrap-example"}],
        notes=["example reports generated"],
    )
    print(
        json.dumps(
            {"ok": True, "glance": str(glance), "daily": str(daily), "health": str(health)},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
