from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

from p2_core.backend import ModelBackend, OllamaBackend
from p2_core.workspace import WorkspacePaths, now_iso, write_json


def _default_backend(model: str) -> ModelBackend:
    return OllamaBackend(model=model)


def _emit_model_chunk(chunk: str) -> None:
    if not chunk:
        return
    sys.stdout.write(chunk)
    sys.stdout.flush()


def _emit_reflection_chunk(chunk: str) -> None:
    _emit_model_chunk(chunk)


def _phase_label(phase: str) -> str:
    labels = {
        "context_selecting": "追加文脈選択",
        "reflecting": "自己診断",
        "generating": "コード生成",
        "acting": "アクション実行",
        "validating": "検証",
        "promoting": "昇格確認",
    }
    return labels.get(phase, phase)


def _run_validation(
    *,
    root: Path,
    candidate_id: str,
    command: list[str],
    cwd: Path,
    retry: bool = False,
) -> dict[str, Any]:
    paths = WorkspacePaths(root)
    started_at = now_iso()
    start = time.monotonic()
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(cwd) if not existing_pythonpath else f"{cwd}:{existing_pythonpath}"
    env["P2_WORKSPACE_ROOT"] = str(root.resolve())
    proc = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    finished_at = now_iso()
    duration_ms = int((time.monotonic() - start) * 1000)
    stdout_path = paths.validation_stdout_path(candidate_id, retry=retry)
    stderr_path = paths.validation_stderr_path(candidate_id, retry=retry)
    stdout_path.write_text(proc.stdout, encoding="utf-8")
    stderr_path.write_text(proc.stderr, encoding="utf-8")
    combined_log = (
        f"command: {command}\n"
        f"cwd: {cwd}\n"
        f"started_at: {started_at}\n"
        f"finished_at: {finished_at}\n"
        f"returncode: {proc.returncode}\n\n"
        f"STDOUT\n{proc.stdout}\n\nSTDERR\n{proc.stderr}"
    )
    paths.validation_log_path(candidate_id, retry=retry).write_text(combined_log, encoding="utf-8")
    report = {
        "candidate_id": candidate_id,
        "command": command,
        "cwd": str(cwd),
        "returncode": proc.returncode,
        "passed": proc.returncode == 0,
        "duration_ms": duration_ms,
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "started_at": started_at,
        "finished_at": finished_at,
        "message": "validation passed" if proc.returncode == 0 else "validation failed",
        "retry": retry,
    }
    write_json(paths.validation_report_path(candidate_id, retry=retry), report)
    return report
