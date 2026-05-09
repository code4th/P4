from __future__ import annotations

import argparse
import json
import signal
from pathlib import Path

from p4_core import __mainline_date__, __version__
from p4_core.benchmark import run_benchmark_suite
from p4_core.dashboard.server import serve_dashboard
from p4_core.ollama_client import OllamaChatClient
from p4_core.runtime import AgentRuntime
from p4_core.workspace import DEFAULT_CONFIG, bootstrap_workspace


class OperatorInterrupt(Exception):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="P4 Core operator CLI")
    parser.add_argument("--root", required=True, help="Path to the P4 workspace root")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    bootstrap_parser = subparsers.add_parser("bootstrap", help="Bootstrap a new P4 workspace")
    bootstrap_parser.add_argument("--force", action="store_true")

    version_parser = subparsers.add_parser("version", help="Show P4 mainline version")
    del version_parser

    status_parser = subparsers.add_parser("status", help="Show runtime status")
    del status_parser

    ollama_parser = subparsers.add_parser("ollama-status", help="Check Ollama connectivity and visible models")
    del ollama_parser

    goal_parser = subparsers.add_parser("set-goal", help="Set the active goal")
    goal_parser.add_argument("--text", required=True)

    chat_parser = subparsers.add_parser("chat", help="Append a user message")
    chat_parser.add_argument("--message", required=True)
    chat_parser.add_argument("--run-immediately", action="store_true")
    chat_parser.add_argument("--model", default=None, help="Pin this message to an Ollama model visible in ollama-status")
    chat_parser.add_argument("--model-role", choices=["reasoning", "fast", "coding", "terminal"], default="coding")

    loop_parser = subparsers.add_parser("run-loop", help="Run until queue is empty")
    loop_parser.add_argument("--max-work-items", type=int, default=None)
    loop_parser.add_argument("--model", default=None, help="Override queued work with an Ollama model visible in ollama-status")
    loop_parser.add_argument("--model-role", choices=["reasoning", "fast", "coding", "terminal"], default="coding")

    worker_parser = subparsers.add_parser("worker", help="Run the background worker loop")
    del worker_parser

    dashboard_parser = subparsers.add_parser("dashboard", help="Serve the dashboard")
    dashboard_parser.add_argument("--host", default="127.0.0.1")
    dashboard_parser.add_argument("--port", type=int, default=8899)

    benchmark_parser = subparsers.add_parser("benchmark", help="Run a live P4 benchmark suite")
    benchmark_parser.add_argument("--models", nargs="+", required=True)
    benchmark_parser.add_argument("--execution-root", default=None)
    benchmark_parser.add_argument("--case-timeout-seconds", type=int, default=90)

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = Path(args.root).expanduser()

    if args.subcommand == "bootstrap":
        payload = bootstrap_workspace(root, force=args.force)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if args.subcommand == "version":
        payload = {
            "version": __version__,
            "mainline_date": __mainline_date__,
            "canonical_handoff": "handoff/p4-canonical-mainline-2026-04-26.md",
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    runtime = AgentRuntime(root)
    if args.subcommand == "status":
        payload = runtime.status_snapshot()
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if args.subcommand == "ollama-status":
        base_url = str(runtime.config.get("ollama_base_url") or DEFAULT_CONFIG["ollama_base_url"])
        payload = OllamaChatClient(base_url=base_url).list_models()
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if args.subcommand == "set-goal":
        payload = runtime.set_goal(args.text)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if args.subcommand == "chat":
        payload = _run_interruptible(
            runtime,
            lambda: runtime.send_message(
                args.message,
                run_immediately=args.run_immediately,
                model=args.model,
                model_role=args.model_role,
            ),
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if args.subcommand == "run-loop":
        selection_override = runtime._operator_model_selection(args.model, model_role=args.model_role) if args.model else None
        payload = _run_interruptible(
            runtime,
            lambda: runtime.run_until_idle(
                max_work_items=args.max_work_items,
                selection_override=selection_override,
            ),
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if args.subcommand == "worker":
        runtime.worker_loop()
        return
    if args.subcommand == "dashboard":
        serve_dashboard(root, host=args.host, port=args.port)
        return
    if args.subcommand == "benchmark":
        payload = run_benchmark_suite(
            root,
            models=list(args.models),
            execution_root=args.execution_root,
            case_timeout_seconds=int(args.case_timeout_seconds),
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    raise ValueError(f"unsupported subcommand: {args.subcommand}")


def _run_interruptible(runtime: AgentRuntime, fn):
    old_sigterm = signal.getsignal(signal.SIGTERM)
    old_sigint = signal.getsignal(signal.SIGINT)

    def _handle_stop(signum, frame):
        del frame
        raise OperatorInterrupt(f"received signal {signum}")

    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)
    try:
        return fn()
    except (KeyboardInterrupt, OperatorInterrupt) as exc:
        return runtime.record_operator_interrupt(operator_reason=str(exc) or "operator interrupted runtime")
    finally:
        signal.signal(signal.SIGTERM, old_sigterm)
        signal.signal(signal.SIGINT, old_sigint)


if __name__ == "__main__":
    main()
