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
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


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
        rows = []
        for line in history.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
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
                    "type": "自己成長",
                    "summary": f"知識{gr.get('records_written', 0)}件 / 提案{gr.get('proposals_written', 0)}件",
                }
            elif row.get("status") == "growth_loop_error":
                executed = {"type": "成長ループエラー", "error": row.get("error", "不明")}
            elif row.get("status") == "idle":
                executed = {"type": "待機"}
            elif row.get("status") == "self_repair_failed":
                executed = {"type": "自己修復失敗", "summary": row.get("metaagent_result", {}).get("message", "エラー")}
            elif row.get("status") == "self_repair_completed":
                executed = {"type": "自己修復完了", "summary": row.get("metaagent_result", {}).get("target", "ファイル更新")}

            status_map = {
                "idle": "待機中", "growth_loop_completed": "成長完了", "growth_loop_error": "成長エラー",
                "self_repair_failed": "自己修復失敗", "self_repair_completed": "自己修復完了",
                "replied": "回答済", "observed": "観測済", "decided": "決定済", "executed": "実行済",
            }
            display_status = status_map.get(row.get("status"), row.get("status"))

            recent_ticks.append({
                "timestamp": row.get("timestamp"),
                "status": display_status,
                "thought": row.get("summary") or row.get("last_tick_summary") or display_status,
                "next_wake_at": row.get("next_wake_at"),
                "trigger_kind": _trigger_kind_for_row(row),
                "executed": executed,
            })
        latest_initiative = latest_row.get("initiative") or {}
        latest_heartbeat = latest_row.get("heartbeat") or {}
        if not state.get("recentInitiatives") and latest_initiative:
            state["recentInitiatives"] = [latest_initiative]
        if not state.get("recentHeartbeats") and latest_heartbeat:
            state["recentHeartbeats"] = [latest_heartbeat]
        if state.get("llmUsage") is None:
            state["llmUsage"] = _read_json(root / "state" / "budgets" / "llm-usage.json", {})

    inbox_counts = state.get("inboxCounts", {})
    action_counts = state.get("actionCounts", {})
    task_counts = state.get("capabilityTaskCounts", {})

    quiet_reason = "P1は待機中です。いまは何もキューにありません。"
    if inbox_counts.get("queued", 0) > 0:
        quiet_reason = "P1は受信箱の仕事を待っています。"
    elif action_counts.get("queued", 0) > 0:
        quiet_reason = "P1はキュー済みのアクション実行を待っています。"
    elif task_counts.get("pending", 0) > 0 or task_counts.get("in_progress", 0) > 0:
        quiet_reason = "P1は能力タスクの進行待ちです。"

    return {
        "generated_at": datetime.now().isoformat(),
        "root": str(root),
        "state": state,
        "history": recent_ticks,
        "quiet_reason": quiet_reason,
    }


def _trigger_kind_for_row(row: dict[str, Any]) -> str:
    status = row.get("status")
    if status == "sleeping": return "予定起床"
    if row.get("reply"): return "受信箱トリガー"
    if row.get("action"): return "アクショントリガー"
    if row.get("proposal") or row.get("review") or row.get("execution"): return "能力タスクトリガー"
    if status in {"growth_loop_completed", "growth_loop_error"}: return "成長ループトリガー"
    return "不定期トリガー"


def render_dashboard_html(snapshot: dict[str, Any]) -> str:
    state = snapshot.get("state", {})
    history = snapshot.get("history", [])
    recent_gaps = state.get("recentCapabilityGaps", [])
    recent_tasks = state.get("recentCapabilityTasks", [])
    recent_metaagent_runs = state.get("recentMetaagentRuns", [])
    llm_usage = state.get("llmUsage", {})
    llm_local_calls = _usage_total(llm_usage, primary="local_daily", alternate="local")

    growth_state = state.get("growthLoopState", {})
    knowledge_total = int(state.get("knowledgeRecordCount", 0))
    growth_processed = int(growth_state.get("last_processed_index", 0))
    purpose = state.get("purpose", {})
    current_focus = escape(str(state.get("current_focus") or "待機中"))
    next_wake_val = str(state.get("next_wake_at") or "なし")
    next_wake = escape(next_wake_val)
    mode = escape(str(state.get("mode") or "不明"))
    generation = int(state.get("generation", 1))

    quiet_reason = snapshot.get("quiet_reason", "不明")

    meaningful_history = [item for item in history if item.get("status") not in {"sleeping", "待機中"}]
    latest_meaningful = meaningful_history[-1] if meaningful_history else (history[-1] if history else None)
    latest_tick = history[-1] if history else None

    def block(title: str, body: str) -> str:
        return f"""<section class="card"><h2>{escape(title)}</h2>{body}</section>"""

    def pill(label: str, value: Any) -> str:
        return f'<span class="pill"><strong>{escape(label)}:</strong> {escape(str(value))}</span>'

    history_rows = []
    for item in history:
        executed = item.get("executed")
        executed_label = "なし"
        if executed:
            etype = executed.get("type", "不明")
            if etype == "reply": executed_label = f"回答 ({executed.get('backend') or 'local'})"
            elif etype == "action": executed_label = f"アクション: {executed.get('kind')} ({executed.get('status')})"
            elif etype == "自己修復完了": executed_label = f"修復完了: {executed.get('summary')}"
            elif etype == "自己修復失敗": executed_label = f"修復失敗: {executed.get('summary')}"
            else: executed_label = str(etype)

        history_rows.append(
            f'<div class="row"><div class="row-top"><span>{escape(str(item.get("timestamp") or "不明"))}</span>'
            f'<span class="status">{escape(str(item.get("status") or "不明"))}</span></div>'
            f'<div class="row-body"><div><strong>思考:</strong> {escape(str(item.get("thought") or ""))}</div>'
            f'<div><strong>実行:</strong> {escape(executed_label)}</div></div></div>'
        )

    metaagent_rows = []
    for run in recent_metaagent_runs[-5:]:
        success_label = "成功" if run.get("success") else "失敗"
        gen_label = f" (第 {run.get('generation')} 世代)" if run.get("generation") else ""
        metaagent_rows.append(
            f"<li><strong>{escape(str(run.get('timestamp') or '不明'))}</strong>: "
            f"<span class='status'>[{escape(success_label)}{escape(gen_label)}]</span> "
            f"<strong>目的:</strong> {escape(str(run.get('purpose') or '改善の適用'))}<br/>"
            f"&nbsp;&nbsp;対象: {escape(str(run.get('target_name') or '不明'))} / 結果: {escape(str(run.get('message') or ''))}</li>"
        )

    report_lines = [
        f"目的: {str(purpose.get('statement') or '未設定')}",
        f"現在の注目: {current_focus}",
        f" tick 総数: {len(history)}",
        f"LLM 利用(local): {llm_local_calls} 回",
        f"知識レコード: {knowledge_total} 件",
        f"成長ループ進捗: {growth_processed} 件",
    ]
    report_html = "".join(f"<li>{escape(line)}</li>" for line in report_lines)

    initiative = state.get("recentInitiatives", [])[-1] if state.get("recentInitiatives") else {}
    prop = initiative.get("proposal", {})

    return f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8" />
  <title>P1 ダッシュボード</title>
  <style>
    :root {{ --bg: #0b1020; --panel: #111833; --text: #e7ecff; --accent: #72a1ff; --accent-2: #7bf0c8; --border: rgba(255,255,255,0.08); }}
    body {{ margin: 0; font-family: sans-serif; background: var(--bg); color: var(--text); padding-bottom: 50px; }}
    header {{ padding: 20px; border-bottom: 1px solid var(--border); background: rgba(0,0,0,0.2); position: sticky; top: 0; z-index: 10; }}
    h1 {{ margin: 0 0 10px; font-size: 24px; color: var(--accent); }}
    .meta {{ display: flex; flex-wrap: wrap; gap: 8px; }}
    .pill {{ padding: 4px 10px; border-radius: 20px; background: rgba(255,255,255,0.05); border: 1px solid var(--border); font-size: 12px; }}
    .hero {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; padding: 20px; }}
    .hero-card {{ background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 15px; }}
    .label {{ font-size: 11px; color: #8a96bc; text-transform: uppercase; margin-bottom: 5px; }}
    .value {{ font-size: 18px; font-weight: bold; }}
    .countdown {{ font-size: 20px; color: var(--accent-2); font-weight: bold; }}
    .wrap {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; padding: 20px; }}
    .card {{ background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 15px; }}
    .card h2 {{ margin: 0 0 12px; font-size: 16px; border-left: 3px solid var(--accent); padding-left: 10px; }}
    .row {{ padding: 10px 0; border-top: 1px solid var(--border); }}
    .row-top {{ display: flex; justify-content: space-between; font-size: 11px; color: #8a96bc; margin-bottom: 4px; }}
    .status {{ color: var(--accent-2); font-weight: bold; }}
    .callout {{ margin: 0 20px; padding: 15px; background: rgba(123, 240, 200, 0.05); border: 1px solid rgba(123, 240, 200, 0.2); border-radius: 12px; }}
  </style>
</head>
<body>
  <header>
    <h1>P1 Dashboard (Japanese)</h1>
    <div class="meta" id="v-meta-pills">
      {pill("世代", generation)}
      {pill("モード", mode)}
      {pill("注目", current_focus)}
      {pill("次回起床", next_wake)}
      {pill("知識ベース", knowledge_total)}
    </div>
  </header>

  <div class="hero">
    <div class="hero-card"><div class="label">ステータス</div><div class="value" id="v-status">{escape(str(state.get("status") or "待機中"))}</div></div>
    <div class="hero-card"><div class="label">静かな理由</div><div class="value" id="v-quiet-reason">{escape(quiet_reason)}</div></div>
    <div class="hero-card"><div class="label">起床まで</div><div class="countdown" id="p1-countdown">{escape(next_wake)}</div></div>
  </div>

  <div class="callout">
    <strong>直近の思考:</strong> <span id="v-thought">{escape(str(latest_tick.get("thought") if latest_tick else "なし"))}</span><br/>
    <strong>次の一手:</strong> <span id="v-next-step">{escape(str(prop.get("next_step") or "なし"))}</span><br/>
    <strong>自己判断:</strong> <span id="v-judgment">{escape(str(prop.get("summary") or "なし"))}</span>
  </div>

  <main class="wrap">
    {block("概要", f'<ul id="v-summary">{report_html}</ul>')}
    {block("自律履歴", f'<div id="v-history">{"".join(history_rows) or "なし"}</div>')}
    {block("自己修復履歴", f"<ul id='v-meta-history'>{''.join(metaagent_rows) or '<li>なし</li>'}</ul>")}
  </main>

  <script>
    const eventSource = new EventSource("/api/events");
    let latestSnapshot = {json.dumps(snapshot, ensure_ascii=False)};

    function formatCountdown(targetStr) {{
      if (!targetStr || targetStr === "なし") return "なし";
      const target = new Date(targetStr);
      const diff = target.getTime() - Date.now();
      if (diff <= 0) return "起床中";
      const s = Math.floor(diff / 1000);
      const m = Math.floor(s / 60);
      return `${{m}}分 ${{s % 60}}秒`;
    }}

    function updateDOM(snap) {{
      const st = snap.state || {{}};
      const hist = snap.history || [];
      const latest = hist[hist.length - 1] || {{}};
      const metaHist = st.recentMetaagentRuns || [];

      document.getElementById("v-status").textContent = st.status || "待機中";
      document.getElementById("v-thought").textContent = latest.thought || "なし";
      document.getElementById("v-quiet-reason").textContent = snap.quiet_reason || "待機中 (キューにタスクがありません)";

      const initiatives = st.recentInitiatives || [];
      const init = initiatives[initiatives.length - 1] || {{}};
      const prop = init.proposal || {{}};
      const _txt = (val) => (typeof val === 'object' ? JSON.stringify(val) : (val || "なし"));
      document.getElementById("v-next-step").textContent = _txt(prop.next_step);
      document.getElementById("v-judgment").textContent = _txt(prop.summary);

      const vHistory = document.getElementById("v-history");
      const vMeta = document.getElementById("v-meta-history");
      const vMetaPills = document.getElementById("v-meta-pills");

      if (vMetaPills) {{
        const gen = st.generation || 1;
        const mode = st.mode || "不明";
        const focus = st.current_focus || "待機中";
        let wakeText = "なし";
        if (st.next_wake_at) {{
          const dt = new Date(st.next_wake_at).getTime() - Date.now();
          if (dt > 0) {{
            wakeText = 'あと ' + Math.ceil(dt / 1000) + ' 秒';
          }} else {{
            wakeText = '起床中...';
          }}
        }}
        const total = st.knowledgeRecordCount || 0;

        vMetaPills.innerHTML = `
          <span class="pill"><strong>世代:</strong> ${{gen}}</span>
          <span class="pill"><strong>モード:</strong> ${{mode}}</span>
          <span class="pill"><strong>注目:</strong> ${{focus}}</span>
          <span class="pill"><strong>次回起床:</strong> ${{wakeText}}</span>
          <span class="pill"><strong>知識ベース:</strong> ${{total}}</span>`;
      }}

      if (vHistory && hist.length > 0) {{
        vHistory.innerHTML = hist.map(item => {{
          const exec = item.executed || {{}};
          let label = "なし";
          if (exec.type === "reply") label = `回答 (${{exec.backend || 'local'}})`
          else if (exec.type === "action") label = `アクション: ${{exec.kind}} (${{exec.status}})`
          else if (exec.type === "自己修復完了") label = `修復完了: ${{exec.summary}}`
          else if (exec.type === "自己修復失敗") label = `修復失敗: ${{exec.summary}}`
          else if (exec.type) label = String(exec.type);

          return `
            <div class="row">
              <div class="row-top">
                <span>${{item.timestamp || "不明"}}</span>
                <span class="status">${{item.status || "不明"}}</span>
              </div>
              <div class="row-body">
                <div><strong>思考:</strong> ${{item.thought || ""}}</div>
                <div><strong>実行:</strong> ${{label}}</div>
              </div>
            </div>`;
        }}).join("");
      }}

      if (vMeta && metaHist.length > 0) {{
        vMeta.innerHTML = metaHist.slice(-5).map(run => {{
          const successLabel = run.success ? '成功' : '失敗';
          const genLabel = run.generation ? ` (第 ${{run.generation}} 世代)` : '';
          return `
            <li><strong>${{run.timestamp || '不明'}}</strong>:
            <span class="status">[${{successLabel}}${{genLabel}}]</span>
            <strong>目的:</strong> ${{run.purpose || '改善の適用'}}<br/>
            &nbsp;&nbsp;対象: ${{run.target_name || '不明'}} / 結果: ${{run.message || ''}}</li>`;
        }}).join("");
      }}
    }}

    eventSource.addEventListener("snapshot", (e) => {{
      latestSnapshot = JSON.parse(e.data);
      updateDOM(latestSnapshot);
    }});

    setInterval(() => {{
      const el = document.getElementById("p1-countdown");
      if (el) {{
        const wake = latestSnapshot.state ? latestSnapshot.state.next_wake_at : null;
        el.textContent = formatCountdown(wake);
      }}
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
                    if current is None: continue
                    body = json.dumps(current, ensure_ascii=False).encode("utf-8")
                    self.wfile.write(b"event: snapshot\ndata: ")
                    self.wfile.write(body)
                    self.wfile.write(b"\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
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
    coordination["dashboard_notify_url"] = f"http://{host}:{port}/api/notify"
    coordination["dashboard_health_url"] = f"http://{host}:{port}/api/health"
    runtime_state["coordination"] = coordination
    runtime_state_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_state_path.write_text(json.dumps(runtime_state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    handler = type("P1DashboardHandler", (_DashboardHandler,), {"root": root})
    url = f"http://{host}:{port}"
    try:
        server = ThreadingHTTPServer((host, port), handler)
    except OSError:
        raise
    print(f"P1 dashboard serving at {url}", flush=True)
    _set_latest_snapshot(dashboard_snapshot(root))
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
