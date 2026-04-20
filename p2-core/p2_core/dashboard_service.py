from __future__ import annotations

import os
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from p2_core.workspace import WorkspacePaths, read_json, reconcile_runtime_status, update_runtime_status


def serve_dashboard(
    root: Path,
    *,
    host: str,
    port: int,
    dashboard_health_ok: Callable[..., bool],
    terminate_standalone_dashboard_listeners: Callable[..., list[int]],
    create_dashboard_server: Callable[..., ThreadingHTTPServer],
) -> None:
    runtime_status = reconcile_runtime_status(
        root,
        read_json(WorkspacePaths(root).runtime_status_path, fallback={}),
    )
    dashboard_owner = str(runtime_status.get("dashboard_owner") or "")
    if dashboard_owner == "watchdog" and dashboard_health_ok(host=host, port=port):
        print(
            f"[P2 dashboard] watchdog 管理中の dashboard が http://{host}:{port} で稼働中です。単体起動は行いません。",
            flush=True,
        )
        return
    terminate_standalone_dashboard_listeners(root=root, host=host, port=port, keep_pid=os.getpid())
    update_runtime_status(
        root,
        dashboard_notify_url=f"http://{host}:{port}/api/notify",
        dashboard_health_url=f"http://{host}:{port}/api/health",
        dashboard_owner="standalone",
        dashboard_mode="process",
        last_event="dashboard_started",
    )
    server = create_dashboard_server(root, host=host, port=port)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        current_runtime_status = read_json(WorkspacePaths(root).runtime_status_path, fallback={})
        if str(current_runtime_status.get("dashboard_owner") or "") == "standalone":
            update_runtime_status(
                root,
                dashboard_notify_url=None,
                dashboard_health_url=None,
                dashboard_owner=None,
                dashboard_mode=None,
                last_event="dashboard_stopped",
            )
        server.server_close()
