from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path

from p2_core.dashboard_presenter import render_dashboard_html
from p2_core.dashboard_runtime import (
    dashboard_health_ok as _dashboard_health_ok_runtime,
    terminate_standalone_dashboard_listeners as _terminate_standalone_dashboard_listeners_runtime,
)
from p2_core.dashboard_server import create_dashboard_server as _create_dashboard_server_core
from p2_core.dashboard_service import serve_dashboard as _serve_dashboard_service
from p2_core.dashboard_snapshot import build_public_snapshot
from p2_core.dashboard_script import DASHBOARD_SCRIPT
from p2_core.workspace import WorkspacePaths, now_iso, read_json, update_goal_from_dashboard, update_runtime_status, write_json


dashboard_health_ok = _dashboard_health_ok_runtime
terminate_standalone_dashboard_listeners = _terminate_standalone_dashboard_listeners_runtime




def create_dashboard_server(root: Path, *, host: str, port: int) -> ThreadingHTTPServer:
    def _goal_update_handler(target_root: Path, payload: dict[str, object]) -> dict[str, object]:
        goal_text = str(payload.get("goal_text") or "").strip()
        reset_mode = str(payload.get("reset_mode") or "").strip() or None
        if reset_mode == "none":
            reset_mode = None
        return update_goal_from_dashboard(target_root, goal_text=goal_text, reset_mode=reset_mode)

    def _is_pid_running(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def _read_pid(path: Path) -> int | None:
        if not path.exists():
            return None
        raw = path.read_text(encoding="utf-8").strip()
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    def _write_worker_shutdown_marker(target_root: Path, *, reason: str, phase: str, note: str) -> None:
        runtime_path = WorkspacePaths(target_root).runtime_status_path
        runtime = read_json(runtime_path, fallback={})
        payload = {
            "reason": reason,
            "phase": phase,
            "note": note,
            "candidate_id": runtime.get("current_candidate_id"),
            "current_action": runtime.get("current_action"),
            "current_action_step": runtime.get("current_action_step"),
            "written_at": now_iso(),
        }
        marker_path = WorkspacePaths(target_root).runtime_state_dir / "worker_shutdown.json"
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        marker_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _set_goal_status(target_root: Path, *, status: str) -> None:
        goal_path = WorkspacePaths(target_root).goal_path
        goal = read_json(goal_path, fallback={})
        if not isinstance(goal, dict):
            return
        goal["status"] = status
        goal["updated_at"] = now_iso()
        write_json(goal_path, goal)

    def _control_handler(target_root: Path, payload: dict[str, object]) -> dict[str, object]:
        action = str(payload.get("action") or "").strip().lower()
        if action not in {"start", "stop"}:
            raise ValueError("action must be start or stop")

        paths = WorkspacePaths(target_root)
        worker_pid_path = paths.runtime_state_dir / "worker.pid"
        watchdog_pid_path = paths.runtime_state_dir / "watchdog.pid"
        loop_log_path = paths.logs_dir / "p2-loop.log"
        loop_log_path.parent.mkdir(parents=True, exist_ok=True)

        worker_pid = _read_pid(worker_pid_path)
        watchdog_pid = _read_pid(watchdog_pid_path)
        watchdog_running = bool(watchdog_pid and _is_pid_running(watchdog_pid))
        worker_running = bool(worker_pid and _is_pid_running(worker_pid))

        if action == "stop":
            _set_goal_status(target_root, status="paused")
            if worker_running and worker_pid:
                _write_worker_shutdown_marker(
                    target_root,
                    reason="dashboard stop requested",
                    phase="dashboard_stop",
                    note="ダッシュボードから停止が要求されました。",
                )
                try:
                    os.kill(worker_pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            update_runtime_status(
                target_root,
                status="idle",
                phase=None,
                current_action=None,
                current_action_step=None,
                last_event="dashboard_stop_requested",
                worker_heartbeat_at=now_iso(),
            )
            return {
                "ok": True,
                "action": "stop",
                "goal_status": "paused",
                "worker_pid": worker_pid,
                "watchdog_running": watchdog_running,
            }

        # start
        _set_goal_status(target_root, status="active")
        started_pid: int | None = None
        if worker_running:
            started_pid = worker_pid
        else:
            command = [sys.executable, "-u", str(target_root / "scripts" / "p2_loop_worker.py")]
            with loop_log_path.open("a", encoding="utf-8") as handle:
                proc = subprocess.Popen(
                    command,
                    cwd=target_root,
                    stdin=subprocess.DEVNULL,
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                    text=True,
                    start_new_session=False,
                    env={**os.environ, "PYTHONUNBUFFERED": "1", "P2_ROOT": str(target_root)},
                )
            started_pid = proc.pid
            worker_pid_path.write_text(str(started_pid), encoding="utf-8")
        update_runtime_status(
            target_root,
            last_event="dashboard_start_requested",
            worker_heartbeat_at=now_iso(),
        )
        return {
            "ok": True,
            "action": "start",
            "goal_status": "active",
            "worker_pid": started_pid,
            "watchdog_running": watchdog_running,
        }

    return _create_dashboard_server_core(
        root,
        host=host,
        port=port,
        snapshot_builder=build_public_snapshot,
        html_renderer=render_dashboard_html,
        dashboard_script=DASHBOARD_SCRIPT,
        goal_update_handler=_goal_update_handler,
        control_handler=_control_handler,
    )


def serve_dashboard(root: Path, *, host: str = "127.0.0.1", port: int = 8897) -> None:
    _serve_dashboard_service(
        root,
        host=host,
        port=port,
        dashboard_health_ok=dashboard_health_ok,
        terminate_standalone_dashboard_listeners=terminate_standalone_dashboard_listeners,
        create_dashboard_server=create_dashboard_server,
    )
