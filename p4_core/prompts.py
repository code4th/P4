from __future__ import annotations

import json
import re
import time
from typing import Any

from p4_core.config_defaults import DEFAULT_TOOL_CONTENT_CHUNK_BYTES

from p4_core.workspace import append_jsonl, append_session_event, now_iso, read_json, read_jsonl


def _current_phase(self, *, user_message: str, steps: list[dict[str, Any]], recent_events: list[dict[str, Any]] = None) -> str:
    if self._deliberation_reasons(user_message=user_message, steps=steps, recent_events=recent_events):
        return "DELIBERATE"

    # Phase 6: Planning for complex tasks
    is_complex = any(kw in user_message.lower() for kw in ["implement", "fix", "refactor", "create", "design", "実装", "修正", "構造", "作成", "新設", "設計"])
    if is_complex and not steps:
        return "PLANNING"

    requested = self._extract_requested_commands(user_message)
    if requested and not steps:
        return "DISCOVER_REQUIRED_COMMANDS"
    if self._missing_requested_commands(user_message=user_message, steps=steps):
        return "EXECUTE_MISSING_COMMANDS"
    if any(str(step.get("tool_name") or "") == "run_command" for step in steps):
        return "SYNTHESIZE_FROM_EVIDENCE"
    return "FINISH"


def _deliberation_reasons(self, *, user_message: str, steps: list[dict[str, Any]], recent_events: list[dict[str, Any]] = None) -> list[str]:
    reasons = []
    # 1. Step count threshold
    if len(steps) >= 10: # Increased threshold as complex tasks need more steps
        reasons.append(f"ステップ数が上限（{len(steps)}/12）に近づいています")

    # 2. Consecutive finish blocks (checking recent events)
    if recent_events:
        consecutive_blocks = 0
        for event in reversed(recent_events):
            if event.get("type") == "system_note" and "完了がブロックされました" in str(event.get("content") or ""):
                consecutive_blocks += 1
            elif event.get("type") == "assistant_message" or event.get("type") == "tool_result":
                # Only count if no progress was made between blocks
                continue
            elif event.get("type") == "operation" and event.get("status") == "running":
                break
        if consecutive_blocks >= 2:
            reasons.append(f"完了が{consecutive_blocks}回連続でブロックされました")

    # 3. Overall command failures
    failed_count = sum(1 for step in steps if step.get("tool_name") == "run_command" and not bool((step.get("tool_result") or {}).get("ok")))
    if failed_count >= 3:
        reasons.append(f"コマンド実行が合計{failed_count}回失敗しています")
    elif len(steps) >= 2:
        last_results = [step.get("tool_result") or {} for step in steps[-2:]]
        if all(not bool(res.get("ok")) for res in last_results) and all(step.get("tool_name") == "run_command" for step in steps[-2:]):
            reasons.append("コマンド実行が連続して失敗しています")

    # 4. Stagnation: Repeated tool outputs without finding target
    if len(steps) >= 3:
        last_three = steps[-3:]
        tools = [s.get("tool_name") for s in last_three]
        results = [json.dumps(s.get("tool_result") or {}, sort_keys=True) for s in last_three]
        if len(set(tools)) == 1 and tools[0] in {"list_files", "search_code", "read_file"}:
            if len(set(results)) == 1:
                reasons.append(f"ツール（{tools[0]}）で同じ結果を繰り返しています")

        # 5. Exact command duplication
        if len(set(tools)) == 1 and tools[0] == "run_command":
            commands = [str((s.get("tool_result") or {}).get("command") or "") for s in last_three]
            if len(set(commands)) == 1 and commands[0]:
                reasons.append(f"まったく同じコマンド（{commands[0]}）を再実行しています")

    # 6. Missing commands persisting
    missing_commands = self._missing_requested_commands(user_message=user_message, steps=steps)
    if missing_commands and len(steps) >= 4:
        reasons.append(f"要求されたコマンド（{', '.join(missing_commands)}）が未実行のままステップを消費しています")

    return reasons


def _system_prompt(self) -> str:
    output_budget = self._output_budget_prompt()
    return (
        "あなたは P4、ローカルエージェントランタイムです。"
        "最重要: assistant の可視 content には、必ず JSON オブジェクトを1個だけ出力してください。"
        "Markdown、コードフェンス、説明文、箇条書き、JSONの前後の文章を content に出してはいけません。"
        "内部の thinking / reasoning は content とは別に保ち、content へ混入させないでください。"
        "JSON スキーマは {\"analysis\": string, \"assistant_message\": string, \"tool_name\": string, \"tool_args\": object} です。"
        "あなたの仕事は、一度に一つのツールを選択し、ツールの実行結果を新しい証拠（evidence）として活用しながら、ユーザーの目標を達成することです。"
        "客観的な完了が確認されるまで停止しないでください。"
        "出力は必ず上記 JSON 形式とし、キーは analysis, assistant_message, tool_name, tool_args としてください。"
        f"{output_budget}"
        "利用可能なツール:\n"
        f"{self.tools.describe_for_prompt()}\n"
        "フレーム操作: 問題が複数の局所目的に分かれる場合は decompose_tasks で良い粒度の子タスク計画を作ってください。"
        "decompose_tasks と open_child_frame は、goal だけでは使えません。各子には work_type, first_action, success_evidence, why_not_direct_action を含む work_package が必要です。"
        "first_action は read_file/search_code/run_command/write_file/append_file/replace_text/list_files の具体的な1 tool callとして書いてください。"
        "decompose_tasks は最初の子フレームを開きます。子が戻ったら親は未完了の子タスクを順に open_child_frame で処理し、必要なら子の中でも同じ契約で decompose_tasks してください。"
        "1つだけ局所目的を切り出せば十分な場合だけ open_child_frame を直接使ってください。親が first_action を直接実行できるなら、分解せず直接実行してください。"
        "子フレームで必要な結果または判断材料が揃ったら finish ではなく return_to_parent を使ってください。"
        "finish はタスクが完了した際、またはこれ以上のツール実行が不要で最善の最終回答を出す際にのみ使用してください。"
        "会話への直接回答だけでツールが不要な場合は tool_name=final_answer, tool_args={\"answer\": string} を使用できます。これは runtime が finish として扱います。"
        "tool_result に存在しない事実を主張しないでください。"
        "ユーザーが特定のコマンドの実行を要求した場合は、それらを一つずつ実行してから finish してください。"
        "コードベースを探索する際は、広範な読み取りを行う前に search_code による検索を優先してください。"
    )


def _output_budget_prompt(self) -> str:
    chunk_bytes = int(getattr(self, "tool_content_chunk_bytes", DEFAULT_TOOL_CONTENT_CHUNK_BYTES) or DEFAULT_TOOL_CONTENT_CHUNK_BYTES)
    return (
        "この応答には生成上限があります。必ず JSON を閉じてください。"
        f"write_file と append_file の tool_args.content は1ステップあたり最大 {chunk_bytes} UTF-8 bytes です。"
        "新規ファイルがこの上限内に完全に収まる場合だけ、write_file で全文を書いてください。"
        f"ファイル内容が {chunk_bytes} bytes を超える場合、この応答では先頭または次に続く1 chunk だけを返し、"
        "次ステップで append_file を続けてください。source code の chunk は原則として行境界で終えてください。"
        "JSON を閉じられない長さになりそうなら、コード全文や説明を出さず、現在の chunk だけを返してください。"
        "既存ファイルは必ず read_file で対象を確認し、replace_text で一意に一致する old_text と new_text だけを返してください。"
        "既存ファイル全体を write_file で再生成しないでください。"
        "編集が大きい場合は1回で終えようとせず、1ステップにつき1つの最小編集だけを返し、次ステップで続けてください。"
        "タスクが複数の局所問題に分かれる場合は decompose_tasks で順序付き子タスクに分け、各子フレームは必要な発見を return_to_parent してください。"
    )


def _build_prompt(
    self,
    *,
    goal_text: str,
    recent_events: list[dict[str, Any]],
    extra_prompt: str | None = None,
    steps: list[dict[str, Any]] = None,
    current_phase: str = "FINISH",
    user_message: str = "",
) -> str:
    rendered_events = self._render_action_context_events(recent_events=recent_events, steps=steps or [], user_message=user_message)
    goal_part = goal_text.strip() or "(目標が設定されていません)"
    frame = self.frame_manager.current_frame()
    frame_block = ""
    if frame is not None:
        wm = frame.working_memory
        has_child_return = any(str(event.get("type") or "") == "child_return" for event in frame.session_events[-5:])
        frame_block = (
            "\n\n現在のフレーム状態:\n"
            f"- 目的: {frame.goal}\n"
            f"- 深さ: {frame.depth} / 最大4\n"
            f"- 観測された事実: {json.dumps(wm.observations, ensure_ascii=False)}\n"
            f"- 現在の焦点: {wm.current_focus or '(なし)'}\n"
            f"- 未解決の問い: {json.dumps(wm.unresolved_questions, ensure_ascii=False)}\n"
            f"- 繰り返すべきでない操作: {json.dumps(wm.avoid_repeating, ensure_ascii=False)}\n"
            f"- 子タスク計画: {json.dumps(wm.child_tasks, ensure_ascii=False)}\n"
            f"- 完了した子タスク: {json.dumps(wm.completed_child_tasks, ensure_ascii=False)}\n"
            "\n利用可能なフレーム操作:\n"
            "- decompose_tasks: 複数の局所タスクを work_package として順序付きに計画し、最初の子フレームを開く\n"
            "- open_child_frame: work_package を持つ局所目的だけを子フレームとして開く\n"
            "- return_to_parent: 子フレームで結果または判断材料が揃ったら親フレームに戻る\n"
        )
        next_task = self.frame_manager.next_pending_child_task(frame)
        if next_task:
            frame_block += (
                "\n重要: 未完了の子タスク計画があります。"
                f"次に扱う候補は {json.dumps(next_task, ensure_ascii=False)} です。"
                "親フレームでは、この子タスクを open_child_frame で開くか、全子タスクが不要になった根拠を示して finish してください。\n"
            )
        if frame.depth > 0 and has_child_return:
            frame_block += (
                "\n重要: このフレームは直近で child_return を受け取り済みです。"
                "新しい局所問題を開かず、親が判断できる summary/findings をまとめて return_to_parent してください。\n"
            )
    prompt = (
        f"現在の目標:\n{goal_part}\n\n"
        f"現在のユーザー依頼:\n{user_message.strip() or '(直近の依頼なし)'}\n\n"
        f"LLM作業ディレクトリ:\n{self.execution_root}\n"
        "このターンのファイル操作とコマンド実行は、この専用 workspace 配下で行われます。相対パスを使ってください。\n\n"
        + frame_block
        + "\n"
        "直近のセッションイベント:\n"
        + ("\n".join(rendered_events) if rendered_events else "(履歴なし)")
        + "\n\n直近のリフレクション (失敗からの教訓):\n"
        + self._reflection_prompt_block()
        + "\n\n編集方針:\n"
        + self._output_budget_prompt()
    )
    if current_phase == "DELIBERATE":
        prompt += "\n\n" + self._build_deliberation_note(user_message=user_message, steps=steps or [], recent_events=recent_events)
    elif current_phase == "PLANNING":
        prompt += (
            "\n\n【計画フェーズ（PLANNING）】\n"
            "複雑なタスクが開始されました。最初のアクションとして、焦って修正や実行を行うのではなく、"
            "まずは関連するファイル構成の確認（list_files）や、コードの検索（search_code）を行い、"
            "現状の把握に努めてください。その後、段階的な実行計画を立ててください。"
        )

    prompt += "\n\n最適と思われる次の一手を決定してください。"
    if extra_prompt:
        prompt += f"\n\n実行の優先設定:\n{extra_prompt.strip()}"
    return prompt


def _render_action_context_events(
    self,
    *,
    recent_events: list[dict[str, Any]],
    steps: list[dict[str, Any]],
    user_message: str,
) -> list[str]:
    current_user = str(user_message or "").strip()
    useful_types = {"user_message", "tool_call", "tool_result", "system_note", "planning_note", "task_plan", "child_return"}
    useful_system_codes = {
        "",
        "llm_output_issue",
        "finish_blocked",
        "command_failed",
        "command_blocked",
        "controller_finish",
        "grounding_judge",
    }
    selected: list[dict[str, Any]] = []
    for event in recent_events:
        event_type = str(event.get("type") or "")
        if event_type not in useful_types:
            continue
        if event_type == "user_message" and current_user and str(event.get("content") or "").strip() != current_user:
            continue
        if event_type == "system_note" and str(event.get("code") or "") not in useful_system_codes:
            continue
        selected.append(event)
    selected = selected[-12:]
    rendered: list[str] = []
    for event in selected:
        event_type = str(event.get("type") or "")
        line = f"[{event_type}] "
        if event_type in {"user_message", "system_note", "planning_note"}:
            line += self._compact_context_text(str(event.get("content") or ""), limit=700)
        elif event_type == "tool_call":
            tool_args = dict(event.get("tool_args") or {})
            if "content" in tool_args:
                tool_args["content"] = self._compact_context_text(str(tool_args.get("content") or ""), limit=220)
            if "new_text" in tool_args:
                tool_args["new_text"] = self._compact_context_text(str(tool_args.get("new_text") or ""), limit=220)
            line += f"{event.get('tool_name')} args={json.dumps(tool_args, ensure_ascii=False)}"
        elif event_type == "tool_result":
            line += f"{event.get('tool_name')} -> {self._compact_context_text(str(event.get('content') or ''), limit=1000)}"
        elif event_type == "task_plan":
            line += self._compact_context_text(
                json.dumps(
                    {
                        "tasks": event.get("tasks") or [],
                        "rationale": event.get("rationale") or "",
                    },
                    ensure_ascii=False,
                ),
                limit=1000,
            )
        elif event_type == "child_return":
            line += self._compact_context_text(
                json.dumps(
                    {
                        "summary": event.get("content") or "",
                        "return_payload": event.get("return_payload") or {},
                        "next_child_task": event.get("next_child_task") or None,
                    },
                    ensure_ascii=False,
                ),
                limit=1000,
            )
        rendered.append(line)
    if steps:
        rendered.append("[current_steps] " + self._compact_context_text(json.dumps(steps[-4:], ensure_ascii=False), limit=1400))
    return rendered


def _compact_context_text(self, text: str, *, limit: int) -> str:
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(clean) <= limit:
        return clean
    return f"{clean[:limit]}... [truncated {len(clean) - limit} chars]"


def _build_planning_note(self, *, user_message: str, goal_text: str) -> str:
    requested_commands = self._extract_requested_commands(user_message)
    if requested_commands:
        return (
            "計画: 要求されたコマンドを一つずつ実行し、各結果を確認してから次へ進みます。"
            "すべての要求がカバーされるまで完了しません。"
        )
    haystack = f"{goal_text}\n{user_message}".lower()
    if any(token in haystack for token in {"code", "file", "search", "コード", "ファイル", "検索"}):
        return (
            "計画: まず関連領域を検索または一覧表示し、必要なファイルのみを読み取ります。"
            "完了前に1ステップにつき1つの最小限の変更または結論を出します。"
        )
    return "計画: 最新の証拠を確認し、具体的な次のアクションを一つ実行して結果を検証します。その上で完了が妥当かどうかを判断します。"


def _build_deliberation_note(self, *, user_message: str, steps: list[dict[str, Any]], recent_events: list[dict[str, Any]]) -> str:
    reasons = self._deliberation_reasons(user_message=user_message, steps=steps, recent_events=recent_events)
    reason_text = "、".join(reasons)
    return (
        f"【熟考指示（DELIBERATE）】\n"
        f"重要：現在、プロセスの停滞が検知されています（理由：{reason_text}）。\n\n"
        "これまでの記録を冷静に分析し、なぜシステムに拒否されたのか、あるいはなぜ目的が未達成なのかを特定してください。"
        "単に同じアプローチを繰り返すのではなく、原因（例：余計なメタ情報の出力、ルートの誤り、JSON生成上限、巨大なtool_args.contentなど）を取り除いた新しい実行計画を立ててください。"
        "長い出力が原因の場合は、前回出力の続きを文章で出さず、次の1 tool callで完了できる最小の write_file / append_file / replace_text だけを返してください。"
        "目的が大きすぎる場合は decompose_tasks で順序付きの局所目的に分け、子フレームの発見を return_to_parent して親フレームで次の一手を判断してください。"
    )


def _reflection_prompt_block(self) -> str:
    rows = read_jsonl(self.paths.reflections_path, limit=3)
    if not rows:
        return "(直近のリフレクションはありません)"
    lines = []
    for row in rows:
        reflection = str(row.get("reflection") or "").strip()
        if not reflection:
            continue
        failure_class = str(row.get("failure_class") or "").strip()
        prefix = f"[{failure_class}] " if failure_class else ""
        lines.append(prefix + reflection)
    return "\n".join(lines[-3:]) if lines else "(直近のリフレクションはありません)"
