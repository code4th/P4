from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import dataclass
from datetime import timezone, datetime
from pathlib import Path
from typing import Any


DEFAULT_CONFIG = {
    "ollama_base_url": "http://127.0.0.1:11434",
    "models": {
        "reasoning": "gemma4:26b",
        "fast": "glm-4.7-flash",
        "coding": "qwen3-coder",
        "terminal": "devstral",
    },
    "ollama_options": {
        "reasoning": {"temperature": 0.2, "num_predict": 512},
        "fast": {"temperature": 0.1, "num_predict": 384},
        "coding": {"temperature": 0.1, "num_predict": 512},
        "terminal": {"temperature": 0.1, "num_predict": 384},
    },
    "runtime": {
        "max_steps_per_message": 12,
        "worker_poll_seconds": 2,
        "chat_timeout_seconds": 180,
        "json_retry_limit": 1,
        "execution_root": "",
        "dedicated_llm_workspace": True,
        "observer_enabled": False,
        "observer_model": "",
    },
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path, fallback: Any | None = None) -> Any:
    if not path.exists():
        if fallback is None:
            raise FileNotFoundError(path)
        return fallback
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(path)


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def read_jsonl(path: Path, *, limit: int | None = None) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if limit is None:
        return rows
    return rows[-limit:]


@dataclass
class WorkspacePaths:
    root: Path

    def __post_init__(self) -> None:
        self.root = self.root.expanduser().resolve()

    @property
    def state_dir(self) -> Path:
        return self.root / "state"

    @property
    def runtime_dir(self) -> Path:
        return self.state_dir / "runtime"

    @property
    def workspaces_dir(self) -> Path:
        return self.root / "workspaces"

    @property
    def llm_runs_dir(self) -> Path:
        return self.workspaces_dir / "runs"

    @property
    def sessions_dir(self) -> Path:
        return self.state_dir / "sessions"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def config_path(self) -> Path:
        return self.root / "config.json"

    @property
    def goal_path(self) -> Path:
        return self.state_dir / "goal.json"

    @property
    def active_session_path(self) -> Path:
        return self.runtime_dir / "active_session.json"

    @property
    def queue_path(self) -> Path:
        return self.runtime_dir / "queue.json"

    @property
    def runtime_status_path(self) -> Path:
        return self.runtime_dir / "status.json"

    @property
    def worker_pid_path(self) -> Path:
        return self.runtime_dir / "worker.pid"

    @property
    def reflections_path(self) -> Path:
        return self.runtime_dir / "reflections.jsonl"

    @property
    def planning_path(self) -> Path:
        return self.runtime_dir / "planning.jsonl"

    @property
    def benchmark_status_path(self) -> Path:
        return self.runtime_dir / "benchmark.json"

    def session_dir(self, session_id: str) -> Path:
        return self.sessions_dir / session_id

    def session_meta_path(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "meta.json"

    def session_events_path(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "events.jsonl"

    def session_prompts_path(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "prompts.jsonl"


def bootstrap_workspace(root: Path, *, force: bool = False) -> dict[str, Any]:
    paths = WorkspacePaths(root)
    if paths.root.exists() and any(paths.root.iterdir()) and not force:
        raise FileExistsError(f"workspace is not empty: {paths.root}")
    if paths.root.exists() and force:
        shutil.rmtree(paths.root)
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    paths.runtime_dir.mkdir(parents=True, exist_ok=True)
    paths.llm_runs_dir.mkdir(parents=True, exist_ok=True)
    paths.sessions_dir.mkdir(parents=True, exist_ok=True)
    paths.logs_dir.mkdir(parents=True, exist_ok=True)

    write_json(paths.config_path, DEFAULT_CONFIG)
    write_json(
        paths.goal_path,
        {"text": "", "status": "active", "updated_at": now_iso()},
    )
    write_json(
        paths.active_session_path,
        {"session_id": "main", "updated_at": now_iso()},
    )
    write_json(paths.queue_path, {"items": []})
    write_json(
        paths.runtime_status_path,
        {
            "status": "idle",
            "current_role": None,
            "current_turn_id": None,
            "current_queue_id": None,
            "current_user_message": None,
            "current_prompt_preview": None,
            "current_stream_text": "",
            "current_plan": None,
            "current_phase": None,
            "current_model": None,
            "current_model_reason": None,
            "current_tool": None,
            "current_llm_workspace": None,
            "last_llm_workspace": None,
            "current_started_at": None,
            "current_finished_at": None,
            "last_error": None,
            "last_system_note": None,
            "last_reflection": None,
            "last_llm_started_at": None,
            "last_llm_finished_at": None,
            "last_llm_duration_ms": None,
            "last_llm_attempt_count": 0,
            "last_llm_raw_preview": None,
            "last_llm_parse_issue": None,
            "last_llm_stream_metadata": None,
            "last_event_at": now_iso(),
            "worker_running": False,
        },
    )
    write_json(
        paths.benchmark_status_path,
        {
            "status": "idle",
            "started_at": None,
            "finished_at": None,
            "current_model": None,
            "current_case": None,
            "completed_models": 0,
            "completed_cases": 0,
            "results": [],
            "ranking": [],
            "recommended_next_target": None,
            "last_error": None,
        },
    )
    write_json(
        paths.session_meta_path("main"),
        {
            "session_id": "main",
            "title": "Main Session",
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "message_count": 0,
            "tool_call_count": 0,
            "last_assistant_message": None,
            "last_finish_message": None,
        },
    )
    return {"ok": True, "root": str(paths.root), "session_id": "main"}


def active_session_id(root: Path) -> str:
    return str(read_json(WorkspacePaths(root).active_session_path, fallback={}).get("session_id") or "main")


def update_goal(root: Path, text: str) -> dict[str, Any]:
    clean_text = str(text or "").strip()
    if not clean_text:
        raise ValueError("goal text must not be empty")
    payload = {"text": clean_text, "status": "active", "updated_at": now_iso()}
    write_json(WorkspacePaths(root).goal_path, payload)
    return {"ok": True, "goal": payload}


def append_session_event(root: Path, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    paths = WorkspacePaths(root)
    event = {"event_id": uuid.uuid4().hex, "timestamp": now_iso(), **payload}
    append_jsonl(paths.session_events_path(session_id), event)
    meta = read_json(paths.session_meta_path(session_id), fallback={})
    meta["updated_at"] = now_iso()
    if payload.get("type") == "user_message":
        meta["message_count"] = int(meta.get("message_count") or 0) + 1
    if payload.get("type") == "tool_call":
        meta["tool_call_count"] = int(meta.get("tool_call_count") or 0) + 1
    if payload.get("type") == "assistant_message":
        meta["last_assistant_message"] = payload.get("content")
    if payload.get("type") == "finish":
        meta["last_finish_message"] = payload.get("content")
    summary_source = payload.get("content") or payload.get("tool_name") or payload.get("type")
    meta["last_event_summary"] = str(summary_source or "")
    if payload.get("type") == "tool_result":
        meta["last_tool_result"] = payload.get("content")
    write_json(paths.session_meta_path(session_id), meta)
    return event


def append_prompt_snapshot(root: Path, session_id: str, payload: dict[str, Any]) -> None:
    append_jsonl(WorkspacePaths(root).session_prompts_path(session_id), {"timestamp": now_iso(), **payload})


def enqueue_message(root: Path, content: str, *, session_id: str | None = None) -> dict[str, Any]:
    clean_content = str(content or "").strip()
    if not clean_content:
        raise ValueError("message content must not be empty")
    session_id = session_id or active_session_id(root)
    append_session_event(
        root,
        session_id,
        {"type": "user_message", "role": "user", "content": clean_content},
    )
    paths = WorkspacePaths(root)
    queue = read_json(paths.queue_path, fallback={"items": []})
    items = list(queue.get("items") or [])
    item = {
        "queue_id": uuid.uuid4().hex,
        "session_id": session_id,
        "content": clean_content,
        "enqueued_at": now_iso(),
    }
    items.append(item)
    write_json(paths.queue_path, {"items": items})
    return {"ok": True, "queued": item}


def pop_next_queue_item(root: Path) -> dict[str, Any] | None:
    paths = WorkspacePaths(root)
    queue = read_json(paths.queue_path, fallback={"items": []})
    items = list(queue.get("items") or [])
    if not items:
        return None
    item = items.pop(0)
    write_json(paths.queue_path, {"items": items})
    return item


def queue_items(root: Path) -> list[dict[str, Any]]:
    return list(read_json(WorkspacePaths(root).queue_path, fallback={"items": []}).get("items") or [])
