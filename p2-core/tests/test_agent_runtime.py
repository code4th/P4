from __future__ import annotations

import unittest

from p2_core.agent_runtime import AgentRuntime, RuntimeValidationError


def _llm(action: str, action_input: dict[str, object], *, thinking: str = "局所判断") -> dict[str, object]:
    return {
        "thinking": thinking,
        "action": action,
        "action_input": action_input,
    }


class AgentRuntimeTests(unittest.TestCase):
    def test_open_child_frame_saves_child_goals(self) -> None:
        runtime = AgentRuntime(goal="目的達成")
        result = runtime.step(
            _llm(
                "open_child_frame",
                {"next_goal": "Aを調査", "child_goals": ["Aを調査", "Aを修正"]},
            )
        )
        root = runtime.frames[runtime.root_frame_id]
        self.assertEqual(root.status, "waiting_child")
        self.assertEqual(root.child_goals, ["Aを調査", "Aを修正"])
        self.assertEqual(root.current_child_index, 0)
        self.assertEqual(root.child_results, [])
        self.assertIn("spawned_child_frame_id", result)

    def test_next_goal_must_match_first_child_goal(self) -> None:
        runtime = AgentRuntime(goal="目的達成")
        with self.assertRaises(RuntimeValidationError):
            runtime.validate_llm_output(
                _llm(
                    "open_child_frame",
                    {"next_goal": "Aを調査", "child_goals": ["Bを調査", "Aを調査"]},
                )
            )

    def test_child_continue_or_return_is_merged_into_parent_results(self) -> None:
        runtime = AgentRuntime(goal="目的達成")
        runtime.step(_llm("open_child_frame", {"next_goal": "A", "child_goals": ["A"]}))
        child = runtime.get_active_frame()
        runtime.step(
            _llm(
                "continue_or_return",
                {"return_payload": {"status": "done", "summary": "A完了", "learned_findings": ["ok"]}},
            )
        )
        root = runtime.frames[runtime.root_frame_id]
        self.assertEqual(root.status, "active")
        self.assertEqual(root.child_results[0]["child_frame_id"], child.frame_id)
        self.assertEqual(root.child_results[0]["return_payload"]["summary"], "A完了")

    def test_parent_processes_pending_children_sequentially(self) -> None:
        runtime = AgentRuntime(goal="目的達成")
        runtime.step(_llm("open_child_frame", {"next_goal": "A", "child_goals": ["A", "B"]}))
        first_child = runtime.get_active_frame()
        result_one = runtime.step(
            _llm("continue_or_return", {"return_payload": {"status": "done", "summary": "A done"}})
        )
        self.assertIn("auto_next_child_frame_id", result_one)
        second_child = runtime.get_active_frame()
        self.assertNotEqual(first_child.frame_id, second_child.frame_id)
        self.assertEqual(second_child.goal, "B")
        result_two = runtime.step(
            _llm("continue_or_return", {"return_payload": {"status": "done", "summary": "B done"}})
        )
        root = runtime.frames[runtime.root_frame_id]
        self.assertTrue(result_two.get("all_children_completed"))
        self.assertEqual(root.status, "active")
        self.assertEqual(root.current_child_index, 2)
        self.assertEqual(len(root.child_results), 2)

    def test_finish_rejected_without_successful_validation(self) -> None:
        runtime = AgentRuntime(goal="目的達成")
        with self.assertRaises(RuntimeValidationError):
            runtime.step(_llm("finish", {}))

    def test_repeated_validation_failure_blocks_apply_patch(self) -> None:
        runtime = AgentRuntime(goal="目的達成")
        runtime.step(_llm("read_file", {"path": "agent/goal_logic.py"}), tool_result={"path": "agent/goal_logic.py"})
        runtime.step(
            _llm("run_validation", {}),
            tool_result={"passed": False, "failure_signature": "SyntaxError:1"},
        )
        runtime.step(
            _llm("run_validation", {}),
            tool_result={"passed": False, "failure_signature": "SyntaxError:1"},
        )
        with self.assertRaises(RuntimeValidationError):
            runtime.step(
                _llm(
                    "apply_patch",
                    {"path": "agent/goal_logic.py", "edits": [{"old_text": "a", "new_text": "b"}]},
                ),
                tool_result={"applied": True},
            )

    def test_needs_replan_allows_parent_to_replace_child_goals(self) -> None:
        runtime = AgentRuntime(goal="目的達成")
        runtime.step(
            _llm(
                "open_child_frame",
                {"next_goal": "A", "child_goals": ["A", "B"]},
            )
        )
        runtime.step(
            _llm(
                "continue_or_return",
                {"return_payload": {"status": "needs_replan", "summary": "前提崩壊"}},
            )
        )
        root = runtime.frames[runtime.root_frame_id]
        self.assertEqual(root.status, "active")
        runtime.step(
            _llm(
                "open_child_frame",
                {"next_goal": "C", "child_goals": ["C", "D", "E"]},
            )
        )
        root = runtime.frames[runtime.root_frame_id]
        self.assertEqual(root.child_goals, ["C", "D", "E"])
        self.assertEqual(root.current_child_index, 0)
        self.assertEqual(root.child_results, [])
        self.assertEqual(runtime.get_active_frame().goal, "C")

    def test_child_three_same_failures_requires_return(self) -> None:
        runtime = AgentRuntime(goal="目的達成")
        runtime.step(_llm("open_child_frame", {"next_goal": "A", "child_goals": ["A"]}))
        child = runtime.get_active_frame()
        runtime.step(_llm("read_file", {"path": "agent/goal_logic.py"}), tool_result={"ok": True})
        for _ in range(3):
            runtime.step(
                _llm("run_validation", {}),
                tool_result={"passed": False, "failure_signature": "same-failure"},
            )
        with self.assertRaises(RuntimeValidationError):
            runtime.step(
                _llm("apply_patch", {"path": "agent/goal_logic.py", "edits": [{"old_text": "a", "new_text": "b"}]}),
                tool_result={"ok": True},
            )
        self.assertEqual(child.frame_id, runtime.get_active_frame().frame_id)


if __name__ == "__main__":
    unittest.main()
