from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from p4_core.runtime import AgentRuntime
from p4_core.workspace import bootstrap_workspace, enqueue_message, read_jsonl


class FakeBackend:
    def __init__(self, responses: list[str], *, models: list[str] | None = None) -> None:
        self.responses = list(responses)
        self.models = list(models or ["test-model"])
        self.messages_seen: list[list[dict[str, str]]] = []

    def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        options: dict | None = None,
        timeout_seconds: int = 180,
    ) -> dict:
        self.messages_seen.append(messages)
        del model, options, timeout_seconds
        if not self.responses:
            raise AssertionError("no fake responses left")
        return {"content": self.responses.pop(0), "raw": {}}

    def chat_stream(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        options: dict | None = None,
        timeout_seconds: int = 180,
    ) -> list[dict]:
        self.messages_seen.append(messages)
        del model, options, timeout_seconds
        if not self.responses:
            raise AssertionError("no fake responses left")
        return [{"message": {"content": self.responses.pop(0)}, "done": True}]

    def list_models(self) -> dict:
        return {"models": [{"name": name} for name in self.models]}


class StreamingFakeBackend(FakeBackend):
    def __init__(self, chunks: list[str], *, models: list[str] | None = None) -> None:
        super().__init__([], models=models)
        self.chunks = list(chunks)

    def iter_chat_stream(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        options: dict | None = None,
        timeout_seconds: int = 180,
    ):
        self.messages_seen.append(messages)
        del model, options, timeout_seconds
        for chunk in self.chunks:
            yield {"message": {"content": chunk}, "done": False}
        yield {"message": {"content": ""}, "done": True}


def tool_step(tool_name: str, path: str = "", *, content: str = "", ok: bool = True, **result: object) -> dict:
    args: dict[str, object] = {}
    if path:
        args["path"] = path
    if content:
        args["content"] = content
    tool_result: dict[str, object] = {"ok": ok}
    if path:
        tool_result["path"] = path
    tool_result.update(result)
    return {"tool_name": tool_name, "tool_args": args, "tool_result": tool_result}


def run_step(command: str, *, ok: bool, stderr: str = "", stdout: str = "") -> dict:
    return {
        "tool_name": "run_command",
        "tool_args": {"command": command},
        "tool_result": {
            "ok": ok,
            "command": command,
            "returncode": 0 if ok else 1,
            "stdout": stdout,
            "stderr": stderr,
        },
    }


class GenericRuntimeContractTests(unittest.TestCase):
    def runtime(self, root: Path | None = None, responses: list[str] | None = None) -> AgentRuntime:
        if root is None:
            root = Path(tempfile.mkdtemp())
        bootstrap_workspace(root)
        return AgentRuntime(root, llm_backend=FakeBackend(responses or []))

    def test_runtime_contains_no_benchmark_task_specializations(self) -> None:
        source = Path("p4_core/runtime.py").read_text(encoding="utf-8")
        forbidden = [
            "ExactCover",
            "Sudoku",
            "Pentomino",
            "WorldBetter",
            "Exact Cover",
            "exact_cover",
            "sudoku",
            "pentomino",
            "world_better",
            "world_improvement",
            "FILNPTUVWXYZ",
            "generate_candidates",
            "score_candidate",
            "select_task",
            "before_after_evaluation",
            "row_id",
            "column_id",
            "row_id -> set",
        ]
        for marker in forbidden:
            self.assertNotIn(marker, source)

    def test_all_runtime_system_note_codes_are_prompt_visible(self) -> None:
        import re

        runtime_source = Path("p4_core/runtime.py").read_text(encoding="utf-8")
        prompt_source = Path("p4_core/prompts.py").read_text(encoding="utf-8")
        runtime_codes = set(re.findall(r'"code": "([^"]+)"', runtime_source))
        match = re.search(r"useful_system_codes = \{(.*?)\n    \}", prompt_source, re.S)
        self.assertIsNotNone(match)
        visible_codes = {item for item in re.findall(r'"([^"]*)"', match.group(1)) if item}

        self.assertEqual(sorted(runtime_codes - visible_codes), [])

    def test_failure_system_notes_include_actionable_recovery_contract(self) -> None:
        import ast

        runtime_source = Path("p4_core/runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(runtime_source)
        failure_markers = ("blocked", "failed", "invalid", "required", "incomplete", "ignored", "interrupt")
        required_detail_keys = {
            "failure_type",
            "blocked_by",
            "allowed_next_actions",
            "suggested_fix",
            "next_required_action",
        }
        missing: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            pairs: dict[str, ast.AST] = {}
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    pairs[key.value] = value
            event_type = pairs.get("type")
            code = pairs.get("code")
            if not (
                isinstance(event_type, ast.Constant)
                and event_type.value == "system_note"
                and isinstance(code, ast.Constant)
                and isinstance(code.value, str)
                and any(marker in code.value for marker in failure_markers)
            ):
                continue
            details = pairs.get("details")
            detail_keys: set[str] = set()
            if isinstance(details, ast.Dict):
                for detail_key in details.keys:
                    if isinstance(detail_key, ast.Constant) and isinstance(detail_key.value, str):
                        detail_keys.add(detail_key.value)
            elif details is not None:
                detail_keys.add("<dynamic>")
            missing_keys = set() if "<dynamic>" in detail_keys else required_detail_keys - detail_keys
            if details is None or missing_keys:
                missing.append(f"line {node.lineno}: {code.value}: {sorted(missing_keys or required_detail_keys)}")

        self.assertEqual(missing, [])

    def test_implementation_missing_requires_python_write_first(self) -> None:
        runtime = self.runtime()
        message = "Pythonで未知の文字列整形ツールを実装し、tests/ にunittestを追加して検証してください。"
        state = runtime._implementation_task_progress_state(user_message=message, steps=[])
        self.assertEqual(state["phase"], "implementation_missing")
        self.assertEqual(state["allowed_next_actions"], ["write_file <implementation>.py"])
        prompt = runtime._implementation_task_progress_prompt(state)
        self.assertIn("小さくても完全に動く単一", prompt)
        self.assertIn("module-level def", prompt)
        self.assertIn("未完成chunk", prompt)

        blocked = runtime._implementation_task_phase_action_block(
            user_message=message,
            tool_name="write_file",
            tool_args={"path": "tests/test_tool.py", "content": "import unittest\n"},
            steps=[],
            session_id="main",
            turn_workspace=runtime.execution_root,
        )
        self.assertEqual(blocked["reason_code"], "implementation_task_phase_requires_initial_implementation")

    def test_placeholder_implementation_cannot_advance_to_tests_or_finish(self) -> None:
        runtime = self.runtime()
        message = "Pythonで normalize_name(text) を実装し、tests/ にunittestを追加して検証してください。"
        impl = "def normalize_name(text):\n    pass\n"
        steps = [tool_step("write_file", "name_tools.py", content=impl)]

        state = runtime._implementation_task_progress_state(user_message=message, steps=steps)
        self.assertEqual(state["phase"], "implementation_present_but_placeholder")

        blocked = runtime._implementation_task_phase_action_block(
            user_message=message,
            tool_name="write_file",
            tool_args={"path": "tests/test_name_tools.py", "content": "import unittest\n"},
            steps=steps,
            session_id="main",
            turn_workspace=runtime.execution_root,
        )
        self.assertEqual(blocked["reason_code"], "implementation_task_phase_requires_placeholder_fix")

    def test_placeholder_phase_blocks_huge_replace_text_and_allows_write_file(self) -> None:
        runtime = self.runtime()
        message = "Pythonで normalize_name(text) を実装し、tests/ にunittestを追加して検証してください。"
        impl = "def normalize_name(text):\n    pass\n"
        fixed = (
            "def normalize_name(text):\n"
            "    return ' '.join(str(text).strip().split()).title()\n"
            + "\n".join(f"# filler {index}" for index in range(180))
            + "\n"
        )
        steps = [tool_step("write_file", "name_tools.py", content=impl)]
        (runtime.execution_root / "name_tools.py").write_text(impl, encoding="utf-8")

        blocked = runtime._implementation_task_phase_action_block(
            user_message=message,
            tool_name="replace_text",
            tool_args={"path": "name_tools.py", "old_text": impl, "new_text": fixed},
            steps=steps,
            session_id="main",
            turn_workspace=runtime.execution_root,
        )
        self.assertEqual(blocked["reason_code"], "implementation_task_placeholder_blocks_broad_replace_text")
        self.assertEqual(blocked["allowed_next_actions"][0], "write_file name_tools.py")
        self.assertTrue(blocked["broad_rewrite"])

        allowed_write = runtime._implementation_task_phase_action_block(
            user_message=message,
            tool_name="write_file",
            tool_args={"path": "name_tools.py", "content": fixed},
            steps=steps,
            session_id="main",
            turn_workspace=runtime.execution_root,
        )
        self.assertIsNone(allowed_write)

    def test_placeholder_phase_allows_small_targeted_replace_text(self) -> None:
        runtime = self.runtime()
        message = "Pythonで normalize_name(text) を実装し、tests/ にunittestを追加して検証してください。"
        impl = "def normalize_name(text):\n    pass\n"
        steps = [tool_step("write_file", "name_tools.py", content=impl)]
        (runtime.execution_root / "name_tools.py").write_text(impl, encoding="utf-8")

        allowed = runtime._implementation_task_phase_action_block(
            user_message=message,
            tool_name="replace_text",
            tool_args={
                "path": "name_tools.py",
                "old_text": "    pass\n",
                "new_text": "    return str(text).strip().title()\n",
            },
            steps=steps,
            session_id="main",
            turn_workspace=runtime.execution_root,
        )
        self.assertIsNone(allowed)

    def test_placeholder_reject_carries_recovery_contract_signature(self) -> None:
        runtime = self.runtime()
        issue = runtime._python_artifact_contract_issue(
            user_message="Pythonで normalize_name(text) を実装し、tests/ にunittestを追加して検証してください。",
            tool_name="write_file",
            tool_args={"path": "name_tools.py", "content": "def normalize_name(text):\n    pass\n"},
        )
        self.assertIsNotNone(issue)
        assert issue is not None
        self.assertEqual(issue["reason_code"], "python_artifact_contract_incomplete")
        self.assertEqual(issue["recovery_class"], "contract_reducing_full_implementation_required")
        self.assertTrue(str(issue["block_signature"]).startswith("placeholder:"))
        self.assertIn("normalize_name", "\n".join(issue["placeholder_markers"]))
        self.assertIn("全callable", issue["suggested_fix"])

    def test_initial_semantic_revision_prompt_requires_full_non_stub_implementation(self) -> None:
        runtime = self.runtime()
        prompt = runtime._implementation_task_progress_prompt(
            {
                "applicable": True,
                "phase": "implementation_missing_needs_semantic_revision",
                "contract_state": "incomplete",
                "missing_requirements": ["reviewed_implementation_strategy_not_applied"],
                "allowed_next_actions": ["write_file <implementation>.py"],
                "implementation_paths": [],
                "test_paths": [],
                "implementation_source_issues": [],
                "semantic_review_issues": ["公開関数がありません。"],
            }
        )
        self.assertIn("placeholder-free", prompt)
        self.assertIn("pass/TODO/ellipsis/NotImplementedError", prompt)
        self.assertIn("module-level def", prompt)
        self.assertIn("骨組み", prompt)

    def test_japanese_named_public_functions_require_module_level_defs(self) -> None:
        runtime = self.runtime()
        message = (
            "Pythonで汎用選択ツールを実装してください。"
            "pick_one は候補を1つ返してください。"
            "pick_all は全候補を返してください。"
            "validate_choice は候補が有効か検証してください。"
            "stats_report は top-level の counters を含む統計を返してください。"
            "入力は item_id -> set(feature_id) の辞書形式です。"
            "tests/ にunittestを追加して検証してください。"
        )
        impl = (
            "class Picker:\n"
            "    def pick_one(self, rows):\n"
            "        return next(iter(rows), None)\n"
            "    def pick_all(self, rows):\n"
            "        return list(rows)\n"
            "    def validate_choice(self, rows, choice):\n"
            "        return choice in rows\n"
            "    def stats_report(self, rows):\n"
            "        return {'counters': len(rows)}\n"
        )
        steps = [tool_step("write_file", "picker.py", content=impl)]

        requested = runtime._requested_top_level_function_names(message)
        self.assertEqual(requested, ["pick_one", "pick_all", "validate_choice", "stats_report"])
        state = runtime._implementation_task_progress_state(user_message=message, steps=steps)
        self.assertEqual(state["phase"], "implementation_present_needs_semantic_review")
        issue_text = "\n".join(state["implementation_source_issues"])
        self.assertIn("pick_one", issue_text)
        self.assertIn("pick_all", issue_text)
        self.assertIn("validate_choice", issue_text)
        self.assertIn("stats_report", issue_text)
        self.assertNotIn("item_id", issue_text)
        self.assertNotIn("feature_id", issue_text)

    def test_requested_public_functions_must_be_exercised_directly_by_tests(self) -> None:
        runtime = self.runtime()
        message = (
            "Pythonで汎用選択ツールを実装してください。"
            "pick_one は候補を1つ返してください。"
            "pick_all は全候補を返してください。"
            "validate_choice は候補が有効か検証してください。"
            "tests/ にunittestを追加して検証してください。"
        )
        impl = (
            "def pick_one(rows):\n"
            "    return next(iter(rows), None)\n"
            "def pick_all(rows):\n"
            "    return list(rows)\n"
            "def validate_choice(rows, choice):\n"
            "    return choice in rows\n"
        )
        test = (
            "import unittest\n\n"
            "class Picker:\n"
            "    def pick_one(self, rows):\n"
            "        return None\n"
            "    def pick_all(self, rows):\n"
            "        return []\n"
            "    def validate_choice(self, rows, choice):\n"
            "        return False\n\n"
            "class TestPicker(unittest.TestCase):\n"
            "    def test_picker_class(self):\n"
            "        picker = Picker()\n"
            "        self.assertIsNone(picker.pick_one({}))\n"
            "        self.assertEqual(picker.pick_all({}), [])\n"
            "        self.assertFalse(picker.validate_choice({}, 'x'))\n"
        )
        issue = runtime._requested_top_level_api_test_issue(
            user_message=message,
            test_sources=[("tests/test_picker.py", test)],
        )
        self.assertIn("pick_one", issue)
        self.assertIn("pick_all", issue)
        self.assertIn("validate_choice", issue)
        clean_issue = runtime._requested_top_level_api_test_issue(
            user_message=message,
            test_sources=[
                (
                    "tests/test_picker.py",
                    (
                        "import unittest\nfrom picker import pick_one, pick_all, validate_choice\n\n"
                        "class TestPicker(unittest.TestCase):\n"
                        "    def test_public_api(self):\n"
                        "        rows = {'a': {1}}\n"
                        "        self.assertEqual(pick_one(rows), 'a')\n"
                        "        self.assertEqual(pick_all(rows), ['a'])\n"
                        "        self.assertTrue(validate_choice(rows, 'a'))\n"
                    ),
                )
            ],
        )
        self.assertEqual(clean_issue, "")
        contract_issues = runtime._semantic_implementation_contract_issues(
            user_message=message,
            implementation_sources=[("picker.py", impl)],
            test_sources=[("tests/test_picker.py", test)],
        )
        self.assertTrue(any("top-level public API" in item for item in contract_issues))

    def test_identifier_mapping_contract_is_generic_not_task_named(self) -> None:
        runtime = self.runtime()
        message = (
            "Pythonで汎用mapping処理を実装してください。"
            "入力は item_id -> set(feature_id) の辞書形式です。"
            "select_item はIDを壊さず候補を返してください。"
            "tests/ にunittestを追加して検証してください。"
        )
        source = (
            "from typing import Dict, Set, List\n\n"
            "def select_item(rows: Dict[int, Set[int]]) -> List[int]:\n"
            "    return list(range(len(rows)))\n"
        )
        issue = runtime._python_source_narrows_requested_input_contract(
            user_message=message,
            source=source,
        )
        self.assertIn("IDを保持するmapping入力", issue)
        self.assertNotIn("item_id", issue)
        self.assertNotIn("feature_id", issue)

    def test_identifier_mapping_semantic_repair_is_read_once_then_targeted_edit(self) -> None:
        runtime = self.runtime()
        message = (
            "Pythonで汎用mapping処理を実装してください。"
            "入力は item_id -> set(feature_id) の辞書形式です。"
            "select_item はIDを壊さず候補を返してください。"
            "tests/ にunittestを追加して検証してください。"
        )
        source = (
            "from typing import Dict, Set, List\n\n"
            "def select_item(rows: Dict[int, Set[int]]) -> List[int]:\n"
            "    return list(range(len(rows)))\n"
        )
        steps = [tool_step("write_file", "picker.py", content=source)]

        state = runtime._implementation_task_progress_state(user_message=message, steps=steps)
        self.assertEqual(state["phase"], "implementation_present_needs_semantic_review")
        self.assertEqual(state["allowed_next_actions"], ["read_file picker.py once"])
        self.assertFalse(state["implementation_read_consumed"])

        steps.append(tool_step("read_file", "picker.py", content=source))
        state = runtime._implementation_task_progress_state(user_message=message, steps=steps)
        self.assertEqual(state["allowed_next_actions"], ["replace_text picker.py"])
        self.assertTrue(state["implementation_read_consumed"])
        prompt = runtime._implementation_task_progress_prompt(state)
        self.assertIn("mapping入力のID", prompt)
        self.assertIn("小さい replace_text", prompt)
        self.assertIn("残っている具体修復箇所", prompt)
        self.assertIn("def select_item(rows: Dict[int, Set[int]]) -> List[int]:", prompt)
        self.assertIn("suggested_new_text", prompt)

        (runtime.execution_root / "picker.py").write_text(source, encoding="utf-8")
        blocked = runtime._implementation_task_phase_action_block(
            user_message=message,
            tool_name="write_file",
            tool_args={"path": "picker.py", "content": source},
            steps=steps,
            session_id="main",
            turn_workspace=runtime.execution_root,
        )
        self.assertEqual(blocked["reason_code"], "implementation_task_phase_requires_contract_reducing_edit")
        self.assertEqual(blocked["allowed_next_actions"], ["replace_text picker.py"])
        self.assertIn("提案後も残る未達", blocked["message"])
        self.assertIn("def select_item(rows: Dict[int, Set[int]]) -> List[int]:", blocked["repair_hints"][0]["current_text"])

        partial_reduction = runtime._implementation_task_phase_action_block(
            user_message=message,
            tool_name="replace_text",
            tool_args={
                "path": "picker.py",
                "old_text": "def select_item(rows: Dict[int, Set[int]]) -> List[int]:",
                "new_text": "def select_item(rows: Dict[Any, Set[Any]]) -> List[int]:",
            },
            steps=steps,
            session_id="main",
            turn_workspace=runtime.execution_root,
        )
        self.assertIsNone(partial_reduction)

    def test_identifier_mapping_semantic_prompt_updates_remaining_repair_hint(self) -> None:
        runtime = self.runtime()
        message = (
            "Pythonで汎用mapping処理を実装してください。"
            "入力は item_id -> set(feature_id) の辞書形式です。"
            "pick_one はIDを壊さず候補を1つ返してください。"
            "pick_all はIDを壊さず全候補を返してください。"
            "tests/ にunittestを追加して検証してください。"
        )
        source = (
            "from typing import Any, Dict, Set, List\n\n"
            "def pick_one(rows: Dict[Any, Set[Any]]) -> Any:\n"
            "    return next(iter(rows), None)\n\n"
            "def pick_all(rows: Dict[str, Set[str]]) -> List[List[str]]:\n"
            "    return [list(rows)]\n"
        )
        steps = [
            tool_step("write_file", "picker.py", content=source),
            tool_step("read_file", "picker.py", content=source),
        ]

        state = runtime._implementation_task_progress_state(user_message=message, steps=steps)
        hints = state["implementation_source_repair_hints"]
        self.assertEqual(len(hints), 1)
        self.assertIn("def pick_all(rows: Dict[str, Set[str]]) -> List[List[str]]:", hints[0]["current_text"])
        self.assertIn("Dict[Any, Set[Any]]", hints[0]["suggested_new_text"])
        self.assertIn("List[List[Any]]", hints[0]["suggested_new_text"])

        prompt = runtime._implementation_task_progress_prompt(state)
        self.assertIn("def pick_all(rows: Dict[str, Set[str]]) -> List[List[str]]:", prompt)
        self.assertNotIn("def pick_one(rows: Dict[Any, Set[Any]]) -> Any:", prompt)
        self.assertNotIn("line 3: def pick_one", prompt)

        reducing_source = (
            "def pick_one(rows):\n"
            "    return next(iter(rows), None)\n\n"
            "def pick_all(rows):\n"
            "    return list(rows)\n"
        )
        self.assertIsNone(
            runtime._implementation_task_phase_action_block(
                user_message=message,
                tool_name="write_file",
                tool_args={"path": "picker.py", "content": reducing_source},
                steps=steps,
                session_id="main",
                turn_workspace=runtime.execution_root,
            )
        )

    def test_class_method_public_api_repair_prefers_wrapper_append(self) -> None:
        runtime = self.runtime()
        message = (
            "Pythonで汎用集計ツールを実装してください。"
            "summarize_values は集計を返してください。"
            "validate_summary は結果を検証してください。"
            "tests/ にunittestを追加して検証してください。"
        )
        source = (
            "class Summarizer:\n"
            "    def __init__(self, values):\n"
            "        self.values = list(values)\n"
            "    def summarize_values(self):\n"
            "        return {'count': len(self.values)}\n"
            "    def validate_summary(self, summary):\n"
            "        return summary.get('count') == len(self.values)\n"
        )
        steps = [
            tool_step("write_file", "summaries.py", content=source),
            tool_step("read_file", "summaries.py", content=source),
        ]

        state = runtime._implementation_task_progress_state(user_message=message, steps=steps)
        self.assertEqual(state["phase"], "implementation_present_needs_semantic_review")
        self.assertEqual(state["allowed_next_actions"], ["append_file summaries.py", "replace_text summaries.py"])
        hints = state["implementation_source_repair_hints"]
        self.assertEqual(hints[0]["suggested_action"], "append_top_level_wrappers")
        self.assertIn("def summarize_values(values):", hints[0]["suggested_new_text"])
        self.assertIn("return Summarizer(values).summarize_values()", hints[0]["suggested_new_text"])
        self.assertIn("def validate_summary(values, summary):", hints[0]["suggested_new_text"])

        prompt = runtime._implementation_task_progress_prompt(state)
        self.assertIn("top-level wrapper修復", prompt)
        self.assertIn("append_file", prompt)
        self.assertIn("巨大 replace_text", prompt)

        wrapper_block = hints[0]["suggested_new_text"]
        self.assertIsNone(
            runtime._implementation_task_phase_action_block(
                user_message=message,
                tool_name="append_file",
                tool_args={"path": "summaries.py", "content": wrapper_block},
                steps=steps,
                session_id="main",
                turn_workspace=runtime.execution_root,
            )
        )

    def test_tests_missing_requires_meaningful_test_artifact(self) -> None:
        runtime = self.runtime()
        message = "Pythonで normalize_name(text) を実装し、tests/ にunittestを追加して検証してください。"
        impl = "def normalize_name(text):\n    return ' '.join(str(text).split()).title()\n"
        steps = [tool_step("write_file", "name_tools.py", content=impl)]

        state = runtime._implementation_task_progress_state(user_message=message, steps=steps)
        self.assertEqual(state["phase"], "tests_missing")
        self.assertEqual(state["allowed_next_actions"], ["write_file tests/test_*.py"])

        blocked = runtime._implementation_task_phase_action_block(
            user_message=message,
            tool_name="run_command",
            tool_args={"command": "python3 -m unittest discover -s tests"},
            steps=steps,
            session_id="main",
            turn_workspace=runtime.execution_root,
        )
        self.assertEqual(blocked["reason_code"], "implementation_task_phase_requires_tests")

    def test_unittest_not_run_blocks_finish_and_allows_unittest(self) -> None:
        runtime = self.runtime()
        message = "Pythonで normalize_name(text) を実装し、tests/ にunittestを追加して検証してください。"
        impl = "def normalize_name(text):\n    return ' '.join(str(text).split()).title()\n"
        test = (
            "import unittest\nfrom name_tools import normalize_name\n\n"
            "class TestNameTools(unittest.TestCase):\n"
            "    def test_normalize(self):\n"
            "        self.assertEqual(normalize_name(' ada   lovelace '), 'Ada Lovelace')\n"
        )
        steps = [
            tool_step("write_file", "name_tools.py", content=impl),
            tool_step("write_file", "tests/test_name_tools.py", content=test),
        ]

        state = runtime._implementation_task_progress_state(user_message=message, steps=steps)
        self.assertEqual(state["phase"], "unittest_not_run")
        blocked = runtime._implementation_task_phase_action_block(
            user_message=message,
            tool_name="finish",
            tool_args={"final_answer": "done"},
            steps=steps,
            session_id="main",
            turn_workspace=runtime.execution_root,
        )
        self.assertEqual(blocked["reason_code"], "implementation_task_phase_requires_unittest")

    def test_replace_text_no_match_gets_one_recovery_read_then_edit(self) -> None:
        runtime = self.runtime()
        message = "Pythonで required_api(text) を実装し、tests/ にunittestを追加して検証してください。"
        impl = "def other_api(text):\n    return text\n"
        steps = [
            tool_step("write_file", "tool.py", content=impl),
            {
                "tool_name": "replace_text",
                "tool_args": {"path": "tool.py", "old_text": "missing", "new_text": impl},
                "tool_result": {"ok": False, "path": "tool.py", "failure_type": "replace_text_no_match"},
            },
        ]
        state = runtime._implementation_task_progress_state(user_message=message, steps=steps)
        self.assertEqual(state["phase"], "implementation_present_needs_semantic_review")
        self.assertEqual(state["allowed_next_actions"], ["read_file tool.py once"])

        read_steps = [*steps, tool_step("read_file", "tool.py", content=impl)]
        blocked = runtime._implementation_task_phase_action_block(
            user_message=message,
            tool_name="read_file",
            tool_args={"path": "tool.py"},
            steps=read_steps,
            session_id="main",
            turn_workspace=runtime.execution_root,
        )
        self.assertEqual(blocked["reason_code"], "implementation_task_phase_requires_targeted_replace_after_no_match")
        self.assertEqual(
            blocked["allowed_next_actions"],
            ["replace_text tool.py", "write_file tool.py"],
        )

    def test_large_replace_text_no_match_recovery_requires_write_file_without_prompt_conflict(self) -> None:
        runtime = self.runtime()
        message = (
            "Pythonで汎用mapping処理を実装してください。"
            "入力は item_id -> set(feature_id) の辞書形式です。"
            "select_item はIDを壊さず候補を返してください。"
            "tests/ にunittestを追加して検証してください。"
        )
        impl = (
            "from typing import Dict, Set, List\n\n"
            "def select_item(rows: Dict[int, Set[int]]) -> List[int]:\n"
            "    return list(range(len(rows)))\n"
        )
        large_old = "missing line\n" * 90
        large_new = "def select_item(rows):\n    return list(rows)\n" + ("# rewritten\n" * 90)
        steps = [
            tool_step("write_file", "picker.py", content=impl),
            {
                "tool_name": "replace_text",
                "tool_args": {"path": "picker.py", "old_text": large_old, "new_text": large_new},
                "tool_result": {"ok": False, "path": "picker.py", "failure_type": "replace_text_no_match"},
            },
            tool_step("read_file", "picker.py", content=impl),
        ]

        state = runtime._implementation_task_progress_state(user_message=message, steps=steps)
        self.assertEqual(state["phase"], "implementation_present_needs_semantic_review")
        self.assertEqual(state["allowed_next_actions"], ["write_file picker.py"])
        prompt = runtime._implementation_task_progress_prompt(state)
        self.assertIn("replace_text no_match後の修復契約", prompt)
        self.assertIn("allowed_next_actions は write_file", prompt)
        self.assertNotIn("小さい replace_text を優先", prompt)

        blocked = runtime._implementation_task_phase_action_block(
            user_message=message,
            tool_name="replace_text",
            tool_args={"path": "picker.py", "old_text": large_old, "new_text": large_new},
            steps=steps,
            session_id="main",
            turn_workspace=runtime.execution_root,
        )
        self.assertEqual(
            blocked["reason_code"],
            "implementation_task_phase_blocks_repeated_or_large_replace_after_no_match",
        )
        self.assertEqual(blocked["allowed_next_actions"], ["write_file picker.py"])
        self.assertIn("old_text 不一致", blocked["message"])

    def test_recursive_backtracking_symmetric_state_is_not_source_contract_issue(self) -> None:
        runtime = self.runtime()
        source = (
            "def choose_items(rows):\n"
            "    remaining = set(rows)\n"
            "    path = []\n"
            "    def search(remaining, path):\n"
            "        if not remaining:\n"
            "            return list(path)\n"
            "        item = next(iter(remaining))\n"
            "        path.append(item)\n"
            "        remaining.remove(item)\n"
            "        result = search(remaining, path)\n"
            "        if result is not None:\n"
            "            return result\n"
            "        path.pop()\n"
            "        remaining.add(item)\n"
            "        return None\n"
            "    return search(remaining, path)\n"
        )

        self.assertEqual(runtime._python_source_has_recursive_destructive_shared_state(source), "")
        state = runtime._implementation_task_progress_state(
            user_message="Pythonで choose_items(rows) を実装し、tests/ にunittestを追加して検証してください。",
            steps=[tool_step("write_file", "chooser.py", content=source)],
        )
        self.assertEqual(state["phase"], "tests_missing")

    def test_recursive_backtracking_snapshot_restore_is_not_source_contract_issue(self) -> None:
        runtime = self.runtime()
        source = (
            "def choose_items(rows):\n"
            "    remaining = set(rows)\n"
            "    def search(remaining):\n"
            "        if not remaining:\n"
            "            return []\n"
            "        item = next(iter(remaining))\n"
            "        old_remaining = set(remaining)\n"
            "        remaining.remove(item)\n"
            "        result = search(remaining)\n"
            "        remaining.clear()\n"
            "        remaining.update(old_remaining)\n"
            "        return [item] + result\n"
            "    return search(remaining)\n"
        )

        self.assertEqual(runtime._python_source_has_recursive_destructive_shared_state(source), "")

    def test_recursive_backtracking_high_risk_destructive_state_is_source_contract_issue(self) -> None:
        runtime = self.runtime()
        source = (
            "def choose_items(rows):\n"
            "    remaining = set(rows)\n"
            "    def search(remaining):\n"
            "        if not remaining:\n"
            "            return []\n"
            "        remaining.difference_update({next(iter(remaining))})\n"
            "        return search(remaining)\n"
            "    return search(remaining)\n"
        )

        issue = runtime._python_source_has_recursive_destructive_shared_state(source)
        self.assertIn("recursive/backtracking実装", issue)
        self.assertIn("remaining.difference_update", issue)

    def test_structural_semantic_issue_gives_line_hints_and_blocks_broad_replace(self) -> None:
        runtime = self.runtime()
        message = "Pythonで choose_items(rows) を実装し、tests/ にunittestを追加して検証してください。"
        source = (
            "def choose_items(rows):\n"
            "    remaining = {row: {row} for row in rows}\n"
            "    solution = []\n"
            "    def search(remaining, solution):\n"
            "        if not any(remaining[row] for row in remaining):\n"
            "            return True\n"
            "        chosen = next(iter(remaining))\n"
            "        solution.append(chosen)\n"
            "        del remaining[chosen]\n"
            "        if search(remaining, solution):\n"
            "            return True\n"
            "        solution.pop()\n"
            "        remaining[chosen] = {chosen}\n"
            "        return False\n"
            "    return solution if search(remaining, solution) else None\n"
        )
        large_source = source + "\n" + "\n".join(f"# filler {index}" for index in range(160)) + "\n"
        steps = [
            tool_step("write_file", "chooser.py", content=large_source),
            tool_step("read_file", "chooser.py", content=large_source),
        ]

        state = runtime._implementation_task_progress_state(user_message=message, steps=steps)
        self.assertEqual(state["phase"], "implementation_present_needs_semantic_review")
        self.assertEqual(state["allowed_next_actions"], ["write_file chooser.py"])
        hints = state["implementation_source_repair_hints"]
        self.assertTrue(any(hint.get("suggested_action") == "rewrite_recursive_branch_local_state" for hint in hints))
        self.assertTrue(any("del remaining[chosen]" in hint.get("current_text", "") for hint in hints))

        (runtime.execution_root / "chooser.py").write_text(large_source, encoding="utf-8")
        blocked = runtime._implementation_task_phase_action_block(
            user_message=message,
            tool_name="replace_text",
            tool_args={
                "path": "chooser.py",
                "old_text": large_source,
                "new_text": large_source.replace("del remaining[chosen]", "next_remaining = dict(remaining)", 1),
            },
            steps=steps,
            session_id="main",
            turn_workspace=runtime.execution_root,
        )
        self.assertEqual(blocked["reason_code"], "implementation_task_phase_blocks_broad_replace_text")
        self.assertEqual(blocked["allowed_next_actions"], ["write_file chooser.py"])

    def test_nonreducing_write_block_uses_write_guidance_when_write_is_allowed(self) -> None:
        runtime = self.runtime()
        message = "Pythonで choose_items(rows) を実装し、tests/ にunittestを追加して検証してください。"
        source = (
            "def choose_items(rows):\n"
            "    remaining = {row: {row} for row in rows}\n"
            "    solution = []\n"
            "    def search(remaining, solution):\n"
            "        if not any(remaining[row] for row in remaining):\n"
            "            return True\n"
            "        chosen = next(iter(remaining))\n"
            "        solution.append(chosen)\n"
            "        del remaining[chosen]\n"
            "        if search(remaining, solution):\n"
            "            return True\n"
            "        solution.pop()\n"
            "        remaining[chosen] = {chosen}\n"
            "        return False\n"
            "    return solution if search(remaining, solution) else None\n"
        )
        steps = [
            tool_step("write_file", "chooser.py", content=source),
            tool_step("read_file", "chooser.py", content=source),
        ]
        (runtime.execution_root / "chooser.py").write_text(source, encoding="utf-8")

        blocked = runtime._implementation_task_phase_action_block(
            user_message=message,
            tool_name="write_file",
            tool_args={"path": "chooser.py", "content": source + "\n# still same defect\n"},
            steps=steps,
            session_id="main",
            turn_workspace=runtime.execution_root,
        )

        self.assertEqual(blocked["reason_code"], "implementation_task_phase_requires_contract_reducing_edit")
        self.assertEqual(blocked["allowed_next_actions"], ["write_file chooser.py"])
        self.assertIn("allowed_next_actions は write_file", blocked["suggested_fix"])
        self.assertNotIn("old_text/new_text", blocked["suggested_fix"])

    def test_failed_unittest_reads_test_and_implementation_once_then_requires_edit(self) -> None:
        runtime = self.runtime()
        message = "Pythonで add_one(value) を実装し、tests/ にunittestを追加して検証してください。"
        impl = "def add_one(value):\n    return value\n"
        test = (
            "import unittest\nfrom math_tools import add_one\n\n"
            "class TestMathTools(unittest.TestCase):\n"
            "    def test_add_one(self):\n"
            "        self.assertEqual(add_one(1), 2)\n"
        )
        stderr = (
            "FAIL: test_add_one (test_math_tools.TestMathTools.test_add_one)\n"
            f"  File \"{runtime.execution_root / 'tests' / 'test_math_tools.py'}\", line 5, in test_add_one\n"
            "AssertionError: 1 != 2\n"
        )
        steps = [
            tool_step("write_file", "math_tools.py", content=impl),
            tool_step("write_file", "tests/test_math_tools.py", content=test),
            run_step("python3 -m unittest discover -s tests", ok=False, stderr=stderr),
        ]
        state = runtime._implementation_task_progress_state(
            user_message=message,
            steps=steps,
            turn_workspace=runtime.execution_root,
        )
        self.assertEqual(state["phase"], "unittest_failed_needs_fix")
        self.assertIn("tests/test_math_tools.py", state["failed_unittest_recovery_read_paths"])
        self.assertIn("math_tools.py", state["failed_unittest_recovery_read_paths"])
        self.assertEqual(
            state["allowed_next_actions"],
            ["read_file tests/test_math_tools.py once", "read_file math_tools.py once"],
        )

        steps_after_reads = [
            *steps,
            tool_step("read_file", "tests/test_math_tools.py", content=test),
            tool_step("read_file", "math_tools.py", content=impl),
        ]
        state_after_reads = runtime._implementation_task_progress_state(
            user_message=message,
            steps=steps_after_reads,
            turn_workspace=runtime.execution_root,
        )
        self.assertEqual(
            state_after_reads["allowed_next_actions"],
            [
                "replace_text tests/test_math_tools.py with a small unique old_text",
                "replace_text math_tools.py with a small unique old_text",
                "write_file tests/test_math_tools.py",
                "write_file math_tools.py",
            ],
        )
        blocked = runtime._implementation_task_phase_action_block(
            user_message=message,
            tool_name="read_file",
            tool_args={"path": "math_tools.py"},
            steps=steps_after_reads,
            session_id="main",
            turn_workspace=runtime.execution_root,
        )
        self.assertEqual(blocked["reason_code"], "implementation_task_failed_unittest_read_already_consumed")

        fixed = "def add_one(value):\n    return value + 1\n"
        steps_after_edit = [*steps_after_reads, tool_step("write_file", "math_tools.py", content=fixed)]
        state_after_edit = runtime._implementation_task_progress_state(
            user_message=message,
            steps=steps_after_edit,
            turn_workspace=runtime.execution_root,
        )
        self.assertEqual(state_after_edit["allowed_next_actions"], ["run_command python3 -m unittest discover -s tests"])

    def test_repeated_same_unittest_failure_after_edit_requires_edit_not_reread(self) -> None:
        runtime = self.runtime()
        message = "Pythonで add_one(value) を実装し、tests/ にunittestを追加して検証してください。"
        impl = "def add_one(value):\n    return value\n"
        test = (
            "import unittest\nfrom math_tools import add_one\n\n"
            "class TestMathTools(unittest.TestCase):\n"
            "    def test_add_one(self):\n"
            "        self.assertEqual(add_one(1), 2)\n"
        )
        stderr = (
            "FAIL: test_add_one (test_math_tools.TestMathTools.test_add_one)\n"
            f"  File \"{runtime.execution_root / 'tests' / 'test_math_tools.py'}\", line 5, in test_add_one\n"
            "AssertionError: 1 != 2\n"
        )
        steps = [
            tool_step("write_file", "math_tools.py", content=impl),
            tool_step("write_file", "tests/test_math_tools.py", content=test),
            run_step("python3 -m unittest discover -s tests", ok=False, stderr=stderr),
            tool_step("read_file", "tests/test_math_tools.py", content=test),
            tool_step("read_file", "math_tools.py", content=impl),
            tool_step("replace_text", "math_tools.py", content=impl),
            run_step("python3 -m unittest discover -s tests", ok=False, stderr=stderr),
        ]

        state = runtime._implementation_task_progress_state(
            user_message=message,
            steps=steps,
            turn_workspace=runtime.execution_root,
        )
        self.assertTrue(state["repeated_unittest_failure_signature"])
        self.assertEqual(state["same_signature_nonreducing_edit_paths"], ["math_tools.py"])
        self.assertEqual(
            state["same_signature_read_paths"],
            ["math_tools.py", "tests/test_math_tools.py"],
        )
        self.assertRegex(state["latest_unittest_failure_signature"], r"^[0-9a-f]{16}$")
        self.assertEqual(
            state["allowed_next_actions"],
            [
                "replace_text tests/test_math_tools.py with a small unique old_text",
                "replace_text math_tools.py with a small unique old_text",
                "write_file tests/test_math_tools.py",
                "write_file math_tools.py",
            ],
        )

        blocked_read = runtime._implementation_task_phase_action_block(
            user_message=message,
            tool_name="read_file",
            tool_args={"path": "math_tools.py"},
            steps=steps,
            session_id="main",
            turn_workspace=runtime.execution_root,
        )
        self.assertEqual(blocked_read["reason_code"], "implementation_task_failed_unittest_read_already_consumed")

        prompt = runtime._implementation_task_progress_prompt(state)
        self.assertIn("同一unittest failure signatureの非進捗", prompt)
        self.assertIn("直前の編集は失敗を減らしていません", prompt)
        self.assertIn("latest_unittest_failure_signature", prompt)
        self.assertIn("math_tools.py", prompt)

    def test_repeated_same_unittest_failure_blocks_noop_write(self) -> None:
        runtime = self.runtime()
        message = "Pythonで add_one(value) を実装し、tests/ にunittestを追加して検証してください。"
        impl = "def add_one(value):\n    return value\n"
        test = (
            "import unittest\nfrom math_tools import add_one\n\n"
            "class TestMathTools(unittest.TestCase):\n"
            "    def test_add_one(self):\n"
            "        self.assertEqual(add_one(1), 2)\n"
        )
        stderr = (
            "FAIL: test_add_one (test_math_tools.TestMathTools.test_add_one)\n"
            f"  File \"{runtime.execution_root / 'tests' / 'test_math_tools.py'}\", line 5, in test_add_one\n"
            "AssertionError: 1 != 2\n"
        )
        steps = [
            tool_step("write_file", "math_tools.py", content=impl),
            tool_step("write_file", "tests/test_math_tools.py", content=test),
            run_step("python3 -m unittest discover -s tests", ok=False, stderr=stderr),
            tool_step("read_file", "tests/test_math_tools.py", content=test),
            tool_step("read_file", "math_tools.py", content=impl),
            tool_step("replace_text", "math_tools.py", content=impl),
            run_step("python3 -m unittest discover -s tests", ok=False, stderr=stderr),
        ]

        blocked = runtime._implementation_task_phase_action_block(
            user_message=message,
            tool_name="write_file",
            tool_args={"path": "math_tools.py", "content": impl},
            steps=steps,
            session_id="main",
            turn_workspace=runtime.execution_root,
        )

        self.assertEqual(blocked["reason_code"], "implementation_task_failed_unittest_blocks_noop_write")
        self.assertIn("failure signature", blocked["message"])
        self.assertRegex(blocked["latest_unittest_failure_signature"], r"^[0-9a-f]{16}$")
        self.assertEqual(blocked["nonreducing_edit_paths"], ["math_tools.py"])
        self.assertEqual(
            blocked["allowed_next_actions"],
            [
                "replace_text tests/test_math_tools.py with a small unique old_text",
                "replace_text math_tools.py with a small unique old_text",
                "write_file tests/test_math_tools.py",
                "write_file math_tools.py",
            ],
        )

        changed = runtime._implementation_task_phase_action_block(
            user_message=message,
            tool_name="write_file",
            tool_args={"path": "math_tools.py", "content": "def add_one(value):\n    return value + 1\n"},
            steps=steps,
            session_id="main",
            turn_workspace=runtime.execution_root,
        )
        self.assertIsNone(changed)

    def test_failed_unittest_blocks_initial_noop_write_after_reads(self) -> None:
        runtime = self.runtime()
        message = "Pythonで add_one(value) を実装し、tests/ にunittestを追加して検証してください。"
        impl = "def add_one(value):\n    return value\n"
        test = (
            "import unittest\nfrom math_tools import add_one\n\n"
            "class TestMathTools(unittest.TestCase):\n"
            "    def test_add_one(self):\n"
            "        self.assertEqual(add_one(1), 2)\n"
        )
        stderr = (
            "FAIL: test_add_one (test_math_tools.TestMathTools.test_add_one)\n"
            f"  File \"{runtime.execution_root / 'tests' / 'test_math_tools.py'}\", line 5, in test_add_one\n"
            "AssertionError: 1 != 2\n"
        )
        steps = [
            tool_step("write_file", "math_tools.py", content=impl),
            tool_step("write_file", "tests/test_math_tools.py", content=test),
            run_step("python3 -m unittest discover -s tests", ok=False, stderr=stderr),
            tool_step("read_file", "tests/test_math_tools.py", content=test),
            tool_step("read_file", "math_tools.py", content=impl),
        ]
        (runtime.execution_root / "math_tools.py").write_text(impl, encoding="utf-8")

        blocked = runtime._implementation_task_phase_action_block(
            user_message=message,
            tool_name="write_file",
            tool_args={"path": "math_tools.py", "content": impl},
            steps=steps,
            session_id="main",
            turn_workspace=runtime.execution_root,
        )

        self.assertEqual(blocked["reason_code"], "implementation_task_failed_unittest_blocks_nonreducing_write")
        self.assertEqual(blocked["nonreducing_reason"], "identical_current_source")
        self.assertIn("失敗signatureを変える可能性が低い", blocked["message"])

    def test_repeated_unittest_blocks_comment_only_write(self) -> None:
        runtime = self.runtime()
        message = "Pythonで add_one(value) を実装し、tests/ にunittestを追加して検証してください。"
        impl = "def add_one(value):\n    return value\n"
        test = (
            "import unittest\nfrom math_tools import add_one\n\n"
            "class TestMathTools(unittest.TestCase):\n"
            "    def test_add_one(self):\n"
            "        self.assertEqual(add_one(1), 2)\n"
        )
        stderr = (
            "FAIL: test_add_one (test_math_tools.TestMathTools.test_add_one)\n"
            f"  File \"{runtime.execution_root / 'tests' / 'test_math_tools.py'}\", line 5, in test_add_one\n"
            "AssertionError: 1 != 2\n"
        )
        steps = [
            tool_step("write_file", "math_tools.py", content=impl),
            tool_step("write_file", "tests/test_math_tools.py", content=test),
            run_step("python3 -m unittest discover -s tests", ok=False, stderr=stderr),
            tool_step("read_file", "tests/test_math_tools.py", content=test),
            tool_step("read_file", "math_tools.py", content=impl),
            tool_step("write_file", "math_tools.py", content=impl),
            run_step("python3 -m unittest discover -s tests", ok=False, stderr=stderr),
        ]
        (runtime.execution_root / "math_tools.py").write_text(impl, encoding="utf-8")

        blocked = runtime._implementation_task_phase_action_block(
            user_message=message,
            tool_name="write_file",
            tool_args={"path": "math_tools.py", "content": impl + "# comment only\n"},
            steps=steps,
            session_id="main",
            turn_workspace=runtime.execution_root,
        )

        self.assertEqual(blocked["reason_code"], "implementation_task_failed_unittest_blocks_noop_write")
        self.assertEqual(blocked["nonreducing_reason"], "semantic_noop_ast")

    def test_failed_unittest_after_reads_allows_small_targeted_replace_text(self) -> None:
        runtime = self.runtime()
        message = "Pythonで add_one(value) を実装し、tests/ にunittestを追加して検証してください。"
        impl = "def add_one(value):\n    return value\n"
        test = (
            "import unittest\nfrom math_tools import add_one\n\n"
            "class TestMathTools(unittest.TestCase):\n"
            "    def test_add_one(self):\n"
            "        self.assertEqual(add_one(1), 2)\n"
        )
        stderr = (
            "FAIL: test_add_one (test_math_tools.TestMathTools.test_add_one)\n"
            f"  File \"{runtime.execution_root / 'tests' / 'test_math_tools.py'}\", line 5, in test_add_one\n"
            "AssertionError: 1 != 2\n"
        )
        steps = [
            tool_step("write_file", "math_tools.py", content=impl),
            tool_step("write_file", "tests/test_math_tools.py", content=test),
            run_step("python3 -m unittest discover -s tests", ok=False, stderr=stderr),
            tool_step("read_file", "tests/test_math_tools.py", content=test),
            tool_step("read_file", "math_tools.py", content=impl),
        ]
        (runtime.execution_root / "math_tools.py").write_text(impl, encoding="utf-8")

        allowed = runtime._implementation_task_phase_action_block(
            user_message=message,
            tool_name="replace_text",
            tool_args={
                "path": "math_tools.py",
                "old_text": "    return value\n",
                "new_text": "    return value + 1\n",
            },
            steps=steps,
            session_id="main",
            turn_workspace=runtime.execution_root,
        )
        self.assertIsNone(allowed)

    def test_failed_unittest_after_reads_blocks_unmatched_replace_text(self) -> None:
        runtime = self.runtime()
        message = "Pythonで add_one(value) を実装し、tests/ にunittestを追加して検証してください。"
        impl = "def add_one(value):\n    return value\n"
        test = (
            "import unittest\nfrom math_tools import add_one\n\n"
            "class TestMathTools(unittest.TestCase):\n"
            "    def test_add_one(self):\n"
            "        self.assertEqual(add_one(1), 2)\n"
        )
        stderr = (
            "FAIL: test_add_one (test_math_tools.TestMathTools.test_add_one)\n"
            f"  File \"{runtime.execution_root / 'tests' / 'test_math_tools.py'}\", line 5, in test_add_one\n"
            "AssertionError: 1 != 2\n"
        )
        steps = [
            tool_step("write_file", "math_tools.py", content=impl),
            tool_step("write_file", "tests/test_math_tools.py", content=test),
            run_step("python3 -m unittest discover -s tests", ok=False, stderr=stderr),
            tool_step("read_file", "tests/test_math_tools.py", content=test),
            tool_step("read_file", "math_tools.py", content=impl),
        ]
        (runtime.execution_root / "math_tools.py").write_text(impl, encoding="utf-8")

        blocked = runtime._implementation_task_phase_action_block(
            user_message=message,
            tool_name="replace_text",
            tool_args={
                "path": "math_tools.py",
                "old_text": "def add_one(value): return value",
                "new_text": "def add_one(value):\n    return value + 1\n",
            },
            steps=steps,
            session_id="main",
            turn_workspace=runtime.execution_root,
        )
        self.assertEqual(blocked["reason_code"], "implementation_task_failed_unittest_blocks_unmatched_replace_text")
        self.assertEqual(
            blocked["allowed_next_actions"],
            [
                "replace_text math_tools.py with a small unique old_text",
                "replace_text tests/test_math_tools.py with a small unique old_text",
                "write_file tests/test_math_tools.py",
                "write_file math_tools.py",
            ],
        )

    def test_failed_unittest_after_reads_blocks_broad_replace_text(self) -> None:
        runtime = self.runtime()
        message = "Pythonで add_one(value) を実装し、tests/ にunittestを追加して検証してください。"
        impl = (
            "def add_one(value):\n"
            "    return value\n"
            + "\n".join(f"# filler {index}" for index in range(180))
            + "\n"
        )
        test = (
            "import unittest\nfrom math_tools import add_one\n\n"
            "class TestMathTools(unittest.TestCase):\n"
            "    def test_add_one(self):\n"
            "        self.assertEqual(add_one(1), 2)\n"
        )
        stderr = (
            "FAIL: test_add_one (test_math_tools.TestMathTools.test_add_one)\n"
            f"  File \"{runtime.execution_root / 'tests' / 'test_math_tools.py'}\", line 5, in test_add_one\n"
            "AssertionError: 1 != 2\n"
        )
        steps = [
            tool_step("write_file", "math_tools.py", content=impl),
            tool_step("write_file", "tests/test_math_tools.py", content=test),
            run_step("python3 -m unittest discover -s tests", ok=False, stderr=stderr),
            tool_step("read_file", "tests/test_math_tools.py", content=test),
            tool_step("read_file", "math_tools.py", content=impl),
        ]
        (runtime.execution_root / "math_tools.py").write_text(impl, encoding="utf-8")

        blocked = runtime._implementation_task_phase_action_block(
            user_message=message,
            tool_name="replace_text",
            tool_args={
                "path": "math_tools.py",
                "old_text": impl,
                "new_text": impl.replace("    return value\n", "    return value + 1\n", 1),
            },
            steps=steps,
            session_id="main",
            turn_workspace=runtime.execution_root,
        )
        self.assertEqual(blocked["reason_code"], "implementation_task_failed_unittest_blocks_broad_replace_text")
        self.assertTrue(blocked["broad_rewrite"])

    def test_run_command_prompt_preserves_traceback_line_and_exception_tail(self) -> None:
        runtime = self.runtime()
        stderr = (
            "FAIL: test_big (tests.test_big.TestBig.test_big)\n"
            + "\n".join(f"noise line {index}" for index in range(120))
            + f"\n  File \"{runtime.execution_root / 'tests' / 'test_big.py'}\", line 999, in test_big\n"
            "    self.assertEqual(actual, expected)\n"
            "AssertionError: {'actual': 1} != {'expected': 2}\n"
        )
        event = {
            "type": "tool_result",
            "tool_name": "run_command",
            "content": json.dumps(
                {
                    "ok": False,
                    "tool": "run_command",
                    "command": "python3 -m unittest discover -s tests",
                    "returncode": 1,
                    "cwd": str(runtime.execution_root),
                    "stdout": "",
                    "stderr": stderr,
                },
                ensure_ascii=False,
            ),
        }

        rendered = runtime._render_tool_result_context(event)

        self.assertIn("returncode=1", rendered)
        self.assertIn("line 999", rendered)
        self.assertIn("AssertionError", rendered)
        self.assertIn("stderr_tail", rendered)

    def test_failed_unittest_with_traceback_does_not_block_on_consultant(self) -> None:
        runtime = self.runtime(responses=[])
        message = "Pythonで add_one(value) を実装し、tests/ にunittestを追加して検証してください。"
        stderr = (
            "FAIL: test_add_one (test_math_tools.TestMathTools.test_add_one)\n"
            f"  File \"{runtime.execution_root / 'tests' / 'test_math_tools.py'}\", line 5, in test_add_one\n"
            "AssertionError: 1 != 2\n"
        )
        note = runtime._validation_failure_consultant_note(
            user_message=message,
            tool_result={
                "ok": False,
                "command": "python3 -m unittest discover -s tests",
                "returncode": 1,
                "stderr": stderr,
                "stdout": "",
            },
            steps=[],
            turn_workspace=runtime.execution_root,
            current_model="test-model",
        )

        self.assertIsNone(note)
        self.assertEqual(runtime.llm_backend.messages_seen, [])

    def test_unittest_failed_semantic_review_does_not_delay_concrete_traceback_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = self.runtime(root, responses=[])
            message = "Pythonで add_one(value) を実装し、tests/ にunittestを追加して検証してください。"
            impl = "def add_one(value):\n    return value\n"
            test = (
                "import unittest\nfrom math_tools import add_one\n\n"
                "class TestMathTools(unittest.TestCase):\n"
                "    def test_add_one(self):\n"
                "        self.assertEqual(add_one(1), 2)\n"
            )
            (runtime.execution_root / "math_tools.py").write_text(impl, encoding="utf-8")
            (runtime.execution_root / "tests").mkdir(parents=True, exist_ok=True)
            (runtime.execution_root / "tests" / "test_math_tools.py").write_text(test, encoding="utf-8")
            steps = [
                tool_step("write_file", "math_tools.py", content=impl),
                tool_step("write_file", "tests/test_math_tools.py", content=test),
            ]
            failed_result = {
                "ok": False,
                "command": "python3 -m unittest discover -s tests",
                "returncode": 1,
                "stderr": (
                    "FAIL: test_add_one (test_math_tools.TestMathTools.test_add_one)\n"
                    f"  File \"{runtime.execution_root / 'tests' / 'test_math_tools.py'}\", line 5, in test_add_one\n"
                    "AssertionError: 1 != 2\n"
                ),
                "stdout": "",
            }

            appended = runtime._append_semantic_implementation_review_if_needed(
                session_id="main",
                turn_id="turn",
                queue_id="queue",
                step_index=3,
                turn_workspace=runtime.execution_root,
                user_message=message,
                steps=[*steps, {"tool_name": "run_command", "tool_args": {"command": failed_result["command"]}, "tool_result": failed_result}],
                current_model="test-model",
                trigger="unittest_failed",
                failed_tool_result=failed_result,
            )

            self.assertFalse(appended)
            self.assertEqual(runtime.llm_backend.messages_seen, [])

    def test_artifacts_ready_semantic_review_skips_consultant_when_no_runtime_issue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = self.runtime(root, responses=[])
            message = "Pythonで add_one(value) を実装し、tests/ にunittestを追加して検証してください。"
            impl = "def add_one(value):\n    return value + 1\n"
            test = (
                "import unittest\nfrom math_tools import add_one\n\n"
                "class TestMathTools(unittest.TestCase):\n"
                "    def test_add_one(self):\n"
                "        self.assertEqual(add_one(1), 2)\n"
            )
            (runtime.execution_root / "math_tools.py").write_text(impl, encoding="utf-8")
            (runtime.execution_root / "tests").mkdir(parents=True, exist_ok=True)
            (runtime.execution_root / "tests" / "test_math_tools.py").write_text(test, encoding="utf-8")
            steps = [
                tool_step("write_file", "math_tools.py", content=impl),
                tool_step("write_file", "tests/test_math_tools.py", content=test),
            ]

            appended = runtime._append_semantic_implementation_review_if_needed(
                session_id="main",
                turn_id="turn",
                queue_id="queue",
                step_index=2,
                turn_workspace=runtime.execution_root,
                user_message=message,
                steps=steps,
                current_model="test-model",
                trigger="artifacts_ready",
            )

            self.assertFalse(appended)
            self.assertEqual(runtime.llm_backend.messages_seen, [])

    def test_artifacts_ready_semantic_review_uses_runtime_observation_without_consultant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = self.runtime(root, responses=[])
            message = "Pythonで add_one(value) を実装し、tests/ にunittestを追加して検証してください。"
            impl = "def add_one(value):\n    return value + 1\n"
            test = (
                "import unittest\n\n"
                "class TestMathTools(unittest.TestCase):\n"
                "    def test_add_one_indirect(self):\n"
                "        self.assertEqual(2, 2)\n"
            )
            (runtime.execution_root / "math_tools.py").write_text(impl, encoding="utf-8")
            (runtime.execution_root / "tests").mkdir(parents=True, exist_ok=True)
            (runtime.execution_root / "tests" / "test_math_tools.py").write_text(test, encoding="utf-8")
            steps = [
                tool_step("write_file", "math_tools.py", content=impl),
                tool_step("write_file", "tests/test_math_tools.py", content=test),
            ]

            appended = runtime._append_semantic_implementation_review_if_needed(
                session_id="main",
                turn_id="turn",
                queue_id="queue",
                step_index=2,
                turn_workspace=runtime.execution_root,
                user_message=message,
                steps=steps,
                current_model="test-model",
                trigger="artifacts_ready",
            )
            events = read_jsonl(root / "state" / "sessions" / "main" / "events.jsonl")

            self.assertTrue(appended)
            self.assertEqual(runtime.llm_backend.messages_seen, [])
            self.assertEqual(events[-1]["code"], "semantic_implementation_review")
            self.assertEqual(events[-1]["reason_code"], "runtime_semantic_review_requires_revision")
            self.assertIn("runtime観測レビュー", events[-1]["content"])

    def test_non_command_tool_failure_prompt_preserves_recovery_contract(self) -> None:
        runtime = self.runtime()
        event = {
            "type": "tool_result",
            "tool_name": "replace_text",
            "content": json.dumps(
                {
                    "ok": False,
                    "tool": "replace_text",
                    "path": "math_tools.py",
                    "error": "old_text must match exactly once; matched 0 times",
                    "failure_type": "replace_text_no_match",
                    "blocked_by": "runtime_edit_validation",
                    "allowed_next_actions": [
                        {"tool": "read_file", "strategy": "inspect the current file"},
                        {"tool": "write_file", "strategy": "rewrite complete valid source"},
                    ],
                    "next_required_action": "read current source before retrying",
                },
                ensure_ascii=False,
            ),
        }

        rendered = runtime._render_tool_result_context(event)

        self.assertIn("failure_type=replace_text_no_match", rendered)
        self.assertIn("old_text must match exactly once", rendered)
        self.assertIn("allowed_next_actions", rendered)
        self.assertIn("next_required_action=read current source", rendered)

    def test_tool_validation_failures_include_block_owner_and_next_action(self) -> None:
        runtime = self.runtime()

        syntax_result = runtime.tools.execute(
            "write_file",
            {"path": "broken.py", "content": "def broken(:\n    pass\n"},
        )
        self.assertFalse(syntax_result["ok"])
        self.assertEqual(syntax_result["failure_type"], "validation_failed")
        self.assertEqual(syntax_result["blocked_by"], "runtime_python_syntax_validation")
        self.assertIn("next_required_action", syntax_result)

        large_result = runtime.tools.execute(
            "write_file",
            {"path": "notes.txt", "content": "x" * (runtime.tools.content_chunk_max_bytes * 2 + 1)},
        )
        self.assertFalse(large_result["ok"])
        self.assertEqual(large_result["failure_type"], "content_too_large")
        self.assertEqual(large_result["blocked_by"], "runtime_content_size_policy")
        self.assertIn("next_required_action", large_result)

        ok = runtime.tools.execute("write_file", {"path": "math_tools.py", "content": "def add_one(value):\n    return value\n"})
        self.assertTrue(ok["ok"])
        replace_result = runtime.tools.execute(
            "replace_text",
            {"path": "math_tools.py", "old_text": "return missing", "new_text": "return value + 1"},
        )
        self.assertFalse(replace_result["ok"])
        self.assertEqual(replace_result["failure_type"], "replace_text_no_match")
        self.assertEqual(replace_result["blocked_by"], "runtime_edit_validation")
        self.assertIn("next_required_action", replace_result)

    def test_progress_block_prompt_preserves_candidate_failure_evidence(self) -> None:
        runtime = self.runtime()
        event = {
            "type": "system_note",
            "role": "system",
            "content": "write_file がブロックされました",
            "code": "implementation_task_progress_blocked",
            "reason_code": "implementation_task_phase_requires_contract_reducing_edit",
            "details": {
                "reason_code": "implementation_task_phase_requires_contract_reducing_edit",
                "failure_type": "implementation_contract_nonreducing_edit_loop",
                "blocked_tool": "write_file",
                "path": "math_tools.py",
                "phase": "implementation_present_needs_semantic_review",
                "blocked_by": "implementation_task_progress_controller",
                "missing_requirements": ["required_api is missing"],
                "candidate_missing_requirements": ["required_api is still missing"],
                "repair_hints": [
                    {
                        "line": 3,
                        "current_text": "def other_api(value):",
                        "reason": "public API name does not match",
                        "suggested_new_text": "def required_api(value):",
                    }
                ],
                "allowed_next_actions": ["replace_text math_tools.py"],
                "suggested_fix": "Rename the API with a small exact replacement.",
            },
        }

        rendered = "\n".join(
            runtime._render_action_context_events(
                recent_events=[event],
                steps=[],
                user_message="Pythonで required_api を実装してください。",
            )
        )

        self.assertIn("reason_code: implementation_task_phase_requires_contract_reducing_edit", rendered)
        self.assertIn("failure_type: implementation_contract_nonreducing_edit_loop", rendered)
        self.assertIn("提案後も残る未達", rendered)
        self.assertIn("required_api is still missing", rendered)
        self.assertIn("修復ヒント", rendered)
        self.assertIn("def required_api(value):", rendered)

    def test_edit_block_prompt_preserves_machine_readable_reason(self) -> None:
        runtime = self.runtime()
        event = {
            "type": "system_note",
            "role": "system",
            "content": "replace_text がブロックされました",
            "code": "edit_blocked",
            "reason_code": "read_file_required_after_edit_failed",
            "details": {
                "reason_code": "read_file_required_after_edit_failed",
                "previous_failure_type": "replace_text_no_match",
                "blocked_tool": "replace_text",
                "path": "math_tools.py",
                "blocked_by": "runtime_edit_validation",
                "allowed_next_actions": ["read_file math_tools.py once"],
                "next_required_action": "read the current source once before retrying",
            },
        }

        rendered = "\n".join(
            runtime._render_action_context_events(
                recent_events=[event],
                steps=[],
                user_message="Pythonで required_api を実装してください。",
            )
        )

        self.assertIn("reason_code: read_file_required_after_edit_failed", rendered)
        self.assertIn("failure_type: replace_text_no_match", rendered)
        self.assertIn("blocked_tool: replace_text", rendered)
        self.assertIn("path: math_tools.py", rendered)
        self.assertIn("next_required_action: read the current source once before retrying", rendered)

    def test_generic_system_note_prompt_fallback_preserves_recovery_fields(self) -> None:
        runtime = self.runtime()
        event = {
            "type": "system_note",
            "role": "system",
            "content": "generic block",
            "code": "command_similarity_warning",
            "reason_code": "similar_recent_command",
            "details": {
                "failure_type": "same_signature_retry",
                "blocked_by": "runtime_generic_gate",
                "blocked_tool": "run_command",
                "path": "tests/test_math_tools.py",
                "missing_requirements": ["previous failure signature did not change"],
                "allowed_next_actions": ["replace_text tests/test_math_tools.py"],
                "suggested_fix": "Change the failing source before retrying.",
                "next_required_action": "edit before rerun",
            },
        }

        rendered = "\n".join(
            runtime._render_action_context_events(
                recent_events=[event],
                steps=[],
                user_message="Pythonで required_api を実装してください。",
            )
        )

        self.assertIn("reason_code: similar_recent_command", rendered)
        self.assertIn("failure_type: same_signature_retry", rendered)
        self.assertIn("blocked_by: runtime_generic_gate", rendered)
        self.assertIn("previous failure signature did not change", rendered)
        self.assertIn("next_required_action: edit before rerun", rendered)

    def test_no_match_recovery_nonreducing_write_keeps_actionable_retry_context(self) -> None:
        runtime = self.runtime()
        message = "Pythonで required_api(text) を実装し、tests/ にunittestを追加して検証してください。"
        impl = "def other_api(text):\n    return text\n"
        steps = [
            tool_step("write_file", "tool.py", content=impl),
            {
                "tool_name": "replace_text",
                "tool_args": {"path": "tool.py", "old_text": "missing", "new_text": impl},
                "tool_result": {"ok": False, "path": "tool.py", "failure_type": "replace_text_no_match"},
            },
            tool_step("read_file", "tool.py", content=impl),
        ]

        blocked = runtime._implementation_task_phase_action_block(
            user_message=message,
            tool_name="write_file",
            tool_args={"path": "tool.py", "content": impl},
            steps=steps,
            session_id="main",
            turn_workspace=runtime.execution_root,
        )

        self.assertIsNotNone(blocked)
        assert blocked is not None
        self.assertEqual(blocked["reason_code"], "implementation_task_phase_requires_contract_reducing_edit")
        self.assertFalse(blocked.get("terminal_failure"))
        self.assertTrue(blocked["missing_requirements"])
        self.assertTrue(blocked["candidate_missing_requirements"])
        self.assertIn("missing_requirementsを減らす編集", blocked["suggested_fix"])

    def test_unittest_failure_progress_prompt_uses_stdout_when_stderr_is_empty(self) -> None:
        runtime = self.runtime()
        message = "Pythonで add_one(value) を実装し、tests/ にunittestを追加して検証してください。"
        impl = "def add_one(value):\n    return value\n"
        test = "import unittest\nfrom math_tools import add_one\n"
        stdout = (
            "FAIL: test_add_one\n"
            "  File \"tests/test_math_tools.py\", line 5, in test_add_one\n"
            "AssertionError: 1 != 2\n"
        )
        steps = [
            tool_step("write_file", "math_tools.py", content=impl),
            tool_step("write_file", "tests/test_math_tools.py", content=test),
            run_step("python3 -m unittest discover -s tests", ok=False, stdout=stdout),
        ]

        state = runtime._implementation_task_progress_state(
            user_message=message,
            steps=steps,
            turn_workspace=runtime.execution_root,
        )
        prompt = runtime._implementation_task_progress_prompt(state)

        self.assertEqual(state["phase"], "unittest_failed_needs_fix")
        self.assertIn("tests/test_math_tools.py", state["failed_unittest_recovery_read_paths"])
        self.assertIn("math_tools.py", state["failed_unittest_recovery_read_paths"])
        self.assertIn("stdout/stderr excerpt", prompt)
        self.assertIn("AssertionError: 1 != 2", prompt)

    def test_llm_output_issue_prompt_preserves_schema_validation_errors(self) -> None:
        runtime = self.runtime()
        event = {
            "type": "system_note",
            "role": "system",
            "content": "LLM応答がツール呼び出しJSONとして解釈できませんでした: schema_validation_failed",
            "code": "llm_output_issue",
            "reason_code": "schema_validation_failed",
            "details": {
                "current_phase": "IMPLEMENTATION_TASK_PROGRESS:unittest_failed_needs_fix",
                "failure_type": "schema_validation_failed",
                "blocked_by": "runtime_tool_schema",
                "raw_output_is_machine_json": True,
                "schema_validation_ok": False,
                "schema_validation": {
                    "errors": [
                        "tool_name must be one of ['write_file', 'read_file']",
                        "tool_args.path is required",
                    ]
                },
                "allowed_tool_names": ["write_file", "read_file"],
                "allowed_next_actions": ["read_file math_tools.py once", "write_file math_tools.py"],
                "missing_requirements": ["valid_tool_json"],
                "combined_text": "{\"tool_name\":\"finish\"}",
                "suggested_fix": "schema_validation_errorsを満たすtool_name/tool_argsだけで返してください。",
                "next_required_action": "return a valid tool call",
            },
        }

        prompt = runtime._build_prompt(
            goal_text="",
            recent_events=[event],
            steps=[],
            user_message="Pythonで add_one(value) を実装してください。",
        )

        self.assertIn("parse_issue: schema_validation_failed", prompt)
        self.assertIn("schema_validation_errors", prompt)
        self.assertIn("tool_args.path is required", prompt)
        self.assertIn("allowed_tool_names", prompt)
        self.assertIn("allowed_next_actions", prompt)
        self.assertIn("blocked_by: runtime_tool_schema", prompt)
        self.assertIn("next_required_action: return a valid tool call", prompt)
        self.assertIn("raw_output_preview", prompt)

    def test_timeout_run_command_prompt_preserves_timeout_recovery_contract(self) -> None:
        runtime = self.runtime()
        event = {
            "type": "tool_result",
            "tool_name": "run_command",
            "content": json.dumps(
                {
                    "ok": False,
                    "tool": "run_command",
                    "command": "python3 -m unittest discover -s tests",
                    "returncode": None,
                    "stdout": "",
                    "stderr": "Timed out after 5s",
                    "failure_type": "command_timeout",
                    "blocked_by": "runtime_command_timeout",
                    "timeout_seconds": 5,
                    "allowed_next_actions": ["run_command with a narrower command"],
                    "suggested_fix": "Narrow the command before retrying.",
                    "next_required_action": "narrow the command or edit the target before rerun",
                },
                ensure_ascii=False,
            ),
        }

        rendered = "\n".join(
            runtime._render_action_context_events(
                recent_events=[event],
                steps=[],
                user_message="Pythonで add_one(value) を実装してください。",
            )
        )

        self.assertIn("failure_type=command_timeout", rendered)
        self.assertIn("blocked_by=runtime_command_timeout", rendered)
        self.assertIn("timeout_seconds=5", rendered)
        self.assertIn("next_required_action=narrow the command", rendered)

    def test_command_failed_system_note_preserves_stdout_stderr_and_line_evidence(self) -> None:
        runtime = self.runtime()
        event = {
            "type": "system_note",
            "role": "system",
            "content": "リカバリモード：直前のコマンド `python3 -m unittest discover -s tests` が失敗しました",
            "code": "command_failed",
            "reason_code": "recovery_guidance",
            "details": {
                "command": "python3 -m unittest discover -s tests",
                "returncode": 1,
                "failure_type": "command_failed",
                "blocked_by": "runtime_command_result",
                "traceback_summary": "traceback_file_lines=tests/test_math_tools.py:5 in test_add_one | last_exception_line=AssertionError: 1 != 2",
                "stdout_tail": "FAIL: test_add_one\n  File \"tests/test_math_tools.py\", line 5, in test_add_one\nAssertionError: 1 != 2\n",
                "stderr_tail": "",
                "allowed_next_actions": ["read_file tests/test_math_tools.py once", "write_file math_tools.py"],
                "suggested_fix": "stdout/stderrの具体行を根拠に修正してください。",
                "next_required_action": "inspect or edit the failing target before rerunning the command",
            },
        }

        prompt = runtime._build_prompt(
            goal_text="",
            recent_events=[event],
            steps=[],
            user_message="Pythonで add_one(value) を実装し、tests/ にunittestを追加して検証してください。",
        )

        self.assertIn("command: python3 -m unittest discover -s tests", prompt)
        self.assertIn("returncode: 1", prompt)
        self.assertIn("tests/test_math_tools.py:5", prompt)
        self.assertIn("stdout_tail", prompt)
        self.assertIn("AssertionError: 1 != 2", prompt)
        self.assertIn("next_required_action: inspect or edit", prompt)

    def test_repetitive_in_progress_write_stream_is_stopped_with_actionable_context(self) -> None:
        prefix = (
            '{"analysis":"","assistant_message":"","tool_name":"write_file",'
            '"tool_args":{"path":"tests/test_generated.py","content":"'
        )
        repeated_chunks = [
            f"    # repeated exploratory fixture {index % 3}: this line should not continue forever\\n"
            for index in range(60)
        ]
        backend = StreamingFakeBackend([prefix, *repeated_chunks])
        root = Path(tempfile.mkdtemp())
        bootstrap_workspace(root)
        runtime = AgentRuntime(root, llm_backend=backend)
        runtime.runtime_config["json_retry_limit"] = 0
        runtime.runtime_config["machine_control_repetition_min_chars"] = 1000
        runtime.runtime_config["machine_control_repetition_min_similar_lines"] = 6

        telemetry = runtime._chat_with_repair(
            role="coding",
            model="test-model",
            prompt="Pythonで add_one(value) を実装し、tests/ にunittestを追加して検証してください。",
            session_id="main",
            turn_id="turn",
            queue_id="queue",
            step_index=1,
            llm_workspace=str(runtime.execution_root),
            current_phase="IMPLEMENTATION_TASK_PROGRESS:tests_missing",
            suppress_frame_operations=True,
            allowed_tool_names=["write_file"],
        )

        self.assertEqual(telemetry["parse_issue"], "repetitive_output")
        metadata = telemetry["stream_metadata"]
        self.assertEqual(metadata["client_abort_reason"], "repetitive_output")
        self.assertGreater(metadata["accumulated_content_chars"], 400)

        issue_event = {
            "type": "system_note",
            "role": "system",
            "content": "LLM応答がツール呼び出しJSONとして解釈できませんでした: repetitive_output",
            "code": "llm_output_issue",
            "reason_code": "repetitive_output",
            "details": {
                "current_phase": "IMPLEMENTATION_TASK_PROGRESS:tests_missing",
                "combined_text": telemetry["combined_text"],
                "stream_metadata": metadata,
                "allowed_tool_names": ["write_file"],
                "schema_validation": telemetry["schema_validation"],
                "schema_validation_ok": telemetry["schema_validation_ok"],
            },
        }
        prompt = runtime._build_prompt(
            goal_text="",
            recent_events=[issue_event],
            steps=[],
            user_message="Pythonで add_one(value) を実装し、tests/ にunittestを追加して検証してください。",
        )
        self.assertIn("parse_issue: repetitive_output", prompt)
        self.assertIn("stream_abort_reason: repetitive_output", prompt)
        self.assertIn("accumulated_content_chars", prompt)
        self.assertIn("allowed_tool_names", prompt)

    def test_stream_char_limit_exits_same_call_repair_loop(self) -> None:
        prefix = (
            '{"analysis":"","assistant_message":"","tool_name":"write_file",'
            '"tool_args":{"path":"generated.py","content":"'
        )
        chunks = [prefix, *[f"def generated_{index}():\\n    return {index}\\n" for index in range(80)]]
        backend = StreamingFakeBackend(chunks)
        root = Path(tempfile.mkdtemp())
        bootstrap_workspace(root)
        runtime = AgentRuntime(root, llm_backend=backend)
        runtime.runtime_config["json_retry_limit"] = 2
        runtime.runtime_config["max_machine_control_stream_chars"] = 600
        runtime.runtime_config["implementation_task_machine_control_stream_chars"] = 600

        telemetry = runtime._chat_with_repair(
            role="coding",
            model="test-model",
            prompt="Pythonで add_one(value) を実装し、tests/ にunittestを追加して検証してください。",
            session_id="main",
            turn_id="turn",
            queue_id="queue",
            step_index=1,
            llm_workspace=str(runtime.execution_root),
            current_phase="IMPLEMENTATION_TASK_PROGRESS:implementation_missing",
            suppress_frame_operations=True,
            allowed_tool_names=["write_file"],
        )

        self.assertEqual(telemetry["parse_issue"], "stream_char_limit")
        self.assertEqual(len(backend.messages_seen), 1)
        self.assertEqual(telemetry["stream_metadata"]["client_abort_reason"], "stream_char_limit")

    def test_stream_char_limit_records_actionable_llm_output_issue_without_crash(self) -> None:
        prefix = (
            '{"analysis":"","assistant_message":"","tool_name":"write_file",'
            '"tool_args":{"path":"generated.py","content":"'
        )
        chunks = [prefix, *[f"def generated_{index}():\\n    return {index}\\n" for index in range(80)]]
        backend = StreamingFakeBackend(chunks)
        root = Path(tempfile.mkdtemp())
        bootstrap_workspace(root)
        runtime = AgentRuntime(root, llm_backend=backend)
        runtime.runtime_config["json_retry_limit"] = 2
        runtime.runtime_config["max_machine_control_stream_chars"] = 600
        runtime.runtime_config["implementation_task_machine_control_stream_chars"] = 600
        runtime.config.setdefault("runtime", {})["max_steps_per_message"] = 1
        runtime.config.setdefault("runtime", {})["verified_implementation_max_steps"] = 1
        enqueue_message(root, "Pythonで add_one(value) を実装し、tests/ にunittestを追加して検証してください。")

        result = runtime.run_until_idle(
            max_work_items=1,
            selection_override={"role": "coding", "model": "test-model", "reason": "test"},
        )

        self.assertFalse(result["last_result"]["ok"])
        events = read_jsonl(runtime.paths.session_events_path("main"))
        issue_events = [
            event
            for event in events
            if event.get("type") == "system_note" and event.get("code") == "llm_output_issue"
        ]
        self.assertTrue(issue_events)
        details = issue_events[-1]["details"]
        self.assertEqual(details["failure_type"], "stream_char_limit")
        self.assertEqual(details["blocked_by"], "runtime_stream_guard")
        self.assertIn("smaller complete reference implementation", details["next_required_action"])

    def test_finish_block_prompt_preserves_missing_action_contract(self) -> None:
        runtime = self.runtime()
        event = {
            "type": "system_note",
            "role": "system",
            "content": "完了がブロックされました: 期待される成果物が見つかりません: math_tools.py",
            "code": "finish_blocked",
            "reason_code": "missing_expected_artifacts",
            "details": {
                "missing_artifacts": ["math_tools.py"],
                "missing_requirements": ["write_file math_tools.py"],
                "blocked_by": "finish_contract_expected_artifacts",
                "allowed_next_actions": ["write_file math_tools.py"],
                "suggested_fix": "期待される成果物を作成してください。",
                "next_required_action": "create the missing artifact: math_tools.py",
            },
        }

        prompt = runtime._build_prompt(
            goal_text="",
            recent_events=[event],
            steps=[],
            user_message="Pythonで add_one(value) を実装してください。",
        )

        self.assertIn("blocked_by: finish_contract_expected_artifacts", prompt)
        self.assertIn("write_file math_tools.py", prompt)
        self.assertIn("next_required_action: create the missing artifact", prompt)

    def test_recent_context_preserves_critical_llm_output_issue_outside_tail(self) -> None:
        runtime = self.runtime()
        runtime._append_session_event(
            "main",
            {
                "type": "system_note",
                "role": "system",
                "content": "LLM output invalid",
                "code": "llm_output_issue",
                "reason_code": "schema_validation_failed",
                "details": {"schema_validation": {"errors": ["bad enum"]}},
            },
        )
        for index in range(20):
            runtime._append_session_event(
                "main",
                {
                    "type": "planning_note",
                    "role": "system",
                    "content": f"ordinary note {index}",
                },
            )

        recent = runtime._recent_events_for_action_context(
            session_id="main",
            current_frame=None,
            limit=3,
        )

        self.assertTrue(any(event.get("code") == "llm_output_issue" for event in recent))

    def test_command_blocked_prompt_shows_allowed_action_and_block_owner(self) -> None:
        runtime = self.runtime()
        event = {
            "type": "system_note",
            "role": "system",
            "content": "run_command がブロックされました",
            "code": "command_blocked",
            "details": {
                "command": "python3 -m unittest discover -s tests",
                "blocked_tool": "run_command",
                "blocked_by": "runtime_command_gate",
                "allowed_next_actions": ["write_file math_tools.py"],
                "suggested_fix": "失敗原因を修正してから再実行してください。",
                "next_required_action": "write_file corrected implementation",
            },
        }

        prompt = runtime._build_prompt(
            goal_text="",
            recent_events=[event],
            steps=[],
            user_message="Pythonで add_one(value) を実装してください。",
        )

        self.assertIn("blocked_by: runtime_command_gate", prompt)
        self.assertIn("write_file math_tools.py", prompt)
        self.assertIn("write_file corrected implementation", prompt)

    def test_first_action_required_prompt_preserves_expected_tool_call(self) -> None:
        runtime = self.runtime()
        event = {
            "type": "system_note",
            "role": "system",
            "content": "子フレームは最初の具体ツール結果を得る前に別の行動を選べません。",
            "code": "first_action_required",
            "details": {
                "requested_tool": "finish",
                "expected_tool": "read_file",
                "expected_args": {"path": "math_tools.py"},
            },
        }

        prompt = runtime._build_prompt(
            goal_text="",
            recent_events=[event],
            steps=[],
            user_message="Pythonで add_one(value) を実装してください。",
        )

        self.assertIn("blocked_tool: finish", prompt)
        self.assertIn("read_file", prompt)
        self.assertIn("math_tools.py", prompt)

    def test_plan_acceptance_block_prompt_preserves_reviewer_reason_and_actions(self) -> None:
        runtime = self.runtime()
        event = {
            "type": "system_note",
            "role": "system",
            "content": "decompose_tasks was blocked by plan acceptance gate",
            "code": "plan_acceptance_blocked",
            "details": {
                "issues": ["child task does not advance the requested implementation"],
                "review": {"rationale": "The plan creates a static note instead of editing code."},
                "allowed_next_actions": [
                    {"tool": "decompose_tasks", "strategy": "retry with code-producing tasks"},
                    {"tool": "finish", "strategy": "ask user to clarify"},
                ],
                "suggested_fix": "Create child tasks whose first_action edits or verifies the requested code.",
            },
        }

        prompt = runtime._build_prompt(
            goal_text="",
            recent_events=[event],
            steps=[],
            user_message="Pythonで add_one(value) を実装してください。",
        )

        self.assertIn("does not advance", prompt)
        self.assertIn("static note", prompt)
        self.assertIn("retry with code-producing tasks", prompt)
        self.assertIn("first_action edits", prompt)

    def test_frame_and_plan_block_events_include_standard_recovery_contract(self) -> None:
        runtime = self.runtime()

        work_note = runtime._work_package_blocked_event(
            session_id="main",
            turn_id="turn",
            queue_id="queue",
            step_index=1,
            turn_workspace=runtime.execution_root,
            tool_name="decompose_tasks",
            issues=["task-1: first_action.tool is required"],
            tasks=[],
            user_message="Pythonで add_one(value) を実装してください。",
        )
        work_details = work_note["details"]
        self.assertEqual(work_details["blocked_by"], "work_package_contract")
        self.assertIn("failure_type", work_details)
        self.assertIn("allowed_next_actions", work_details)
        self.assertIn("suggested_fix", work_details)
        self.assertIn("next_required_action", work_details)

        plan_note = runtime._plan_acceptance_blocked_event(
            session_id="main",
            turn_id="turn",
            queue_id="queue",
            step_index=2,
            turn_workspace=runtime.execution_root,
            tool_name="decompose_tasks",
            issues=["plan does not advance the requested implementation"],
            review={
                "reason_code": "plan_semantic_mismatch",
                "rationale": "The plan creates notes instead of code.",
                "suggested_next_action": "retry with code-producing first_action",
            },
            tasks=[],
        )
        plan_details = plan_note["details"]
        self.assertEqual(plan_details["blocked_by"], "plan_acceptance_gate")
        self.assertEqual(plan_details["failure_type"], "plan_acceptance_blocked")
        self.assertIn("allowed_next_actions", plan_details)
        self.assertIn("suggested_fix", plan_details)
        self.assertIn("next_required_action", plan_details)

        empty_plan = runtime._handle_decompose_tasks(
            session_id="main",
            turn_id="turn",
            queue_id="queue",
            step_index=3,
            tool_args={"tasks": []},
            turn_workspace=runtime.execution_root,
        )
        empty_details = empty_plan["event"]["details"]
        self.assertEqual(empty_details["blocked_by"], "work_package_contract")
        self.assertEqual(empty_details["failure_type"], "empty_task_plan")
        self.assertIn("next_required_action", empty_details)

        root_return = runtime._handle_return_to_parent(
            session_id="main",
            turn_id="turn",
            queue_id="queue",
            step_index=4,
            tool_args={"summary": "done"},
            turn_workspace=runtime.execution_root,
        )
        return_details = root_return["event"]["details"]
        self.assertEqual(return_details["blocked_by"], "frame_contract")
        self.assertEqual(return_details["failure_type"], "root_frame_cannot_return")
        self.assertIn("allowed_next_actions", return_details)
        self.assertIn("next_required_action", return_details)

    def test_finish_acceptance_prompt_is_not_filtered_out(self) -> None:
        runtime = self.runtime()
        event = {
            "type": "system_note",
            "role": "system",
            "content": "完了受理判定: blocked",
            "code": "finish_acceptance",
            "details": {
                "status": "blocked",
                "reason": "unittest evidence is missing",
                "missing": ["unittest_run"],
                "suggested_fix": "run python3 -m unittest discover -s tests",
            },
        }

        prompt = runtime._build_prompt(
            goal_text="",
            recent_events=[event],
            steps=[],
            user_message="Pythonで add_one(value) を実装してください。",
        )

        self.assertIn("完了受理判定: blocked", prompt)
        self.assertIn("unittest evidence is missing", prompt)

    def test_run_command_multi_command_denial_is_structured_for_recovery(self) -> None:
        runtime = self.runtime()

        result = runtime.tools.execute("run_command", {"command": "echo one && echo two"})

        self.assertFalse(result["ok"])
        self.assertEqual(result["failure_type"], "multi_command_denied")
        self.assertEqual(result["blocked_by"], "tool_safety_policy")
        self.assertIn("allowed_next_actions", result)
        self.assertIn("next_required_action", result)

    def test_test_semantic_review_blocks_broad_replace_and_allows_write_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = self.runtime(root)
            message = "Pythonで add_one(value) を実装し、tests/ にunittestを追加して検証してください。"
            impl = "def add_one(value):\n    return value + 1\n"
            test = (
                "import unittest\nfrom math_tools import MathTools\n\n"
                "class TestMathTools(unittest.TestCase):\n"
                "    def test_class_only(self):\n"
                "        self.assertEqual(MathTools().add_one(1), 2)\n"
            )
            large_test = test + "\n" + "\n".join(f"# filler {index}" for index in range(180)) + "\n"
            (runtime.execution_root / "tests").mkdir(parents=True, exist_ok=True)
            (runtime.execution_root / "tests" / "test_math_tools.py").write_text(large_test, encoding="utf-8")
            steps = [
                tool_step("write_file", "math_tools.py", content=impl),
                tool_step("write_file", "tests/test_math_tools.py", content=large_test),
                tool_step("read_file", "tests/test_math_tools.py", content=large_test),
            ]
            runtime._append_session_event(
                "main",
                {
                    "type": "system_note",
                    "role": "system",
                    "content": "tests do not directly exercise requested top-level public API add_one",
                    "code": "semantic_implementation_review",
                    "reason_code": "runtime_semantic_review_requires_revision",
                    "details": {
                        "review": "tests do not directly exercise requested top-level public API add_one",
                        "review_source": "runtime",
                        "requires_revision": True,
                        "semantic_issues": ["tests do not directly exercise requested top-level public API add_one"],
                        "test_paths": ["tests/test_math_tools.py"],
                        "fingerprint": "test-api-coverage",
                    },
                    "step_index": 2,
                },
            )

            state = runtime._implementation_task_progress_state(
                user_message=message,
                steps=steps,
                session_id="main",
                turn_workspace=runtime.execution_root,
            )
            self.assertEqual(state["phase"], "tests_present_needs_semantic_review")
            prompt = runtime._implementation_task_progress_prompt(state)
            self.assertIn("テストファイル全体の巨大 replace_text は禁止", prompt)
            self.assertIn("このphaseでactionableな編集対象は tests/test_*.py だけです", prompt)
            self.assertIn("implementation editは次phaseで許可されるまで実行しない", prompt)

            blocked = runtime._implementation_task_phase_action_block(
                user_message=message,
                tool_name="replace_text",
                tool_args={
                    "path": "tests/test_math_tools.py",
                    "old_text": large_test,
                    "new_text": large_test.replace("MathTools().add_one(1)", "add_one(1)", 1),
                },
                steps=steps,
                session_id="main",
                turn_workspace=runtime.execution_root,
            )
            self.assertEqual(blocked["reason_code"], "implementation_task_test_semantic_blocks_broad_replace_text")
            self.assertEqual(
                blocked["allowed_next_actions"],
                [
                    "replace_text tests/test_math_tools.py with a small unique old_text",
                    "write_file tests/test_math_tools.py",
                ],
            )
            self.assertEqual(blocked["blocked_by"], "implementation_task_progress_controller")

    def test_failed_unittest_no_match_after_reads_requires_write_file(self) -> None:
        runtime = self.runtime()
        message = "Pythonで add_one(value) を実装し、tests/ にunittestを追加して検証してください。"
        impl = "def add_one(value):\n    return value\n"
        test = (
            "import unittest\nfrom math_tools import add_one\n\n"
            "class TestMathTools(unittest.TestCase):\n"
            "    def test_add_one(self):\n"
            "        self.assertEqual(add_one(1), 2)\n"
        )
        stderr = (
            "FAIL: test_add_one (test_math_tools.TestMathTools.test_add_one)\n"
            f"  File \"{runtime.execution_root / 'tests' / 'test_math_tools.py'}\", line 5, in test_add_one\n"
            "AssertionError: 1 != 2\n"
        )
        steps = [
            tool_step("write_file", "math_tools.py", content=impl),
            tool_step("write_file", "tests/test_math_tools.py", content=test),
            run_step("python3 -m unittest discover -s tests", ok=False, stderr=stderr),
            tool_step("read_file", "tests/test_math_tools.py", content=test),
            tool_step("read_file", "math_tools.py", content=impl),
            {
                "tool_name": "replace_text",
                "tool_args": {"path": "math_tools.py", "old_text": "def add_one(value): return value", "new_text": "def add_one(value):\n    return value + 1\n"},
                "tool_result": {"ok": False, "path": "math_tools.py", "failure_type": "replace_text_no_match"},
            },
        ]

        state = runtime._implementation_task_progress_state(
            user_message=message,
            steps=steps,
            turn_workspace=runtime.execution_root,
        )
        self.assertEqual(state["phase"], "unittest_failed_needs_fix")
        self.assertEqual(state["allowed_next_actions"], ["write_file math_tools.py"])
        self.assertEqual(state["failed_unittest_no_match_write_only_paths"], ["math_tools.py"])

        blocked = runtime._implementation_task_phase_action_block(
            user_message=message,
            tool_name="replace_text",
            tool_args={
                "path": "math_tools.py",
                "old_text": "    return value",
                "new_text": "    return value + 1",
            },
            steps=steps,
            session_id="main",
            turn_workspace=runtime.execution_root,
        )
        self.assertEqual(blocked["reason_code"], "implementation_task_failed_unittest_requires_write_after_no_match")

        blocked_read = runtime._implementation_task_phase_action_block(
            user_message=message,
            tool_name="read_file",
            tool_args={"path": "math_tools.py"},
            steps=steps,
            session_id="main",
            turn_workspace=runtime.execution_root,
        )
        self.assertEqual(blocked_read["reason_code"], "implementation_task_failed_unittest_requires_write_after_no_match")

        broad_write = runtime._implementation_task_phase_action_block(
            user_message=message,
            tool_name="write_file",
            tool_args={
                "path": "math_tools.py",
                "content": "def add_one(value):\n    return value + 1\n",
            },
            steps=steps,
            session_id="main",
            turn_workspace=runtime.execution_root,
        )
        self.assertIsNone(broad_write)

    def test_failed_unittest_blocks_unrelated_implementation_edit(self) -> None:
        runtime = self.runtime()
        message = "Pythonで add_one(value) を実装し、tests/ にunittestを追加して検証してください。"
        impl = "def add_one(value):\n    return value\n"
        test = (
            "import unittest\nfrom math_tools import add_one\n\n"
            "class TestMathTools(unittest.TestCase):\n"
            "    def test_add_one(self):\n"
            "        self.assertEqual(add_one(1), 2)\n"
        )
        stderr = (
            "FAIL: test_add_one (test_math_tools.TestMathTools.test_add_one)\n"
            f"  File \"{runtime.execution_root / 'tests' / 'test_math_tools.py'}\", line 5, in test_add_one\n"
            "AssertionError: 1 != 2\n"
        )
        steps = [
            tool_step("write_file", "math_tools.py", content=impl),
            tool_step("write_file", "tests/test_math_tools.py", content=test),
            run_step("python3 -m unittest discover -s tests", ok=False, stderr=stderr),
            tool_step("read_file", "tests/test_math_tools.py", content=test),
            tool_step("read_file", "math_tools.py", content=impl),
        ]

        blocked = runtime._implementation_task_phase_action_block(
            user_message=message,
            tool_name="replace_text",
            tool_args={
                "path": "other_tools.py",
                "old_text": "return value",
                "new_text": "return value + 1",
            },
            steps=steps,
            session_id="main",
            turn_workspace=runtime.execution_root,
        )
        self.assertEqual(blocked["reason_code"], "implementation_task_failed_unittest_requires_targeted_fix")

    def test_unittest_success_reaches_finish_phase(self) -> None:
        runtime = self.runtime()
        message = "Pythonで add_one(value) を実装し、tests/ にunittestを追加して検証してください。"
        impl = "def add_one(value):\n    return value + 1\n"
        test = (
            "import unittest\nfrom math_tools import add_one\n\n"
            "class TestMathTools(unittest.TestCase):\n"
            "    def test_add_one(self):\n"
            "        self.assertEqual(add_one(1), 2)\n"
        )
        steps = [
            tool_step("write_file", "math_tools.py", content=impl),
            tool_step("write_file", "tests/test_math_tools.py", content=test),
            run_step("python3 -m unittest discover -s tests", ok=True, stderr=".\nOK\n"),
        ]
        state = runtime._implementation_task_progress_state(user_message=message, steps=steps)
        self.assertEqual(state["phase"], "external_audit_required")
        self.assertEqual(state["allowed_next_actions"], ["run_command python3 -m unittest discover -s tests"])

        audited_steps = [
            *steps,
            run_step("python3 -m unittest discover -s tests", ok=True, stderr=".\nOK\n"),
        ]
        state = runtime._implementation_task_progress_state(user_message=message, steps=audited_steps)
        self.assertEqual(state["phase"], "external_contract_satisfied")
        self.assertEqual(state["allowed_next_actions"], ["finish"])

    def test_external_audit_required_blocks_finish_until_second_unittest(self) -> None:
        runtime = self.runtime()
        message = "Pythonで add_one(value) を実装し、tests/ にunittestを追加して検証してください。"
        steps = [
            tool_step("write_file", "math_tools.py", content="def add_one(value):\n    return value + 1\n"),
            tool_step(
                "write_file",
                "tests/test_math_tools.py",
                content=(
                    "import unittest\nfrom math_tools import add_one\n\n"
                    "class TestMathTools(unittest.TestCase):\n"
                    "    def test_add_one(self):\n"
                    "        self.assertEqual(add_one(1), 2)\n"
                ),
            ),
            run_step("python3 -m unittest discover -s tests", ok=True, stderr=".\nOK\n"),
        ]
        blocked = runtime._implementation_task_phase_action_block(
            user_message=message,
            tool_name="finish",
            tool_args={"final_answer": "done"},
            steps=steps,
            session_id="main",
            turn_workspace=runtime.execution_root,
        )
        self.assertEqual(blocked["reason_code"], "implementation_task_phase_requires_external_audit")

    def test_unittest_failure_after_previous_success_is_rendered_as_external_audit_failure(self) -> None:
        runtime = self.runtime()
        message = "Pythonで add_one(value) を実装し、tests/ にunittestを追加して検証してください。"
        impl = "def add_one(value):\n    return value + 1\n"
        test = (
            "import unittest\nfrom math_tools import add_one\n\n"
            "class TestMathTools(unittest.TestCase):\n"
            "    def test_add_one(self):\n"
            "        self.assertEqual(add_one(1), 2)\n"
        )
        stderr = (
            "FAIL: test_add_one (test_math_tools.TestMathTools.test_add_one)\n"
            "  File \"tests/test_math_tools.py\", line 5, in test_add_one\n"
            "AssertionError: 1 != 2\n"
        )
        steps = [
            tool_step("write_file", "math_tools.py", content=impl),
            tool_step("write_file", "tests/test_math_tools.py", content=test),
            run_step("python3 -m unittest discover -s tests", ok=True, stderr=".\nOK\n"),
            run_step("python3 -m unittest discover -s tests", ok=False, stderr=stderr),
        ]

        state = runtime._implementation_task_progress_state(
            user_message=message,
            steps=steps,
            turn_workspace=runtime.execution_root,
        )
        prompt = runtime._implementation_task_progress_prompt(state)

        self.assertEqual(state["phase"], "unittest_failed_needs_fix")
        self.assertEqual(state["latest_unittest_failure_type"], "external_audit_failed_after_previous_success")
        self.assertIn("外部audit/regression失敗", prompt)
        self.assertIn("previous_successful_unittest_run_count: 1", prompt)

    def test_consultant_advice_without_structured_issue_does_not_block_unittest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = self.runtime(root)
            message = "Pythonで add_one(value) を実装し、tests/ にunittestを追加して検証してください。"
            impl = "def add_one(value):\n    return value + 1\n"
            test = (
                "import unittest\nfrom math_tools import add_one\n\n"
                "class TestMathTools(unittest.TestCase):\n"
                "    def test_add_one(self):\n"
                "        self.assertEqual(add_one(1), 2)\n"
            )
            steps = [
                tool_step("write_file", "math_tools.py", content=impl),
                tool_step("write_file", "tests/test_math_tools.py", content=test),
            ]
            runtime._append_session_event(
                "main",
                {
                    "type": "system_note",
                    "role": "system",
                    "content": "相談役LLMからの実装レビュー: additional cases could be useful, but requested behavior is covered",
                    "code": "semantic_implementation_review",
                    "reason_code": "consultant_advice",
                    "details": {
                        "review": "additional cases could be useful, but requested behavior is covered",
                        "review_source": "consultant",
                        "requires_revision": True,
                        "semantic_issues": [],
                        "fingerprint": "advisory-only",
                    },
                    "step_index": 2,
                },
            )
            state = runtime._implementation_task_progress_state(
                user_message=message,
                steps=steps,
                session_id="main",
                turn_workspace=runtime.execution_root,
            )
            self.assertEqual(state["phase"], "unittest_not_run")

    def test_unknown_python_task_runs_through_generic_contract_to_controller_finish(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            impl = "def slugify(text):\n    return '-'.join(str(text).strip().lower().split())\n"
            test = (
                "import unittest\nfrom text_tools import slugify\n\n"
                "class TestTextTools(unittest.TestCase):\n"
                "    def test_slugify(self):\n"
                "        self.assertEqual(slugify(' Hello  P4 Runtime '), 'hello-p4-runtime')\n"
            )
            responses = [
                json.dumps({
                    "assistant_message": "write implementation",
                    "tool_name": "write_file",
                    "tool_args": {"path": "text_tools.py", "content": impl},
                }),
                json.dumps({
                    "assistant_message": "write tests",
                    "tool_name": "write_file",
                    "tool_args": {"path": "tests/test_text_tools.py", "content": test},
                }),
            ]
            runtime = self.runtime(root, responses)
            result = runtime.send_message(
                "Pythonで未知のslugify(text)を text_tools.py に実装し、tests/ にunittestを追加して検証してください。",
                run_immediately=True,
            )
            self.assertTrue(result["ok"])
            self.assertTrue((root / "workspaces").exists())
            events = read_jsonl(root / "state" / "sessions" / "main" / "events.jsonl")
            self.assertTrue(any(event.get("code") == "controller_finish" for event in events))
            self.assertTrue(any(event.get("type") == "finish" for event in events))
            unittest_results = [
                event
                for event in events
                if event.get("type") == "tool_result"
                and event.get("tool_name") == "run_command"
                and "unittest" in str(event.get("content") or "")
            ]
            self.assertEqual(len(unittest_results), 2)
            self.assertTrue(any(event.get("code") == "implementation_task_progress" for event in events))

    def test_repo_map_summarizes_workspace_symbols_for_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_workspace(root)
            (root / "name_tools.py").write_text(
                "import re\n\nclass Normalizer:\n    def clean(self, text):\n        return re.sub(r'\\s+', ' ', text).strip()\n",
                encoding="utf-8",
            )
            runtime = AgentRuntime(root, llm_backend=FakeBackend([]))
            state = runtime._implementation_task_progress_state(
                user_message="Pythonで normalize_name(text) を実装し、tests/ にunittestを追加して検証してください。",
                steps=[tool_step("write_file", "name_tools.py", content="def normalize_name(text):\n    return text\n")],
                turn_workspace=root,
            )
            self.assertIn("repo_map:", state["repo_map_excerpt"])
            self.assertIn("Normalizer.clean", state["repo_map_excerpt"])
            tool_result = runtime.tools.execute("repo_map", {"path": "."})
            self.assertTrue(tool_result["ok"])
            self.assertTrue(any(item.get("name") == "Normalizer.clean" for item in tool_result["repo_map"]["symbols"]))


if __name__ == "__main__":
    unittest.main()
