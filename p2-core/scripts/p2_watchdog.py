from __future__ import annotations

import hashlib
import http.client
import json
import os
import signal
import socket
import subprocess
import sys
import time
import threading
import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import TextIOWrapper
from pathlib import Path
from typing import Any

ROOT = Path(os.environ.get("P2_ROOT", "/tmp/p2-demo")).expanduser()
P2_CORE_DIR = Path(__file__).resolve().parent.parent
if str(P2_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(P2_CORE_DIR))

from p2_core.dashboard import create_dashboard_server
from p2_core.workspace import (
    build_status_snapshot,
    ensure_workspace_prerequisites,
    now_iso,
    resolve_model_roles,
    resolve_runtime_kernel,
    update_runtime_status,
)

DASHBOARD_HOST = os.environ.get("P2_DASHBOARD_HOST", "127.0.0.1")
DASHBOARD_PORT = int(os.environ.get("P2_DASHBOARD_PORT", "8897"))
_MODEL_ROLES = resolve_model_roles(
    ROOT,
    model=os.environ.get("P2_MODEL"),
    thinking_model=os.environ.get("P2_THINKING_MODEL"),
    coding_model=os.environ.get("P2_CODING_MODEL"),
    exploratory_coding_model=os.environ.get("P2_EXPLORATORY_CODING_MODEL"),
    stagnation_coding_model=os.environ.get("P2_STAGNATION_CODING_MODEL"),
)
MODEL = _MODEL_ROLES["model"]
THINKING_MODEL = _MODEL_ROLES["thinking_model"]
CODING_MODEL = _MODEL_ROLES["coding_model"]
EXPLORATORY_CODING_MODEL = _MODEL_ROLES["exploratory_coding_model"]
STAGNATION_CODING_MODEL = _MODEL_ROLES["stagnation_coding_model"]
RUNTIME_KERNEL = resolve_runtime_kernel(ROOT, runtime_kernel=os.environ.get("P2_RUNTIME_KERNEL"))
MONITOR_LOG = ROOT / "logs" / "p2-watchdog.log"
STATE_PATH = ROOT / "state" / "runtime" / "status.json"
WATCHDOG_PID_PATH = ROOT / "state" / "runtime" / "watchdog.pid"
WORKER_PID_PATH = ROOT / "state" / "runtime" / "worker.pid"
WORKER_SHUTDOWN_MARKER_PATH = ROOT / "state" / "runtime" / "worker_shutdown.json"


def _is_pid_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate_pid(pid: int, *, timeout_seconds: float = 4.0) -> bool:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if not _is_pid_running(pid):
            return True
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    return not _is_pid_running(pid)


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


def _claim_watchdog_slot() -> int | None:
    WATCHDOG_PID_PATH.parent.mkdir(parents=True, exist_ok=True)
    replaced_pid: int | None = None
    previous_pid = _read_pid(WATCHDOG_PID_PATH)
    if previous_pid and previous_pid != os.getpid() and _is_pid_running(previous_pid):
        _terminate_pid(previous_pid)
        replaced_pid = previous_pid
    WATCHDOG_PID_PATH.write_text(str(os.getpid()), encoding="utf-8")
    return replaced_pid


def _release_watchdog_slot() -> None:
    current = _read_pid(WATCHDOG_PID_PATH)
    if current == os.getpid():
        WATCHDOG_PID_PATH.unlink(missing_ok=True)


def _terminate_stale_worker_from_pid_file() -> int | None:
    worker_pid = _read_pid(WORKER_PID_PATH)
    if not worker_pid or worker_pid == os.getpid():
        return None
    if not _is_pid_running(worker_pid):
        WORKER_PID_PATH.unlink(missing_ok=True)
        return None
    _terminate_pid(worker_pid)
    WORKER_PID_PATH.unlink(missing_ok=True)
    return worker_pid


def _read_runtime_status_payload() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {}
    try:
        payload = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_worker_shutdown_marker(*, reason: str, phase: str, note: str) -> None:
    runtime = _read_runtime_status_payload()
    payload = {
        "reason": reason,
        "phase": phase,
        "note": note,
        "candidate_id": runtime.get("current_candidate_id"),
        "current_action": runtime.get("current_action"),
        "current_action_step": runtime.get("current_action_step"),
        "written_at": now_iso(),
    }
    WORKER_SHUTDOWN_MARKER_PATH.parent.mkdir(parents=True, exist_ok=True)
    WORKER_SHUTDOWN_MARKER_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _clear_worker_shutdown_marker() -> None:
    WORKER_SHUTDOWN_MARKER_PATH.unlink(missing_ok=True)


def _compute_code_signature() -> str:
    digest = hashlib.sha256()
    for base in (P2_CORE_DIR / "p2_core", P2_CORE_DIR / "scripts"):
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.py")):
            try:
                stat = path.stat()
            except OSError:
                continue
            rel = str(path.relative_to(P2_CORE_DIR))
            digest.update(rel.encode("utf-8"))
            digest.update(str(stat.st_mtime_ns).encode("utf-8"))
            digest.update(str(stat.st_size).encode("utf-8"))
    return digest.hexdigest()[:16]


@dataclass
class ManagedProcess:
    name: str
    command: list[str]
    log_path: Path
    process: subprocess.Popen[str] | None = None
    log_handle: TextIOWrapper | None = None
    log_offset: int = 0

    def start(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_offset = self.log_path.stat().st_size if self.log_path.exists() else 0
        self.log_handle = self.log_path.open("a", encoding="utf-8")
        self.process = subprocess.Popen(
            self.command,
            cwd=P2_CORE_DIR,
            stdin=subprocess.DEVNULL,
            stdout=self.log_handle,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=False,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        self._log(f"started pid={self.process.pid} cmd={' '.join(self.command)}")

    def poll(self) -> int | None:
        if self.process is None:
            return None
        return self.process.poll()

    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def stop(self, *, reason: str | None = None) -> None:
        if self.process is None:
            return
        if self.process.poll() is None:
            if self.name == "loop-worker":
                _write_worker_shutdown_marker(
                    reason=reason or "watchdog が loop-worker の停止を要求しました。",
                    phase="watchdog_stop",
                    note="watchdog が意図して worker を停止しました。",
                )
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        if self.name == "loop-worker":
            _clear_worker_shutdown_marker()
        self._log(f"stopped rc={self.process.poll() if self.process else 'unknown'}")
        self.process = None
        if self.log_handle is not None:
            self.log_handle.close()
            self.log_handle = None

    def restart(self, *, reason: str) -> None:
        self._log(f"restarting because {reason}")
        self.stop(reason=reason)
        self.start()

    def read_new_lines(self, *, max_lines: int = 20) -> list[str]:
        if not self.log_path.exists():
            return []
        with self.log_path.open("r", encoding="utf-8") as handle:
            handle.seek(self.log_offset)
            lines = handle.readlines()
            self.log_offset = handle.tell()
        trimmed = [line.rstrip("\n") for line in lines if line.strip()]
        if len(trimmed) > max_lines:
            omitted = len(trimmed) - max_lines
            trimmed = [f"... 省略された過去ログ {omitted} 行 ...", *trimmed[-max_lines:]]
        return trimmed

    def _log(self, message: str) -> None:
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"[watchdog:{self.name}] {time.strftime('%Y-%m-%dT%H:%M:%S%z')} {message}\n")


@dataclass
class ManagedDashboard:
    root: Path
    host: str
    port: int
    log_path: Path
    server: Any | None = None
    thread: threading.Thread | None = None

    @property
    def name(self) -> str:
        return "dashboard"

    def start(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.server = create_dashboard_server(self.root, host=self.host, port=self.port)
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.1},
            daemon=True,
            name="p2-dashboard",
        )
        self.thread.start()
        self._log(f"started in-process host={self.host} port={self.port}")

    def poll(self) -> int | None:
        return None if self.alive() else 1

    def alive(self) -> bool:
        return self.server is not None and self.thread is not None and self.thread.is_alive()

    def stop(self, *, reason: str | None = None) -> None:
        del reason
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=2)
        self._log("stopped")
        self.server = None
        self.thread = None

    def restart(self, *, reason: str) -> None:
        self._log(f"restarting because {reason}")
        self.stop()
        stale_dashboard_pids = _terminate_conflicting_dashboards(root=self.root, host=self.host, port=self.port)
        if stale_dashboard_pids:
            self._log(f"terminated stale dashboard listeners before restart pids={stale_dashboard_pids}")
        self.start()

    def read_new_lines(self, *, max_lines: int = 20) -> list[str]:
        return []

    def _log(self, message: str) -> None:
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"[watchdog:{self.name}] {time.strftime('%Y-%m-%dT%H:%M:%S%z')} {message}\n")


class HealthMonitor:
    def __init__(self, log_path: Path, *, state_path: Path | None = None) -> None:
        self.log_path = log_path
        self.state_path = state_path or STATE_PATH
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._dashboard_failures = 0
        self._worker_failures = 0

    def read_status(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def dashboard_ok(self) -> bool:
        try:
            conn = http.client.HTTPConnection(DASHBOARD_HOST, DASHBOARD_PORT, timeout=2)
            conn.request("GET", "/api/health")
            response = conn.getresponse()
            body = response.read()
            conn.close()
            return response.status == 200 and body.strip() == b"ok"
        except (OSError, socket.timeout, http.client.HTTPException):
            return False

    def worker_fresh(self, *, max_age_seconds: int = 120, running_grace_seconds: int = 1800) -> bool:
        status = self.read_status()
        now = datetime.now(timezone.utc)
        runtime_status = str(status.get("status") or "")
        phase = str(status.get("phase") or "")
        if runtime_status == "running":
            if phase in {"context_selecting", "reflecting", "generating"}:
                return True
            anchor = status.get("worker_heartbeat_at") or status.get("last_loop_started_at")
            if not anchor:
                return False
            try:
                started = datetime.fromisoformat(anchor)
            except ValueError:
                return False
            return now - started <= timedelta(seconds=running_grace_seconds)

        heartbeat = status.get("worker_heartbeat_at") or status.get("last_loop_finished_at")
        if not heartbeat:
            return False
        try:
            moment = datetime.fromisoformat(heartbeat)
        except ValueError:
            return False
        return now - moment <= timedelta(seconds=max_age_seconds)

    def record_dashboard_probe(self, ok: bool) -> bool:
        self._dashboard_failures = 0 if ok else self._dashboard_failures + 1
        return self._dashboard_failures >= 3

    def record_worker_probe(self, ok: bool) -> bool:
        self._worker_failures = 0 if ok else self._worker_failures + 1
        return self._worker_failures >= 3

    def log(self, message: str) -> None:
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"[monitor] {time.strftime('%Y-%m-%dT%H:%M:%S%z')} {message}\n")


def _dashboard_command_signature(*, root: Path, host: str, port: int) -> list[str]:
    return [
        "p2_core.cli",
        "--root",
        str(root),
        "dashboard",
        "--host",
        host,
        "--port",
        str(port),
    ]


def _find_conflicting_dashboard_pids(*, root: Path, host: str, port: int) -> list[int]:
    signature = _dashboard_command_signature(root=root, host=host, port=port)
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


def _terminate_conflicting_dashboards(*, root: Path, host: str, port: int, keep_pid: int | None = None) -> list[int]:
    targets = [pid for pid in _find_conflicting_dashboard_pids(root=root, host=host, port=port) if pid != keep_pid]
    for pid in targets:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
    deadline = time.time() + 3
    while time.time() < deadline:
        remaining = [pid for pid in targets if pid in _find_conflicting_dashboard_pids(root=root, host=host, port=port)]
        if not remaining:
            return targets
        time.sleep(0.1)
    for pid in targets:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            continue
    return targets


def print_snapshot_summary(root: Path) -> None:
    snapshot = build_status_snapshot(root)
    goal = snapshot.get("goal") or {}
    version = snapshot.get("version") or {}
    runtime = snapshot.get("runtime_status") or {}
    attempt = snapshot.get("latest_attempt") or {}
    validation = snapshot.get("latest_validation") or {}
    retry_validation = snapshot.get("latest_retry_validation") or {}
    reasoning = attempt.get("reasoning_summary") or {}
    change = attempt.get("change_summary") or {}
    recent_history = snapshot.get("recent_history") or []
    last_event = recent_history[-1] if recent_history else {}

    _emit(
        "[P2 watchdog] 状態 "
        f"世代={version.get('active_generation')} "
        f"現在版={version.get('active_version_id')} "
        f"ゴール={goal.get('status')} "
        f"実行状態={runtime.get('status')} "
        f"イベント={runtime.get('last_event')}",
    )
    _emit(
        "[P2 watchdog] 最新試行 "
        f"候補ID={attempt.get('candidate_id')} "
        f"状態={attempt.get('status')} "
        f"対象={attempt.get('target_file')} "
        f"判断理由={attempt.get('decision_reason')}",
    )
    if attempt:
        _emit(
            "[P2 watchdog] 変更概要 "
            f"summary={change.get('summary')} "
            f"added={change.get('added_lines')} "
            f"removed={change.get('removed_lines')}",
        )
    if reasoning:
        _emit(
            "[P2 watchdog] 推論要約 "
            f"diagnosis={reasoning.get('diagnosis')} "
            f"expected={reasoning.get('expected_effect')}",
        )
    if validation:
        _emit(
            "[P2 watchdog] 検証結果 "
            f"passed={validation.get('passed')} "
            f"rc={validation.get('returncode')} "
            f"duration_ms={validation.get('duration_ms')}",
        )
    if retry_validation:
        _emit(
            "[P2 watchdog] 再検証結果 "
            f"passed={retry_validation.get('passed')} "
            f"rc={retry_validation.get('returncode')} "
            f"duration_ms={retry_validation.get('duration_ms')}",
        )
    if last_event:
        _emit(
            "[P2 watchdog] 履歴 "
            f"step={last_event.get('step')} "
            f"outcome={last_event.get('outcome')} "
            f"message={last_event.get('message')}",
        )


def _emit(message: str) -> None:
    try:
        print(message, flush=True)
    except (BrokenPipeError, OSError):
        # Keep watchdog alive even if stdout/stderr is detached.
        pass


def main() -> None:
    try:
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
    except (AttributeError, OSError):
        pass
    try:
        signal.signal(signal.SIGPIPE, signal.SIG_IGN)
    except (AttributeError, OSError):
        pass

    replaced_watchdog_pid = _claim_watchdog_slot()
    replaced_worker_pid = _terminate_stale_worker_from_pid_file()
    _clear_worker_shutdown_marker()
    _emit(
        f"[P2 watchdog] 起動します root={ROOT} dashboard=http://{DASHBOARD_HOST}:{DASHBOARD_PORT} "
        f"model={MODEL} 思考モデル={THINKING_MODEL} コーディングモデル={CODING_MODEL} "
        f"探索コーディングモデル={EXPLORATORY_CODING_MODEL} 停滞打開モデル={STAGNATION_CODING_MODEL}",
    )
    if replaced_watchdog_pid is not None:
        _emit(f"[P2 watchdog] 古い watchdog pid={replaced_watchdog_pid} を停止して置き換えました")
    if replaced_worker_pid is not None:
        _emit(f"[P2 watchdog] 古い loop-worker pid={replaced_worker_pid} を停止して置き換えました")

    repaired = ensure_workspace_prerequisites(ROOT)
    if repaired["created_dirs"] or repaired["created_files"]:
        _emit(
            "[P2 watchdog] 起動前の自己修復を実行しました "
            f"created_dirs={repaired['created_dirs']} created_files={repaired['created_files']}",
        )

    code_signature = _compute_code_signature()
    update_runtime_status(
        ROOT,
        status="initializing",
        phase="watchdog_starting",
        current_action="watchdog_boot",
        current_runtime_kernel=RUNTIME_KERNEL,
        model=MODEL,
        thinking_model=THINKING_MODEL,
        coding_model=CODING_MODEL,
        exploratory_coding_model=EXPLORATORY_CODING_MODEL,
        stagnation_coding_model=STAGNATION_CODING_MODEL,
        dashboard_notify_url=f"http://{DASHBOARD_HOST}:{DASHBOARD_PORT}/api/notify",
        dashboard_health_url=f"http://{DASHBOARD_HOST}:{DASHBOARD_PORT}/api/health",
        dashboard_owner="watchdog",
        dashboard_mode="in_process",
        watchdog_pid=os.getpid(),
        watchdog_code_signature=code_signature,
        last_event="watchdog_starting",
    )
    worker = ManagedProcess(
        name="loop-worker",
        command=[sys.executable, "-u", str(P2_CORE_DIR / "scripts" / "p2_loop_worker.py")],
        log_path=ROOT / "logs" / "p2-loop.log",
    )
    dashboard = ManagedDashboard(
        root=ROOT,
        host=DASHBOARD_HOST,
        port=DASHBOARD_PORT,
        log_path=ROOT / "logs" / "dashboard.log",
    )
    monitor = HealthMonitor(MONITOR_LOG)
    stale_dashboard_pids = _terminate_conflicting_dashboards(root=ROOT, host=DASHBOARD_HOST, port=DASHBOARD_PORT)
    if stale_dashboard_pids:
        monitor.log(f"terminated stale dashboard listeners pids={stale_dashboard_pids}")
        _emit(f"[P2 watchdog] 古い dashboard listener を停止しました pids={stale_dashboard_pids}")
    processes = [dashboard, worker]
    for process in processes:
        process.start()
        pid_text = getattr(getattr(process, "process", None), "pid", None)
        pid_label = pid_text if pid_text is not None else "in-process"
        _emit(
            f"[P2 watchdog] {process.name} を起動しました pid={pid_label} log={process.log_path}",
        )

    try:
        while True:
            try:
                time.sleep(5)
                snapshot = build_status_snapshot(ROOT)
                goal_status = str((snapshot.get("goal") or {}).get("status") or "")
                pause_mode = goal_status == "paused"
                statuses = " ".join(f"{proc.name}={'alive' if proc.alive() else 'dead'}" for proc in processes)
                monitor.log("tick " + statuses)
                _emit(f"[P2 watchdog] heartbeat {statuses}")
                for process in processes:
                    rc = process.poll()
                    if rc is not None:
                        if process.name == "loop-worker" and pause_mode:
                            monitor.log(f"{process.name} exited rc={rc} while goal paused; keep stopped")
                            _emit(f"[P2 watchdog] {process.name} は goal=paused のため停止状態を維持します")
                            continue
                        monitor.log(f"{process.name} exited rc={rc}")
                        _emit(f"[P2 watchdog] {process.name} が終了しました rc={rc}。再起動します")
                        process.restart(reason=f"exit rc={rc}")
                        _emit(f"[P2 watchdog] {process.name} を再起動しました")
                dashboard_ok = dashboard.alive() and monitor.dashboard_ok()
                _emit(f"[P2 watchdog] dashboard_health={'ok' if dashboard_ok else 'bad'}")
                if monitor.record_dashboard_probe(dashboard_ok):
                    monitor.log("dashboard probe failed 3x; restarting dashboard")
                    _emit("[P2 watchdog] dashboard の health check が 3 回連続で失敗したため再起動します")
                    stale_dashboard_pids = _terminate_conflicting_dashboards(
                        root=ROOT,
                        host=DASHBOARD_HOST,
                        port=DASHBOARD_PORT,
                    )
                    if stale_dashboard_pids:
                        monitor.log(f"terminated stale dashboard listeners before probe restart pids={stale_dashboard_pids}")
                    dashboard.restart(reason="dashboard probe failed")
                    _emit("[P2 watchdog] dashboard を再起動しました")
                worker_ok = True if pause_mode else (worker.alive() and monitor.worker_fresh())
                _emit(f"[P2 watchdog] worker_health={'ok' if worker_ok else 'bad'}")
                if monitor.record_worker_probe(worker_ok):
                    monitor.log("worker probe failed 3x; restarting loop worker")
                    _emit("[P2 watchdog] worker の health check が 3 回連続で失敗したため再起動します")
                    worker.restart(reason="worker probe failed")
                    _emit(f"[P2 watchdog] loop-worker を再起動しました pid={worker.process.pid if worker.process else 'n/a'}")

                latest_signature = _compute_code_signature()
                if latest_signature != code_signature:
                    previous_signature = code_signature
                    code_signature = latest_signature
                    monitor.log(
                        f"code signature changed {previous_signature} -> {latest_signature}; restarting watchdog process"
                    )
                    _emit(
                        f"[P2 watchdog] P2 更新を検知しました code={previous_signature}->{latest_signature}。"
                        " watchdog プロセスごと再起動して反映します。"
                    )
                    for process in processes:
                        process.stop(reason="code update restart")
                    update_runtime_status(
                        ROOT,
                        watchdog_code_signature=code_signature,
                        watchdog_pid=os.getpid(),
                        worker_pid=None,
                        last_event="watchdog_restarting_for_code_update",
                    )
                    _release_watchdog_slot()
                    os.execve(sys.executable, [sys.executable, "-u", str(Path(__file__).resolve())], os.environ.copy())

                for process in processes:
                    for line in process.read_new_lines():
                        _emit(f"[{process.name}-log] {line}")
                print_snapshot_summary(ROOT)
            except Exception as exc:
                monitor.log(f"tick exception: {exc.__class__.__name__}: {exc}")
                monitor.log(traceback.format_exc())
                _emit(f"[P2 watchdog] tick 例外を回復しました: {exc.__class__.__name__}: {exc}")
    except KeyboardInterrupt:
        _emit("[P2 watchdog] 割り込みを受けたため停止します")
        pass
    finally:
        for process in processes:
            process.stop(reason="watchdog shutdown")
        update_runtime_status(
            ROOT,
            status="idle",
            active_loop_run_id=None,
            current_candidate_id=None,
            dashboard_owner=None,
            dashboard_mode=None,
            dashboard_notify_url=None,
            dashboard_health_url=None,
            watchdog_pid=None,
            last_loop_finished_at=now_iso(),
            last_event="watchdog_stopped",
            worker_heartbeat_at=now_iso(),
        )
        _release_watchdog_slot()
        _emit("[P2 watchdog] 停止しました")


if __name__ == "__main__":
    main()
