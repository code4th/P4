from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
import json


AllowedAction = Literal[
    "read_file",
    "search_code",
    "apply_patch",
    "run_validation",
    "open_child_frame",
    "continue_or_return",
    "finish",
]
FrameStatus = Literal["active", "executing", "waiting_child", "completed", "blocked"]
ReturnStatus = Literal["done", "blocked", "needs_replan"]

ALLOWED_ACTIONS: tuple[AllowedAction, ...] = (
    "read_file",
    "search_code",
    "apply_patch",
    "run_validation",
    "open_child_frame",
    "continue_or_return",
    "finish",
)
ALLOWED_RETURN_STATUS: tuple[ReturnStatus, ...] = ("done", "blocked", "needs_replan")


class RuntimeValidationError(ValueError):
    pass


def _brief(value: Any, *, max_chars: int = 240) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


@dataclass
class Frame:
    frame_id: str
    parent_frame_id: str | None
    goal: str
    status: FrameStatus = "active"
    child_goals: list[str] = field(default_factory=list)
    current_child_index: int = 0
    child_results: list[dict[str, Any]] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    last_action: str = ""
    last_validation_result: dict[str, Any] | None = None
    observations: list[dict[str, Any]] = field(default_factory=list)
    patches_applied: list[dict[str, Any]] = field(default_factory=list)
    validations_run: list[dict[str, Any]] = field(default_factory=list)
    validation_success: bool = False
    known_open_questions: list[str] = field(default_factory=list)

    def has_pending_children(self) -> bool:
        return self.current_child_index < len(self.child_goals)

    def current_child_goal(self) -> str | None:
        if not self.has_pending_children():
            return None
        return self.child_goals[self.current_child_index]


@dataclass
class AgentRuntime:
    goal: str
    root_frame_id: str = "f0001"
    max_failed_patch_streak: int = 2
    max_child_same_failure_streak: int = 3
    frames: dict[str, Frame] = field(default_factory=dict)
    active_frame_id: str = ""
    _frame_seq: int = 1

    # State transition model:
    # active -> executing -> active
    # active -> waiting_child (open_child_frame)
    # waiting_child -> active (child continue_or_return)
    # active -> completed (finish)
    # active -> blocked (forced loop guard for child)
    def __post_init__(self) -> None:
        if self.frames:
            return
        root = Frame(frame_id=self.root_frame_id, parent_frame_id=None, goal=self.goal, status="active")
        self.frames[root.frame_id] = root
        self.active_frame_id = root.frame_id

    def get_active_frame(self) -> Frame:
        frame = self.frames.get(self.active_frame_id)
        if frame is None:
            raise RuntimeValidationError("active frame not found")
        return frame

    def validate_llm_output(self, raw_output: str | dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any]
        if isinstance(raw_output, str):
            try:
                decoded = json.loads(raw_output)
            except json.JSONDecodeError as exc:
                raise RuntimeValidationError(f"invalid JSON: {exc}") from exc
            if not isinstance(decoded, dict):
                raise RuntimeValidationError("LLM output must be a JSON object")
            payload = decoded
        elif isinstance(raw_output, dict):
            payload = dict(raw_output)
        else:
            raise RuntimeValidationError("LLM output must be dict or JSON string")

        for key in ("thinking", "action", "action_input"):
            if key not in payload:
                raise RuntimeValidationError(f"missing field: {key}")
        if not isinstance(payload["action_input"], dict):
            raise RuntimeValidationError("action_input must be object")
        if isinstance(payload["action_input"].get("actions"), list):
            raise RuntimeValidationError("1ターン1 action 制約違反: action_input.actions は禁止")

        action = str(payload["action"]).strip()
        if action not in ALLOWED_ACTIONS:
            raise RuntimeValidationError(f"unsupported action: {action}")

        action_input = dict(payload["action_input"])
        if action == "open_child_frame":
            next_goal = _brief(action_input.get("next_goal"))
            if not next_goal:
                raise RuntimeValidationError("open_child_frame requires next_goal")
            child_goals = action_input.get("child_goals")
            if child_goals is None:
                child_goals = [next_goal]
            if not isinstance(child_goals, list):
                raise RuntimeValidationError("open_child_frame.child_goals must be array")
            normalized: list[str] = []
            for item in child_goals:
                candidate = _brief(item)
                if candidate:
                    normalized.append(candidate)
            if not normalized:
                normalized = [next_goal]
            if normalized[0] != next_goal:
                raise RuntimeValidationError("next_goal must equal child_goals[0]")
            action_input["next_goal"] = next_goal
            action_input["child_goals"] = normalized

        if action == "continue_or_return":
            return_payload = action_input.get("return_payload")
            if not isinstance(return_payload, dict):
                raise RuntimeValidationError("continue_or_return requires return_payload")
            status = str(return_payload.get("status") or "").strip()
            if status not in ALLOWED_RETURN_STATUS:
                raise RuntimeValidationError("return_payload.status must be done / blocked / needs_replan")

        payload["action"] = action
        payload["action_input"] = action_input
        payload["thinking"] = _brief(payload.get("thinking"), max_chars=1000)
        return payload

    def validate_action(self, frame: Frame, payload: dict[str, Any]) -> None:
        action = str(payload["action"])
        action_input = dict(payload["action_input"])

        if frame.status != "active":
            raise RuntimeValidationError(f"frame is not active: {frame.status}")
        if self._force_return_to_parent(frame) and action != "continue_or_return":
            raise RuntimeValidationError("child frame is in repeated-failure guard; continue_or_return required")
        if action == "finish":
            if not frame.last_validation_result or not frame.last_validation_result.get("passed"):
                raise RuntimeValidationError("finish requires successful run_validation immediately before finish")
            if not frame.validation_success:
                raise RuntimeValidationError("finish requires validation_success")
        if action == "apply_patch":
            if not frame.observations:
                raise RuntimeValidationError("apply_patch requires prior observation")
            if self._should_block_patch(frame):
                raise RuntimeValidationError("apply_patch temporarily blocked due to repeated validation failures")
        if action == "open_child_frame":
            next_goal = _brief(action_input.get("next_goal"))
            child_goals = action_input.get("child_goals")
            if not next_goal or not isinstance(child_goals, list) or not child_goals:
                raise RuntimeValidationError("open_child_frame requires next_goal + child_goals")
            if str(child_goals[0]) != next_goal:
                raise RuntimeValidationError("next_goal must equal child_goals[0]")
        if action == "continue_or_return":
            if frame.parent_frame_id is None:
                raise RuntimeValidationError("continue_or_return is child-only action")
            if not isinstance(action_input.get("return_payload"), dict):
                raise RuntimeValidationError("continue_or_return requires return_payload")
        if frame.parent_frame_id is None and self._parent_is_replan_or_blocked(frame):
            if action not in {"read_file", "search_code", "open_child_frame"}:
                raise RuntimeValidationError("blocked/replan parent can only observe or replan child goals")

    def step(self, raw_output: str | dict[str, Any], *, tool_result: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = self.validate_llm_output(raw_output)
        frame = self.get_active_frame()
        self.validate_action(frame, payload)

        action = str(payload["action"])
        action_input = dict(payload["action_input"])
        frame.last_action = action

        if action in {"read_file", "search_code", "apply_patch", "run_validation"}:
            frame.status = "executing"
            result = dict(tool_result or {})
            self._apply_tool_result(frame, action, action_input, result)
            frame.status = "active"
            return {"frame_id": frame.frame_id, "action": action, "result": result, "status": frame.status}

        if action == "open_child_frame":
            child_goals = [str(item) for item in (action_input.get("child_goals") or [])]
            frame.child_goals = child_goals
            frame.current_child_index = 0
            frame.child_results = []
            frame.status = "waiting_child"
            child = self._spawn_child_frame(parent=frame, goal=child_goals[0], context={"planned_goals": child_goals})
            self.active_frame_id = child.frame_id
            return {
                "frame_id": frame.frame_id,
                "action": action,
                "status": frame.status,
                "spawned_child_frame_id": child.frame_id,
                "child_goals": list(child_goals),
            }

        if action == "continue_or_return":
            return_payload = dict(action_input["return_payload"])
            return self._handle_child_return(frame, return_payload=return_payload)

        if action == "finish":
            frame.status = "completed"
            return {"frame_id": frame.frame_id, "action": action, "status": "completed"}

        raise RuntimeValidationError(f"unsupported runtime action path: {action}")

    def build_turn_context(self, *, frame_id: str | None = None) -> dict[str, Any]:
        frame = self.frames[frame_id or self.active_frame_id]
        parent = self.frames.get(frame.parent_frame_id or "")
        recent_observation = frame.observations[-1] if frame.observations else {}
        recent_validation = frame.validations_run[-1] if frame.validations_run else {}
        latest_child = frame.child_results[-1] if frame.child_results else {}
        return {
            "frame_goal": frame.goal,
            "parent_goal_summary": _brief(parent.goal if parent else "", max_chars=160),
            "frame_status": frame.status,
            "recent_observation": recent_observation,
            "recent_validation": recent_validation,
            "known_open_questions": list(frame.known_open_questions)[-6:],
            "child_goals": list(frame.child_goals),
            "current_child_index": frame.current_child_index,
            "latest_child_return": latest_child,
            "child_results_summary": [
                {
                    "status": ((item.get("return_payload") or {}).get("status")),
                    "summary": _brief(((item.get("return_payload") or {}).get("summary")), max_chars=120),
                }
                for item in frame.child_results[-6:]
            ],
        }

    def _spawn_child_frame(self, *, parent: Frame, goal: str, context: dict[str, Any] | None = None) -> Frame:
        self._frame_seq += 1
        frame_id = f"f{self._frame_seq:04d}"
        child = Frame(
            frame_id=frame_id,
            parent_frame_id=parent.frame_id,
            goal=_brief(goal),
            status="active",
            context=dict(context or {}),
        )
        self.frames[child.frame_id] = child
        return child

    def _apply_tool_result(self, frame: Frame, action: str, action_input: dict[str, Any], result: dict[str, Any]) -> None:
        if action in {"read_file", "search_code"}:
            frame.observations.append({"action": action, "input": action_input, "result": result})
            if isinstance(result.get("open_questions"), list):
                frame.known_open_questions = [_brief(item) for item in result["open_questions"] if _brief(item)]
            return
        if action == "apply_patch":
            frame.patches_applied.append({"input": action_input, "result": result})
            return
        if action == "run_validation":
            passed = bool(result.get("passed"))
            signature = _brief(result.get("failure_signature") or result.get("error") or result.get("summary"))
            entry = {"passed": passed, "signature": signature, "result": result}
            frame.validations_run.append(entry)
            frame.last_validation_result = result
            frame.validation_success = passed
            return

    def _handle_child_return(self, child: Frame, *, return_payload: dict[str, Any]) -> dict[str, Any]:
        parent = self.frames.get(child.parent_frame_id or "")
        if parent is None:
            raise RuntimeValidationError("parent frame not found for child return")

        child_status = str(return_payload.get("status") or "")
        child.status = "completed" if child_status == "done" else "blocked"

        while len(parent.child_results) <= parent.current_child_index:
            parent.child_results.append({})
        parent.child_results[parent.current_child_index] = {
            "child_frame_id": child.frame_id,
            "child_goal": parent.current_child_goal(),
            "return_payload": return_payload,
        }
        parent.context["latest_child_return"] = dict(return_payload)
        parent.status = "active"
        self.active_frame_id = parent.frame_id

        if child_status == "done":
            parent.current_child_index += 1
            if parent.current_child_index < len(parent.child_goals):
                next_goal = parent.child_goals[parent.current_child_index]
                parent.status = "waiting_child"
                next_child = self._spawn_child_frame(parent=parent, goal=next_goal)
                self.active_frame_id = next_child.frame_id
                return {
                    "action": "continue_or_return",
                    "returned_from_child": child.frame_id,
                    "parent_frame_id": parent.frame_id,
                    "auto_next_child_frame_id": next_child.frame_id,
                    "next_child_goal": next_goal,
                }
            return {
                "action": "continue_or_return",
                "returned_from_child": child.frame_id,
                "parent_frame_id": parent.frame_id,
                "all_children_completed": True,
            }

        # blocked / needs_replan: keep index so parent can replan or recover.
        return {
            "action": "continue_or_return",
            "returned_from_child": child.frame_id,
            "parent_frame_id": parent.frame_id,
            "child_status": child_status,
            "requires_parent_replan": child_status == "needs_replan",
        }

    def _should_block_patch(self, frame: Frame) -> bool:
        failures = [entry for entry in frame.validations_run if not entry.get("passed")]
        if len(failures) < self.max_failed_patch_streak:
            return False
        last = failures[-1].get("signature")
        prev = failures[-2].get("signature")
        return bool(last and prev and last == prev)

    def _force_return_to_parent(self, frame: Frame) -> bool:
        if frame.parent_frame_id is None:
            return False
        failures = [entry for entry in frame.validations_run if not entry.get("passed")]
        if len(failures) < self.max_child_same_failure_streak:
            return False
        last = failures[-1].get("signature")
        if not last:
            return False
        window = failures[-self.max_child_same_failure_streak :]
        return all(item.get("signature") == last for item in window)

    def _parent_is_replan_or_blocked(self, frame: Frame) -> bool:
        if not frame.child_results:
            return False
        latest_payload = (frame.child_results[-1].get("return_payload") or {})
        return str(latest_payload.get("status") or "") in {"blocked", "needs_replan"}
