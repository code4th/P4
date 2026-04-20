from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from p2_core.dashboard import serve_dashboard
from p2_core.loop import run_loop, show_attempt
from p2_core.workspace import (
    bootstrap_workspace,
    build_status_snapshot,
    read_history,
    read_json,
    reset_workspace,
    resolve_model_roles,
    resolve_runtime_kernel,
    sync_self_model_payload,
)


def operator_bootstrap(root: Path, *, force: bool = False) -> dict[str, Any]:
    return bootstrap_workspace(root, force=force)


def operator_status(root: Path) -> dict[str, Any]:
    return build_status_snapshot(root)


def operator_run_loop(
    root: Path,
    *,
    model: str,
    thinking_model: str | None = None,
    coding_model: str | None = None,
    exploratory_coding_model: str | None = None,
    stagnation_coding_model: str | None = None,
    max_iterations: int = 1,
) -> dict[str, Any]:
    return run_loop(
        root,
        model=model,
        thinking_model=thinking_model,
        coding_model=coding_model,
        exploratory_coding_model=exploratory_coding_model,
        stagnation_coding_model=stagnation_coding_model,
        max_iterations=max_iterations,
    )


def operator_show_history(root: Path, *, limit: int = 20) -> dict[str, Any]:
    rows = read_history(root, limit=limit)
    return {"count": len(rows), "history": rows}


def operator_show_attempt(root: Path, *, candidate_id: str) -> dict[str, Any]:
    return show_attempt(root, candidate_id)


def operator_reset(root: Path, *, mode: str) -> dict[str, Any]:
    return reset_workspace(root, mode=mode)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Unified operator CLI for P2 Core")
    parser.add_argument("--root", required=True, help="Path to the P2 workspace root")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    bootstrap_parser = subparsers.add_parser("bootstrap", help="Bootstrap a new P2 workspace")
    bootstrap_parser.add_argument("--force", action="store_true")

    subparsers.add_parser("status", help="Show workspace status")

    run_loop_parser = subparsers.add_parser("run-loop", help="Run one or more self-edit loop iterations")
    run_loop_parser.add_argument("--model", default=None)
    run_loop_parser.add_argument("--thinking-model", default=None)
    run_loop_parser.add_argument("--coding-model", default=None)
    run_loop_parser.add_argument("--exploratory-coding-model", default=None)
    run_loop_parser.add_argument("--stagnation-coding-model", default=None)
    run_loop_parser.add_argument("--max-iterations", type=int, default=1)

    history_parser = subparsers.add_parser("show-history", help="Show recent history rows")
    history_parser.add_argument("--limit", type=int, default=20)

    attempt_parser = subparsers.add_parser("show-attempt", help="Inspect a single attempt in detail")
    attempt_parser.add_argument("--candidate-id", required=True)

    reset_parser = subparsers.add_parser("reset", help="Reset the workspace back to bootstrap state")
    reset_parser.add_argument("--mode", default="initial")

    dashboard_parser = subparsers.add_parser("dashboard", help="Serve the realtime dashboard")
    dashboard_parser.add_argument("--host", default="127.0.0.1")
    dashboard_parser.add_argument("--port", type=int, default=8897)

    watchdog_parser = subparsers.add_parser("watchdog", help="Run the background worker and dashboard watchdog")
    watchdog_parser.add_argument("--model", default=None)
    watchdog_parser.add_argument("--thinking-model", default=None)
    watchdog_parser.add_argument("--coding-model", default=None)
    watchdog_parser.add_argument("--exploratory-coding-model", default=None)
    watchdog_parser.add_argument("--stagnation-coding-model", default=None)
    watchdog_parser.add_argument("--host", default="127.0.0.1")
    watchdog_parser.add_argument("--port", type=int, default=8897)

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = Path(args.root).expanduser()

    if args.subcommand == "bootstrap":
        payload = operator_bootstrap(root, force=args.force)
    elif args.subcommand == "status":
        payload = operator_status(root)
    elif args.subcommand == "run-loop":
        model_roles = resolve_model_roles(
            root,
            model=args.model,
            thinking_model=args.thinking_model,
            coding_model=args.coding_model,
            exploratory_coding_model=args.exploratory_coding_model,
            stagnation_coding_model=args.stagnation_coding_model,
        )
        payload = operator_run_loop(
            root,
            model=model_roles["model"],
            thinking_model=model_roles["thinking_model"],
            coding_model=model_roles["coding_model"],
            exploratory_coding_model=model_roles["exploratory_coding_model"],
            stagnation_coding_model=model_roles["stagnation_coding_model"],
            max_iterations=args.max_iterations,
        )
    elif args.subcommand == "show-history":
        payload = operator_show_history(root, limit=args.limit)
    elif args.subcommand == "show-attempt":
        payload = operator_show_attempt(root, candidate_id=args.candidate_id)
    elif args.subcommand == "reset":
        payload = operator_reset(root, mode=args.mode)
    elif args.subcommand == "dashboard":
        serve_dashboard(root, host=args.host, port=args.port)
        return
    elif args.subcommand == "watchdog":
        model_roles = resolve_model_roles(
            root,
            model=args.model,
            thinking_model=args.thinking_model,
            coding_model=args.coding_model,
            exploratory_coding_model=args.exploratory_coding_model,
            stagnation_coding_model=args.stagnation_coding_model,
        )
        watchdog = Path(__file__).resolve().parent.parent / "scripts" / "p2_watchdog.py"
        env = os.environ.copy()
        env["P2_ROOT"] = str(root)
        env["P2_MODEL"] = model_roles["model"]
        env["P2_THINKING_MODEL"] = model_roles["thinking_model"]
        env["P2_CODING_MODEL"] = model_roles["coding_model"]
        env["P2_EXPLORATORY_CODING_MODEL"] = model_roles["exploratory_coding_model"]
        env["P2_STAGNATION_CODING_MODEL"] = model_roles["stagnation_coding_model"]
        env["P2_RUNTIME_KERNEL"] = resolve_runtime_kernel(root)
        env["P2_DASHBOARD_HOST"] = args.host
        env["P2_DASHBOARD_PORT"] = str(args.port)
        os.execve(sys.executable, [sys.executable, "-u", str(watchdog)], env)
    else:
        raise ValueError(f"unsupported subcommand: {args.subcommand}")

    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
