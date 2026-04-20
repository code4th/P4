from __future__ import annotations

import json
import os
import shutil
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib import error, request
from urllib.parse import urlparse


INITIAL_AGENT_INIT = '''"""Editable demo agent package for P2."""\n'''

INITIAL_GOAL_LOGIC = """from __future__ import annotations

import argparse
import json


AGENT_NAME = "P2自己改善エージェント"
STREAM_STYLE = "plain"
OPERATOR_GUIDANCE = [
    "観測しやすいこと",
    "失敗理由を残すこと",
]


def describe_agent() -> dict[str, object]:
    return {
        "agent_name": AGENT_NAME,
        "stream_style": STREAM_STYLE,
        "operator_guidance": list(OPERATOR_GUIDANCE),
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
    print(json.dumps(describe_agent(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
"""

INITIAL_TEST = """from __future__ import annotations

import subprocess
import unittest

from agent.goal_logic import describe_agent, render_operator_message, self_check


class SelfImprovementAgentTests(unittest.TestCase):
    def test_describe_agent_has_core_fields(self) -> None:
        payload = describe_agent()
        self.assertTrue(payload["agent_name"])
        self.assertIn(payload["stream_style"], {"plain", "structured", "rich"})
        self.assertGreaterEqual(len(payload["operator_guidance"]), 2)

    def test_operator_message_mentions_self_improvement(self) -> None:
        self.assertIn("自己改善", render_operator_message())

    def test_self_check_succeeds(self) -> None:
        self.assertEqual(self_check(), 0)

    def test_cli_check_mode_succeeds(self) -> None:
        proc = subprocess.run(
            ["python3", "agent/goal_logic.py", "--check"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
"""


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


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


def copytree_clean(source: Path, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))


def copytree_archive_best_effort(source: Path, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    try:
        shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
        return
    except shutil.Error as exc:
        filtered_errors: list[tuple[Any, ...]] = []
        for item in exc.args[0]:
            if len(item) < 3:
                filtered_errors.append(item)
                continue
            _, _, message = item
            if "No such file or directory" in str(message):
                continue
            filtered_errors.append(item)
        if filtered_errors:
            raise shutil.Error(filtered_errors) from exc
    if not target.exists():
        target.mkdir(parents=True, exist_ok=True)


def tail_text(path: Path, *, max_chars: int = 4000) -> str:
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def next_identifier(last_value: str | None, *, prefix: str) -> str:
    if last_value and last_value.startswith(prefix):
        try:
            number = int(last_value[len(prefix) :])
        except ValueError:
            number = 0
        return f"{prefix}{number + 1:04d}"
    return f"{prefix}0001"


@dataclass(slots=True)
class WorkspacePaths:
    root: Path

    def __post_init__(self) -> None:
        self.root = self.root.expanduser().resolve()

    @property
    def seed_initial_dir(self) -> Path:
        return self.root / "seed" / "initial"

    @property
    def seed_initial_version_dir(self) -> Path:
        return self.seed_initial_dir / "version"

    @property
    def seed_initial_state_dir(self) -> Path:
        return self.seed_initial_dir / "state"

    @property
    def runtime_versions_dir(self) -> Path:
        return self.root / "runtime" / "versions"

    @property
    def runtime_candidates_dir(self) -> Path:
        return self.root / "runtime" / "candidates"

    @property
    def state_dir(self) -> Path:
        return self.root / "state"

    @property
    def attempts_dir(self) -> Path:
        return self.state_dir / "attempts"

    @property
    def validations_dir(self) -> Path:
        return self.state_dir / "validations"

    @property
    def runtime_state_dir(self) -> Path:
        return self.state_dir / "runtime"

    @property
    def history_path(self) -> Path:
        return self.state_dir / "history.jsonl"

    @property
    def goal_path(self) -> Path:
        return self.state_dir / "goal.json"

    @property
    def self_model_path(self) -> Path:
        return self.state_dir / "self_model.json"

    @property
    def version_path(self) -> Path:
        return self.state_dir / "version.json"

    @property
    def system_skills_path(self) -> Path:
        return self.state_dir / "system_skills.json"

    @property
    def memos_path(self) -> Path:
        return self.state_dir / "memos.jsonl"

    @property
    def runtime_status_path(self) -> Path:
        return self.runtime_state_dir / "status.json"

    @property
    def queue_path(self) -> Path:
        return self.runtime_state_dir / "queue.jsonl"

    @property
    def archive_resets_dir(self) -> Path:
        return self.state_dir / "archive" / "resets"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def loop_log_path(self) -> Path:
        return self.logs_dir / "p2-loop.log"

    @property
    def dashboard_log_path(self) -> Path:
        return self.logs_dir / "dashboard.log"

    @property
    def validation_logs_dir(self) -> Path:
        return self.logs_dir / "validation"

    def attempt_report_path(self, candidate_id: str) -> Path:
        return self.attempts_dir / f"{candidate_id}.json"

    def raw_model_output_path(self, candidate_id: str) -> Path:
        return self.attempts_dir / f"{candidate_id}.raw.txt"

    def diff_path(self, candidate_id: str) -> Path:
        return self.attempts_dir / f"{candidate_id}.diff"

    def stream_log_path(self, candidate_id: str) -> Path:
        return self.attempts_dir / f"{candidate_id}.stream.txt"

    def session_events_path(self, candidate_id: str) -> Path:
        return self.attempts_dir / f"{candidate_id}.events.jsonl"

    def prompt_snapshots_path(self, candidate_id: str) -> Path:
        return self.attempts_dir / f"{candidate_id}.prompts.jsonl"

    def validation_report_path(self, candidate_id: str, *, retry: bool = False) -> Path:
        suffix = "-retry" if retry else ""
        return self.validations_dir / f"{candidate_id}{suffix}.json"

    def validation_stdout_path(self, candidate_id: str, *, retry: bool = False) -> Path:
        suffix = "-retry" if retry else ""
        return self.validations_dir / f"{candidate_id}{suffix}.stdout.txt"

    def validation_stderr_path(self, candidate_id: str, *, retry: bool = False) -> Path:
        suffix = "-retry" if retry else ""
        return self.validations_dir / f"{candidate_id}{suffix}.stderr.txt"

    def validation_log_path(self, candidate_id: str, *, retry: bool = False) -> Path:
        suffix = "-retry" if retry else ""
        return self.validation_logs_dir / f"{candidate_id}{suffix}.log"


def seed_goal_payload() -> dict[str, Any]:
    return {
        "goal_id": "goal-continuous-self-improvement",
        "text": "自己改善ループを継続し、エージェント本体の可観測性、堅牢性、運用者向け説明、自己診断能力を少しずつ高め続けること。",
        "acceptance": {
            "command": ["python3", "-m", "unittest", "discover", "-s", "tests"],
            "description": "エージェント本体の自己チェックとユニットテストが成功すること。",
            "contract": [
                "describe_agent() が主要な説明フィールドを返す",
                "render_operator_message() が自己改善中であることを示す",
                "self_check() が 0 を返す",
                "python3 agent/goal_logic.py --check が成功する",
            ],
        },
        "status": "active",
        "created_at": now_iso(),
        "last_attempt_at": None,
        "satisfied_at": None,
        "mode": "continuous_self_improvement",
        "cycle_count": 0,
        "focus_areas": [
            "可観測性を上げる",
            "失敗時の説明を分かりやすくする",
            "自己診断を強くする",
            "運用者向けの出力を整える",
            "保守しやすい構造にする",
        ],
        "next_focus_index": 0,
        "last_promoted_candidate_id": None,
        "last_promoted_at": None,
    }


def advance_goal_after_promotion(goal: dict[str, Any], *, candidate_id: str) -> dict[str, Any]:
    updated = dict(goal)
    updated["status"] = "active"
    updated["cycle_count"] = int(updated.get("cycle_count", 0)) + 1
    updated = advance_goal_focus(updated)
    updated["last_promoted_candidate_id"] = candidate_id
    updated["last_promoted_at"] = now_iso()
    updated["satisfied_at"] = None
    return updated


def advance_goal_focus(goal: dict[str, Any]) -> dict[str, Any]:
    updated = dict(goal)
    focus_areas = list(updated.get("focus_areas", []))
    if focus_areas:
        updated["next_focus_index"] = (int(updated.get("next_focus_index", 0)) + 1) % len(focus_areas)
    return updated


def seed_self_model_payload() -> dict[str, Any]:
    return {
        "editable_zones": ["agent/goal_logic.py"],
        "editable_zone_specs": [
            {
                "zone_id": "agent_goal_logic",
                "path": "agent/goal_logic.py",
                "scope": "workspace_candidate",
                "edit_mode": "full_file",
                "selection_enabled": True,
                "description": "候補 workspace 内の本体ロジック。従来どおり自己改善の主対象。",
            },
            {
                "zone_id": "operator_runtime_loop",
                "path": "p2_core/loop.py",
                "scope": "operator_runtime",
                "edit_mode": "restricted_regions",
                "selection_enabled": False,
                "description": "runtime 側の認知環境。段階開放として prompt/working memory/delta_context 更新まわりだけを編集対象にする。",
                "allowed_regions": [
                    "session_action_prompt_building",
                    "reference_selection_prompt_building",
                    "reflection_prompt_building",
                    "delta_context_update",
                    "working_memory_summarization",
                ],
            },
        ],
        "immutable_paths": ["tests/"],
        "runtime_kernel": "legacy_phase_loop_v1",
        "available_runtime_kernels": ["legacy_phase_loop_v1", "session_action_loop_v1"],
        "capabilities": [
            "single_file_full_replacement",
            "candidate_validation",
            "promotion",
            "reset_to_initial_state",
            "dashboard_notification",
            "history_aware_meta_diagnosis",
            "meta_search_mode_switching",
            "system_skill_lookup",
            "self_memo_lookup",
            "self_memo_writeback",
            "session_action_loop_v1",
            "event_sourced_action_log",
            "structured_patch_edits",
        ],
        "default_validation_command": ["python3", "-m", "unittest", "discover", "-s", "tests"],
        "default_model": "qwen3-coder:latest",
        "default_thinking_model": "gemma4:26b",
        "default_coding_model": "qwen3-coder:latest",
        "default_exploratory_coding_model": "devstral:latest",
        "default_stagnation_coding_model": "gemma4:26b",
        "max_frame_depth": 3,
        "max_action_steps": 8,
    }


def seed_system_skills_payload() -> list[dict[str, Any]]:
    return [
        {
            "skill_id": "decompose_problem",
            "title": "問題分解",
            "summary": "因果が粗い、同じ失敗が続く、スコープが広すぎる時は、目的を局所ゴールへ狭めて『子フレームへ降りる』。",
            "when_useful": [
                "同型失敗が続いている",
                "どの変更が壊したのか結び付きが弱い",
                "一度に多くを直そうとしている",
            ],
            "how_to_use": [
                "次に目指す局所ゴール (`next_goal`) を具体化する",
                "フレーム遷移要求の decision に `open_child_frame` を指定する",
                "対象ファイルや失敗位置の近傍だけに焦点を当て、終わったら親フレームへ戻る",
            ],
            "expected_benefit": "局所修復の成功率が上がり、親フレームの認知負荷が下がる。",
            "keywords": ["分解", "階層", "局所修復", "子フレーム"],
        },
        {
            "skill_id": "review_after_change",
            "title": "変更後レビュー",
            "summary": "コード変更後は、自分の変更点と検証結果を突き合わせて見直すと見落としに気づきやすい。",
            "when_useful": [
                "コードを書いた直後",
                "差分はあるが自信が低い",
                "検証に失敗したが原因が曖昧",
            ],
            "how_to_use": [
                "変更行と失敗行の距離を見る",
                "直前差分と検証結果を同時に読む",
                "必要ならレビュー用の局所ゴールを作り、`open_child_frame` で子フレームへ降りる",
            ],
            "expected_benefit": "自分の変更と壊れた結果の因果が見えやすくなる。",
            "keywords": ["レビュー", "差分", "検証", "因果"],
        },
        {
            "skill_id": "work_unit_commitment",
            "title": "作業単位の確定",
            "summary": "今のフレームで何をやり切るかを 1 単位に絞る。1 回の編集と 1 回の検証で閉じないなら、子フレームへ渡す局所ゴールへ分解する。",
            "when_useful": [
                "何を今やるかが曖昧",
                "コードを書きながら考え続けてしまう",
                "goal や commitment が広すぎる",
            ],
            "how_to_use": [
                "question_to_answer は次の編集を始める前に答えるべき 1 問にする",
                "commitment はこのフレームでやり切る 1 単位にする",
                "閉じないと判断したら next_goal を狭く書き、`open_child_frame` を選ぶ",
            ],
            "expected_benefit": "平面的に考え続ける癖が減り、階層へ降りる判断がしやすくなる。",
            "keywords": ["作業単位", "commitment", "局所ゴール", "子フレーム"],
        },
        {
            "skill_id": "recursive_frame_ops",
            "title": "再帰フレーム運用",
            "summary": "別視点の作業は、文脈を引き継いだ子フレームとして整理できる。基本は再帰フレームとして扱う。",
            "when_useful": [
                "本流の文脈を汚したくない",
                "局所問題だけを独立に見たい",
                "レビューや比較の視点を切り出したい",
            ],
            "how_to_use": [
                "まず親フレームの目的を局所ゴールへ分解する",
                "フレーム遷移要求の decision に `open_child_frame` を指定する",
                "子フレームでは要点だけを作り、終わったら親フレームへ戻る",
            ],
            "expected_benefit": "別視点の作業も再帰フレームの中で一貫して扱える。",
            "keywords": ["再帰フレーム", "別視点", "局所化", "成果物"],
        },
        {
            "skill_id": "frame_transition_judgment",
            "title": "フレーム遷移判断",
            "summary": "continue_here は今の文脈でやり切れる時だけ使う。未解決の下位問題が先なら open_child_frame、局所結果を持ち帰るなら return_to_parent を使う。",
            "when_useful": [
                "同じ失敗が続いている",
                "原因がまだ特定できない",
                "レビューや切り分けを先に行うべき",
            ],
            "how_to_use": [
                "continue_here は 1 回の編集と 1 回の検証まで見通せる時だけ選ぶ",
                "open_child_frame は原因切り分けや差分レビューなど下位問題が先の時に選ぶ",
                "return_to_parent は局所結論は得たが、このフレームでは前進しない時に選ぶ",
            ],
            "expected_benefit": "階層を『次の話』にせず、その場で適切に使う判断がしやすくなる。",
            "keywords": ["遷移判断", "continue_here", "open_child_frame", "return_to_parent"],
        },
        {
            "skill_id": "memo_hygiene",
            "title": "メモ活用",
            "summary": "メモは日記ではなく、条件付きで再利用できる短い戦術カードとして残す。",
            "when_useful": [
                "同型失敗を繰り返した",
                "有効だった手段を再利用したい",
                "次回の探索候補を増やしたい",
            ],
            "how_to_use": [
                "自己メモの条件・戦術・理由 (`when` / `tactic` / `why`) を短く書く",
                "証拠は candidate_id や失敗種別に寄せる",
                "関連する時だけ読み、全部は読まない",
            ],
            "expected_benefit": "一度の気づきを、次回も候補に上げやすくなる。",
            "keywords": ["メモ", "学習", "再利用", "戦術"],
        },
    ]


def sync_system_skills_catalog(root: Path) -> None:
    paths = WorkspacePaths(root.expanduser())
    expected = seed_system_skills_payload()
    for path in [paths.seed_initial_state_dir / "system_skills.json", paths.system_skills_path]:
        if not path.parent.exists():
            continue
        current = read_json(path, fallback=None)
        if current != expected:
            write_json(path, expected)


def sync_self_model_payload(root: Path) -> None:
    paths = WorkspacePaths(root.expanduser())
    expected = seed_self_model_payload()
    for path in [paths.seed_initial_state_dir / "self_model.json", paths.self_model_path]:
        if not path.parent.exists():
            continue
        current = read_json(path, fallback=None)
        if current is None:
            write_json(path, expected)
            continue
        merged = dict(current)
        for key, value in expected.items():
            if key not in merged:
                merged[key] = value
        expected_specs = {str(spec.get("zone_id")): spec for spec in expected.get("editable_zone_specs", [])}
        current_specs = {
            str(spec.get("zone_id")): spec
            for spec in list(merged.get("editable_zone_specs") or [])
            if isinstance(spec, dict) and spec.get("zone_id")
        }
        for zone_id, spec in expected_specs.items():
            if zone_id not in current_specs:
                current_specs[zone_id] = spec
        merged["editable_zone_specs"] = list(current_specs.values())
        if merged != current:
            write_json(path, merged)


def resolve_model_roles(
    root: Path,
    *,
    model: str | None = None,
    thinking_model: str | None = None,
    coding_model: str | None = None,
    exploratory_coding_model: str | None = None,
    stagnation_coding_model: str | None = None,
) -> dict[str, str]:
    sync_self_model_payload(root)
    self_model = read_json(WorkspacePaths(root.expanduser()).self_model_path, fallback={})
    resolved_model = model or self_model.get("default_model") or "qwen3-coder:latest"
    resolved_thinking = thinking_model or self_model.get("default_thinking_model") or resolved_model
    resolved_coding = coding_model or self_model.get("default_coding_model") or resolved_model
    resolved_exploratory = (
        exploratory_coding_model or self_model.get("default_exploratory_coding_model") or resolved_coding
    )
    resolved_stagnation = (
        stagnation_coding_model or self_model.get("default_stagnation_coding_model") or resolved_thinking
    )
    return {
        "model": str(resolved_model),
        "thinking_model": str(resolved_thinking),
        "coding_model": str(resolved_coding),
        "exploratory_coding_model": str(resolved_exploratory),
        "stagnation_coding_model": str(resolved_stagnation),
    }


def resolve_runtime_kernel(root: Path, *, runtime_kernel: str | None = None) -> str:
    sync_self_model_payload(root)
    self_model = read_json(WorkspacePaths(root.expanduser()).self_model_path, fallback={})
    resolved = runtime_kernel or self_model.get("runtime_kernel") or "legacy_phase_loop_v1"
    return str(resolved)


def seed_version_payload(root: Path) -> dict[str, Any]:
    return {
        "active_generation": 1,
        "active_version_id": "v0001",
        "active_path": str((root / "runtime" / "versions" / "v0001").resolve()),
        "last_candidate_id": None,
        "updated_at": now_iso(),
    }


def seed_runtime_status_payload() -> dict[str, Any]:
    return {
        "status": "idle",
        "active_loop_run_id": None,
        "current_candidate_id": None,
        "current_task_stack": None,
        "current_focus": None,
        "current_observations": {"target_file_contents": {}},
        "recent_tool_results": [],
        "recent_validation_results": {},
        "recent_diffs": {},
        "working_memory": {"local_working_memory": {}},
        "child_return_payloads": [],
        "current_runtime_kernel": None,
        "current_action": None,
        "current_action_step": None,
        "goal_reset_pending": False,
        "goal_reset_at": None,
        "goal_reset_required_first_actions": ["read_file", "search_code"],
        "goal_preflight": {},
        "phase": None,
        "phase_started_at": None,
        "last_output_at": None,
        "current_stream_path": None,
        "model": None,
        "thinking_model": None,
        "coding_model": None,
        "exploratory_coding_model": None,
        "stagnation_coding_model": None,
        "last_loop_started_at": None,
        "last_loop_finished_at": None,
        "last_error": None,
        "last_event": "workspace_bootstrapped",
        "dashboard_notify_url": None,
        "dashboard_health_url": None,
        "worker_heartbeat_at": None,
        "updated_at": now_iso(),
    }


def ensure_workspace_prerequisites(root: Path) -> dict[str, int]:
    root = root.expanduser()
    paths = WorkspacePaths(root)
    created_dirs = 0
    created_files = 0

    def ensure_dir(path: Path) -> None:
        nonlocal created_dirs
        if path.exists():
            return
        path.mkdir(parents=True, exist_ok=True)
        created_dirs += 1

    def ensure_json(path: Path, payload: dict[str, Any] | list[dict[str, Any]]) -> None:
        nonlocal created_files
        if path.exists():
            return
        write_json(path, payload)
        created_files += 1

    def ensure_text(path: Path, content: str = "") -> None:
        nonlocal created_files
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        created_files += 1

    for directory in [
        paths.seed_initial_version_dir / "agent",
        paths.seed_initial_version_dir / "tests",
        paths.seed_initial_state_dir,
        paths.runtime_versions_dir,
        paths.runtime_candidates_dir,
        paths.attempts_dir,
        paths.validations_dir,
        paths.runtime_state_dir,
        paths.archive_resets_dir,
        paths.logs_dir,
        paths.validation_logs_dir,
    ]:
        ensure_dir(directory)

    ensure_text(paths.seed_initial_version_dir / "agent" / "__init__.py", INITIAL_AGENT_INIT)
    ensure_text(paths.seed_initial_version_dir / "agent" / "goal_logic.py", INITIAL_GOAL_LOGIC)
    ensure_text(paths.seed_initial_version_dir / "tests" / "test_goal_logic.py", INITIAL_TEST)

    ensure_json(paths.seed_initial_state_dir / "goal.json", seed_goal_payload())
    ensure_json(paths.seed_initial_state_dir / "self_model.json", seed_self_model_payload())
    ensure_json(paths.seed_initial_state_dir / "version.json", seed_version_payload(root))
    ensure_json(paths.seed_initial_state_dir / "system_skills.json", seed_system_skills_payload())
    ensure_json(paths.seed_initial_state_dir / "runtime_status.json", seed_runtime_status_payload())
    ensure_text(paths.seed_initial_state_dir / "history.jsonl")
    ensure_text(paths.seed_initial_state_dir / "memos.jsonl")

    ensure_json(paths.goal_path, seed_goal_payload())
    ensure_json(paths.self_model_path, seed_self_model_payload())
    ensure_json(paths.version_path, seed_version_payload(root))
    ensure_json(paths.system_skills_path, seed_system_skills_payload())
    ensure_json(paths.runtime_status_path, seed_runtime_status_payload())
    ensure_text(paths.history_path)
    ensure_text(paths.memos_path)
    ensure_text(paths.queue_path)
    ensure_text(paths.loop_log_path)
    ensure_text(paths.dashboard_log_path)

    active_seed_version = paths.runtime_versions_dir / "v0001"
    if not active_seed_version.exists() and paths.seed_initial_version_dir.exists():
        copytree_clean(paths.seed_initial_version_dir, active_seed_version)
        created_dirs += 1

    return {"created_dirs": created_dirs, "created_files": created_files}


def history_event(
    *,
    step: str,
    outcome: str,
    message: str,
    goal_id: str | None = None,
    generation: int | None = None,
    candidate_id: str | None = None,
    loop_run_id: str | None = None,
) -> dict[str, Any]:
    return {
        "timestamp": now_iso(),
        "loop_run_id": loop_run_id,
        "goal_id": goal_id,
        "generation": generation,
        "step": step,
        "outcome": outcome,
        "candidate_id": candidate_id,
        "message": message,
    }


def append_history(root: Path, payload: dict[str, Any]) -> None:
    append_jsonl(WorkspacePaths(root).history_path, payload)


def append_loop_log(root: Path, message: str) -> None:
    paths = WorkspacePaths(root)
    paths.loop_log_path.parent.mkdir(parents=True, exist_ok=True)
    with paths.loop_log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"[p2-loop] {now_iso()} {message}\n")


def update_runtime_status(root: Path, **updates: Any) -> dict[str, Any]:
    paths = WorkspacePaths(root)
    payload = read_json(paths.runtime_status_path, fallback=seed_runtime_status_payload())
    payload.update(updates)
    payload["updated_at"] = now_iso()
    write_json(paths.runtime_status_path, payload)
    return payload


def _is_pid_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _dashboard_health_ok_from_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or not parsed.port:
        return False
    try:
        with request.urlopen(url, timeout=1.5) as response:
            body = response.read().strip()
        return response.status == 200 and body == b"ok"
    except (error.URLError, TimeoutError, ValueError, OSError):
        return False


def reconcile_runtime_status(root: Path, payload: dict[str, Any] | None = None, *, persist: bool = True) -> dict[str, Any]:
    paths = WorkspacePaths(root)
    runtime_status = dict(payload or read_json(paths.runtime_status_path, fallback=seed_runtime_status_payload()))
    changed = False

    watchdog_pid = runtime_status.get("watchdog_pid")
    if isinstance(watchdog_pid, int) and watchdog_pid > 0 and not _is_pid_running(watchdog_pid):
        runtime_status["watchdog_pid"] = None
        changed = True

    worker_pid = runtime_status.get("worker_pid")
    if isinstance(worker_pid, int) and worker_pid > 0 and not _is_pid_running(worker_pid):
        runtime_status["worker_pid"] = None
        changed = True

    dashboard_owner = str(runtime_status.get("dashboard_owner") or "")
    health_url = str(runtime_status.get("dashboard_health_url") or "").strip()
    dashboard_alive = _dashboard_health_ok_from_url(health_url) if health_url else False
    watchdog_alive = isinstance(runtime_status.get("watchdog_pid"), int) and bool(runtime_status.get("watchdog_pid"))

    if dashboard_owner == "watchdog" and (not watchdog_alive) and not dashboard_alive:
        runtime_status["dashboard_owner"] = None
        runtime_status["dashboard_mode"] = None
        runtime_status["dashboard_notify_url"] = None
        runtime_status["dashboard_health_url"] = None
        if runtime_status.get("last_event") == "worker_stopped":
            runtime_status["last_event"] = "watchdog_stale_state_cleared"
        changed = True
    elif dashboard_owner == "standalone" and not dashboard_alive:
        runtime_status["dashboard_owner"] = None
        runtime_status["dashboard_mode"] = None
        runtime_status["dashboard_notify_url"] = None
        runtime_status["dashboard_health_url"] = None
        runtime_status["last_event"] = "dashboard_stale_state_cleared"
        changed = True

    if changed:
        runtime_status["updated_at"] = now_iso()
        if persist:
            write_json(paths.runtime_status_path, runtime_status)
    return runtime_status


def dequeue_queue_item(root: Path) -> dict[str, Any] | None:
    paths = WorkspacePaths(root)
    if not paths.queue_path.exists():
        return None
    rows = [line for line in paths.queue_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        return None
    head = json.loads(rows[0])
    tail = rows[1:]
    paths.queue_path.write_text("".join(f"{line}\n" for line in tail), encoding="utf-8")
    return head


def bootstrap_workspace(root: Path, *, force: bool = False) -> dict[str, Any]:
    root = root.expanduser()
    root.mkdir(parents=True, exist_ok=True)
    managed_paths = [
        root / "seed",
        root / "runtime",
        root / "state",
        root / "logs",
    ]
    if force:
        for path in managed_paths:
            if path.exists():
                shutil.rmtree(path)

    paths = WorkspacePaths(root)
    created: list[str] = []
    for directory in [
        paths.seed_initial_version_dir / "agent",
        paths.seed_initial_version_dir / "tests",
        paths.seed_initial_state_dir,
        paths.runtime_versions_dir,
        paths.runtime_candidates_dir,
        paths.attempts_dir,
        paths.validations_dir,
        paths.archive_resets_dir,
        paths.runtime_state_dir,
        paths.validation_logs_dir,
    ]:
        directory.mkdir(parents=True, exist_ok=True)
        created.append(str(directory))

    (paths.seed_initial_version_dir / "agent" / "__init__.py").write_text(INITIAL_AGENT_INIT, encoding="utf-8")
    (paths.seed_initial_version_dir / "agent" / "goal_logic.py").write_text(INITIAL_GOAL_LOGIC, encoding="utf-8")
    (paths.seed_initial_version_dir / "tests" / "test_goal_logic.py").write_text(INITIAL_TEST, encoding="utf-8")

    goal_payload = seed_goal_payload()
    self_model_payload = seed_self_model_payload()
    version_payload = seed_version_payload(root)
    runtime_status_payload = seed_runtime_status_payload()

    write_json(paths.seed_initial_state_dir / "goal.json", goal_payload)
    write_json(paths.seed_initial_state_dir / "self_model.json", self_model_payload)
    write_json(paths.seed_initial_state_dir / "version.json", version_payload)
    write_json(paths.seed_initial_state_dir / "system_skills.json", seed_system_skills_payload())
    write_json(paths.seed_initial_state_dir / "runtime_status.json", runtime_status_payload)
    paths.seed_initial_state_dir.joinpath("history.jsonl").write_text("", encoding="utf-8")
    paths.seed_initial_state_dir.joinpath("memos.jsonl").write_text("", encoding="utf-8")

    copytree_clean(paths.seed_initial_version_dir, paths.runtime_versions_dir / "v0001")
    write_json(paths.goal_path, goal_payload)
    write_json(paths.self_model_path, self_model_payload)
    write_json(paths.version_path, version_payload)
    write_json(paths.system_skills_path, seed_system_skills_payload())
    write_json(paths.runtime_status_path, runtime_status_payload)
    paths.history_path.write_text("", encoding="utf-8")
    paths.memos_path.write_text("", encoding="utf-8")
    paths.queue_path.write_text("", encoding="utf-8")
    paths.loop_log_path.write_text("", encoding="utf-8")
    paths.dashboard_log_path.write_text("", encoding="utf-8")

    append_history(
        root,
        history_event(
            step="bootstrap",
            outcome="completed",
            message="workspace bootstrapped from initial seed",
            goal_id=goal_payload["goal_id"],
            generation=1,
        ),
    )
    append_loop_log(root, "workspace bootstrapped from initial seed")
    notify_dashboard(root)
    return {"ok": True, "root": str(root), "created": created}


def reset_workspace(root: Path, *, mode: str = "initial") -> dict[str, Any]:
    if mode != "initial":
        raise ValueError(f"unsupported reset mode: {mode}")

    root = root.expanduser()
    paths = WorkspacePaths(root)
    previous_runtime_status = read_json(paths.runtime_status_path, fallback={})
    archive_dir = paths.archive_resets_dir / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    archive_dir.mkdir(parents=True, exist_ok=True)

    for relative in [
        "runtime/versions",
        "runtime/candidates",
        "state/attempts",
        "state/validations",
        "state/runtime",
        "state/goal.json",
        "state/self_model.json",
        "state/version.json",
        "state/system_skills.json",
        "state/memos.jsonl",
        "state/history.jsonl",
        "logs",
    ]:
        source = root / relative
        target = archive_dir / relative
        if not source.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            copytree_archive_best_effort(source, target)
        else:
            shutil.copy2(source, target)

    for path in [
        paths.runtime_versions_dir,
        paths.runtime_candidates_dir,
        paths.attempts_dir,
        paths.validations_dir,
        paths.runtime_state_dir,
        paths.logs_dir,
    ]:
        if path.exists():
            shutil.rmtree(path)

    copytree_clean(paths.seed_initial_version_dir, paths.runtime_versions_dir / "v0001")
    paths.runtime_candidates_dir.mkdir(parents=True, exist_ok=True)
    paths.attempts_dir.mkdir(parents=True, exist_ok=True)
    paths.validations_dir.mkdir(parents=True, exist_ok=True)
    paths.runtime_state_dir.mkdir(parents=True, exist_ok=True)
    paths.validation_logs_dir.mkdir(parents=True, exist_ok=True)
    paths.logs_dir.mkdir(parents=True, exist_ok=True)
    paths.loop_log_path.write_text("", encoding="utf-8")
    paths.dashboard_log_path.write_text("", encoding="utf-8")

    write_json(paths.goal_path, read_json(paths.seed_initial_state_dir / "goal.json"))
    write_json(paths.self_model_path, read_json(paths.seed_initial_state_dir / "self_model.json"))
    write_json(paths.version_path, read_json(paths.seed_initial_state_dir / "version.json"))
    write_json(paths.system_skills_path, read_json(paths.seed_initial_state_dir / "system_skills.json"))
    restored_runtime_status = read_json(paths.seed_initial_state_dir / "runtime_status.json")
    for key in ("dashboard_notify_url", "dashboard_health_url"):
        if previous_runtime_status.get(key):
            restored_runtime_status[key] = previous_runtime_status[key]
    restored_runtime_status["last_event"] = "reset_applied"
    write_json(paths.runtime_status_path, restored_runtime_status)
    paths.history_path.write_text((paths.seed_initial_state_dir / "history.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
    paths.memos_path.write_text((paths.seed_initial_state_dir / "memos.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
    paths.queue_path.write_text("", encoding="utf-8")

    goal = read_json(paths.goal_path)
    append_history(
        root,
        history_event(
            step="reset",
            outcome="applied",
            message="workspace reset to bootstrap seed",
            goal_id=goal.get("goal_id"),
            generation=1,
        ),
    )
    append_loop_log(root, f"workspace reset to bootstrap seed archive={archive_dir}")
    notify_dashboard(root)
    return {"ok": True, "root": str(root), "archive_path": str(archive_dir), "mode": mode}


def update_goal_from_dashboard(
    root: Path,
    *,
    goal_text: str,
    reset_mode: str | None = None,
) -> dict[str, Any]:
    normalized_goal = str(goal_text or "").strip()
    if not normalized_goal:
        raise ValueError("goal_text is required")

    root = root.expanduser()
    if reset_mode:
        reset_workspace(root, mode=reset_mode)

    paths = WorkspacePaths(root)
    goal = read_json(paths.goal_path, fallback=seed_goal_payload())
    if not isinstance(goal, dict):
        goal = seed_goal_payload()
    goal["text"] = normalized_goal
    goal["status"] = "active"
    goal["satisfied_at"] = None
    goal["updated_at"] = now_iso()
    if reset_mode:
        goal["cycle_count"] = 0
        goal["next_focus_index"] = 0
        goal["last_promoted_candidate_id"] = None
        goal["last_promoted_at"] = None
        goal["last_attempt_at"] = None
    write_json(paths.goal_path, goal)

    def _goal_dependent_runtime_reset() -> dict[str, Any]:
        return {
            "current_task_stack": None,
            "current_focus": None,
            "current_action": None,
            "current_action_step": None,
            "current_observations": {"target_file_contents": {}},
            "recent_tool_results": [],
            "recent_validation_results": {},
            "recent_diffs": {},
            "working_memory": {"local_working_memory": {}},
            "child_return_payloads": [],
        }

    def _select_target_file_for_goal() -> str:
        sync_self_model_payload(root)
        self_model = read_json(paths.self_model_path, fallback={})
        version = read_json(paths.version_path, fallback={})
        active_path = Path(str(version.get("active_path") or "")).expanduser()
        for candidate in list(self_model.get("editable_zones") or []):
            if not isinstance(candidate, str):
                continue
            rel = candidate.strip()
            if not rel:
                continue
            if (active_path / rel).exists():
                return rel
        raise FileNotFoundError("goal preflight: editable target file not found")

    def _goal_preflight_payload() -> dict[str, Any]:
        checks: list[dict[str, Any]] = []
        ok = True
        errors: list[str] = []
        target_file = ""
        validation_command: list[str] = []
        workspace_seed = str(paths.seed_initial_version_dir)
        focus = ""
        try:
            target_file = _select_target_file_for_goal()
            checks.append({"name": "target_file", "ok": True, "value": target_file})
        except Exception as exc:
            ok = False
            errors.append(str(exc))
            checks.append({"name": "target_file", "ok": False, "error": str(exc)})

        self_model = read_json(paths.self_model_path, fallback={})
        raw_command = list(self_model.get("default_validation_command") or [])
        if raw_command:
            validation_command = [str(item) for item in raw_command]
            checks.append({"name": "validation_command", "ok": True, "value": validation_command})
        else:
            ok = False
            message = "goal preflight: default_validation_command is missing"
            errors.append(message)
            checks.append({"name": "validation_command", "ok": False, "error": message})

        if paths.seed_initial_version_dir.exists():
            checks.append({"name": "workspace_seed", "ok": True, "value": workspace_seed})
        else:
            ok = False
            message = f"goal preflight: workspace seed missing: {workspace_seed}"
            errors.append(message)
            checks.append({"name": "workspace_seed", "ok": False, "error": message})

        focus_areas = list(goal.get("focus_areas") or [])
        if focus_areas:
            index = int(goal.get("next_focus_index", 0) or 0) % len(focus_areas)
            focus = str(focus_areas[index] or "")
        else:
            focus = normalized_goal
        checks.append({"name": "focus", "ok": bool(focus), "value": focus})
        if not focus:
            ok = False
            errors.append("goal preflight: focus could not be computed")

        return {
            "ok": ok,
            "errors": errors,
            "checks": checks,
            "target_file": target_file,
            "validation_command": validation_command,
            "workspace_seed": workspace_seed,
            "current_focus": focus,
        }

    goal_preflight = _goal_preflight_payload()

    append_history(
        root,
        history_event(
            step="goal_update",
            outcome="applied",
            message=f"goal updated from dashboard reset_mode={reset_mode or 'none'}",
            goal_id=str(goal.get("goal_id") or ""),
            generation=int(read_json(paths.version_path, fallback={}).get("active_generation") or 1),
        ),
    )
    append_loop_log(root, f"goal updated from dashboard reset_mode={reset_mode or 'none'}")
    runtime_updates = {
        **_goal_dependent_runtime_reset(),
        "last_event": "goal_updated_from_dashboard",
        "phase": "context_selecting" if goal_preflight.get("ok") else "blocked",
        "status": "idle" if goal_preflight.get("ok") else "blocked",
        "last_error": None if goal_preflight.get("ok") else "; ".join(goal_preflight.get("errors") or []),
        "goal_reset_pending": bool(goal_preflight.get("ok")),
        "goal_reset_at": now_iso(),
        "goal_reset_required_first_actions": ["read_file", "search_code"],
        "goal_preflight": goal_preflight,
        "current_focus": goal_preflight.get("current_focus") or None,
    }
    update_runtime_status(root, **runtime_updates)
    notify_dashboard(root)
    return {
        "ok": True,
        "goal_text": normalized_goal,
        "reset_applied": bool(reset_mode),
        "reset_mode": reset_mode or "",
        "preflight_ok": bool(goal_preflight.get("ok")),
        "preflight": goal_preflight,
    }


def read_history(root: Path, *, limit: int | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    path = WorkspacePaths(root).history_path
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rows.append(json.loads(line))
    if limit is not None:
        return rows[-limit:]
    return rows


def read_jsonl_rows(path: Path, *, limit: int | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    if limit is not None:
        return rows[-limit:]
    return rows


def read_attempt_report(root: Path, candidate_id: str) -> dict[str, Any]:
    return read_json(WorkspacePaths(root).attempt_report_path(candidate_id))


def read_validation_report(root: Path, candidate_id: str, *, retry: bool = False) -> dict[str, Any] | None:
    path = WorkspacePaths(root).validation_report_path(candidate_id, retry=retry)
    if not path.exists():
        return None
    return read_json(path)


def read_system_skills(root: Path) -> list[dict[str, Any]]:
    payload = read_json(WorkspacePaths(root).system_skills_path, fallback=[])
    return payload if isinstance(payload, list) else []


def read_memos(root: Path, *, limit: int | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    path = WorkspacePaths(root).memos_path
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rows.append(json.loads(line))
    if limit is not None:
        return rows[-limit:]
    return rows


def append_memo(root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    paths = WorkspacePaths(root)
    existing = read_memos(root)
    last_memo_id = existing[-1]["memo_id"] if existing else None
    enriched = dict(payload)
    enriched["memo_id"] = next_identifier(last_memo_id, prefix="m")
    enriched["created_at"] = enriched.get("created_at") or now_iso()
    append_jsonl(paths.memos_path, enriched)
    return enriched


def recent_attempt_reports(root: Path, *, limit: int = 10) -> list[dict[str, Any]]:
    paths = WorkspacePaths(root)
    reports = []
    for path in sorted(paths.attempts_dir.glob("c*.json")):
        try:
            reports.append(read_json(path))
        except (FileNotFoundError, json.JSONDecodeError):
            continue
    return reports[-limit:]


def build_status_snapshot(root: Path, *, attempt_limit: int = 8, history_limit: int = 20) -> dict[str, Any]:
    root = root.expanduser()
    ensure_workspace_prerequisites(root)
    paths = WorkspacePaths(root)
    sync_system_skills_catalog(root)
    sync_self_model_payload(root)
    goal = read_json(paths.goal_path, fallback={})
    version = read_json(paths.version_path, fallback={})
    self_model = read_json(paths.self_model_path, fallback={})
    runtime_status = reconcile_runtime_status(root, read_json(paths.runtime_status_path, fallback={}))
    recent_attempts = recent_attempt_reports(root, limit=attempt_limit)
    recent_memos = read_memos(root, limit=8)
    system_skills = read_system_skills(root)
    latest_attempt = recent_attempts[-1] if recent_attempts else None
    latest_validation = None
    latest_retry_validation = None
    if latest_attempt:
        latest_validation = read_validation_report(root, latest_attempt["candidate_id"])
        latest_retry_validation = read_validation_report(root, latest_attempt["candidate_id"], retry=True)
    recent_history = read_history(root, limit=history_limit)
    candidate_paths = [str(path.resolve()) for path in sorted(paths.runtime_candidates_dir.glob("c*")) if path.is_dir()]
    current_stream_text = ""
    current_stream_path = runtime_status.get("current_stream_path")
    if current_stream_path:
        current_stream_text = tail_text(Path(current_stream_path))
    latest_session_events: list[dict[str, Any]] = []
    latest_prompt_snapshots: list[dict[str, Any]] = []
    if latest_attempt:
        session_events_path = latest_attempt.get("session_events_path")
        if session_events_path:
            latest_session_events = read_jsonl_rows(Path(str(session_events_path)), limit=12)
        elif latest_attempt.get("candidate_id"):
            latest_session_events = read_jsonl_rows(
                paths.session_events_path(str(latest_attempt["candidate_id"])),
                limit=12,
            )
        prompt_snapshots_path = latest_attempt.get("prompt_snapshots_path")
        if prompt_snapshots_path:
            latest_prompt_snapshots = read_jsonl_rows(Path(str(prompt_snapshots_path)), limit=6)
        elif latest_attempt.get("candidate_id"):
            latest_prompt_snapshots = read_jsonl_rows(
                paths.prompt_snapshots_path(str(latest_attempt["candidate_id"])),
                limit=6,
            )
    recent_timings: list[int] = []
    for attempt in reversed(recent_attempts):
        timings = attempt.get("llm_timings")
        if isinstance(timings, dict) and timings.get("total_duration_ms") is not None:
            recent_timings.append(int(timings["total_duration_ms"]))
        if len(recent_timings) >= 5:
            break
    average_timing = int(sum(recent_timings) / len(recent_timings)) if recent_timings else None
    return {
        "generated_at": now_iso(),
        "root": str(root),
        "goal": goal,
        "version": version,
        "runtime_status": runtime_status,
        "self_model_summary": {
            "editable_zones": list(self_model.get("editable_zones") or []),
            "editable_zone_specs": list(self_model.get("editable_zone_specs") or []),
            "immutable_paths": list(self_model.get("immutable_paths") or []),
            "runtime_kernel": self_model.get("runtime_kernel"),
            "default_model": self_model.get("default_model"),
            "default_thinking_model": self_model.get("default_thinking_model"),
            "default_coding_model": self_model.get("default_coding_model"),
            "default_exploratory_coding_model": self_model.get("default_exploratory_coding_model"),
            "default_stagnation_coding_model": self_model.get("default_stagnation_coding_model"),
        },
        "active_generation": version.get("active_generation"),
        "active_version_id": version.get("active_version_id"),
        "active_path": version.get("active_path"),
        "candidate_paths": candidate_paths,
        "latest_attempt": latest_attempt,
        "latest_self_memo": (latest_attempt or {}).get("self_memo"),
        "latest_reasoning_summary": (latest_attempt or {}).get("reasoning_summary"),
        "latest_situation_report": (latest_attempt or {}).get("situation_report"),
        "latest_pre_edit_reflection": (latest_attempt or {}).get("pre_edit_reflection"),
        "latest_post_edit_reflection": (latest_attempt or {}).get("post_edit_reflection"),
        "latest_meta_diagnosis": (latest_attempt or {}).get("meta_diagnosis"),
        "latest_search_mode": (latest_attempt or {}).get("search_mode"),
        "latest_reference_index": (latest_attempt or {}).get("reference_index"),
        "latest_selected_context": (latest_attempt or {}).get("selected_context"),
        "latest_resolved_context": (latest_attempt or {}).get("resolved_context"),
        "latest_delta_context": (latest_attempt or {}).get("delta_context"),
        "latest_task_frame": (latest_attempt or {}).get("task_frame"),
        "latest_frame_trace": (latest_attempt or {}).get("frame_trace"),
        "latest_frame_affordances": (latest_attempt or {}).get("frame_affordances"),
        "latest_system_capabilities": (latest_attempt or {}).get("system_capabilities"),
        "latest_recent_attempt_bundle": (latest_attempt or {}).get("recent_attempt_bundle"),
        "latest_attempted_change": {
            "candidate_id": (latest_attempt or {}).get("candidate_id"),
            "target_file": (latest_attempt or {}).get("target_file"),
            "status": (latest_attempt or {}).get("status"),
            "decision_reason": (latest_attempt or {}).get("decision_reason"),
            "change_summary": (latest_attempt or {}).get("change_summary"),
        }
        if latest_attempt
        else None,
        "latest_session_events": latest_session_events,
        "latest_prompt_snapshots": latest_prompt_snapshots,
        "latest_prompt_snapshot": latest_prompt_snapshots[-1] if latest_prompt_snapshots else None,
        "current_stream_text": current_stream_text,
        "current_task_stack": runtime_status.get("current_task_stack"),
        "recent_memos": recent_memos,
        "system_skills": system_skills,
        "llm_timing_trend": {
            "recent_total_duration_ms": list(reversed(recent_timings)),
            "recent_average_duration_ms": average_timing,
        },
        "latest_validation": latest_validation,
        "latest_retry_validation": latest_retry_validation,
        "recent_attempts": recent_attempts,
        "recent_history": recent_history,
    }


def load_runtime_status(paths: WorkspacePaths) -> dict[str, Any]:
    return read_json(paths.runtime_status_path, fallback={})


def save_runtime_status(paths: WorkspacePaths, payload: dict[str, Any]) -> dict[str, Any]:
    payload = dict(payload)
    payload["updated_at"] = now_iso()
    write_json(paths.runtime_status_path, payload)
    return payload


def build_snapshot(root: Path, *, attempt_limit: int = 8, history_limit: int = 20) -> dict[str, Any]:
    snapshot = build_status_snapshot(root, attempt_limit=attempt_limit, history_limit=history_limit)
    paths = WorkspacePaths(root.expanduser())
    snapshot["runtime"] = snapshot.get("runtime_status", {})
    snapshot["history"] = snapshot.get("recent_history", [])
    snapshot["paths"] = {
        "candidate_dir": str(paths.runtime_candidates_dir),
        "attempts_dir": str(paths.attempts_dir),
        "validations_dir": str(paths.validations_dir),
        "archive_resets_dir": str(paths.archive_resets_dir),
        "system_skills_path": str(paths.system_skills_path),
        "memos_path": str(paths.memos_path),
    }
    return snapshot


def notify_dashboard(root: Path) -> None:
    runtime_status = read_json(WorkspacePaths(root).runtime_status_path, fallback={})
    notify_url = runtime_status.get("dashboard_notify_url")
    if not notify_url:
        return
    data = json.dumps({"event": "refresh"}, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        str(notify_url),
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=2):
            return
    except (error.URLError, TimeoutError, ValueError):
        return


def post_dashboard_snapshot(root: Path) -> None:
    notify_dashboard(root)


def make_loop_run_id() -> str:
    return f"run-{uuid.uuid4()}"
