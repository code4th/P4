from __future__ import annotations

import errno
import difflib
import json
import threading
import webbrowser
from datetime import datetime
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from p1_core.autonomy import AutonomyRuntime


_EVENT_CONDITION = threading.Condition()
_EVENT_SEQUENCE = 0
_LATEST_SNAPSHOT: dict[str, Any] | None = None


def _set_latest_snapshot(snapshot: dict[str, Any]) -> None:
    global _EVENT_SEQUENCE, _LATEST_SNAPSHOT
    with _EVENT_CONDITION:
        _LATEST_SNAPSHOT = snapshot
        _EVENT_SEQUENCE += 1
        _EVENT_CONDITION.notify_all()


def _latest_snapshot() -> dict[str, Any] | None:
    with _EVENT_CONDITION:
        if _LATEST_SNAPSHOT is None:
            return None
        return _LATEST_SNAPSHOT


def _read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    return json.loads(path.read_text(encoding="utf-8"))


def _usage_total(usage: dict[str, Any], *, primary: str, alternate: str | None = None) -> int:
    value = usage.get(primary)
    if value is None and alternate:
        value = usage.get(alternate)
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _read_text(path: str | None) -> str | None:
    if not path:
        return None
    file_path = Path(path)
    if not file_path.exists():
        return None
    try:
        return file_path.read_text(encoding="utf-8")
    except Exception:
        return None


def _metaagent_change_preview(run: dict[str, Any], *, max_lines: int = 12) -> str:
    target = _read_text(str(run.get("target")))
    backup = _read_text(str(run.get("backup_path")))
    if target is None or backup is None:
        return "変更差分はまだ確認できません。"
    diff = list(
        difflib.unified_diff(
            backup.splitlines(),
            target.splitlines(),
            fromfile="before",
            tofile="after",
            lineterm="",
        )
    )
    if not diff:
        return "差分はありません。"
    limited = diff[:max_lines]
    if len(diff) > max_lines:
        limited.append("...")
    return "\n".join(limited)


def _health_body() -> bytes:
    return b"ok"


def dashboard_snapshot(root: Path, *, history_limit: int = 12) -> dict[str, Any]:
    runtime = AutonomyRuntime(root=root, local_llm_backend=object())
    state = runtime.show_state()
    history = runtime.ticks_path
    recent_ticks: list[dict[str, Any]] = []
    latest_row: dict[str, Any] = {}
    if history.exists():
        rows = [json.loads(line) for line in history.read_text(encoding="utf-8").splitlines() if line.strip()]
        latest_row = rows[-1] if rows else {}
        for row in rows[-history_limit:]:
            executed: dict[str, Any] | None = None
            if row.get("reply"):
                executed = {
                    "type": "reply",
                    "backend": row.get("reply_backend"),
                    "message_id": row.get("message_id"),
                }
            elif row.get("action"):
                action = row.get("action", {})
                executed = {
                    "type": "action",
                    "kind": action.get("kind"),
                    "status": action.get("status"),
                    "action_id": action.get("action_id"),
                }
            elif row.get("proposal"):
                proposal = row.get("proposal", {})
                executed = {
                    "type": "capability_proposal",
                    "proposal_id": proposal.get("proposal_id"),
                    "gap_id": proposal.get("gap_id"),
                }
            elif row.get("review"):
                review = row.get("review", {})
                governance = review.get("governance", {})
                proposal = governance.get("proposal", {})
                executed = {
                    "type": "capability_review",
                    "proposal_id": proposal.get("proposal_id"),
                    "next_step": governance.get("next_step"),
                }
            elif row.get("execution"):
                execution = row.get("execution", {})
                executed = {
                    "type": "capability_execution",
                    "proposal_id": execution.get("proposal_id"),
                    "status": execution.get("status"),
                }
            elif row.get("growth_result"):
                gr = row.get("growth_result", {})
                executed = {
                    "type": "growth_loop",
                    "records_written": gr.get("records_written", 0),
                    "proposals_written": gr.get("proposals_written", 0),
                }
            elif row.get("status") == "growth_loop_error":
                executed = {
                    "type": "growth_loop_error",
                    "error": row.get("error", "unknown"),
                }
            elif row.get("status") == "idle":
                executed = {"type": "idle"}
            recent_ticks.append(
                {
                    "timestamp": row.get("timestamp"),
                    "status": row.get("status"),
                    "thought": row.get("summary") or row.get("last_tick_summary") or row.get("status"),
                    "next_wake_at": row.get("next_wake_at"),
                    "trigger_kind": _trigger_kind_for_row(row),
                    "executed": executed,
                }
            )
        latest_initiative = latest_row.get("initiative") or {}
        latest_heartbeat = latest_row.get("heartbeat") or {}
        if not state.get("recentInitiatives") and latest_initiative:
            state["recentInitiatives"] = [latest_initiative]
        if not state.get("recentHeartbeats") and latest_heartbeat:
            state["recentHeartbeats"] = [latest_heartbeat]
        if state.get("llmUsage") is None:
            state["llmUsage"] = _read_json(root / "state" / "budgets" / "llm-usage.json", {})

    return {
        "generated_at": datetime.now().isoformat(),
        "root": str(root),
        "state": state,
        "history": recent_ticks,
        "recent_reports": _read_json(root / "state" / "reports" / "daily" / "latest.json", {}),
    }


def _trigger_kind_for_row(row: dict[str, Any]) -> str:
    if row.get("status") == "sleeping":
        return "予定起床"
    if row.get("reply"):
        return "受信箱トリガー"
    if row.get("action"):
        return "アクショントリガー"
    if row.get("proposal") or row.get("review") or row.get("execution"):
        return "能力タスクトリガー"
    if row.get("status") in {"growth_loop_completed", "growth_loop_error"}:
        return "成長ループトリガー"
    if row.get("status") in {"replied", "observed", "decided", "executed"}:
        return "不定期トリガー"
    return "不定期トリガー"


def render_dashboard_html(snapshot: dict[str, Any]) -> str:
    state = snapshot["state"]
    history = snapshot["history"]
    recent_gaps = state.get("recentCapabilityGaps", [])
    recent_tasks = state.get("recentCapabilityTasks", [])
    recent_conversation = state.get("recentConversation", [])
    recent_heartbeats = state.get("recentHeartbeats", [])
    recent_initiatives = state.get("recentInitiatives", [])
    recent_metaagent_runs = state.get("recentMetaagentRuns", [])
    llm_usage = state.get("llmUsage", {})
    llm_local_calls = _usage_total(llm_usage, primary="local_daily", alternate="local")
    llm_openclaw_calls = _usage_total(llm_usage, primary="openclaw_daily", alternate="openclaw")
    llm_local_3h = _usage_total(llm_usage, primary="local_3h", alternate="local_window")
    llm_openclaw_3h = _usage_total(llm_usage, primary="openclaw_3h", alternate="openclaw_window")
    growth_state = state.get("growthLoopState", {})
    knowledge_counts = state.get("knowledgeStateCounts", {})
    knowledge_total = int(state.get("knowledgeRecordCount", 0))
    growth_last_error = str(growth_state.get("last_error") or "")
    growth_last_success = str(growth_state.get("last_success_at") or "なし")
    growth_processed = int(growth_state.get("last_processed_index", 0))
    total_ticks = len(history)
    purpose = state.get("purpose", {})
    coordination = state.get("coordination", {})
    current_focus = escape(str(state.get("current_focus") or "idle"))
    next_wake = escape(str(state.get("next_wake_at") or "none"))
    mode = escape(str(state.get("mode") or "unknown"))
    last_tick_summary = escape(str(state.get("last_tick_summary") or "none"))
    inbox_counts = state.get("inboxCounts", {})
    action_counts = state.get("actionCounts", {})
    task_counts = state.get("capabilityTaskCounts", {})
    quiet_reason = "P1は待機中です。いまは何もキューにありません。"
    if inbox_counts.get("queued", 0) > 0:
        quiet_reason = "P1は受信箱の仕事を待っています。"
    elif action_counts.get("queued", 0) > 0:
        quiet_reason = "P1はキュー済みのアクション実行を待っています。"
    elif task_counts.get("pending", 0) > 0 or task_counts.get("in_progress", 0) > 0 or task_counts.get("deferred", 0) > 0:
        quiet_reason = "P1は能力タスクの進行待ちです。"

    meaningful_history = [item for item in history if item.get("status") not in {"sleeping"}]
    latest_meaningful = meaningful_history[-1] if meaningful_history else None
    latest_tick = history[-1] if history else None
    wake_reason = "次の起床時刻はまだありません。"
    if next_wake != "none":
        wake_reason = "新しい仕事が来なければ、予定時刻に再起動します。"
    latest_history = history[0] if history else None
    latest_trigger = "予定起床"
    if latest_meaningful:
        latest_trigger = str(latest_meaningful.get("trigger_kind") or "不定期トリガー")

    def block(title: str, body: str) -> str:
        return f"""
        <section class="card">
          <h2>{escape(title)}</h2>
          {body}
        </section>
        """

    def pill(label: str, value: Any) -> str:
        return f'<span class="pill"><strong>{escape(label)}:</strong> {escape(str(value))}</span>'

    history_rows = []
    for item in history:
        executed = item.get("executed")
        executed_label = "none"
        if executed:
            executed_type = executed.get("type")
            if executed_type == "reply":
                executed_label = f"reply via {executed.get('backend') or 'local'}"
            elif executed_type == "action":
                executed_label = f"action {executed.get('kind')} ({executed.get('status')})"
            elif executed_type == "capability_proposal":
                executed_label = f"proposal {executed.get('proposal_id')}"
            elif executed_type == "capability_review":
                executed_label = f"review {executed.get('proposal_id')}"
            elif executed_type == "capability_execution":
                executed_label = f"execution {executed.get('proposal_id')} ({executed.get('status')})"
            elif executed_type == "growth_loop":
                executed_label = f"成長ループ: {executed.get('records_written', 0)}件の知識 / {executed.get('proposals_written', 0)}件の提案"
            elif executed_type == "growth_loop_error":
                executed_label = f"成長ループエラー: {executed.get('error', 'unknown')}"
            else:
                executed_label = executed_type
        history_rows.append(
            f"""
            <div class="row">
              <div class="row-top">
                <span>{escape(str(item.get('timestamp') or 'unknown'))}</span>
                <span class="status">{escape(str(item.get('status') or 'unknown'))}</span>
              </div>
              <div class="row-body">
                <div><strong>Thought:</strong> {escape(str(item.get('thought') or ''))}</div>
                <div><strong>Executed:</strong> {escape(executed_label)}</div>
                <div><strong>Next wake:</strong> {escape(str(item.get('next_wake_at') or 'none'))}</div>
              </div>
            </div>
            """
        )

    gap_rows = []
    for gap in recent_gaps[:8]:
        gap_rows.append(
            f"<li><strong>{escape(str(gap.get('title') or gap.get('gap_id') or 'gap'))}</strong> "
            f"<span>{escape(str(gap.get('source') or 'unknown'))}</span></li>"
        )

    task_rows = []
    for task in recent_tasks[:8]:
        task_rows.append(
            f"<li><strong>{escape(str(task.get('title') or task.get('task_id') or 'task'))}</strong> "
            f"<span>{escape(str(task.get('status') or 'unknown'))}</span></li>"
        )

    convo_rows = []
    for msg in recent_conversation[-8:]:
        convo_rows.append(
            f"<li><strong>{escape(str(msg.get('role') or 'message'))}</strong>: {escape(str(msg.get('content') or ''))}</li>"
        )

    usage_rows = "".join(
        f"<li><strong>{escape(str(k))}</strong>: {escape(str(v))}</li>" for k, v in sorted(llm_usage.items())
    )
    heartbeat_rows = []
    for item in recent_heartbeats[-5:]:
        heartbeat_rows.append(
            f"<li><strong>{escape(str(item.get('timestamp') or 'unknown'))}</strong>: {escape(str(item.get('note') or ''))}</li>"
        )
    initiative_rows = []
    for item in recent_initiatives[-5:]:
        initiative_rows.append(
            f"<li><strong>{escape(str(item.get('timestamp') or 'unknown'))}</strong>: {escape(str(item.get('note') or ''))}</li>"
        )
    history_html = "".join(history_rows) or '<div class="muted">まだ履歴はありません。</div>'
    latest_meaningful_html = (
        f"<div id='p1-latest-meaningful'><strong>{escape(str(latest_meaningful['timestamp']) if latest_meaningful else 'なし')}</strong></div><div class='muted'>{escape(str(latest_meaningful['thought']) if latest_meaningful else 'なし')}</div>"
        if latest_meaningful
        else '<div id="p1-latest-meaningful" class="muted">まだ意味のある tick はありません。</div>'
    )
    gaps_html = "<ul>" + "".join(gap_rows) + "</ul>" if gap_rows else '<div class="muted">ギャップはありません。</div>'
    tasks_html = "<ul>" + "".join(task_rows) + "</ul>" if task_rows else '<div class="muted">タスクはありません。</div>'
    conversation_html = "<ul>" + "".join(convo_rows) + "</ul>" if convo_rows else '<div class="muted">会話はありません。</div>'
    usage_html = "<ul>" + usage_rows + "</ul>" if usage_rows else '<div class="muted">LLM 使用はまだありません。</div>'
    heartbeat_html = "<ul>" + "".join(heartbeat_rows) + "</ul>" if heartbeat_rows else '<div class="muted">heartbeat はまだありません。</div>'
    initiative_html = "<ul>" + "".join(initiative_rows) + "</ul>" if initiative_rows else '<div class="muted">initiative はまだありません。</div>'
    metaagent_rows = []
    for run in recent_metaagent_runs[-5:]:
        change_preview = _metaagent_change_preview(run)
        metaagent_rows.append(
            f"""
            <li>
              <strong>{escape(str(run.get('timestamp') or 'unknown'))}</strong>:
              {escape('成功' if run.get('success') else '失敗')} /
              {escape(str(run.get('target_name') or run.get('target') or 'unknown'))} /
              {escape(str(run.get('message') or ''))}
              <div class="muted">判断: {escape(str(run.get('purpose') or ''))}</div>
              <div class="muted">制約: {escape(str(run.get('constraints') or ''))}</div>
              <div class="muted">backend/model: {escape(str(run.get('backend') or 'unknown'))} / {escape(str(run.get('model') or 'unknown'))}</div>
              <div class="muted">backup: {escape(str(run.get('backup_path') or 'なし'))}</div>
              <pre class="diff">{escape(change_preview)}</pre>
            </li>
            """
        )
    self_repair_html = "<ul>" + "".join(metaagent_rows) + "</ul>" if metaagent_rows else '<div class="muted">self-repair はまだありません。</div>'
    latest_heartbeat = recent_heartbeats[-1] if recent_heartbeats else {}
    latest_heartbeat_note = str(latest_heartbeat.get("note") or "heartbeat はまだありません。")
    latest_heartbeat_reason = str(latest_heartbeat.get("reason") or "unknown")
    latest_initiative = recent_initiatives[-1] if recent_initiatives else {}
    latest_initiative_note = str(latest_initiative.get("note") or "initiative はまだありません。")
    latest_initiative_reason = str(latest_initiative.get("reason") or "unknown")
    latest_initiative_candidates = latest_initiative.get("proposal", {}).get("candidates", [])
    latest_initiative_summary = str((latest_initiative.get("proposal") or {}).get("summary") or "initiative はまだありません。")
    latest_initiative_next_step = str((latest_initiative.get("proposal") or {}).get("next_step") or "なし")
    latest_initiative_selection_reason = str((latest_initiative.get("proposal") or {}).get("selection_reason") or "なし")
    latest_initiative_problem = str((latest_initiative.get("proposal") or {}).get("problem_statement") or "なし")
    latest_initiative_diagnosis = str((latest_initiative.get("proposal") or {}).get("diagnosis") or "なし")
    latest_thought = str((latest_tick.get("thought") if latest_tick else "なし"))
    latest_metaagent = recent_metaagent_runs[-1] if recent_metaagent_runs else {}
    metaagent_recent_count = len(recent_metaagent_runs)
    metaagent_success_count = sum(1 for row in recent_metaagent_runs if row.get("success"))
    metaagent_failure_count = sum(1 for row in recent_metaagent_runs if not row.get("success"))
    report_lines = [
        f"目的: {str(purpose.get('statement') or '未設定')}",
        f"現在状態: {str(state.get('last_tick_summary') or state.get('status') or '待機中')}",
        f"最後の実行: {str(latest_meaningful['executed']['type']) if latest_meaningful and latest_meaningful.get('executed') else 'なし'}",
        f"総 tick 数: {total_ticks}",
        f"local LLM 回数: {llm_local_calls}",
        f"OpenClaw LLM 回数: {llm_openclaw_calls}",
        f"local LLM 3h: {llm_local_3h}",
        f"OpenClaw LLM 3h: {llm_openclaw_3h}",
        f"最後の heartbeat: {latest_heartbeat_note}",
        f"heartbeat 理由: {latest_heartbeat_reason}",
        f"最後の initiative: {latest_initiative_note}",
        f"initiative 理由: {latest_initiative_reason}",
        f"initiative 候補数: {len(latest_initiative_candidates)}",
        f"initiative 要約: {latest_initiative_summary}",
        f"initiative 問題: {latest_initiative_problem}",
        f"initiative 診断: {latest_initiative_diagnosis}",
        f"initiative 次手: {latest_initiative_next_step}",
        f"initiative 選択理由: {latest_initiative_selection_reason}",
        f"self-repair 回数: {metaagent_recent_count}",
        f"self-repair 成功: {metaagent_success_count}",
        f"self-repair 失敗: {metaagent_failure_count}",
        f"self-repair 最新対象: {str(latest_metaagent.get('target_name') or latest_metaagent.get('target') or 'なし')}",
        f"self-repair 最新結果: {str(latest_metaagent.get('message') or 'なし')}",
        f"成長ループ処理済み: {growth_processed} 観察",
        f"成長ループ最終成功: {growth_last_success}",
        f"成長ループ最終エラー: {growth_last_error or 'なし'}",
        f"知識レコード合計: {knowledge_total}",
        f"知識状態: {', '.join(f'{k}={v}' for k, v in sorted(knowledge_counts.items())) if knowledge_counts else 'なし'}",
        f"最近の思考: {latest_thought}",
        f"次の起床: {str(next_wake)}",
    ]
    report_html = "".join(f"<li>{escape(line)}</li>" for line in report_lines)

    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>P1 ダッシュボード</title>
  <style>
    :root {{
      --bg: #0b1020;
      --panel: #111833;
      --panel-2: #172043;
      --text: #e7ecff;
      --muted: #9aa7d1;
      --accent: #72a1ff;
      --accent-2: #7bf0c8;
      --danger: #ff8a8a;
      --border: rgba(255,255,255,0.08);
    }}
    body {{
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, sans-serif;
      background: radial-gradient(circle at top, #18224d 0%, var(--bg) 50%);
      color: var(--text);
    }}
    header {{
      padding: 24px 28px 8px;
      border-bottom: 1px solid var(--border);
      background: rgba(0,0,0,0.18);
      backdrop-filter: blur(10px);
      position: sticky;
      top: 0;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 28px;
    }}
    .sub {{
      color: var(--muted);
      font-size: 14px;
    }}
    .subline {{
      margin-top: 6px;
      color: var(--muted);
      font-size: 13px;
    }}
    .hero {{
      display: grid;
      gap: 10px;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      padding: 16px 24px 0;
    }}
    .hero-card {{
      background: rgba(255,255,255,0.05);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 12px 14px;
    }}
    .hero-card .label {{
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 4px;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}
    .hero-card .value {{
      font-size: 18px;
      font-weight: 700;
      line-height: 1.3;
    }}
    .callout {{
      margin: 16px 24px 0;
      padding: 14px 16px;
      border-radius: 14px;
      border: 1px solid rgba(123, 240, 200, 0.22);
      background: rgba(123, 240, 200, 0.08);
      color: var(--text);
      line-height: 1.5;
    }}
    .countdown {{
      font-size: 22px;
      font-weight: 800;
      color: var(--accent-2);
      letter-spacing: 0.02em;
    }}
    .trigger {{
      color: var(--accent);
      font-weight: 800;
    }}
    .wrap {{
      padding: 20px 24px 40px;
      display: grid;
      gap: 16px;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
    }}
    .card {{
      background: linear-gradient(180deg, var(--panel), var(--panel-2));
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 16px;
      box-shadow: 0 8px 30px rgba(0,0,0,0.22);
    }}
    .card h2 {{
      margin: 0 0 12px;
      font-size: 16px;
    }}
    .meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 12px;
    }}
    .pill {{
      display: inline-flex;
      gap: 6px;
      padding: 6px 10px;
      border-radius: 999px;
      background: rgba(255,255,255,0.06);
      border: 1px solid var(--border);
      color: var(--text);
      font-size: 13px;
    }}
    .row {{
      padding: 10px 0;
      border-top: 1px solid var(--border);
    }}
    .row:first-child {{
      border-top: none;
      padding-top: 0;
    }}
    .row-top {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 6px;
    }}
    .status {{
      color: var(--accent-2);
      font-weight: 700;
    }}
    ul {{
      margin: 0;
      padding-left: 18px;
    }}
    li {{
      margin: 6px 0;
    }}
    strong {{
      color: var(--text);
    }}
    .wide {{
      grid-column: 1 / -1;
    }}
    .muted {{
      color: var(--muted);
    }}
    .diff {{
      white-space: pre-wrap;
      word-break: break-word;
      margin: 8px 0 0;
      padding: 10px;
      border-radius: 12px;
      background: rgba(0,0,0,0.22);
      border: 1px solid var(--border);
      color: #dce6ff;
      font-size: 12px;
      line-height: 1.45;
    }}
    code {{
      background: rgba(255,255,255,0.08);
      padding: 2px 6px;
      border-radius: 6px;
    }}
  </style>
</head>
<body>
  <header>
    <h1>P1 Dashboard</h1>
    <div class="sub">OpenClaw は土台、P1 本体は external core。ローカル優先の自律運用。生成時刻 {escape(snapshot["generated_at"])}</div>
    <div class="subline">画面は P1 の通知で更新します。手動リロードでも最新を表示できます。起床までの残り時間だけは毎秒更新します。</div>
    <div class="subline">P1 の目的: {escape(str(purpose.get("statement") or "未設定"))}</div>
    <div class="subline">coordination の出所: {escape(str(coordination.get("source_of_truth") or "runtime-state.json"))}</div>
    <div class="meta">
      {pill("モード", mode)}
      {pill("注目", current_focus)}
      {pill("次回起床", next_wake)}
      {pill("直近の要約", last_tick_summary)}
      {pill("local LLM", llm_local_calls)}
      {pill("OpenClaw LLM", llm_openclaw_calls)}
      {pill("self-repair", metaagent_recent_count)}
      {pill("待機アクション", state.get("actionCounts", {}).get("queued", 0))}
      {pill("未処理タスク", state.get("capabilityTaskCounts", {}).get("pending", 0))}
      {pill("知識レコード", knowledge_total)}
      {pill("成長ループ処理済", growth_processed)}
    </div>
  </header>
  {block("P1 の進行報告", f"<ul>{report_html}</ul>")}
  <section class="hero">
    <div class="hero-card">
      <div class="label">今の状態</div>
      <div class="value" id="p1-current-status">{escape(str(state.get("status") or state.get("last_tick_summary") or "待機中"))}</div>
    </div>
    <div class="hero-card">
      <div class="label">静かな理由</div>
      <div class="value" id="p1-quiet-reason">{escape(quiet_reason)}</div>
    </div>
    <div class="hero-card">
      <div class="label">最後に考えたこと</div>
      <div class="value" id="p1-last-thought">{escape(latest_thought)}</div>
    </div>
    <div class="hero-card">
      <div class="label">最後の実行</div>
      <div class="value" id="p1-last-execution">{escape(str(latest_meaningful["executed"]["type"]) if latest_meaningful and latest_meaningful.get("executed") else "なし")}</div>
    </div>
    <div class="hero-card">
      <div class="label">起床まで</div>
      <div class="countdown" id="p1-countdown">{escape(next_wake)}</div>
    </div>
    <div class="hero-card">
      <div class="label">トリガー種別</div>
      <div class="value trigger" id="p1-trigger-kind">{escape(str(latest_trigger))}</div>
    </div>
  </section>
  <div class="callout" id="p1-callout">
    <strong>直近の実行:</strong> {escape(str(latest_tick["executed"]["type"]) if latest_tick and latest_tick.get("executed") else "なし")}<br />
    <strong>静かに見える理由:</strong> {escape(quiet_reason)}<br />
    <strong>起床計画:</strong> {escape(wake_reason)}<br />
    <strong>実績:</strong> tick {escape(str(total_ticks))} 回 / local LLM {escape(str(llm_local_calls))} 回 / OpenClaw LLM {escape(str(llm_openclaw_calls))} 回<br />
    <strong>考えた候補:</strong> {escape(str(len(latest_initiative_candidates)))} 個<br />
    <strong>最後の initiative:</strong> {escape(latest_initiative_summary)}<br />
    <strong>問題:</strong> {escape(latest_initiative_problem)}<br />
    <strong>診断:</strong> {escape(latest_initiative_diagnosis)}<br />
    <strong>次の一手:</strong> {escape(latest_initiative_next_step)}<br />
    <strong>選択理由:</strong> {escape(latest_initiative_selection_reason)}<br />
    <strong>読み取り:</strong> 今は止まっているのではなく、保守的に待機しつつ内部候補を残しています。
  </div>
  <main class="wrap">
    {block("自律履歴", f'<div id="p1-history">{history_html}</div>')}
    {block("最後の意味ある tick", latest_meaningful_html)}
    {block("最近の能力ギャップ", f'<div id="p1-gaps">{gaps_html}</div>')}
    {block("最近の能力タスク", f'<div id="p1-tasks">{tasks_html}</div>')}
    {block("最近の会話", f'<div id="p1-conversation">{conversation_html}</div>')}
    {block("最近の heartbeat", f'<div id="p1-heartbeat">{heartbeat_html}</div>')}
    {block("最近の initiative", f'<div id="p1-initiative">{initiative_html}</div>')}
    {block("最近の self-repair", f'<div id="p1-metaagent">{self_repair_html}</div>')}
    {block("成長ループ (Growth Loop)", f'''<div id="p1-growth">
      <ul>
        <li><strong>処理済み観察数:</strong> {escape(str(growth_processed))}</li>
        <li><strong>最後の成功:</strong> {escape(growth_last_success)}</li>
        <li><strong>最後のエラー:</strong> {escape(growth_last_error) if growth_last_error else "なし"}</li>
      </ul>
      <h3 style="margin-top:12px;font-size:14px;">知識ストア</h3>
      <ul>
        <li><strong>合計レコード:</strong> {escape(str(knowledge_total))}</li>
        {"".join(f'<li><strong>{escape(str(k))}:</strong> {escape(str(v))}</li>' for k, v in sorted(knowledge_counts.items())) if knowledge_counts else '<li class="muted">まだ知識レコードがありません。</li>'}
      </ul>
    </div>''')}
    {block("LLM 使用量", f'<div id="p1-usage">{usage_html}</div>')}
  </main>
  <script>
    let latestSnapshot = {json.dumps(snapshot, ensure_ascii=False)};
    const eventSource = new EventSource("/api/events");

    function formatCountdown(value) {{
      if (!value || value === "none") return "なし";
      const target = new Date(value);
      if (Number.isNaN(target.getTime())) return "なし";
      const delta = target.getTime() - Date.now();
      if (delta <= 0) {{
        return "P1通知待ち";
      }}
      const totalSeconds = Math.floor(delta / 1000);
      const hours = Math.floor(totalSeconds / 3600);
      const minutes = Math.floor((totalSeconds % 3600) / 60);
      const seconds = totalSeconds % 60;
      const parts = [];
      if (hours > 0) parts.push(`${{hours}}時間`);
      if (minutes > 0 || hours > 0) parts.push(`${{minutes}}分`);
      parts.push(`${{seconds}}秒`);
      return parts.join("");
    }}

    function renderTick(item) {{
      const executed = item.executed;
      let executedLabel = "なし";
      if (executed) {{
        if (executed.type === "reply") executedLabel = `reply via ${{executed.backend || "local"}}`;
        else if (executed.type === "action") executedLabel = `action ${{executed.kind}} (${{executed.status}})`;
        else if (executed.type === "capability_proposal") executedLabel = `proposal ${{executed.proposal_id}}`;
        else if (executed.type === "capability_review") executedLabel = `review ${{executed.proposal_id}}`;
        else if (executed.type === "capability_execution") executedLabel = `execution ${{executed.proposal_id}} (${{executed.status}})`;
        else if (executed.type === "growth_loop") executedLabel = `成長ループ: ${{executed.records_written}}件の知識 / ${{executed.proposals_written}}件の提案`;
        else if (executed.type === "growth_loop_error") executedLabel = `成長ループエラー: ${{executed.error}}`;
        else executedLabel = executed.type;
      }}
      return `
        <div class="row">
          <div class="row-top">
            <span>${{item.timestamp || "unknown"}}</span>
            <span class="status">${{item.status || "unknown"}}</span>
          </div>
          <div class="row-body">
            <div><strong>思考:</strong> ${{item.thought || ""}}</div>
            <div><strong>実行:</strong> ${{executedLabel}}</div>
            <div><strong>トリガー:</strong> ${{item.trigger_kind || "不定期トリガー"}}</div>
            <div><strong>次回起床:</strong> ${{item.next_wake_at || "なし"}}</div>
          </div>
        </div>
      `;
    }}

    function renderList(selector, rows, emptyText) {{
      const el = document.querySelector(selector);
      if (!el) return;
      el.innerHTML = rows.length ? `<ul>${{rows.join("")}}</ul>` : `<div class="muted">${{emptyText}}</div>`;
    }}

    function applySnapshot(snapshot) {{
      latestSnapshot = snapshot;
      const state = snapshot.state || {{}};
      const history = snapshot.history || [];
      const meaningful = history.find((item) => item.status !== "sleeping") || history[0] || null;
      const latest = history[history.length - 1] || null;
      document.getElementById("p1-current-status").textContent = state.status || state.last_tick_summary || "待機中";
      document.getElementById("p1-quiet-reason").textContent = state.inboxCounts?.queued > 0
        ? "P1は受信箱の仕事を待っています。"
        : state.actionCounts?.queued > 0
        ? "P1はキュー済みのアクション実行を待っています。"
        : (state.capabilityTaskCounts?.pending > 0 || state.capabilityTaskCounts?.in_progress > 0 || state.capabilityTaskCounts?.deferred > 0)
        ? "P1は能力タスクの進行待ちです。"
        : "P1は待機中です。いまは何もキューにありません。";
      document.getElementById("p1-last-thought").textContent = meaningful ? (meaningful.thought || "なし") : "なし";
      document.getElementById("p1-last-execution").textContent = meaningful && meaningful.executed ? (meaningful.executed.type || "なし") : "なし";
      document.getElementById("p1-trigger-kind").textContent = meaningful ? (meaningful.trigger_kind || "不定期トリガー") : "予定起床";
      document.getElementById("p1-countdown").textContent = formatCountdown(state.next_wake_at);
      const latestMeaningful = document.getElementById("p1-latest-meaningful");
      if (latestMeaningful) {{
        latestMeaningful.innerHTML = meaningful
          ? `<strong>${{meaningful.timestamp || "なし"}}</strong><div class="muted">${{meaningful.thought || "なし"}}</div>`
          : '<div class="muted">まだ意味のある tick はありません。</div>';
      }}
      const callout = document.getElementById("p1-callout");
      if (callout) {{
        callout.innerHTML = `
          <strong>直近の実行:</strong> ${{latest && latest.executed ? latest.executed.type : "なし"}}<br />
          <strong>静かに見える理由:</strong> ${{document.getElementById("p1-quiet-reason").textContent}}<br />
          <strong>起床計画:</strong> ${{state.next_wake_at ? "次の P1 通知を待っています。" : "次の起床時刻はまだありません。"}}<br />
          <strong>実績:</strong> tick ${{history.length}} 回 / local LLM ${{usage.local_daily ?? usage.local ?? 0}} 回 / OpenClaw LLM ${{usage.openclaw_daily ?? usage.openclaw ?? 0}} 回<br />
          <strong>考えた候補:</strong> ${{(state.recentInitiatives || []).slice(-1)[0]?.proposal?.candidates?.length || 0}} 個<br />
          <strong>最後の initiative:</strong> ${{(state.recentInitiatives || []).slice(-1)[0]?.proposal?.summary || "initiative はまだありません。"}}<br />
          <strong>問題:</strong> ${{(state.recentInitiatives || []).slice(-1)[0]?.proposal?.problem_statement || "なし"}}<br />
          <strong>診断:</strong> ${{(state.recentInitiatives || []).slice(-1)[0]?.proposal?.diagnosis || "なし"}}<br />
          <strong>次の一手:</strong> ${{(state.recentInitiatives || []).slice(-1)[0]?.proposal?.next_step || "なし"}}<br />
          <strong>選択理由:</strong> ${{(state.recentInitiatives || []).slice(-1)[0]?.proposal?.selection_reason || "なし"}}<br />
          <strong>読み取り:</strong> 今は止まっているのではなく、保守的に待機しつつ内部候補を残しています。
        `;
      }}
      renderList("#p1-history", history.map(renderTick), "まだ履歴はありません。");
      renderList("#p1-gaps", (state.recentCapabilityGaps || []).slice(0, 8).map((gap) => `<li><strong>${{gap.title || gap.gap_id || "gap"}}</strong> <span>${{gap.source || "unknown"}}</span></li>`), "ギャップはありません。");
      renderList("#p1-tasks", (state.recentCapabilityTasks || []).slice(0, 8).map((task) => `<li><strong>${{task.title || task.task_id || "task"}}</strong> <span>${{task.status || "unknown"}}</span></li>`), "タスクはありません。");
      renderList("#p1-conversation", (state.recentConversation || []).slice(-8).map((msg) => `<li><strong>${{msg.role || "message"}}</strong>: ${{msg.content || ""}}</li>`), "会話はありません。");
      renderList("#p1-metaagent", (state.recentMetaagentRuns || []).slice(-8).map((run) => `<li><strong>${{run.timestamp || "unknown"}}</strong>: ${{run.success ? "成功" : "失敗"}} / ${{run.target_name || run.target || "unknown"}} / ${{run.message || ""}}</li>`), "self-repair はまだありません。");
      const usage = state.llmUsage || {{}};
      renderList("#p1-usage", Object.keys(usage).sort().map((key) => `<li><strong>${{key}}</strong>: ${{usage[key]}}</li>`), "LLM 使用はまだありません。");
    }}

    applySnapshot(latestSnapshot);
    eventSource.addEventListener("snapshot", (event) => {{
      try {{
        applySnapshot(JSON.parse(event.data));
      }} catch (error) {{
        console.error("failed to apply snapshot", error);
      }}
    }});
    eventSource.addEventListener("ping", () => {{}});
    setInterval(() => {{
      const nextWake = latestSnapshot?.state?.next_wake_at;
      document.getElementById("p1-countdown").textContent = formatCountdown(nextWake);
    }}, 1000);
  </script>
</body>
</html>
"""


class _DashboardHandler(BaseHTTPRequestHandler):
    root: Path

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/api/health"):
            body = _health_body()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        snapshot = dashboard_snapshot(self.root)
        if self.path in {"/", "/index.html"}:
            body = render_dashboard_html(snapshot).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/api/snapshot"):
            body = json.dumps(snapshot, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/api/events"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            last_seen = -1
            try:
                while True:
                    with _EVENT_CONDITION:
                        _EVENT_CONDITION.wait_for(lambda: _EVENT_SEQUENCE > last_seen, timeout=30)
                        if _EVENT_SEQUENCE <= last_seen:
                            self.wfile.write(b"event: ping\ndata: keep-alive\n\n")
                            self.wfile.flush()
                            continue
                        last_seen = _EVENT_SEQUENCE
                        current = _LATEST_SNAPSHOT
                    if current is None:
                        continue
                    body = json.dumps(current, ensure_ascii=False).encode("utf-8")
                    self.wfile.write(b"event: snapshot\n")
                    self.wfile.write(b"data: ")
                    self.wfile.write(body)
                    self.wfile.write(b"\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                return
        if self.path == "/api/notify":
            self.send_error(405, "method not allowed")
            return
        self.send_error(404, "not found")

    def do_HEAD(self) -> None:  # noqa: N802
        if self.path.startswith("/api/health"):
            body = _health_body()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return
        snapshot = dashboard_snapshot(self.root)
        if self.path in {"/", "/index.html"}:
            body = render_dashboard_html(snapshot).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return
        if self.path.startswith("/api/snapshot"):
            body = json.dumps(snapshot, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return
        if self.path.startswith("/api/events"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            return
        self.send_error(404, "not found")

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/notify":
            self.send_error(404, "not found")
            return
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            self.send_error(400, "invalid json")
            return
        _set_latest_snapshot(payload)
        body = json.dumps({"ok": True}, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return


def serve_dashboard(root: Path, *, host: str = "127.0.0.1", port: int = 8899) -> None:
    runtime_state_path = root / "state" / "autonomy" / "runtime-state.json"
    if runtime_state_path.exists():
        try:
            runtime_state = json.loads(runtime_state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            runtime_state = {}
    else:
        runtime_state = {}
    coordination = dict(runtime_state.get("coordination", {}))
    coordination.setdefault("source_of_truth", "runtime-state.json")
    coordination["dashboard_notify_url"] = f"http://{host}:{port}/api/notify"
    coordination["dashboard_health_url"] = f"http://{host}:{port}/api/health"
    coordination.setdefault("update_policy", "push_on_tick")
    coordination.setdefault("single_writer", "autonomy_runtime")
    runtime_state["coordination"] = coordination
    runtime_state_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_state_path.write_text(json.dumps(runtime_state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    handler = type("P1DashboardHandler", (_DashboardHandler,), {"root": root})
    url = f"http://{host}:{port}"
    try:
        server = ThreadingHTTPServer((host, port), handler)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            print(f"P1 dashboard already running at {url}", flush=True)
            try:
                webbrowser.open(url)
            except Exception:
                pass
            return
        raise
    print(f"P1 dashboard serving at {url}", flush=True)
    snapshot = dashboard_snapshot(root)
    _set_latest_snapshot(snapshot)
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
