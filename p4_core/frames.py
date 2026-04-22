from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from p4_core.workspace import append_jsonl, now_iso, read_jsonl


@dataclass
class WorkingMemory:
    observations: list[str] = field(default_factory=list)
    current_focus: str = ""
    unresolved_questions: list[str] = field(default_factory=list)
    avoid_repeating: list[str] = field(default_factory=list)
    child_tasks: list[dict[str, Any]] = field(default_factory=list)
    completed_child_tasks: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Frame:
    frame_id: str
    parent_frame_id: str | None
    depth: int
    goal: str
    inherited_context: dict[str, Any]
    working_memory: WorkingMemory = field(default_factory=WorkingMemory)
    session_events: list[dict[str, Any]] = field(default_factory=list)
    return_payload: dict[str, Any] | None = None
    status: str = "active"
    created_at: str = field(default_factory=now_iso)
    returned_at: str | None = None
    step_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["working_memory"] = asdict(self.working_memory)
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Frame":
        data = dict(payload)
        data["working_memory"] = WorkingMemory(**dict(data.get("working_memory") or {}))
        return cls(**data)


class FrameManager:
    def __init__(self, root: Path, *, max_depth: int = 4) -> None:
        self.root = Path(root).expanduser().resolve()
        self.max_depth = max_depth
        self.frames: dict[str, Frame] = {}
        self.active_frame_id: str | None = None
        self.events_path = self.root / "state" / "frames" / "frames.jsonl"
        self._load_latest()

    def create_root_frame(self, goal: str) -> Frame:
        frame = Frame(
            frame_id=uuid.uuid4().hex,
            parent_frame_id=None,
            depth=0,
            goal=str(goal or "root turn"),
            inherited_context={},
        )
        self.frames = {frame.frame_id: frame}
        self.active_frame_id = frame.frame_id
        self._persist("root_created")
        return frame

    def open_child_frame(self, goal: str, inherited_context: dict[str, Any]) -> Frame:
        parent = self.current_frame()
        if parent is None:
            parent = self.create_root_frame("root turn")
        if parent.depth + 1 > self.max_depth:
            raise ValueError(f"frame depth limit exceeded: max depth is {self.max_depth}")
        child = Frame(
            frame_id=uuid.uuid4().hex,
            parent_frame_id=parent.frame_id,
            depth=parent.depth + 1,
            goal=str(goal or "child frame"),
            inherited_context=dict(inherited_context or {}),
        )
        self.frames[child.frame_id] = child
        self.active_frame_id = child.frame_id
        self._persist("child_opened")
        return child

    def return_to_parent(self, return_payload: dict[str, Any]) -> Frame:
        child = self.current_frame()
        if child is None or child.parent_frame_id is None:
            raise ValueError("cannot return to parent from root frame")
        child.return_payload = dict(return_payload or {})
        child.status = "returned"
        child.returned_at = now_iso()
        parent = self.frames[child.parent_frame_id]
        self.active_frame_id = parent.frame_id
        self._persist("child_returned")
        return parent

    def current_frame(self) -> Frame | None:
        if self.active_frame_id is None:
            return None
        return self.frames.get(self.active_frame_id)

    def frame_stack(self) -> list[Frame]:
        current = self.current_frame()
        if current is None:
            return []
        stack = [current]
        while stack[-1].parent_frame_id:
            parent = self.frames.get(str(stack[-1].parent_frame_id))
            if parent is None:
                break
            stack.append(parent)
        return list(reversed(stack))

    def update_working_memory(self, updates: dict[str, Any]) -> None:
        frame = self.current_frame()
        if frame is None:
            return
        wm = frame.working_memory
        for key in ("observations", "unresolved_questions", "avoid_repeating"):
            values = updates.get(key)
            if not values:
                continue
            target = getattr(wm, key)
            for value in values if isinstance(values, list) else [values]:
                clean = str(value or "").strip()
                if clean and clean not in target:
                    target.append(clean)
        if "current_focus" in updates:
            wm.current_focus = str(updates.get("current_focus") or "")
        self._persist("working_memory_updated")

    def update_from_tool_result(self, tool_name: str, tool_args: dict[str, Any], tool_result: dict[str, Any]) -> None:
        if self.current_frame() is None:
            return
        ok = bool(tool_result.get("ok"))
        updates: dict[str, Any] = {}
        if tool_name == "read_file":
            path = str(tool_result.get("path") or tool_args.get("path") or "")
            updates["observations"] = [f"read_file: {path}"] if path else []
            updates["current_focus"] = path
        elif tool_name == "search_code":
            query = str(tool_args.get("query") or "")
            updates["observations"] = [f"search_code: {query}"] if query else []
            updates["current_focus"] = query
        elif tool_name == "run_command":
            command = str(tool_result.get("command") or tool_args.get("command") or "")
            status = "succeeded" if ok else "failed"
            updates["observations"] = [f"run_command {status}: {command}"] if command else []
            if not ok and command:
                updates["avoid_repeating"] = [command]
        elif tool_name in {"write_file", "append_file", "replace_text"}:
            path = str(tool_result.get("path") or tool_args.get("path") or "")
            status = "succeeded" if ok else "failed"
            updates["observations"] = [f"{tool_name} {status}: {path}"] if path else []
            updates["current_focus"] = path
        self.update_working_memory(updates)

    def set_child_tasks(self, tasks: list[dict[str, Any]]) -> None:
        frame = self.current_frame()
        if frame is None:
            return
        frame.working_memory.child_tasks = [dict(task) for task in tasks]
        frame.working_memory.completed_child_tasks = []
        self._persist("child_tasks_planned")

    def next_pending_child_task(self, frame: Frame | None = None) -> dict[str, Any] | None:
        target = frame or self.current_frame()
        if target is None:
            return None
        completed_ids = {
            str(item.get("task_id") or "")
            for item in target.working_memory.completed_child_tasks
            if item.get("task_id")
        }
        for task in target.working_memory.child_tasks:
            task_id = str(task.get("task_id") or "")
            if task_id and task_id not in completed_ids:
                return dict(task)
        return None

    def mark_child_task_completed(self, *, parent: Frame, child: Frame | None, return_payload: dict[str, Any]) -> None:
        if child is None:
            return
        task_id = str(child.inherited_context.get("child_task_id") or "")
        if not task_id:
            return
        already_done = {
            str(item.get("task_id") or "")
            for item in parent.working_memory.completed_child_tasks
            if item.get("task_id")
        }
        if task_id in already_done:
            return
        parent.working_memory.completed_child_tasks.append(
            {
                "task_id": task_id,
                "goal": child.goal,
                "summary": str(return_payload.get("summary") or ""),
                "findings": list(return_payload.get("findings") or []),
            }
        )
        self._persist("child_task_completed")

    def append_event(self, event: dict[str, Any], *, frame_id: str | None = None) -> None:
        frame = self.frames.get(frame_id or str(self.active_frame_id or ""))
        if frame is None:
            return
        frame.session_events.append(dict(event))
        self._persist("event_appended")

    def increment_step(self) -> int:
        frame = self.current_frame()
        if frame is None:
            return 0
        frame.step_count += 1
        self._persist("step_incremented")
        return frame.step_count

    def abandon_all(self) -> None:
        for frame in self.frames.values():
            if frame.status == "active":
                frame.status = "abandoned"
                frame.returned_at = now_iso()
        self.active_frame_id = None
        self._persist("abandoned")

    def snapshot(self) -> dict[str, Any]:
        all_events = [event for frame in self.frames.values() for event in frame.session_events]
        return {
            "active_frame_id": self.active_frame_id,
            "frame_stack": [frame.to_dict() for frame in self.frame_stack()],
            "frames": [frame.to_dict() for frame in sorted(self.frames.values(), key=lambda item: item.created_at)],
            "metrics": {
                "frame_opened": sum(1 for event in all_events if event.get("type") == "frame_opened"),
                "frame_returned": sum(1 for event in all_events if event.get("type") == "frame_returned"),
                "child_return": sum(1 for event in all_events if event.get("type") == "child_return"),
                "active_depth": self.current_frame().depth if self.current_frame() else 0,
            },
        }

    def _persist(self, event_type: str) -> None:
        append_jsonl(
            self.events_path,
            {
                "type": event_type,
                "timestamp": now_iso(),
                "snapshot": {
                    "active_frame_id": self.active_frame_id,
                    "frames": [frame.to_dict() for frame in self.frames.values()],
                },
            },
        )

    def _load_latest(self) -> None:
        rows = read_jsonl(self.events_path, limit=1)
        if not rows:
            return
        snapshot = rows[-1].get("snapshot") or {}
        self.frames = {
            str(item.get("frame_id")): Frame.from_dict(item)
            for item in snapshot.get("frames") or []
            if item.get("frame_id")
        }
        self.active_frame_id = snapshot.get("active_frame_id")
