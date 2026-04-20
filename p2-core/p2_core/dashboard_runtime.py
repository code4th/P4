from __future__ import annotations

import os
import signal
import socket
import subprocess
import threading
from pathlib import Path


def dashboard_health_ok(*, host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.5) as sock:
            request_bytes = (
                f"GET /api/health HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: close\r\n\r\n".encode("utf-8")
            )
            sock.sendall(request_bytes)
            payload = sock.recv(4096).decode("utf-8", errors="replace")
        return "200 OK" in payload and payload.rstrip().endswith("ok")
    except OSError:
        return False


def _find_dashboard_cli_pids(*, root: Path, host: str, port: int) -> list[int]:
    signature = [
        "p2_core.cli",
        "--root",
        str(root),
        "dashboard",
        "--host",
        host,
        "--port",
        str(port),
    ]
    try:
        output = subprocess.check_output(["pgrep", "-af", "p2_core\\.cli"], text=True)
    except subprocess.CalledProcessError:
        return []
    pids: list[int] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            continue
        pid_text, command = parts
        if all(token in command for token in signature):
            try:
                pids.append(int(pid_text))
            except ValueError:
                continue
    return pids


def terminate_standalone_dashboard_listeners(*, root: Path, host: str, port: int, keep_pid: int | None = None) -> list[int]:
    targets = [pid for pid in _find_dashboard_cli_pids(root=root, host=host, port=port) if pid != keep_pid]
    for pid in targets:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
    deadline = threading.Event()
    deadline.wait(0.3)
    remaining = [pid for pid in targets if pid in _find_dashboard_cli_pids(root=root, host=host, port=port)]
    for pid in remaining:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            continue
    return targets
