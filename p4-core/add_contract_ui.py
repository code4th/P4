import re

TEMPLATES_PATH = "/Users/satojunichi/Documents/openclaw/p4-core/p4_core/dashboard/templates.py"

with open(TEMPLATES_PATH, "r") as f:
    code = f.read()

# 1. Inject HTML section
html_section = """
    <section class="card" id="contractProgressCard">
      <h2>Contract Progress</h2>
      <div id="contractProgressBody" style="margin-top:8px; padding:12px; background:#0f1316; border:1px solid #2d3944; border-radius:4px;">
        <div class="commentator-line"><strong>契約状態</strong> <span>__CONTRACT_STATE__</span></div>
        <div class="commentator-line"><strong>ファイル作成</strong> <span>__CONTRACT_ARTIFACT__</span></div>
        <div class="commentator-line"><strong>コマンド実行</strong> <span>__CONTRACT_COMMAND__</span></div>
        <div class="commentator-line"><strong>標準出力</strong> <span>__CONTRACT_STDOUT__</span></div>
        <div class="commentator-line"><strong>ユーザ応答</strong> <span>__CONTRACT_RESULT__</span></div>
      </div>
    </section>

    <section class="card" id="latestResultCard">
"""

if "contractProgressCard" not in code:
    code = code.replace('<section class="card" id="latestResultCard">', html_section)

# 2. Inject Python SSR
python_ssr = """
    res = res.replace("__CONTRACT_STATE__", esc(str(snapshot.get("contract_progress", {}).get("contract_state", "unknown"))))
    res = res.replace("__CONTRACT_ARTIFACT__", esc(str(snapshot.get("contract_progress", {}).get("artifact_written", "no"))))
    res = res.replace("__CONTRACT_COMMAND__", esc(str(snapshot.get("contract_progress", {}).get("command_executed", "no"))))
    res = res.replace("__CONTRACT_STDOUT__", esc(str(snapshot.get("contract_progress", {}).get("stdout_displayed", "no"))))
    res = res.replace("__CONTRACT_RESULT__", esc(str(snapshot.get("contract_progress", {}).get("result_selected_for_user", "no"))))
    res = res.replace("__SNAPSHOT_JSON__", "null")
"""

if "__CONTRACT_STATE__" not in code.split("def render_dashboard_html")[-1]:
    code = code.replace('res = res.replace("__SNAPSHOT_JSON__", "null")', python_ssr)

# 3. Inject JS CSR
js_csr = """
      if (data.contract_progress) {
          const cp = data.contract_progress;
          const body = document.getElementById("contractProgressBody");
          if (body) {
              body.innerHTML = `
                <div class="commentator-line"><strong>契約状態</strong> <span>${esc(cp.contract_state)}</span></div>
                <div class="commentator-line"><strong>ファイル作成</strong> <span>${esc(cp.artifact_written)}</span></div>
                <div class="commentator-line"><strong>コマンド実行</strong> <span>${esc(cp.command_executed)}</span></div>
                <div class="commentator-line"><strong>標準出力</strong> <span>${esc(cp.stdout_displayed)}</span></div>
                <div class="commentator-line"><strong>ユーザ応答</strong> <span>${esc(cp.result_selected_for_user)}</span></div>
              `;
          }
      }
      
      const el = document.getElementById("operationsPanel");
"""

if "data.contract_progress" not in code:
    code = code.replace('const el = document.getElementById("operationsPanel");', js_csr)

with open(TEMPLATES_PATH, "w") as f:
    f.write(code)

print("Updated templates.py with Contract Progress")
