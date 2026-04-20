from __future__ import annotations

import http.client
import json
import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any


ROOT = Path("/Users/satojunichi/.openclaw/workspace/systems/p1")
P1_CORE_DIR = Path("/Users/satojunichi/Documents/openclaw/p1-core")
LOG_DIR = ROOT / "state" / "processes"
LOOP_LOG = LOG_DIR / "autonomy-loop.log"
DASHBOARD_LOG = LOG_DIR / "dashboard.log"
MONITOR_LOG = LOG_DIR / "monitor.log"
STATE_PATH = ROOT / "state" / "autonomy" / "runtime-state.json"
DASHBOARD_URL = "http://127.0.0.1:8898"


@dataclass
class ManagedProcess:
    name: str
    command: list[str]
    log_path: Path
    process: subprocess.Popen[str] | None = None

    def start(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = self.log_path.open("a", encoding="utf-8")
        self.process = subprocess.Popen(
            self.command,
            cwd=P1_CORE_DIR,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        self._log(f"started pid={self.process.pid} cmd={' '.join(self.command)}")

    def poll(self) -> int | None:
        if self.process is None:
            return None
        return self.process.poll()

    def stop(self) -> None:
        if self.process is None:
            return
        if self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        self._log(f"stopped rc={self.process.poll() if self.process else 'unknown'}")
        self.process = None

    def restart(self, *, reason: str) -> None:
        self._log(f"restarting because {reason}")
        self.stop()
        self.start()

    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def _log(self, message: str) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"[supervisor:{self.name}] {time.strftime('%Y-%m-%dT%H:%M:%S%z')} {message}\n")


class HealthMonitor:
    def __init__(self, root: Path, log_path: Path) -> None:
        self.root = root
        self.log_path = log_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._dashboard_failures = 0
        self._loop_failures = 0

    def read_state(self) -> dict[str, Any]:
        if not STATE_PATH.exists():
            return {}
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def dashboard_ok(self) -> bool:
        try:
            conn = http.client.HTTPConnection("127.0.0.1", 8898, timeout=2)
            conn.request("GET", "/api/health")
            resp = conn.getresponse()
            body = resp.read()
            ok = resp.status == 200 and body.strip() == b"ok"
            conn.close()
            return ok
        except (OSError, socket.timeout, http.client.HTTPException):
            return False

    def loop_fresh(self, *, max_age_seconds: int = 120) -> bool:
        state = self.read_state()
        last_tick_at = state.get("last_tick_at")
        if not last_tick_at:
            return False
        try:
            moment = datetime.fromisoformat(last_tick_at)
        except ValueError:
            return False
        now = datetime.now(timezone.utc)
        return (now - moment) <= timedelta(seconds=max_age_seconds)

    def log(self, message: str) -> None:
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"[monitor] {time.strftime('%Y-%m-%dT%H:%M:%S%z')} {message}\n")

    def record_dashboard_probe(self, ok: bool) -> bool:
        self._dashboard_failures = 0 if ok else self._dashboard_failures + 1
        return self._dashboard_failures >= 3

    def record_loop_probe(self, ok: bool) -> bool:
        self._loop_failures = 0 if ok else self._loop_failures + 1
        return self._loop_failures >= 3

def main() -> None:
    loop = ManagedProcess(
        name="autonomy-loop",
        command=[sys.executable, "-u", str(P1_CORE_DIR / "scripts" / "p1_autonomy_loop.py")],
        log_path=LOOP_LOG,
    )
    dashboard = ManagedProcess(
        name="dashboard",
        command=[
            sys.executable,
            "-u",
            "-m",
            "p1_core.cli",
            "--root",
            str(ROOT),
        ] + (["--verification-mode"] if os.environ.get("P1_VERIFICATION_MODE") == "1" else []) + [
            "dashboard",
            "--host",
            "127.0.0.1",
            "--port",
            "8898",
        ],
        log_path=DASHBOARD_LOG,
    )
    monitor = HealthMonitor(ROOT, MONITOR_LOG)
    processes = [loop, dashboard]
    for proc in processes:
        proc.start()

    try:
        while True:
            time.sleep(5)
            monitor.log(
                "tick "
                + " ".join(
                    f"{proc.name}={'alive' if proc.alive() else 'dead'}"
                    for proc in processes
                )
            )
            for proc in processes:
                rc = proc.poll()
                if rc is not None:
                    monitor.log(f"{proc.name} exited rc={rc}")
                    proc.restart(reason=f"exit rc={rc}")
            dashboard_ok = dashboard.alive() and monitor.dashboard_ok()
            if monitor.record_dashboard_probe(dashboard_ok):
                monitor.log("dashboard probe failed 3x; restarting dashboard")
                dashboard.restart(reason="dashboard probe failed")
            loop_ok = loop.alive() and monitor.loop_fresh()
            if monitor.record_loop_probe(loop_ok):
                monitor.log("loop probe failed 3x; restarting autonomy loop")
                loop.restart(reason="loop probe failed or stale")
    except KeyboardInterrupt:
        pass
    finally:
        for proc in processes:
            proc.stop()


if __name__ == "__main__":
    main()
