from __future__ import annotations
import html
import json
import re
from typing import Any

_DASHBOARD_TPL = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>P3 Dashboard</title>
  <style>
    :root { --color-running: #5b8def; --color-success: #2d8f6f; --color-failed: #b05656; --color-blocked: #f39c12; }
    html { overflow-anchor: none; }
    body { margin: 0; font-family: ui-sans-serif, system-ui, sans-serif; color: #e8ecef; background: #111417; line-height: 1.5; }
    .shell { max-width: 1000px; margin: 0 auto; padding: 20px; }
    .card { background: #171c20; border: 1px solid #26303a; border-radius: 12px; padding: 16px; margin-bottom: 16px; }
    .headline { font-size: 24px; font-weight: 700; }
    .subtle { color: #92a3b3; font-size: 14px; }
    .pill { display: inline-block; padding: 4px 12px; border-radius: 999px; background: #22303d; font-size: 12px; margin: 4px 4px 0 0; }
    textarea { width: 100%; box-sizing: border-box; min-height: 80px; background: #0f1316; color: #f4f7fa; border: 1px solid #2d3944; border-radius: 8px; padding: 8px; font: inherit; }
    button { border: 0; border-radius: 4px; padding: 8px 16px; background: #2d8f6f; color: white; cursor: pointer; }
    pre { white-space: pre-wrap; font-family: ui-monospace, monospace; font-size: 12px; margin: 0; }

    .operation-card { border: 1px solid #2a3440; background: #12171b; border-radius: 8px; margin-bottom: 8px; overflow: hidden; }
    .operation-head { width: 100%; border: 0; background: transparent; color: inherit; text-align: left; padding: 12px; cursor: pointer; display: flex; justify-content: space-between; align-items: center; }
    .operation-status { font-size: 10px; text-transform: uppercase; padding: 2px 8px; border-radius: 999px; }
    .operation-status.running { background: var(--color-running); }
    .operation-status.success { background: var(--color-success); }
    .operation-status.failed { background: var(--color-failed); }
    .operation-status.blocked { background: var(--color-blocked); color: #111417; }
    .operation-card.blocked { border-color: var(--color-blocked); }
    .blocked-reason { margin: 0 12px 12px 12px; padding: 10px; border-left: 3px solid var(--color-blocked); background: #1a1510; color: #ffd08a; border-radius: 4px; }

    .operation-body.closed, .nested-content.closed { display: none; }
    .nested-block { margin: 8px 12px; border: 1px solid #2a3440; border-radius: 6px; background: #101519; }
    .nested-toggle { width: 100%; border: 0; background: transparent; color: #c6d2dc; text-align: left; padding: 8px; cursor: pointer; font-size: 12px; }
    .operation-output { max-height: 300px; overflow: auto; padding: 8px; background: #080a0c; border-top: 1px solid #2a3440; }
    .bubble-meta { font-size: 11px; color: #92a3b3; padding: 0 12px 8px 12px; }
    .activity-row { padding: 8px; border-bottom: 1px solid #26303a; font-size: 13px; }
    .activity-row:last-child { border-bottom: 0; }

    .tool-pill { display: inline-block; background: #1a232e; padding: 2px 8px; border-radius: 4px; font-size: 11px; margin-right: 4px; border: 1px solid #2a3440; }
    .tool-result-meta { margin-bottom: 8px; }

    /* Flow Steps Styling */
    .flow-step { border-left: 2px solid #2d8f6f; padding-left: 12px; margin: 12px 0; }
    .flow-step-title { font-weight: 700; font-size: 13px; margin-bottom: 4px; display: flex; justify-content: space-between; color: #92a3b3; }
    .flow-phase { font-size: 10px; opacity: 0.7; }
    .flow-item { margin-top: 8px; }
    .flow-label { font-size: 10px; text-transform: uppercase; color: #5b6e7f; margin-bottom: 2px; }
    .flow-content { background: #0c1013; padding: 8px; border-radius: 4px; border: 1px solid #1f272e; }

    .flow-item.assistant_message .flow-content { border-left: 2px solid var(--color-running); }
    .flow-item.finish .flow-content { border-left: 2px solid var(--color-success); background: #0e1513; }
    .flow-item.system_note .flow-content, .flow-item.planning_note .flow-content { background: #13171b; font-style: italic; color: #c6d2dc; }
    .flow-item.observer_note .flow-content { background: #101820; border-left: 2px solid #3498db; color: #d9ecff; }
    .flow-item.observer_note .flow-label { color: #7fc8ff; font-weight: bold; }

    /* Blocked status styling */
    .flow-item.system_note.blocked .flow-content { border-left: 2px solid var(--color-blocked); background: #1a1510; color: #f39c12; }
    .flow-item.system_note.blocked .flow-label { color: var(--color-blocked); font-weight: bold; }

    .tool-stream-block { margin-top: 8px; }
    .flow-k { font-size: 10px; color: #5b6e7f; text-transform: uppercase; margin-bottom: 2px; }
    .tool-stream-block.stderr pre { color: #f58e8e; }
    .live-state { padding: 8px; border-radius: 4px; border: 1px solid #2a3440; margin-bottom: 8px; }
    .live-state.waiting { background: #12171b; border-left: 2px solid #92a3b3; }
    .live-state.running { background: #101519; border-left: 2px solid var(--color-running); }
    .commentator-panel { display: grid; gap: 10px; }
    .commentator-note { border: 1px solid #24445b; border-left: 3px solid #3498db; border-radius: 6px; background: #0f171d; overflow: hidden; }
    .commentator-head { display: flex; gap: 8px; justify-content: space-between; align-items: center; padding: 8px 10px; background: #101e28; color: #d9ecff; font-size: 12px; }
    .commentator-title { font-weight: 700; color: #9bd4ff; }
    .commentator-body { display: grid; gap: 6px; padding: 10px; }
    .commentator-line { display: grid; grid-template-columns: 132px 1fr; gap: 8px; align-items: start; }
    .commentator-line strong { color: #7fc8ff; font-size: 12px; }
    .commentator-line span { color: #e7f4ff; font-size: 13px; }
    .commentator-empty { padding: 10px; color: #92a3b3; border: 1px dashed #2a4050; border-radius: 6px; background: #101519; }
    @media (max-width: 720px) {
      .shell { padding: 12px; }
      .commentator-line { grid-template-columns: 1fr; gap: 2px; }
      .operation-head { align-items: flex-start; gap: 8px; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <section class="card">
      <div class="headline">P3 ダッシュボード</div>
      <div class="subtle">P3 の実行状況、ツール結果、判断の流れを確認できます。</div>
      <div id="pills">
        <div class="pill" id="statusPill">状態: __STATUS__</div>
        <div class="pill" id="modelPill">モデル: __MODEL__</div>
        <div class="pill" id="lastLlmPill">直近LLM: __LAST_LLM__</div>
        <div class="pill" id="workspacePill">作業場: __LLM_WORKSPACE__</div>
      </div>
    </section>

    <section class="card">
      <h2>現在の実行</h2>
      <pre id="currentRunMessage" style="margin-bottom: 12px;">__USER_MESSAGE__</pre>
      <div class="nested-block">
        <button class="nested-toggle" onclick="toggleNested('current-stream')">ライブ出力</button>
        <div id="current-stream" class="nested-content">
          <pre id="currentRunStream" class="operation-output">__STREAM_TEXT__</pre>
        </div>
      </div>
    </section>

    <section class="card">
      <h2>メッセージ送信</h2>
      <div style="display: flex; gap: 8px; margin-bottom: 8px;">
        <select id="modeSelect" style="flex: 1; background: #0f1316; color: white; border: 1px solid #2d3944; border-radius: 4px; padding: 4px;">
          <option value="native_chat">通常チャット</option>
          <option value="terminal_agent">ターミナルエージェント</option>
        </select>
        <select id="modelSelect" style="flex: 1; background: #0f1316; color: white; border: 1px solid #2d3944; border-radius: 4px; padding: 4px;">__MODEL_OPTIONS__</select>
        <select id="shellSelect" style="background: #0f1316; color: white; border: 1px solid #2d3944; border-radius: 4px; padding: 4px;">
           <option value="auto">自動シェル</option>
           <option value="zsh">zsh</option>
           <option value="bash">bash</option>
        </select>
      </div>
      <textarea id="messageText" placeholder="メッセージを入力..."></textarea>
      <button onclick="sendMessage()" style="margin-top: 8px; width: 100%;">送信</button>
      <div id="chatResult" class="subtle" style="margin-top: 4px;"></div>
    </section>

    <section class="card">
      <h2 id="operationsSummary">実行操作 (__OP_COUNT__)</h2>
      <div id="operationsPanel">__OP_ROWS__</div>
    </section>

    <section class="card">
      <h2>進捗ログ</h2>
      <div id="updatesPanel">__UPDATE_ROWS__</div>
    </section>
  </div>

  <script>
    let latestSnapshot = __SNAPSHOT_JSON__;
    const openOperationIds = new Set();
    const closedOperationIds = new Set();
    const openNestedIds = new Set(["current-stream"]);
    const closedNestedIds = new Set();
    let suspendOperationsRenderUntil = 0;

    function esc(v) {
      return String(v ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
    }

    function toggleOperation(opId) {
      const card = document.querySelector(`.operation-card[data-operation-id="${opId}"]`);
      const body = card?.querySelector(".operation-body");
      if (!body) return;
      const closing = !body.classList.contains("closed");
      body.classList.toggle("closed", closing);
      if (closing) { openOperationIds.delete(opId); closedOperationIds.add(opId); }
      else { openOperationIds.add(opId); closedOperationIds.delete(opId); }
      suspendOperationsRenderUntil = Date.now() + 2000;
    }

    function toggleNested(nId) {
      const section = nId === 'current-stream' ? document.getElementById(nId) : document.querySelector(`[data-nested-id="${nId}"]`);
      if (!section) return;
      const closing = !section.classList.contains("closed");
      section.classList.toggle("closed", closing);
      if (closing) { openNestedIds.delete(nId); closedNestedIds.add(nId); }
      else { openNestedIds.add(nId); closedNestedIds.delete(nId); }
      suspendOperationsRenderUntil = Date.now() + 2000;
    }

    function renderFlowSteps(steps) {
      if (!steps || steps.length === 0) return '<div class="subtle" style="padding:8px;">フローはまだありません。</div>';
      return steps.map(step => `
        <section class="flow-step">
          <div class="flow-step-title">
            <span>${esc(step.title || 'Step')}</span>
            <span class="flow-phase">${esc(step.phase || '-')}</span>
          </div>
          ${(step.items || []).map(item => renderFlowItem(item)).join('')}
        </section>
      `).join('');
    }

    function renderFlowItem(item) {
      const labels = { observer_note: '解説者', system_note: 'システム', planning_note: '計画', activity_update: 'システム状態', assistant_message: 'LLM応答', user_message: 'ユーザー', tool_call: 'ツール呼び出し', tool_result: 'ツール結果', finish: '完了' };
      const label = esc(labels[item.label] || item.label || "");
      let content = esc(item.content || "");

      // Highlight blocked states
      const isBlocked = (item.label === 'system_note' && content.includes('ブロックされました'));
      const extraClass = isBlocked ? 'blocked' : '';

      if (item.label === 'tool_result' && item.tool_name === 'run_command' && item.parsed_payload) {
        const p = item.parsed_payload;
        content = `<div class="tool-result-card">
          <div class="tool-result-meta">
            ${p.command ? `<span class="tool-pill"><strong>cmd:</strong> ${esc(p.command)}</span>` : ''}
            ${p.returncode !== undefined ? `<span class="tool-pill"><strong>ret:</strong> ${esc(p.returncode)}</span>` : ''}
          </div>
          ${p.stdout ? `<div class="flow-label">stdout</div><pre>${esc(p.stdout)}</pre>` : ''}
          ${p.stderr ? `<div class="flow-label">stderr</div><pre class="stderr">${esc(p.stderr)}</pre>` : ''}
          ${p.error ? `<div class="flow-label">error</div><pre class="stderr">${esc(p.error)}</pre>` : ''}
        </div>`;
      } else if (item.label === 'observer_note') {
        content = renderCommentatorContent(item.content || "");
      } else {
        content = `<div class="flow-content"><pre>${content}</pre></div>`;
      }
      return `<div class="flow-item ${esc(item.label)} ${extraClass}">
        <div class="flow-label">${label}${isBlocked ? ' (BLOCKED)' : ''}</div>
        ${content}
      </div>`;
    }

    function commentatorRows(text) {
      const rawLines = String(text || "").split(/\\n+/).map(v => v.trim()).filter(Boolean);
      const rows = [];
      for (const line of rawLines) {
        const m = line.match(/^(?:\\d+[\\.．、:]\\s*)?([^:：]{2,22})[:：]\\s*(.*)$/);
        if (m) rows.push({ label: m[1], body: m[2] });
        else rows.push({ label: "解説", body: line });
      }
      return rows.length ? rows : [{ label: "解説", body: "まだ解説本文はありません。" }];
    }

    function renderCommentatorContent(text) {
      return `<div class="commentator-body">${
        commentatorRows(text).map(row => `
          <div class="commentator-line">
            <strong>${esc(row.label)}</strong>
            <span>${esc(row.body)}</span>
          </div>
        `).join("")
      }</div>`;
    }

    function captureScrollState() {
      const boxes = {};
      const current = document.getElementById("currentRunStream");
      if (current) boxes["current-stream"] = { top: current.scrollTop, left: current.scrollLeft };
      document.querySelectorAll("[data-nested-id] .operation-output").forEach(el => {
        const owner = el.closest("[data-nested-id]");
        const id = owner ? owner.getAttribute("data-nested-id") : "";
        if (id) boxes[id] = { top: el.scrollTop, left: el.scrollLeft };
      });
      return { x: window.scrollX, y: window.scrollY, boxes };
    }

    function restoreScrollState(state) {
      const current = document.getElementById("currentRunStream");
      if (current && state.boxes["current-stream"]) {
        current.scrollTop = state.boxes["current-stream"].top;
        current.scrollLeft = state.boxes["current-stream"].left;
      }
      document.querySelectorAll("[data-nested-id] .operation-output").forEach(el => {
        const owner = el.closest("[data-nested-id]");
        const id = owner ? owner.getAttribute("data-nested-id") : "";
        const saved = id ? state.boxes[id] : null;
        if (saved) {
          el.scrollTop = saved.top;
          el.scrollLeft = saved.left;
        }
      });
      window.scrollTo(state.x, state.y);
    }

    function renderSnapshot(snapshot) {
      const scrollState = captureScrollState();
      console.log("Snapshot received", snapshot);
      latestSnapshot = snapshot;
      const rt = snapshot.runtime || {};
      const ops = snapshot.recent_operations || [];
      const upd = snapshot.recent_updates || [];

      document.getElementById("statusPill").textContent = `状態: ${rt.status || "idle"}`;
      document.getElementById("modelPill").textContent = `モデル: ${rt.current_model || snapshot.model}`;
      const parseIssue = rt.last_llm_parse_issue ? ` / 失敗分類: ${rt.last_llm_parse_issue}` : "";
      const doneReason = rt.last_llm_stream_metadata && rt.last_llm_stream_metadata.done_reason ? ` / done: ${rt.last_llm_stream_metadata.done_reason}` : "";
      document.getElementById("lastLlmPill").textContent = `直近LLM: ${rt.last_llm_duration_ms || "-"}ms${parseIssue}${doneReason}`;
      document.getElementById("workspacePill").textContent = `作業場: ${rt.current_llm_workspace || rt.last_llm_workspace || "-"}`;
      document.getElementById("currentRunMessage").textContent = rt.current_user_message || "実行中の要求はありません。";
      document.getElementById("currentRunStream").textContent = rt.current_stream_text || "ストリーム出力はありません。";
      document.getElementById("operationsSummary").textContent = `実行操作 (${ops.length})`;

      if (Date.now() > suspendOperationsRenderUntil) {
         const panel = document.getElementById("operationsPanel");
         panel.innerHTML = ops.map(op => {
           const opId = op.operation_id || "";
           const open = openOperationIds.has(opId) || (!closedOperationIds.has(opId) && op.status === 'running');
           const blockedReason = op.blocked_reason ? `<div class="blocked-reason"><strong>ブロック理由</strong><pre>${esc(op.blocked_reason)}</pre></div>` : '';
           return `<section class="operation-card ${op.status}" data-operation-id="${opId}">
             <button class="operation-head" onclick="toggleOperation('${opId}')">
               <strong>${esc(op.title)}</strong>
               <span class="operation-status ${op.status}">${op.status}</span>
             </button>
             <div class="bubble-meta">${esc(op.started_at)}</div>
             <div class="operation-body ${open ? '' : 'closed'}">
               <pre class="operation-detail" style="padding: 12px;">${esc(op.detail)}</pre>
               ${blockedReason}
               <div class="flow-container" style="padding: 0 12px 12px 12px;">
                 ${renderFlowSteps(op.flow_steps || [])}
               </div>
               <div class="nested-block">
                 <button class="nested-toggle" onclick="toggleNested('${opId}:live')">ライブ出力</button>
                 <div class="nested-content ${openNestedIds.has(`${opId}:live`) ? '' : 'closed'}" data-nested-id="${opId}:live">
                   <div class="operation-output"><pre>${esc(op.output_preview)}</pre></div>
                 </div>
               </div>
             </div>
           </section>`;
         }).join("") || "<div>まだ実行操作はありません。</div>";
      }

      document.getElementById("updatesPanel").innerHTML = upd.map(u =>
        `<div class="activity-row"><div class="bubble-meta">${esc(u.timestamp)}</div><div>${esc(u.message)}</div></div>`
      ).join("") || "<div>まだ更新ログはありません。</div>";
      requestAnimationFrame(() => restoreScrollState(scrollState));
    }

    async function refresh() {
      try {
        const r = await fetch("/api/snapshot");
        if (r.ok) renderSnapshot(await r.json());
      } catch(e) {}
    }

    async function sendMessage() {
      const msg = document.getElementById("messageText").value.trim();
      if (!msg) return;
      document.getElementById("chatResult").textContent = "送信中...";
      try {
        const r = await fetch("/api/message", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            content: msg,
            model: document.getElementById("modelSelect").value,
            mode: document.getElementById("modeSelect").value,
            shell: document.getElementById("shellSelect").value
          })
        });
        if (r.ok) {
          document.getElementById("chatResult").textContent = "送信しました。";
          document.getElementById("messageText").value = "";
          setTimeout(refresh, 100);
        } else {
          document.getElementById("chatResult").textContent = "送信に失敗しました。";
        }
      } catch(e) {
        document.getElementById("chatResult").textContent = "ネットワークエラーです。";
      }
    }

    const events = new EventSource("/api/events");
    events.addEventListener("snapshot", (e) => renderSnapshot(JSON.parse(e.data)));
    setInterval(refresh, 2000);
  </script>
</body>
</html>"""

def _phase_for_flow_step(step: dict[str, Any]) -> str:
    step_index = int(step.get("step_index") or 0)
    if step_index == 0:
        return "DISCOVER_REQUIRED_COMMANDS"
    items = step.get("items") or []
    labels = {str(item.get("label") or "") for item in items if isinstance(item, dict)}
    tool_names = {str(item.get("tool_name") or "") for item in items if isinstance(item, dict)}
    if "finish" in labels:
        return "FINISH"
    if "tool_call" in labels and "run_command" in tool_names:
        return "EXECUTE_MISSING_COMMANDS"
    if "tool_result" in labels and "run_command" in tool_names:
        return "SYNTHESIZE_FROM_EVIDENCE"
    if "assistant_message" in labels:
        return "SYNTHESIZE_FROM_EVIDENCE"
    return "DISCOVER_REQUIRED_COMMANDS"

def _render_flow_steps_html(steps: list[dict[str, Any]]) -> str:
    if not steps:
        return "<div class=\"flow-empty\">まだ finish までの flow はありません。</div>"
    parts: list[str] = []
    for step in steps:
        items_html = "".join(_render_flow_item_html(item) for item in step.get("items") or [])
        parts.append(
            "<section class=\"flow-step\">"
            f"<div class=\"flow-step-title\">{html.escape(str(step.get('title') or 'Step'))}"
            f"<span class=\"flow-phase\">{html.escape(str(step.get('phase') or '-'))}</span></div>"
            f"{items_html}"
            "</section>"
        )
    return "".join(parts)

def _render_flow_item_html(item: dict[str, Any]) -> str:
    label_map = {
        "observer_note": "解説者",
        "system_note": "システム",
        "planning_note": "計画",
        "activity_update": "システム状態",
        "assistant_message": "LLM応答",
        "user_message": "ユーザー",
        "tool_call": "ツール呼び出し",
        "tool_result": "ツール結果",
        "finish": "完了",
    }
    label = html.escape(label_map.get(str(item.get("label") or ""), str(item.get("label") or "")))
    content = str(item.get("content") or "")
    tool_name = str(item.get("tool_name") or "")

    # Blocked status detection
    extra_class = ""
    is_blocked = (str(item.get("label")) == "system_note" and "ブロックされました" in content)
    if is_blocked:
        extra_class = " blocked"
        label = f"{label} (BLOCKED)"

    if str(item.get("label") or "") == "tool_result":
        payload = item.get("parsed_payload")
        if isinstance(payload, dict) and tool_name == "run_command":
            return (
                f"<div class=\"flow-item\"><div class=\"flow-label\">{label}</div>"
                f"{_render_command_result_html(payload)}"
                "</div>"
            )
    if str(item.get("label") or "") == "observer_note":
        return (
            f"<div class=\"flow-item observer_note\">"
            f"<div class=\"flow-label\">{label}</div>"
            f"{_render_commentator_content_html(content)}"
            "</div>"
        )
    return (
        f"<div class=\"flow-item {html.escape(str(item.get('label') or ''))}{extra_class}\">"
        f"<div class=\"flow-label\">{label}</div>"
        f"<div class=\"flow-content\"><pre>{html.escape(content)}</pre></div>"
        "</div>"
    )

def _extract_json_object(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for index in range(start, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None

def _render_live_output_html(text: str) -> str:
    clean = str(text or "")
    if not clean:
        return "<div class=\"flow-empty\">まだ出力はありません。</div>"
    if clean.startswith("Waiting for model response"):
        return "<div class=\"live-state waiting\"><div class=\"flow-k\">status</div><pre>Waiting for model response...</pre></div>"
    if clean.startswith("Running command via "):
        return f"<div class=\"live-state running\"><div class=\"flow-k\">status</div><pre>{html.escape(clean)}</pre></div>"
    candidate = _extract_json_object(clean)
    if candidate is not None:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict) and str(payload.get("tool") or "") == "run_command":
            return _render_command_result_html(payload)
    return f"<pre>{html.escape(clean)}</pre>"

def _render_command_result_html(payload: dict[str, Any]) -> str:
    meta_rows = [
        ("command", payload.get("command")),
        ("shell", payload.get("shell")),
        ("cwd", payload.get("cwd")),
        ("returncode", payload.get("returncode")),
        ("duration_ms", payload.get("duration_ms")),
        ("active_stream", payload.get("active_stream")),
    ]
    meta = "".join(
        f"<span class=\"tool-pill\"><strong>{html.escape(str(key))}:</strong> {html.escape(str(value))}</span>"
        for key, value in meta_rows
        if value not in {None, ""}
    )
    parts = [f"<div class=\"tool-result-meta\">{meta}</div>" if meta else ""]
    stdout = payload.get("stdout")
    stderr = payload.get("stderr")
    error = payload.get("error")
    if stdout not in {None, ""}:
        parts.append(
            "<div class=\"tool-stream-block\">"
            "<div class=\"flow-k\">stdout</div>"
            f"<pre>{html.escape(str(stdout))}</pre>"
            "</div>"
        )
    if stderr not in {None, ""}:
        parts.append(
            "<div class=\"tool-stream-block stderr\">"
            "<div class=\"flow-k\">stderr</div>"
            f"<pre>{html.escape(str(stderr))}</pre>"
            "</div>"
        )
    if error not in {None, ""}:
        parts.append(
            "<div class=\"tool-stream-block stderr\">"
            "<div class=\"flow-k\">error</div>"
            f"<pre>{html.escape(str(error))}</pre>"
            "</div>"
        )
    return f"<div class=\"tool-result-card\">{''.join(parts)}</div>"

def _commentator_rows(text: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        cleaned = re.sub(r"^\d+[\.．、:]\s*", "", line)
        match = re.match(r"^([^:：]{2,22})[:：]\s*(.*)$", cleaned)
        if match:
            rows.append((match.group(1), match.group(2)))
        else:
            rows.append(("解説", cleaned))
    return rows or [("解説", "まだ解説本文はありません。")]

def _render_commentator_content_html(text: str) -> str:
    rows = "".join(
        "<div class=\"commentator-line\">"
        f"<strong>{html.escape(label)}</strong>"
        f"<span>{html.escape(body)}</span>"
        "</div>"
        for label, body in _commentator_rows(text)
    )
    return f"<div class=\"commentator-body\">{rows}</div>"

def render_dashboard_html(snapshot: dict[str, Any]) -> str:
    runtime = snapshot.get("runtime") or {}
    model = snapshot.get("model") or "gemma4:26b"
    available_models = snapshot.get("available_models") or [model]
    operations = snapshot.get("recent_operations") or []
    updates = snapshot.get("recent_updates") or []

    esc = html.escape
    def _op_row(op: dict[str, Any]) -> str:
        op_id = str(op.get("operation_id") or "")
        status = str(op.get("status") or "idle")
        title = str(op.get("title") or "Operation")
        detail = str(op.get("detail") or "...")
        started_at = str(op.get("started_at") or "")
        output_preview = str(op.get("output_preview") or "")
        blocked_reason = str(op.get("blocked_reason") or "")
        flow_html = _render_flow_steps_html(op.get("flow_steps") or [])
        blocked_html = (
            f"<div class=\"blocked-reason\"><strong>ブロック理由</strong><pre>{esc(blocked_reason)}</pre></div>"
            if blocked_reason
            else ""
        )

        return (
            f"<section class=\"operation-card {esc(status)}\" data-operation-id=\"{esc(op_id)}\">"
            f"<button class=\"operation-head\" onclick=\"toggleOperation('{esc(op_id)}')\">"
            f"<strong>{esc(title)}</strong>"
            f"<span class=\"operation-status {esc(status)}\">{esc(status)}</span>"
            f"</button>"
            f"<div class=\"bubble-meta\">{esc(started_at)}</div>"
            f"<div class=\"operation-body closed\">"
            f"<pre class=\"operation-detail\" style=\"padding: 12px;\">{esc(detail)}</pre>"
            f"{blocked_html}"
            f"<div class=\"flow-container\" id=\"flow-{esc(op_id)}\" style=\"padding: 0 12px 12px 12px;\">{flow_html}</div>"
            f"<div class=\"nested-block\">"
            f"<button class=\"nested-toggle\" onclick=\"toggleNested('{esc(op_id)}:live')\">Live Output</button>"
            f"<div class=\"nested-content closed\" data-nested-id=\"{esc(op_id)}:live\">"
            f"<div class=\"operation-output\"><pre>{esc(output_preview)}</pre></div>"
            f"</div>"
            f"</div>"
            f"</div>"
            f"</section>"
        )

    operation_rows = "".join(_op_row(op) for op in operations) or "<div>まだ実行操作はありません。</div>"
    update_rows = "".join(
        f"<div class=\"activity-row\"><div class=\"bubble-meta\">{esc(str(u.get('timestamp') or ''))}</div><div>{esc(str(u.get('message') or ''))}</div></div>"
        for u in updates
    ) or "<div>まだ更新ログはありません。</div>"

    model_options = "".join(f"<option value=\"{esc(m)}\"{' selected' if m == runtime.get('current_model', model) else ''}>{esc(m)}</option>" for m in available_models)

    res = _DASHBOARD_TPL.replace("__STATUS__", esc(str(runtime.get("status") or "idle")))
    res = res.replace("__MODEL__", esc(str(runtime.get("current_model") or model)))
    last_llm_parts = [f"{runtime.get('last_llm_duration_ms') or '-'}ms"]
    if runtime.get("last_llm_parse_issue"):
        last_llm_parts.append(f"失敗分類: {runtime.get('last_llm_parse_issue')}")
    metadata = runtime.get("last_llm_stream_metadata") or {}
    if isinstance(metadata, dict) and metadata.get("done_reason"):
        last_llm_parts.append(f"done: {metadata.get('done_reason')}")
    res = res.replace("__LAST_LLM__", esc(" / ".join(str(part) for part in last_llm_parts)))
    res = res.replace("__LLM_WORKSPACE__", esc(str(runtime.get("current_llm_workspace") or runtime.get("last_llm_workspace") or "-")))
    res = res.replace("__USER_MESSAGE__", esc(str(runtime.get("current_user_message") or "No active run.")))
    res = res.replace("__STREAM_TEXT__", esc(str(runtime.get("current_stream_text") or "No stream data.")))
    res = res.replace("__MODEL_OPTIONS__", model_options)
    res = res.replace("__OP_COUNT__", str(len(operations)))
    res = res.replace("__OP_ROWS__", operation_rows)
    res = res.replace("__UPDATE_ROWS__", update_rows)
    res = res.replace("__SNAPSHOT_JSON__", json.dumps(snapshot, ensure_ascii=False))
    return res
