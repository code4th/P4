from __future__ import annotations

import os
import select
import signal
import subprocess
import sys
import time
import json
from pathlib import Path

if str(Path(__file__).resolve().parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from p2_core.loop_attempt_meta import _mark_attempt_failed
from p2_core.workspace import (
    WorkspacePaths,
    build_status_snapshot,
    load_runtime_status,
    now_iso,
    resolve_model_roles,
    update_runtime_status,
)


ROOT = Path(os.environ.get("P2_ROOT", "/tmp/p2-demo")).expanduser()
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
INTERVAL_SECONDS = int(os.environ.get("P2_LOOP_INTERVAL", "5"))
CHILD_MAX_ATTEMPT_SECONDS = float(os.environ.get("P2_CHILD_MAX_ATTEMPT_SECONDS", os.environ.get("P2_CHILD_HARD_TIMEOUT_SECONDS", "900")))
CHILD_NO_PROGRESS_TIMEOUT_SECONDS = float(
    os.environ.get("P2_CHILD_NO_PROGRESS_TIMEOUT_SECONDS", os.environ.get("P2_CHILD_IDLE_TIMEOUT_SECONDS", "120"))
)
P2_CORE_DIR = Path(__file__).resolve().parent.parent
WORKER_PID_PATH = ROOT / "state" / "runtime" / "worker.pid"
WORKER_SHUTDOWN_MARKER_PATH = ROOT / "state" / "runtime" / "worker_shutdown.json"
CURRENT_CHILD: subprocess.Popen[str] | None = None


def _is_pid_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate_pid(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.time() + 3
    while time.time() < deadline:
        if not _is_pid_running(pid):
            return
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def _claim_worker_slot() -> int | None:
    WORKER_PID_PATH.parent.mkdir(parents=True, exist_ok=True)
    replaced: int | None = None
    previous_raw = WORKER_PID_PATH.read_text(encoding="utf-8").strip() if WORKER_PID_PATH.exists() else ""
    if previous_raw:
        try:
            previous_pid = int(previous_raw)
        except ValueError:
            previous_pid = None
        if previous_pid and previous_pid != os.getpid() and _is_pid_running(previous_pid):
            _terminate_pid(previous_pid)
            replaced = previous_pid
    WORKER_PID_PATH.write_text(str(os.getpid()), encoding="utf-8")
    return replaced


def _release_worker_slot() -> None:
    if not WORKER_PID_PATH.exists():
        return
    current = WORKER_PID_PATH.read_text(encoding="utf-8").strip()
    if current == str(os.getpid()):
        WORKER_PID_PATH.unlink(missing_ok=True)


def _read_shutdown_marker() -> dict[str, object]:
    if not WORKER_SHUTDOWN_MARKER_PATH.exists():
        return {}
    try:
        payload = json.loads(WORKER_SHUTDOWN_MARKER_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if isinstance(payload, dict):
        return payload
    return {}


def _clear_shutdown_marker() -> None:
    WORKER_SHUTDOWN_MARKER_PATH.unlink(missing_ok=True)


def _handle_shutdown(signum: int, _frame: object | None) -> None:
    shutdown_marker = _read_shutdown_marker()
    runtime_status = load_runtime_status(WorkspacePaths(ROOT))
    candidate_id = str(runtime_status.get("current_candidate_id") or "").strip()
    if candidate_id:
        reason = str(shutdown_marker.get("reason") or "worker が停止要求を受けました。").strip()
        phase = str(shutdown_marker.get("phase") or "worker_stop").strip() or "worker_stop"
        note = str(shutdown_marker.get("note") or "watchdog が worker を停止しました。").strip()
        _mark_attempt_failed(
            ROOT,
            candidate_id=candidate_id,
            phase=phase,
            reason=reason,
            note=note,
        )
    if CURRENT_CHILD is not None and CURRENT_CHILD.poll() is None:
        try:
            CURRENT_CHILD.terminate()
        except ProcessLookupError:
            pass
    _clear_shutdown_marker()
    _release_worker_slot()
    update_runtime_status(
        ROOT,
        status="idle",
        active_loop_run_id=None,
        current_candidate_id=None,
        current_task_stack=None,
        phase=None,
        phase_started_at=None,
        current_stream_path=None,
        last_output_at=None,
        last_event="worker_stopped",
        worker_heartbeat_at=now_iso(),
    )
    print(f"[P2ワーカー] シグナル {signum} を受信したため停止します", flush=True)
    raise SystemExit(0)


def _queue_has_pending_work(root: Path) -> bool:
    queue_path = WorkspacePaths(root).queue_path
    if not queue_path.exists():
        return False
    return any(line.strip() for line in queue_path.read_text(encoding="utf-8").splitlines())


def _progress_timeout_budget(runtime: dict[str, object]) -> float:
    phase = str(runtime.get("phase") or "")
    action = str(runtime.get("current_action") or "")
    if action == "run_validation":
        return max(CHILD_NO_PROGRESS_TIMEOUT_SECONDS, 240.0)
    if action == "apply_patch":
        return max(CHILD_NO_PROGRESS_TIMEOUT_SECONDS, 150.0)
    if action in {"read_file", "search_code"}:
        return min(max(CHILD_NO_PROGRESS_TIMEOUT_SECONDS, 90.0), 150.0)
    if action == "open_child_frame":
        return min(max(CHILD_NO_PROGRESS_TIMEOUT_SECONDS, 75.0), 120.0)
    if phase in {"context_selecting", "reflecting", "generating"}:
        return max(CHILD_NO_PROGRESS_TIMEOUT_SECONDS, 180.0)
    return CHILD_NO_PROGRESS_TIMEOUT_SECONDS


def _progress_signature(snapshot: dict[str, object]) -> str:
    runtime = snapshot.get("runtime_status") or {}
    latest_attempt = snapshot.get("latest_attempt") or {}
    latest_validation = snapshot.get("latest_validation") or {}
    payload = {
        "candidate": runtime.get("current_candidate_id"),
        "phase": runtime.get("phase"),
        "action": runtime.get("current_action"),
        "step": runtime.get("current_action_step"),
        "event": runtime.get("last_event"),
        "task_stack_size": len(runtime.get("current_task_stack") or []),
        "attempt_status": latest_attempt.get("status"),
        "attempt_reason": latest_attempt.get("decision_reason"),
        "validation_summary": latest_attempt.get("validation_summary"),
        "change_summary": latest_attempt.get("change_summary"),
        "latest_validation": {
            "passed": latest_validation.get("passed"),
            "returncode": latest_validation.get("returncode"),
            "duration_ms": latest_validation.get("duration_ms"),
        },
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _child_timeout_reason(
    *,
    started_at: float,
    last_progress_at: float,
    now: float,
    runtime: dict[str, object],
) -> str | None:
    hard_timed_out = CHILD_MAX_ATTEMPT_SECONDS > 0 and now - started_at >= CHILD_MAX_ATTEMPT_SECONDS
    no_progress_budget = _progress_timeout_budget(runtime)
    no_progress_timed_out = no_progress_budget > 0 and now - last_progress_at >= no_progress_budget
    if hard_timed_out:
        return f"worker max-attempt timeout after {int(CHILD_MAX_ATTEMPT_SECONDS)}s"
    if no_progress_timed_out:
        phase = str(runtime.get("phase") or "unknown")
        action = str(runtime.get("current_action") or "unknown")
        return (
            f"worker no-progress timeout after {int(no_progress_budget)}s "
            f"(phase={phase}, action={action})"
        )
    return None


def _read_child_until_exit(root: Path, child: subprocess.Popen[str]) -> int:
    assert child.stdout is not None
    started_at = time.monotonic()
    last_progress_at = started_at
    last_signature = ""
    while True:
        if child.poll() is not None:
            for line in child.stdout:
                update_runtime_status(
                    ROOT,
                    status="running",
                    worker_heartbeat_at=now_iso(),
                    last_event="child_output",
                )
                print(f"[モデル実行] {line.rstrip()}", flush=True)
            return child.returncode or 0

        ready, _, _ = select.select([child.stdout], [], [], 1.0)
        if ready:
            line = child.stdout.readline()
            if line:
                last_progress_at = time.monotonic()
                update_runtime_status(
                    ROOT,
                    status="running",
                    worker_heartbeat_at=now_iso(),
                    last_event="child_output",
                )
                print(f"[モデル実行] {line.rstrip()}", flush=True)
        snapshot = build_status_snapshot(root)
        runtime = snapshot.get("runtime_status") or {}
        signature = _progress_signature(snapshot)
        if signature != last_signature:
            last_signature = signature
            last_progress_at = time.monotonic()

        now = time.monotonic()
        reason = _child_timeout_reason(
            started_at=started_at,
            last_progress_at=last_progress_at,
            now=now,
            runtime=runtime,
        )
        if reason is None:
            continue
        print(f"[P2ワーカー] 子プロセスを強制停止します: {reason}", flush=True)
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)
        snapshot = build_status_snapshot(root)
        latest_attempt = snapshot.get("latest_attempt") or {}
        candidate_id = str(latest_attempt.get("candidate_id") or "").strip()
        if candidate_id and str(latest_attempt.get("status") or "").strip() == "started":
            _mark_attempt_failed(
                root,
                candidate_id=candidate_id,
                phase="worker_timeout",
                reason=reason,
                note="worker が hung attempt を強制終了しました。",
            )
        return child.returncode or 1


def main() -> None:
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)
    _clear_shutdown_marker()
    replaced_pid = _claim_worker_slot()
    cmd = [
        sys.executable,
        "-m",
        "p2_core.cli",
        "--root",
        str(ROOT),
        "run-loop",
        "--model",
        MODEL,
        "--thinking-model",
        THINKING_MODEL,
        "--coding-model",
        CODING_MODEL,
        "--exploratory-coding-model",
        EXPLORATORY_CODING_MODEL,
        "--stagnation-coding-model",
        STAGNATION_CODING_MODEL,
        "--max-iterations",
        "1",
    ]
    print(
        f"[P2ワーカー] 起動しました: root={ROOT} model={MODEL} 思考モデル={THINKING_MODEL} "
        f"コーディングモデル={CODING_MODEL} 探索コーディングモデル={EXPLORATORY_CODING_MODEL} "
        f"停滞打開モデル={STAGNATION_CODING_MODEL}",
        flush=True,
    )
    if replaced_pid is not None:
        print(f"[P2ワーカー] 古い worker pid={replaced_pid} を停止して置き換えました", flush=True)
    update_runtime_status(
        ROOT,
        status="idle",
        active_loop_run_id=None,
        current_candidate_id=None,
        current_task_stack=None,
        phase=None,
        phase_started_at=None,
        current_stream_path=None,
        last_output_at=None,
        last_event="worker_booted",
        worker_pid=os.getpid(),
        worker_heartbeat_at=now_iso(),
    )
    try:
        idle_reason: str | None = None
        while True:
            try:
                snapshot = build_status_snapshot(ROOT)
                goal_status = (snapshot.get("goal") or {}).get("status")
                queue_pending = _queue_has_pending_work(ROOT)
                if goal_status == "paused" and not queue_pending:
                    update_runtime_status(
                        ROOT,
                        status="idle",
                        active_loop_run_id=None,
                        current_candidate_id=None,
                        last_event="waiting_for_resume",
                        worker_heartbeat_at=snapshot["generated_at"],
                    )
                    if idle_reason != "waiting_for_resume":
                        print("[P2ワーカー] 目標は一時停止中です。再開されるまで待機します", flush=True)
                        idle_reason = "waiting_for_resume"
                    time.sleep(INTERVAL_SECONDS)
                    continue

                idle_reason = None
                print("[P2ワーカー] 自己改善ループを 1 回実行します", flush=True)
                global CURRENT_CHILD
                CURRENT_CHILD = subprocess.Popen(
                    cmd,
                    cwd=P2_CORE_DIR,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                rc = _read_child_until_exit(ROOT, CURRENT_CHILD)
                CURRENT_CHILD = None
                snapshot = build_status_snapshot(ROOT)
                latest_attempt = snapshot.get("latest_attempt") or {}
                print(
                    "[P2ワーカー] 1 回の実行が完了しました: "
                    f"rc={rc} "
                    f"generation={snapshot.get('active_generation')} "
                    f"candidate={latest_attempt.get('candidate_id')} "
                    f"status={latest_attempt.get('status')} "
                    f"decision={latest_attempt.get('decision_reason')}",
                    flush=True,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[P2ワーカー] エラーが発生しました: {exc}", flush=True)
            time.sleep(INTERVAL_SECONDS)
    finally:
        _release_worker_slot()


if __name__ == "__main__":
    main()
