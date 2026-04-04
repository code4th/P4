from __future__ import annotations

import argparse
import json

from .bridge import approvals, classify_command, execute_intervention, keeper_report, keeper_status


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenClaw adapter for the P1 Keeper")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    status_parser = subparsers.add_parser("status", help="Read the latest P1 glance report")
    status_parser.add_argument("--date")

    report_parser = subparsers.add_parser("report", help="Read the latest P1 report")
    report_parser.add_argument("--date")
    report_parser.add_argument("--kind", choices=["daily", "glance", "weekly"], default="daily")

    approvals_parser = subparsers.add_parser("approvals", help="Show approval-pending state from P1")
    approvals_parser.add_argument("--date")

    intervene_parser = subparsers.add_parser("intervene", help="Forward a command to P1 intervene.js")
    intervene_parser.add_argument("command")

    for name in ("approve", "reject", "rollback"):
        action_parser = subparsers.add_parser(name, help=f"Forward a {name} command to P1")
        action_parser.add_argument("args", nargs="*")

    risk_parser = subparsers.add_parser("risk", help="Classify a Keeper command without executing it")
    risk_parser.add_argument("command")

    args = parser.parse_args()

    if args.subcommand == "status":
        payload = keeper_status(date=args.date)
    elif args.subcommand == "report":
        payload = keeper_report(date=args.date, kind=args.kind)
    elif args.subcommand == "approvals":
        payload = approvals(date=args.date)
    elif args.subcommand == "intervene":
        payload = execute_intervention(args.command)
    elif args.subcommand in {"approve", "reject", "rollback"}:
        command = " ".join([args.subcommand, *args.args]).strip()
        payload = execute_intervention(command)
    else:
        routed = classify_command(args.command)
        payload = {"command": routed.command, "riskTier": routed.risk_tier}

    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
