from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from p2_core.backend import StaticBackend
from p2_core.loop import run_loop
from p2_core.workspace import (
    bootstrap_workspace,
    build_status_snapshot,
    copytree_archive_best_effort,
    read_history,
    read_json,
    resolve_model_roles,
    resolve_runtime_kernel,
    reset_workspace,
    reconcile_runtime_status,
    update_goal_from_dashboard,
    write_json,
)


SUCCESS_RESPONSE = json.dumps(
    {
        "reasoning_summary": {
            "problem_statement": "自己説明が弱い。",
            "diagnosis": "改善ノートがない。",
            "edit_intent": "改善ノートを追加する。",
            "why_this_file": "このファイルが自己改善対象だから。",
            "expected_effect": "テストを維持したまま説明性が上がる。",
            "validation_hypothesis": "既存契約を壊さなければ通る。",
            "next_if_fail": "stderr を確認する。",
        },
        "change_summary": "改善ノートを追加した。",
        "revised_file_content": """from __future__ import annotations

import argparse
import json


AGENT_NAME = "P2自己改善エージェント"
STREAM_STYLE = "structured"
OPERATOR_GUIDANCE = [
    "観測しやすいこと",
    "失敗理由を残すこと",
]


def render_improvement_note() -> str:
    return "改善ノートを追加しました。"


def describe_agent() -> dict[str, object]:
    return {
        "agent_name": AGENT_NAME,
        "stream_style": STREAM_STYLE,
        "operator_guidance": list(OPERATOR_GUIDANCE),
        "improvement_note": render_improvement_note(),
    }


def render_operator_message() -> str:
    return "P2 は自己改善ループを実行中です。"


def self_check() -> int:
    payload = describe_agent()
    if not payload["agent_name"]:
        return 1
    if len(payload["operator_guidance"]) < 2:
        return 1
    if "自己改善" not in render_operator_message():
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="P2 self improvement demo agent")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--describe", action="store_true")
    args = parser.parse_args()
    if args.check:
        return self_check()
    if args.describe:
        print(json.dumps(describe_agent(), ensure_ascii=False, indent=2))
        return 0
    print(render_operator_message())
    print(render_improvement_note())
    print(json.dumps(describe_agent(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
""",
    }
)


class WorkspaceTests(unittest.TestCase):
    def test_copytree_archive_best_effort_ignores_missing_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            target = root / "target"
            source.mkdir(parents=True)
            (source / "data.txt").write_text("ok", encoding="utf-8")

            def flaky_copytree(*args, **kwargs):
                raise shutil.Error(
                    [
                        (
                            str(source / "gone.pyc"),
                            str(target / "gone.pyc"),
                            "[Errno 2] No such file or directory",
                        )
                    ]
                )

            with mock.patch("p2_core.workspace.shutil.copytree", side_effect=flaky_copytree):
                copytree_archive_best_effort(source, target)

            self.assertTrue(target.exists())

    def test_bootstrap_creates_seed_runtime_and_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = bootstrap_workspace(root, force=True)
            snapshot = build_status_snapshot(root)

            self.assertTrue(payload["ok"])
            self.assertEqual(snapshot["goal"]["status"], "active")
            self.assertEqual(snapshot["active_generation"], 1)
            self.assertEqual(snapshot["self_model_summary"]["editable_zones"], ["agent/goal_logic.py"])
            self.assertEqual(snapshot["self_model_summary"]["editable_zone_specs"][1]["zone_id"], "operator_runtime_loop")
            self.assertEqual(snapshot["self_model_summary"]["runtime_kernel"], "legacy_phase_loop_v1")
            self.assertEqual(snapshot["self_model_summary"]["default_thinking_model"], "gemma4:26b")
            self.assertEqual(snapshot["self_model_summary"]["default_coding_model"], "qwen3-coder:latest")
            self.assertEqual(snapshot["self_model_summary"]["default_exploratory_coding_model"], "devstral:latest")
            self.assertEqual(snapshot["self_model_summary"]["default_stagnation_coding_model"], "gemma4:26b")
            self.assertTrue((root / "seed" / "initial" / "version" / "agent" / "goal_logic.py").exists())
            self.assertTrue((root / "runtime" / "versions" / "v0001" / "tests" / "test_goal_logic.py").exists())
            self.assertGreaterEqual(len(snapshot["system_skills"]), 3)
            self.assertEqual(snapshot["recent_memos"], [])
            history = read_history(root)
            self.assertEqual(history[-1]["step"], "bootstrap")

    def test_reset_restores_initial_state_and_archives_previous_live_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            result = run_loop(root, model="fake-model", backend=StaticBackend(SUCCESS_RESPONSE))
            self.assertEqual(result["goal_status"], "active")

            reset = reset_workspace(root, mode="initial")
            snapshot = build_status_snapshot(root)
            current_source = (root / "runtime" / "versions" / "v0001" / "agent" / "goal_logic.py").read_text(encoding="utf-8")

            self.assertTrue(reset["ok"])
            self.assertEqual(snapshot["goal"]["status"], "active")
            self.assertEqual(snapshot["goal"]["goal_id"], "goal-continuous-self-improvement")
            self.assertEqual(snapshot["goal"]["cycle_count"], 0)
            self.assertEqual(snapshot["active_generation"], 1)
            self.assertEqual(snapshot["recent_history"][-1]["step"], "reset")
            self.assertIn("P2 は自己改善ループを実行中です。", current_source)
            self.assertTrue(Path(reset["archive_path"]).exists())
            self.assertEqual(snapshot["recent_attempts"], [])
            self.assertEqual(snapshot["recent_memos"], [])

    def test_status_snapshot_syncs_legacy_self_model_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            legacy_payload = {
                "editable_zones": ["agent/goal_logic.py"],
                "immutable_paths": ["tests/"],
                "runtime_kernel": "session_action_loop_v1",
            }
            (root / "state" / "self_model.json").write_text(
                json.dumps(legacy_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            snapshot = build_status_snapshot(root)

            self.assertEqual(snapshot["self_model_summary"]["editable_zones"], ["agent/goal_logic.py"])
            self.assertEqual(snapshot["self_model_summary"]["runtime_kernel"], "session_action_loop_v1")
            self.assertEqual(snapshot["self_model_summary"]["editable_zone_specs"][0]["zone_id"], "agent_goal_logic")
            self.assertEqual(snapshot["self_model_summary"]["editable_zone_specs"][1]["zone_id"], "operator_runtime_loop")
            self.assertEqual(snapshot["self_model_summary"]["default_thinking_model"], "gemma4:26b")
            self.assertEqual(snapshot["self_model_summary"]["default_exploratory_coding_model"], "devstral:latest")

    def test_resolve_model_roles_uses_workspace_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)

            roles = resolve_model_roles(root)

            self.assertEqual(roles["model"], "qwen3-coder:latest")
            self.assertEqual(roles["thinking_model"], "gemma4:26b")
            self.assertEqual(roles["coding_model"], "qwen3-coder:latest")
            self.assertEqual(roles["exploratory_coding_model"], "devstral:latest")
            self.assertEqual(roles["stagnation_coding_model"], "gemma4:26b")

    def test_resolve_runtime_kernel_uses_workspace_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)

            self.assertEqual(resolve_runtime_kernel(root), "legacy_phase_loop_v1")

    def test_update_goal_from_dashboard_resets_goal_dependent_runtime_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            status_path = root / "state" / "runtime" / "status.json"
            runtime = read_json(status_path)
            runtime.update(
                {
                    "current_task_stack": [{"frame_id": "old"}],
                    "current_focus": "old focus",
                    "current_observations": {"target_file_contents": {"agent/goal_logic.py": "old"}},
                    "recent_tool_results": [{"action": "apply_patch"}],
                    "recent_validation_results": {"latest_failure": {"summary": "old"}},
                    "recent_diffs": {"action_raw": {"diff_excerpt": ["old"]}},
                    "working_memory": {"local_working_memory": {"current_focus": "old"}},
                    "child_return_payloads": [{"summary": "old"}],
                }
            )
            write_json(status_path, runtime)

            result = update_goal_from_dashboard(root, goal_text="迷路作成CLIを完成させる", reset_mode=None)
            snapshot = build_status_snapshot(root)
            updated = snapshot["runtime_status"]

            self.assertTrue(result["preflight_ok"])
            self.assertIsNone(updated["current_task_stack"])
            self.assertNotEqual(updated["current_focus"], "old focus")
            self.assertEqual(updated["current_focus"], updated["goal_preflight"]["current_focus"])
            self.assertEqual(updated["current_observations"], {"target_file_contents": {}})
            self.assertEqual(updated["recent_tool_results"], [])
            self.assertEqual(updated["recent_validation_results"], {})
            self.assertEqual(updated["recent_diffs"], {})
            self.assertEqual(updated["working_memory"], {"local_working_memory": {}})
            self.assertEqual(updated["child_return_payloads"], [])
            self.assertTrue(updated["goal_reset_pending"])
            self.assertEqual(updated["goal_preflight"]["target_file"], "agent/goal_logic.py")
            self.assertTrue(updated["goal_preflight"]["validation_command"])

    @mock.patch("p2_core.workspace._dashboard_health_ok_from_url", return_value=False)
    @mock.patch("p2_core.workspace._is_pid_running", return_value=False)
    def test_reconcile_runtime_status_clears_stale_watchdog_dashboard_state(
        self,
        _mock_pid_running: mock.Mock,
        _mock_dashboard_health: mock.Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            status_path = root / "state" / "runtime" / "status.json"
            runtime = read_json(status_path)
            runtime.update(
                {
                    "dashboard_owner": "watchdog",
                    "dashboard_mode": "in_process",
                    "dashboard_notify_url": "http://127.0.0.1:8897/api/notify",
                    "dashboard_health_url": "http://127.0.0.1:8897/api/health",
                    "watchdog_pid": 11111,
                    "worker_pid": 22222,
                    "last_event": "worker_stopped",
                }
            )
            write_json(status_path, runtime)

            updated = reconcile_runtime_status(root)

            self.assertIsNone(updated["watchdog_pid"])
            self.assertIsNone(updated["worker_pid"])
            self.assertIsNone(updated["dashboard_owner"])
            self.assertIsNone(updated["dashboard_mode"])
            self.assertIsNone(updated["dashboard_notify_url"])
            self.assertIsNone(updated["dashboard_health_url"])
            self.assertEqual(updated["last_event"], "watchdog_stale_state_cleared")

    @mock.patch("p2_core.workspace._dashboard_health_ok_from_url", return_value=False)
    def test_reconcile_runtime_status_clears_stale_standalone_dashboard_state(
        self,
        _mock_dashboard_health: mock.Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root, force=True)
            status_path = root / "state" / "runtime" / "status.json"
            runtime = read_json(status_path)
            runtime.update(
                {
                    "dashboard_owner": "standalone",
                    "dashboard_mode": "process",
                    "dashboard_notify_url": "http://127.0.0.1:8897/api/notify",
                    "dashboard_health_url": "http://127.0.0.1:8897/api/health",
                    "last_event": "dashboard_started",
                }
            )
            write_json(status_path, runtime)

            updated = reconcile_runtime_status(root)

            self.assertIsNone(updated["dashboard_owner"])
            self.assertIsNone(updated["dashboard_mode"])
            self.assertIsNone(updated["dashboard_notify_url"])
            self.assertIsNone(updated["dashboard_health_url"])
            self.assertEqual(updated["last_event"], "dashboard_stale_state_cleared")
