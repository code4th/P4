from __future__ import annotations

import json
import subprocess
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from p1_core.core.background_job_store import BackgroundJobStore
from p1_core.core.governance_store import compare_risk_levels
from p1_core.core.world_store import WorldStore


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _resolve_within_root(root: Path, relative_path: str) -> Path:
    candidate = (root / relative_path).resolve()
    root_resolved = root.resolve()
    if candidate != root_resolved and root_resolved not in candidate.parents:
        raise ValueError(f"path escapes P1 root: {relative_path}")
    return candidate


INTRINSIC_ACTION_RISK = {
    "append_note": "low",
    "write_note_file": "low",
    "write_capability_task": "low",
    "plan_capability_task": "low",
    "read_file": "low",
    "ingest_observation": "low",
    "queue_background_analysis": "low",
    "write_file": "medium",
    "run_command": "high",
}

MAX_AUTONOMOUS_TIMEOUT_SECONDS = 15


class OpenClawActionBackend(Protocol):
    def run_command(self, *, argv: list[str], cwd: str, timeout_seconds: int) -> dict[str, Any]: ...

    def read_file(self, *, path: str) -> dict[str, Any]: ...

    def write_file(self, *, path: str, content: str) -> dict[str, Any]: ...


@dataclass(slots=True)
class ActionSpec:
    kind: str
    inputs: dict[str, Any]
    risk_level: str = "low"
    backend: str = "local"
    goal: str | None = None
    idempotency_key: str | None = None
    action_id: str = field(default_factory=lambda: f"action:{uuid.uuid4()}")
    status: str = "queued"
    requested_at: str = field(default_factory=_timestamp)


@dataclass(slots=True)
class ActionResult:
    action_id: str
    kind: str
    status: str
    backend: str
    stdout: str = ""
    stderr: str = ""
    artifacts: list[str] = field(default_factory=list)
    rollback_hint: str | None = None
    duration_ms: int | None = None
    recorded_at: str = field(default_factory=_timestamp)


@dataclass(slots=True)
class ActionStore:
    root: Path

    def __post_init__(self) -> None:
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        self.history_dir.mkdir(parents=True, exist_ok=True)
        self.approval_dir.mkdir(parents=True, exist_ok=True)
        self.failed_dir.mkdir(parents=True, exist_ok=True)
        self.deferred_dir.mkdir(parents=True, exist_ok=True)

    @property
    def queue_dir(self) -> Path:
        return self.root / "queue"

    @property
    def history_dir(self) -> Path:
        return self.root / "history"

    @property
    def approval_dir(self) -> Path:
        return self.root / "approval-required"

    @property
    def failed_dir(self) -> Path:
        return self.root / "failed"

    @property
    def deferred_dir(self) -> Path:
        return self.root / "deferred"

    def enqueue(self, spec: ActionSpec) -> dict[str, Any]:
        path = self.queue_dir / f"{spec.action_id.replace(':', '-')}.json"
        path.write_text(json.dumps(asdict(spec), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return json.loads(path.read_text(encoding="utf-8"))

    def list_queued(self) -> list[dict[str, Any]]:
        return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(self.queue_dir.glob("*.json"))]

    def next_queued(self) -> dict[str, Any] | None:
        queued = self.list_queued()
        return queued[0] if queued else None

    def mark_completed(self, action_id: str, result: ActionResult) -> dict[str, Any]:
        return self._move(self.queue_dir, self.history_dir, action_id, result)

    def mark_approval_required(self, action_id: str, reason: str) -> dict[str, Any]:
        payload = self._read(self.queue_dir, action_id)
        payload["status"] = "approval_required"
        payload["approval_reason"] = reason
        return self._move_payload(self.queue_dir, self.approval_dir, action_id, payload)

    def mark_failed(self, action_id: str, error: str) -> dict[str, Any]:
        payload = self._read(self.queue_dir, action_id)
        payload["status"] = "failed"
        payload["error"] = error
        return self._move_payload(self.queue_dir, self.failed_dir, action_id, payload)

    def mark_deferred(self, action_id: str, reason: str, *, retry_after_at: str) -> dict[str, Any]:
        payload = self._read(self.queue_dir, action_id)
        retries = int(payload.get("retry_count", 0)) + 1
        payload["status"] = "deferred"
        payload["defer_reason"] = reason
        payload["retry_after_at"] = retry_after_at
        payload["retry_count"] = retries
        return self._move_payload(self.queue_dir, self.deferred_dir, action_id, payload)

    def requeue_due_deferred(self, *, now_iso: str) -> list[dict[str, Any]]:
        now = _parse_iso(now_iso) or datetime.now(UTC)
        requeued: list[dict[str, Any]] = []
        for path in sorted(self.deferred_dir.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            retry_after_at = _parse_iso(str(payload.get("retry_after_at", "")))
            if retry_after_at and retry_after_at > now:
                continue
            if int(payload.get("retry_count", 0)) >= int(payload.get("max_retries", 3)):
                payload["status"] = "failed"
                payload["error"] = payload.get("defer_reason", "retry limit reached")
                target = self.failed_dir / path.name
                target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                path.unlink()
                continue
            payload["status"] = "queued"
            payload["requeued_at"] = now_iso
            target = self.queue_dir / path.name
            target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            path.unlink()
            requeued.append(payload)
        return requeued

    def counts(self) -> dict[str, int]:
        return {
            "queued": len(list(self.queue_dir.glob("*.json"))),
            "completed": len(list(self.history_dir.glob("*.json"))),
            "approval_required": len(list(self.approval_dir.glob("*.json"))),
            "deferred": len(list(self.deferred_dir.glob("*.json"))),
            "failed": len(list(self.failed_dir.glob("*.json"))),
        }

    def _filename(self, action_id: str) -> str:
        return f"{action_id.replace(':', '-')}.json"

    def _read(self, source_dir: Path, action_id: str) -> dict[str, Any]:
        path = source_dir / self._filename(action_id)
        return json.loads(path.read_text(encoding="utf-8"))

    def _move(self, source_dir: Path, target_dir: Path, action_id: str, result: ActionResult) -> dict[str, Any]:
        payload = self._read(source_dir, action_id)
        payload["status"] = result.status
        payload["result"] = asdict(result)
        return self._move_payload(source_dir, target_dir, action_id, payload)

    def _move_payload(self, source_dir: Path, target_dir: Path, action_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        source = source_dir / self._filename(action_id)
        target = target_dir / self._filename(action_id)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        source.unlink()
        return payload


@dataclass(slots=True)
class ActionPolicy:
    governance_profile: dict[str, Any]

    def decide(self, spec: ActionSpec) -> tuple[str, str]:
        operations = self.governance_profile.get("operations", {})
        laws = self.governance_profile.get("laws", {})
        feedback = self.governance_profile.get("feedback", {})
        if not operations.get("autonomy_enabled", True):
            return ("defer", "autonomy is disabled")
        if feedback.get("freeze_low_risk_autonomy", False):
            return ("defer", "low-risk autonomy is frozen by governance feedback")

        intrinsic_risk = INTRINSIC_ACTION_RISK.get(spec.kind)
        if intrinsic_risk is None:
            return ("reject", f"unsupported autonomous action kind: {spec.kind}")

        effective_risk = self.effective_risk(spec)
        max_autonomous_risk = str(operations.get("max_autonomous_risk", "low"))
        if compare_risk_levels(effective_risk, max_autonomous_risk) > 0:
            return ("approval_required", f"{spec.kind} exceeds max autonomous risk {max_autonomous_risk}")

        if effective_risk == "high" and laws.get("high_risk_requires_cloud_approval", True):
            return ("approval_required", "high-risk actions require approval")
        if effective_risk == "medium" and laws.get("medium_risk_requires_cloud_approval", True):
            return ("approval_required", "medium-risk actions require approval")
        return ("allow", "within current autonomy policy")

    def effective_risk(self, spec: ActionSpec) -> str:
        intrinsic_risk = INTRINSIC_ACTION_RISK[spec.kind]
        if compare_risk_levels(spec.risk_level, intrinsic_risk) > 0:
            return spec.risk_level
        return intrinsic_risk


@dataclass(slots=True)
class ActionExecutor:
    root: Path
    background_job_store: BackgroundJobStore
    world_store: WorldStore
    openclaw_backend: OpenClawActionBackend | None = None

    def execute(self, spec: ActionSpec) -> ActionResult:
        started_at = datetime.now(UTC)
        stdout = ""
        stderr = ""
        artifacts: list[str] = []
        rollback_hint: str | None = None
        try:
            if spec.kind == "append_note":
                note_dir = self.root / "state" / "actions" / "notes"
                note_dir.mkdir(parents=True, exist_ok=True)
                note_path = note_dir / f"{spec.action_id.replace(':', '-')}.md"
                backup_path = self._backup_if_exists(note_path)
                note_path.write_text(str(spec.inputs.get("content", "")) + "\n", encoding="utf-8")
                stdout = f"wrote note to {note_path}"
                artifacts.append(str(note_path))
                if backup_path:
                    artifacts.append(str(backup_path))
                    rollback_hint = f"restore {backup_path} to {note_path}"
                else:
                    rollback_hint = f"delete {note_path}"
            elif spec.kind == "write_note_file":
                path = _resolve_within_root(self.root, str(spec.inputs["path"]))
                path.parent.mkdir(parents=True, exist_ok=True)
                backup_path = self._backup_if_exists(path)
                path.write_text(str(spec.inputs.get("content", "")) + "\n", encoding="utf-8")
                stdout = f"wrote note file {path}"
                artifacts.append(str(path))
                if backup_path:
                    artifacts.append(str(backup_path))
                    rollback_hint = f"restore {backup_path} to {path}"
                else:
                    rollback_hint = f"delete {path}"
            elif spec.kind == "write_capability_task":
                task_dir = self.root / "state" / "capabilities" / "tasks"
                task_dir.mkdir(parents=True, exist_ok=True)
                proposal_id = str(spec.inputs.get("proposal_id", spec.action_id)).replace(":", "-")
                task_path = task_dir / f"{proposal_id}.json"
                task_payload = {
                    "proposal_id": spec.inputs.get("proposal_id"),
                    "gap_id": spec.inputs.get("gap_id"),
                    "summary": spec.inputs.get("summary"),
                    "detail": spec.inputs.get("detail"),
                    "implementation_scope": spec.inputs.get("implementation_scope", "bounded_internal_task"),
                    "target_files": spec.inputs.get("target_files", []),
                    "acceptance_checks": spec.inputs.get("acceptance_checks", []),
                    "created_by_action_id": spec.action_id,
                    "created_at": _timestamp(),
                }
                task_path.write_text(json.dumps(task_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                stdout = f"wrote capability task to {task_path}"
                artifacts.append(str(task_path))
                rollback_hint = f"delete {task_path}"
            elif spec.kind == "plan_capability_task":
                task_dir = self.root / "state" / "capabilities" / "tasks"
                task_dir.mkdir(parents=True, exist_ok=True)
                task_id = str(spec.action_id).replace("action:", "task:")
                task_path = task_dir / f"{task_id}.json"
                task_payload = {
                    "task_id": task_id,
                    "status": "pending",
                    "source_action_id": spec.action_id,
                    "proposal_id": spec.inputs.get("proposal_id"),
                    "gap_id": spec.inputs.get("gap_id"),
                    "summary": spec.inputs.get("summary"),
                    "implementation_scope": spec.inputs.get("implementation_scope", "bounded_internal_task"),
                    "target_files": spec.inputs.get("target_files", []),
                    "acceptance_checks": spec.inputs.get("acceptance_checks", []),
                    "created_at": _timestamp(),
                }
                task_path.write_text(json.dumps(task_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                stdout = f"planned capability task at {task_path}"
                artifacts.append(str(task_path))
                rollback_hint = f"delete {task_path}"
            elif spec.kind == "read_file":
                path = _resolve_within_root(self.root, str(spec.inputs["path"]))
                if spec.backend == "openclaw":
                    if self.openclaw_backend is None:
                        raise RuntimeError("openclaw action backend is not configured")
                    payload = self.openclaw_backend.read_file(path=str(path))
                    stdout = json.dumps(payload, ensure_ascii=False)
                else:
                    stdout = path.read_text(encoding="utf-8")[:10000]
                artifacts.append(str(path))
            elif spec.kind == "write_file":
                path = _resolve_within_root(self.root, str(spec.inputs["path"]))
                backup_path: Path | None = None
                if spec.backend == "openclaw":
                    if self.openclaw_backend is None:
                        raise RuntimeError("openclaw action backend is not configured")
                    payload = self.openclaw_backend.write_file(path=str(path), content=str(spec.inputs.get("content", "")))
                    stdout = json.dumps(payload, ensure_ascii=False)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    backup_path = self._backup_if_exists(path)
                    path.write_text(str(spec.inputs.get("content", "")), encoding="utf-8")
                    stdout = f"wrote file {path}"
                artifacts.append(str(path))
                if backup_path:
                    artifacts.append(str(backup_path))
                    rollback_hint = f"restore {backup_path} to {path}"
                else:
                    rollback_hint = f"delete {path}"
            elif spec.kind == "run_command":
                argv = spec.inputs.get("argv")
                if not isinstance(argv, list) or not argv:
                    raise ValueError("run_command requires non-empty argv list")
                timeout_seconds = min(
                    int(spec.inputs.get("timeout_seconds", MAX_AUTONOMOUS_TIMEOUT_SECONDS)),
                    MAX_AUTONOMOUS_TIMEOUT_SECONDS,
                )
                cwd = str(_resolve_within_root(self.root, str(spec.inputs.get("cwd", "."))))
                if spec.backend == "openclaw":
                    if self.openclaw_backend is None:
                        raise RuntimeError("openclaw action backend is not configured")
                    payload = self.openclaw_backend.run_command(argv=argv, cwd=cwd, timeout_seconds=timeout_seconds)
                    stdout = json.dumps(payload, ensure_ascii=False)
                else:
                    completed = subprocess.run(
                        argv,
                        cwd=cwd,
                        capture_output=True,
                        text=True,
                        timeout=timeout_seconds,
                        check=False,
                    )
                    stdout = completed.stdout.strip()
                    stderr = completed.stderr.strip()
                    if completed.returncode != 0:
                        raise RuntimeError(f"command exited with code {completed.returncode}")
            elif spec.kind == "queue_background_analysis":
                queued = self.background_job_store.enqueue(
                    job_type="background_analysis",
                    model=str(spec.inputs["model"]),
                    payload={"input_text": str(spec.inputs["input_text"])},
                    date=spec.inputs.get("date"),
                )
                stdout = f"queued background job {queued['job_id']}"
                artifacts.append(queued["job_id"])
            elif spec.kind == "ingest_observation":
                payload = self.world_store.observe(str(spec.inputs["text"]), source=str(spec.inputs.get("source", "autonomy")))
                stdout = payload["observation_id"]
                artifacts.append(payload["observation_id"])
            else:
                raise ValueError(f"unsupported action kind: {spec.kind}")
        except Exception as exc:
            return ActionResult(
                action_id=spec.action_id,
                kind=spec.kind,
                status="failed",
                backend=spec.backend,
                stdout=stdout,
                stderr=str(exc) if not stderr else stderr,
                artifacts=artifacts,
                rollback_hint=rollback_hint,
                duration_ms=int((datetime.now(UTC) - started_at).total_seconds() * 1000),
            )

        return ActionResult(
            action_id=spec.action_id,
            kind=spec.kind,
            status="completed",
            backend=spec.backend,
            stdout=stdout,
            stderr=stderr,
            artifacts=artifacts,
            rollback_hint=rollback_hint,
            duration_ms=int((datetime.now(UTC) - started_at).total_seconds() * 1000),
        )

    def _backup_if_exists(self, path: Path) -> Path | None:
        if not path.exists():
            return None
        backup_dir = self.root / "state" / "rollback" / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"{path.name}.{uuid.uuid4()}.bak"
        backup_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        return backup_path
