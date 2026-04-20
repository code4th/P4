from __future__ import annotations


DASHBOARD_SCRIPT = """
    const eventSource = new EventSource("/api/events");
    window.p2ActiveStreamTab = "auto";
    const SCROLLABLE_SELECTORS = ".mono-box, .event-box, .hierarchy-tree, .hierarchy-detail";

    function replaceChildren(node, children) {
      node.replaceChildren(...children);
    }

    function isScrollableSelectable(node) {
      return node instanceof HTMLElement && node.classList.contains("scroll-selectable");
    }

    function markScrollableSelectable(root = document) {
      const scopes = [];
      if (root instanceof HTMLElement) scopes.push(root);
      if (root instanceof Document) scopes.push(root.documentElement);
      for (const scope of scopes) {
        for (const node of scope.querySelectorAll(SCROLLABLE_SELECTORS)) {
          if (!(node instanceof HTMLElement)) continue;
          if (!node.classList.contains("scroll-selectable")) {
            node.classList.add("scroll-selectable");
          }
          if (!node.hasAttribute("tabindex")) {
            node.tabIndex = 0;
          }
        }
      }
    }

    function selectNodeContents(node) {
      if (!(node instanceof HTMLElement)) return;
      const selection = window.getSelection();
      if (!selection) return;
      const range = document.createRange();
      range.selectNodeContents(node);
      selection.removeAllRanges();
      selection.addRange(range);
    }

    function closestScrollableSelectable(target) {
      if (!target) return null;
      if (target instanceof HTMLElement) {
        return target.closest(".scroll-selectable");
      }
      if (target instanceof Node) {
        const parent = target.parentElement;
        if (parent instanceof HTMLElement) {
          return parent.closest(".scroll-selectable");
        }
      }
      return null;
    }

    function resolveSelectAllTarget() {
      const activeElement = document.activeElement;
      if (isScrollableSelectable(activeElement)) {
        return activeElement;
      }
      const selection = window.getSelection();
      const anchorNode = selection ? selection.anchorNode : null;
      const fromSelection = closestScrollableSelectable(anchorNode);
      return fromSelection instanceof HTMLElement ? fromSelection : null;
    }

    function pill(text) {
      const span = document.createElement("span");
      span.className = "pill";
      span.textContent = text;
      return span;
    }

    function formatDurationMs(value) {
      if (value === null || value === undefined || value === "") {
        return "n/a";
      }
      const seconds = Number(value) / 1000;
      if (!Number.isFinite(seconds)) {
        return "n/a";
      }
      return `${seconds.toFixed(seconds >= 10 ? 1 : 2)} 秒`;
    }

    function renderSummaryList(items) {
      const list = document.getElementById("summary-list");
      replaceChildren(list, items.map((item) => {
        const li = document.createElement("li");
        li.textContent = item;
        return li;
      }));
    }

    function phaseKey(runtimePhase) {
      const mapping = {
        context_selecting: "context_selecting",
        reflecting: "reflecting",
        generating: "generating",
        acting: "acting",
      };
      return mapping[runtimePhase] || "all";
    }

    function streamSectionMap(snapshot) {
      const sections = snapshot.current_stream_sections || {};
      return {
        all: snapshot.current_stream_text || "",
        context_selecting: sections["追加文脈選択"] || "",
        reflecting: sections["自己診断"] || "",
        generating: sections["コード生成"] || "",
        acting: sections["アクション実行"] || sections["acting"] || "",
      };
    }

    function updateStreamTabButtons(activeKey) {
      const buttons = document.querySelectorAll("[data-stream-tab]");
      for (const button of buttons) {
        const isActive = button.dataset.streamTab === activeKey;
        button.classList.toggle("active", isActive);
      }
    }

    window.setStreamTab = function setStreamTab(key) {
      window.p2ActiveStreamTab = key;
      const snapshot = window.__p2LatestSnapshot;
      if (snapshot) {
        renderStream(snapshot);
      }
    };
    window.__p2GoalEditorDirty = false;
    let p2InteractionHandlersBound = false;

    function ensureGoalEditorCard() {
      if (document.getElementById("goal-editor-text")) return;
      const maincol = document.querySelector(".maincol");
      if (!(maincol instanceof HTMLElement)) return;
      const section = document.createElement("section");
      section.className = "card";
      section.id = "goal-editor-card";
      section.innerHTML = `
        <h2>目的を編集してやり直し</h2>
        <div class="goal-editor">
          <div class="row"><strong>使い方:</strong> 目的を書き換えて適用すると、すぐ次の実行で反映されます。必要なら初期状態から再実行します。</div>
          <textarea id="goal-editor-text" spellcheck="false"></textarea>
          <div class="goal-editor-controls">
            <label>適用方法:
              <select id="goal-reset-mode">
                <option value="none">目的のみ更新</option>
                <option value="initial">初期状態からやり直し</option>
              </select>
            </label>
            <button type="button" id="goal-apply-button">適用して実行確認</button>
            <button type="button" id="p2-start-button">P2起動</button>
            <button type="button" id="p2-stop-button">P2停止</button>
            <span id="goal-editor-status" class="status">待機中</span>
          </div>
        </div>
      `;
      const cards = Array.from(maincol.querySelectorAll(".card"));
      const streamCard = cards.find((card) => {
        const heading = card.querySelector("h2");
        return heading && heading.textContent && heading.textContent.includes("モデルのリアルタイム出力");
      });
      if (streamCard) {
        maincol.insertBefore(section, streamCard);
      } else {
        maincol.prepend(section);
      }
    }

    window.applyGoalUpdate = async function applyGoalUpdate() {
      const textarea = document.getElementById("goal-editor-text");
      const resetModeSelect = document.getElementById("goal-reset-mode");
      const statusNode = document.getElementById("goal-editor-status");
      if (!(textarea instanceof HTMLTextAreaElement)) return;
      const goalText = textarea.value.trim();
      const resetMode = (resetModeSelect && resetModeSelect.value) ? resetModeSelect.value : "none";
      if (!goalText) {
        if (statusNode) statusNode.textContent = "ゴール本文を入力してください。";
        return;
      }
      if (statusNode) statusNode.textContent = "適用中...";
      try {
        const requestBody = {
          goal_text: goalText,
          reset_mode: resetMode,
        };
        let response = await fetch("/api/goal", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(requestBody),
        });
        if (!response.ok && response.status >= 500) {
          await new Promise((resolve) => setTimeout(resolve, 500));
          response = await fetch("/api/goal", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(requestBody),
          });
        }
        if (!response.ok) {
          let detail = "";
          try {
            const payload = await response.json();
            detail = payload && payload.error ? String(payload.error) : "";
          } catch (_error) {
            try {
              detail = await response.text();
            } catch (_innerError) {
              detail = "";
            }
          }
          throw new Error(detail || `HTTP ${response.status}`);
        }
        await response.json().catch(() => null);
        window.__p2GoalEditorDirty = false;
        if (statusNode) {
          statusNode.textContent = resetMode === "initial"
            ? "目的を更新し、初期状態からやり直しを適用しました。"
            : "目的を更新しました。";
        }
        const snapshotResponse = await fetch("/api/snapshot");
        if (snapshotResponse.ok) {
          const snapshot = await snapshotResponse.json();
          renderSnapshot(snapshot);
        }
      } catch (error) {
        if (statusNode) statusNode.textContent = `更新失敗: ${String(error)}`;
      }
    };

    window.applyRuntimeControl = async function applyRuntimeControl(action) {
      const statusNode = document.getElementById("goal-editor-status");
      const normalized = String(action || "").trim().toLowerCase();
      if (!["start", "stop"].includes(normalized)) {
        if (statusNode) statusNode.textContent = "不正な制御アクションです。";
        return;
      }
      if (statusNode) statusNode.textContent = normalized === "start" ? "P2 起動要求中..." : "P2 停止要求中...";
      try {
        const response = await fetch("/api/control", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({action: normalized}),
        });
        if (!response.ok) {
          let detail = "";
          try {
            const payload = await response.json();
            detail = payload && payload.error ? String(payload.error) : "";
          } catch (_error) {
            detail = "";
          }
          throw new Error(detail || `HTTP ${response.status}`);
        }
        const payload = await response.json().catch(() => ({}));
        if (statusNode) {
          const workerPid = payload && payload.worker_pid ? ` worker_pid=${payload.worker_pid}` : "";
          statusNode.textContent = normalized === "start" ? `P2 起動要求を送信しました。${workerPid}` : "P2 停止要求を送信しました。";
        }
        const snapshotResponse = await fetch("/api/snapshot");
        if (snapshotResponse.ok) {
          const snapshot = await snapshotResponse.json();
          renderSnapshot(snapshot);
        }
      } catch (error) {
        if (statusNode) statusNode.textContent = `制御失敗: ${String(error)}`;
      }
    };

    function bindInteractionHandlers() {
      if (p2InteractionHandlersBound) return;
      p2InteractionHandlersBound = true;

      document.addEventListener("click", (event) => {
        const target = event.target;
        if (!(target instanceof HTMLElement)) return;
        const tabButton = target.closest("[data-stream-tab]");
        if (tabButton instanceof HTMLElement) {
          const key = tabButton.dataset.streamTab || "all";
          window.setStreamTab(key);
          return;
        }
        const goalButton = target.closest("#goal-apply-button");
        if (goalButton instanceof HTMLElement) {
          window.applyGoalUpdate();
          return;
        }
        const startButton = target.closest("#p2-start-button");
        if (startButton instanceof HTMLElement) {
          window.applyRuntimeControl("start");
          return;
        }
        const stopButton = target.closest("#p2-stop-button");
        if (stopButton instanceof HTMLElement) {
          window.applyRuntimeControl("stop");
        }
      });
    }

    function labelForStatus(status) {
      const mapping = {
        started: "実行中",
        promoted: "昇格",
        rejected: "却下",
        failed: "異常終了",
        completed: "完了",
      };
      return mapping[status] || status || "不明";
    }

    function renderInsightCards(insights) {
      const container = document.getElementById("insight-list");
      if (!insights.length) {
        container.textContent = "まだ重要点はありません。";
        return;
      }
      const nodes = insights.map((insight) => {
        const row = document.createElement("div");
        row.className = "row";
        const top = document.createElement("div");
        top.className = "row-top";
        const left = document.createElement("span");
        left.textContent = insight.title || "重要点";
        const right = document.createElement("span");
        right.className = "status";
        right.textContent = insight.level || "info";
        top.append(left, right);
        const body = document.createElement("div");
        body.textContent = insight.body || "";
        row.append(top, body);
        return row;
      });
      replaceChildren(container, nodes);
    }

    function renderImplementationNotes(notes) {
      const container = document.getElementById("implementation-list");
      if (!notes.length) {
        container.textContent = "まだ情報はありません。";
        return;
      }
      const nodes = notes.map((note) => {
        const row = document.createElement("div");
        row.className = "row";
        const top = document.createElement("div");
        top.className = "row-top";
        const left = document.createElement("span");
        left.textContent = note.title || "項目";
        top.append(left);
        const body = document.createElement("div");
        body.textContent = note.body || "";
        row.append(top, body);
        return row;
      });
      replaceChildren(container, nodes);
    }

    function renderGenerationReport(entries) {
      const container = document.getElementById("generation-report-list");
      if (!container) return;
      if (!entries.length) {
        container.textContent = "まだ世代更新レポートはありません。";
        return;
      }
      const nodes = entries.map((entry) => {
        const row = document.createElement("div");
        row.className = "row";

        const top = document.createElement("div");
        top.className = "row-top";
        const left = document.createElement("span");
        left.textContent = `gen ${entry.generation || "n/a"} / ${entry.version_id || "n/a"}`;
        const right = document.createElement("span");
        right.className = "status";
        right.textContent = entry.candidate_id || "n/a";
        top.append(left, right);

        const target = document.createElement("div");
        target.textContent = `対象: ${entry.target_file || "n/a"}`;

        const changed = document.createElement("div");
        const fn = (entry.changed_functions || []).join(", ") || "関数検出なし";
        const added = entry.added_lines ?? "n/a";
        const removed = entry.removed_lines ?? "n/a";
        changed.textContent = `変更箇所: ${fn} / 差分: +${added} -${removed}`;

        const outcome = document.createElement("div");
        outcome.textContent = `実績: ${entry.outcome || "情報なし"}`;

        const excerpt = document.createElement("pre");
        excerpt.className = "mono-box";
        excerpt.style.height = "140px";
        excerpt.textContent = (entry.diff_excerpt || []).join("\\n") || "差分抜粋なし";

        row.append(top, target, changed, outcome, excerpt);
        return row;
      });
      replaceChildren(container, nodes);
    }

    function promptText(value) {
      if (value === null || value === undefined || value === "") {
        return "情報なし";
      }
      if (typeof value === "string") {
        const trimmed = value.trim();
        if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
          try {
            return JSON.stringify(JSON.parse(trimmed), null, 2);
          } catch (_error) {
            // fall through
          }
        }
        if (value.includes("\\\\u")) {
          try {
            const escaped = value.replaceAll("\\\\", "\\\\\\\\").replaceAll('"', '\\\\"');
            const decoded = JSON.parse(`"${escaped}"`);
            const decodedTrimmed = decoded.trim();
            if (decodedTrimmed.startsWith("{") || decodedTrimmed.startsWith("[")) {
              try {
                return JSON.stringify(JSON.parse(decodedTrimmed), null, 2);
              } catch (_error) {
                return decoded;
              }
            }
            return decoded;
          } catch (_error) {
            return value;
          }
        }
        return value;
      }
      try {
        return JSON.stringify(value, null, 2);
      } catch (_error) {
        return String(value);
      }
    }

    function renderPromptSnapshots(snapshot) {
      const promptSnapshots = snapshot.latest_prompt_snapshots || [];
      const latestPrompt = snapshot.latest_prompt_snapshot || promptSnapshots[promptSnapshots.length - 1] || {};
      const meta = document.getElementById("prompt-meta");
      const systemPrompt = document.getElementById("prompt-system");
      const userPrompt = document.getElementById("prompt-user");
      const requestBody = document.getElementById("prompt-request-body");

      if (!latestPrompt || !Object.keys(latestPrompt).length) {
        meta.textContent = "まだ prompt snapshot はありません。";
        systemPrompt.textContent = "情報なし";
        userPrompt.textContent = "情報なし";
        requestBody.textContent = "情報なし";
        return;
      }

      const requestInfo = latestPrompt.request || {};
      const parts = [
        `phase=${latestPrompt.phase || "n/a"}`,
        `step=${latestPrompt.step ?? "n/a"}`,
        `frame=${latestPrompt.frame_id || "n/a"}`,
        `depth=${latestPrompt.frame_depth ?? "n/a"}`,
        `model=${latestPrompt.model || "n/a"}`,
      ];
      if (requestInfo.transport) parts.push(`transport=${requestInfo.transport}`);
      if (requestInfo.url) parts.push(`url=${requestInfo.url}`);
      meta.textContent = parts.join(" / ");
      systemPrompt.textContent = promptText(latestPrompt.system_prompt);
      userPrompt.textContent = promptText(latestPrompt.user_prompt);
      requestBody.textContent = promptText(requestInfo.request_body || requestInfo.request_payload || "");
    }

    function renderSkillCards(skills) {
      const container = document.getElementById("skill-list");
      if (!skills.length) {
        container.textContent = "まだスキルはありません。";
        return;
      }
      const nodes = skills.map((skill) => {
        const row = document.createElement("div");
        row.className = "row";
        const top = document.createElement("div");
        top.className = "row-top";
        const left = document.createElement("span");
        left.textContent = skill.title || skill.skill_id || "skill";
        const right = document.createElement("span");
        right.className = "status";
        right.textContent = skill.skill_id || "";
        top.append(left, right);
        row.appendChild(top);

        const addText = (label, value) => {
          if (!value) return;
          const block = document.createElement("div");
          const strong = document.createElement("strong");
          strong.textContent = `${label}: `;
          block.append(strong, document.createTextNode(value));
          row.appendChild(block);
        };

        addText("概要", skill.summary || "");
        addText("期待効果", skill.expected_benefit || "");

        const addList = (label, values) => {
          const items = (values || []).filter(Boolean);
          if (!items.length) return;
          const block = document.createElement("div");
          const strong = document.createElement("strong");
          strong.textContent = `${label}: `;
          block.appendChild(strong);
          block.appendChild(document.createTextNode(items.join(" / ")));
          row.appendChild(block);
        };

        addList("使いどころ", skill.when_useful || []);
        addList("使い方", skill.how_to_use || []);
        addList("タグ", skill.keywords || []);
        return row;
      });
      replaceChildren(container, nodes);
    }

    function renderMemoCards(memos, latestSelfMemo) {
      const container = document.getElementById("memo-list");
      const nodes = [];

      if (latestSelfMemo && Object.keys(latestSelfMemo).length) {
        const row = document.createElement("div");
        row.className = "row";
        const top = document.createElement("div");
        top.className = "row-top";
        const left = document.createElement("span");
        left.textContent = "今回の自己メモ";
        const right = document.createElement("span");
        right.className = "status";
        right.textContent = `${Math.round(Number(latestSelfMemo.confidence || 0) * 100)}%`;
        top.append(left, right);
        row.appendChild(top);
        const lines = [
          ["題名", latestSelfMemo.title],
          ["戦術", latestSelfMemo.tactic],
          ["理由", latestSelfMemo.why],
          ["使う条件", latestSelfMemo.when],
          ["タグ", (latestSelfMemo.tags || []).join(" / ")],
        ];
        for (const [label, value] of lines) {
          if (!value) continue;
          const block = document.createElement("div");
          const strong = document.createElement("strong");
          strong.textContent = `${label}: `;
          block.append(strong, document.createTextNode(value));
          row.appendChild(block);
        }
        nodes.push(row);
      }

      for (const memo of memos || []) {
        const row = document.createElement("div");
        row.className = "row";
        const top = document.createElement("div");
        top.className = "row-top";
        const left = document.createElement("span");
        left.textContent = memo.title || memo.memo_id || "memo";
        const right = document.createElement("span");
        right.className = "status";
        right.textContent = `${memo.memo_id || ""}${memo.confidence !== undefined && memo.confidence !== null ? ` / ${Math.round(Number(memo.confidence) * 100)}%` : ""}`;
        top.append(left, right);
        row.appendChild(top);

        const addText = (label, value) => {
          if (!value) return;
          const block = document.createElement("div");
          const strong = document.createElement("strong");
          strong.textContent = `${label}: `;
          block.append(strong, document.createTextNode(value));
          row.appendChild(block);
        };

        addText("tactic", memo.tactic || "");
        addText("why", memo.why || "");
        addText("when", memo.when || "");
        addText("由来", memo.source_candidate_id || "");
        addText("証拠", [memo.evidence?.error_type, memo.evidence?.failure_detail].filter(Boolean).join(" / "));
        addText("タグ", (memo.tags || []).join(" / "));
        nodes.push(row);
      }

      if (!nodes.length) {
        container.textContent = "まだメモはありません。";
        return;
      }
      replaceChildren(container, nodes);
    }

    function renderReasoningList(reasoning) {
      const list = document.getElementById("reasoning-list");
      const fields = [
        ["problem_statement", "問題認識"],
        ["diagnosis", "診断"],
        ["edit_intent", "変更意図"],
        ["why_this_file", "対象ファイルの理由"],
        ["expected_effect", "期待効果"],
        ["validation_hypothesis", "検証仮説"],
        ["next_if_fail", "次の一手"],
      ];
      const hasContent = fields.some(([key]) => reasoning && reasoning[key]);
      if (!hasContent) {
        list.innerHTML = "<li>まだ思考要約はありません。</li>";
        return;
      }
      const items = fields.map(([key, label]) => {
        const li = document.createElement("li");
        const strong = document.createElement("strong");
        strong.textContent = `${label}:`;
        li.appendChild(strong);
        li.appendChild(document.createTextNode(` ${reasoning[key] || ""}`));
        return li;
      });
      replaceChildren(list, items);
    }

    function renderReflectionList(pre, post) {
      const container = document.getElementById("reflection-list");
      const sections = [];
      const preEntries = Object.entries(pre || {}).filter(([, value]) => value);
      const postEntries = Object.entries(post || {}).filter(([, value]) => value);
      if (!preEntries.length && !postEntries.length) {
        container.textContent = "まだ自己診断はありません。";
        return;
      }
      const appendSection = (title, entries) => {
        if (!entries.length) {
          return;
        }
        const labels = {
          what_i_tried: "何を試したか",
          what_kept_happening: "何が繰り返し起きたか",
          what_this_suggests_about_my_search: "探索の何が問題だと読んだか",
          what_i_might_be_missing: "見落としている可能性",
          what_must_be_different_this_time: "今回変えないといけない点",
          did_i_actually_change_behavior: "実際に行動を変えたか",
          how_is_this_different_from_recent_failures: "最近の失敗と何が違うか",
          why_this_is_not_another_no_change: "なぜ今回が実質無変更ではないか",
          remaining_risk: "残るリスク",
        };
        const block = document.createElement("div");
        block.className = "row";
        const heading = document.createElement("div");
        heading.className = "row-top";
        const left = document.createElement("span");
        left.textContent = title;
        heading.append(left);
        block.appendChild(heading);
        const list = document.createElement("ul");
        for (const [key, value] of entries) {
          const li = document.createElement("li");
          const strong = document.createElement("strong");
          strong.textContent = `${labels[key] || key}:`;
          li.appendChild(strong);
          li.appendChild(document.createTextNode(` ${value}`));
          list.appendChild(li);
        }
        block.appendChild(list);
        sections.push(block);
      };
      appendSection("変更前の自己診断", preEntries);
      appendSection("変更後の自己評価", postEntries);
      replaceChildren(container, sections);
    }

    function renderContextList(selection, delta) {
      const container = document.getElementById("context-list");
      const sections = [];
      const selected = ((selection || {}).selected_context || []).filter(Boolean);
      const latestFailure = (delta || {}).latest_failure || {};
      const actionRaw = (delta || {}).action_raw || {};
      const resultRaw = (delta || {}).result_raw || {};
      const mustAvoid = ((delta || {}).must_avoid_next || []).filter(Boolean);
      if (!selected.length && !latestFailure.summary && !mustAvoid.length) {
        container.textContent = "まだ追加文脈はありません。";
        return;
      }
      if (selected.length) {
        const block = document.createElement("div");
        block.className = "row";
        const heading = document.createElement("div");
        heading.className = "row-top";
        const left = document.createElement("span");
        left.textContent = "今回読んだ追加文脈";
        heading.append(left);
        block.appendChild(heading);
        const list = document.createElement("ul");
        for (const item of selected) {
          const li = document.createElement("li");
          li.textContent = item;
          list.appendChild(li);
        }
        block.appendChild(list);
        sections.push(block);
      }
      if (latestFailure.summary || mustAvoid.length) {
        const block = document.createElement("div");
        block.className = "row";
        const heading = document.createElement("div");
        heading.className = "row-top";
        const left = document.createElement("span");
        left.textContent = "直近失敗の局所差分";
        heading.append(left);
        block.appendChild(heading);
        const list = document.createElement("ul");
        if (latestFailure.error_type) {
          const li = document.createElement("li");
          li.textContent = `失敗型: ${latestFailure.error_type}`;
          list.appendChild(li);
        }
        if (latestFailure.file) {
          const li = document.createElement("li");
          li.textContent = `場所: ${latestFailure.file}${latestFailure.line ? `:${latestFailure.line}` : ""}`;
          list.appendChild(li);
        }
        if (latestFailure.detail) {
          const li = document.createElement("li");
          li.textContent = `詳細: ${latestFailure.detail}`;
          list.appendChild(li);
        }
        for (const item of mustAvoid) {
          const li = document.createElement("li");
          li.textContent = `次回制約: ${item}`;
          list.appendChild(li);
        }
        block.appendChild(list);
        sections.push(block);
      }
      if (actionRaw.diff_excerpt || actionRaw.after_snippet || actionRaw.before_snippet) {
        const block = document.createElement("div");
        block.className = "row";
        const heading = document.createElement("div");
        heading.className = "row-top";
        const left = document.createElement("span");
        left.textContent = "直近変更の raw";
        heading.append(left);
        block.appendChild(heading);
        const list = document.createElement("ul");
        if ((actionRaw.changed_line_numbers || []).length) {
          const li = document.createElement("li");
          li.textContent = `変更行: ${(actionRaw.changed_line_numbers || []).join(", ")}`;
          list.appendChild(li);
        }
        if (actionRaw.diff_excerpt) {
          const li = document.createElement("li");
          li.textContent = `diff: ${actionRaw.diff_excerpt}`;
          list.appendChild(li);
        }
        if (actionRaw.before_snippet) {
          const li = document.createElement("li");
          li.textContent = `変更前: ${actionRaw.before_snippet}`;
          list.appendChild(li);
        }
        if (actionRaw.after_snippet) {
          const li = document.createElement("li");
          li.textContent = `変更後: ${actionRaw.after_snippet}`;
          list.appendChild(li);
        }
        block.appendChild(list);
        sections.push(block);
      }
      if (resultRaw.failure_snippet || resultRaw.stderr_excerpt) {
        const block = document.createElement("div");
        block.className = "row";
        const heading = document.createElement("div");
        heading.className = "row-top";
        const left = document.createElement("span");
        left.textContent = "直近結果の raw";
        heading.append(left);
        block.appendChild(heading);
        const list = document.createElement("ul");
        if ((resultRaw.command || []).length) {
          const li = document.createElement("li");
          li.textContent = `コマンド: ${(resultRaw.command || []).join(" ")}`;
          list.appendChild(li);
        }
        if (resultRaw.returncode !== null && resultRaw.returncode !== undefined) {
          const li = document.createElement("li");
          li.textContent = `return code: ${resultRaw.returncode}`;
          list.appendChild(li);
        }
        if (resultRaw.failure_file) {
          const li = document.createElement("li");
          li.textContent = `失敗位置: ${resultRaw.failure_file}${resultRaw.failure_line ? `:${resultRaw.failure_line}` : ""}`;
          list.appendChild(li);
        }
        if (resultRaw.failure_snippet) {
          const li = document.createElement("li");
          li.textContent = `失敗箇所: ${resultRaw.failure_snippet}`;
          list.appendChild(li);
        }
        if (resultRaw.stderr_excerpt) {
          const li = document.createElement("li");
          li.textContent = `stderr: ${resultRaw.stderr_excerpt}`;
          list.appendChild(li);
        }
        block.appendChild(list);
        sections.push(block);
      }
      replaceChildren(container, sections);
    }

    function renderContextAudit(audit) {
      const container = document.getElementById("context-audit-list");
      const checks = ((audit || {}).checks || []).filter(Boolean);
      if (!checks.length) {
        container.textContent = "まだ監査結果はありません。";
        return;
      }
      const nodes = checks.map((check) => {
        const row = document.createElement("div");
        row.className = "row";
        const top = document.createElement("div");
        top.className = "row-top";
        const left = document.createElement("span");
        left.textContent = check.label || "監査項目";
        const right = document.createElement("span");
        right.className = "status";
        right.textContent = check.status_label || check.status || "n/a";
        top.append(left, right);
        row.appendChild(top);
        if (check.detail) {
          const body = document.createElement("div");
          body.textContent = check.detail;
          row.appendChild(body);
        }
        return row;
      });
      replaceChildren(container, nodes);
    }

    function renderHierarchicalContext(snapshot) {
      const hierarchy = snapshot.task_hierarchy || [];
      const treeContainer = document.getElementById("hierarchy-tree");
      const detailContainer = document.getElementById("hierarchy-detail");
      const pathContainer = document.getElementById("hierarchy-current-path");
      const historyContainer = document.getElementById("thought-history-tree");
      const historySource = document.getElementById("thought-history-source");
      const historyCurrentEventCount = document.getElementById("thought-history-current-event-count");
      const historyCurrentEventPresence = document.getElementById("thought-history-current-event-presence");
      if (historySource) {
        historySource.textContent = snapshot.thought_action_chain_source || "current_snapshot";
      }
      if (historyCurrentEventCount) {
        historyCurrentEventCount.textContent = String(snapshot.current_candidate_event_count ?? 0);
      }
      if (historyCurrentEventPresence) {
        historyCurrentEventPresence.textContent = snapshot.current_candidate_has_events ? "yes" : "no";
      }
      if (!hierarchy.length) {
        treeContainer.textContent = "まだ階層構造はありません。";
        detailContainer.textContent = "まだ階層コンテキストはありません。";
        if (pathContainer) pathContainer.textContent = "まだ現在の思考パスはありません。";
      }

      const thoughtActionChain = snapshot.thought_action_chain || [];
      const thoughtHistory = snapshot.thought_history || [];
      if (!thoughtActionChain.length && !thoughtHistory.length) {
        if (historyContainer) historyContainer.textContent = "まだ思考履歴はありません。";
      } else if (historyContainer) {
        const historyNodes = thoughtActionChain.length
          ? thoughtActionChain.map((event) => {
              const row = document.createElement("div");
              row.className = "hierarchy-node";
              row.style.marginLeft = `${Math.max(0, Number(event.depth || 0)) * 18}px`;
              const top = document.createElement("div");
              top.className = "hierarchy-node-top";
              const left = document.createElement("span");
              const frameTitle = event.frame_goal || event.frame_id || "frame";
              left.textContent = `step ${event.step ?? "?"} / ${event.action || "unknown"} / ${frameTitle}`;
              const right = document.createElement("span");
              right.className = "status";
              right.textContent = event.result_ok === false ? "failed" : "ok";
              top.append(left, right);
              row.append(top);

              const addMeta = (label, value) => {
                if (!value) return;
                const detail = document.createElement("div");
                detail.className = "hierarchy-node-meta";
                detail.textContent = `${label}: ${value}`;
                row.append(detail);
              };

              addMeta("timestamp", event.timestamp || "n/a");
              addMeta("frame", `${event.frame_id || "frame"} / depth=${event.depth ?? 0}`);
              addMeta("thinking", event.thinking || "");
              addMeta("action_input", event.action_input_text || "");
              addMeta("result", event.result_text || "");
              const nextParts = [];
              if (event.transition_label) nextParts.push(event.transition_label);
              if (event.next_action) nextParts.push(`次action=${event.next_action}`);
              if (event.next_thinking) nextParts.push(`次thinking=${event.next_thinking}`);
              addMeta("next", nextParts.join(" / "));
              return row;
            })
          : thoughtHistory.map((event) => {
              const row = document.createElement("div");
              row.className = "hierarchy-node";
              row.style.marginLeft = `${Math.max(0, Number(event.depth || 0)) * 18}px`;
              const top = document.createElement("div");
              top.className = "hierarchy-node-top";
              const left = document.createElement("span");
              left.textContent = event.label || event.frame_id || "frame";
              const right = document.createElement("span");
              right.className = "status";
              right.textContent =
                event.type === "return_to_parent"
                  ? "親へ返却"
                  : event.type === "recent_attempt_frame"
                    ? "最近の試行から抽出"
                  : event.type === "frame_context"
                    ? "フレーム"
                    : "子へ分解";
              top.append(left, right);
              const meta = document.createElement("div");
              meta.className = "hierarchy-node-meta";
              meta.textContent = `${event.timestamp || "n/a"} / depth=${event.depth ?? 0} / ${event.message || event.outcome || ""}`;
              row.append(top, meta);
              const carryBack = event.finding || event.unresolved || event.focus || "";
              const details = [
                ["狙い", event.summary],
                ["判断理由", event.reason],
                ["持ち帰り", carryBack],
              ].filter(([, value]) => value);
              for (const [label, value] of details) {
                const detail = document.createElement("div");
                detail.className = "hierarchy-node-meta";
                detail.textContent = `${label}: ${value}`;
                row.append(detail);
              }
              return row;
            });
        replaceChildren(historyContainer, historyNodes);
      }

      if (!hierarchy.length) {
        return;
      }

      const nodesById = new Map();
      for (const node of hierarchy) {
        nodesById.set(node.frame_id, {...node, children: []});
      }
      const roots = [];
      for (const node of nodesById.values()) {
        if (node.parent_frame_id && nodesById.has(node.parent_frame_id)) {
          nodesById.get(node.parent_frame_id).children.push(node);
        } else {
          roots.push(node);
        }
      }

      const renderTreeNode = (node) => {
        const item = document.createElement("div");
        item.className = `hierarchy-node${node.is_current ? " current" : ""}`;
        const top = document.createElement("div");
        top.className = "hierarchy-node-top";
        const title = document.createElement("span");
        title.textContent = node.goal || node.frame_id || "frame";
        const status = document.createElement("span");
        status.className = "status";
        status.textContent = node.is_current ? `current / ${node.result_status || "active"}` : (node.result_status || "active");
        top.append(title, status);
        const meta = document.createElement("div");
        meta.className = "hierarchy-node-meta";
        meta.textContent = `depth=${node.depth ?? 0} / ${node.search_mode || "unknown"} / ${node.decision_label || node.decision || "このフレームで続行"}`;
        item.append(top, meta);
        if ((node.children || []).length) {
          const childWrap = document.createElement("div");
          childWrap.className = "hierarchy-tree-children";
          for (const child of node.children) {
            childWrap.appendChild(renderTreeNode(child));
          }
          item.appendChild(childWrap);
        }
        return item;
      };

      replaceChildren(treeContainer, roots.map((node) => renderTreeNode(node)));

      const currentFrame = hierarchy.find((frame) => frame.is_current) || hierarchy[hierarchy.length - 1];
      const currentPath = [];
      if (currentFrame) {
        let cursor = currentFrame;
        const byId = new Map(hierarchy.map((frame) => [frame.frame_id, frame]));
        while (cursor) {
          currentPath.push(cursor);
          cursor = cursor.parent_frame_id ? byId.get(cursor.parent_frame_id) : null;
        }
      }
      if (pathContainer) {
        const pathText = currentPath.length
          ? currentPath
              .slice()
              .reverse()
              .map((frame) => frame.goal || frame.frame_id || "frame")
              .join(" -> ")
          : "まだ現在の思考パスはありません。";
        pathContainer.textContent = pathText;
      }

      const detailFrames = currentPath.length
        ? currentPath.concat(hierarchy.filter((frame) => !currentPath.some((entry) => entry.frame_id === frame.frame_id)))
        : hierarchy;
      const detailBlocks = detailFrames.map((frame) => {
        const block = document.createElement("div");
        block.className = `hierarchy-detail-block${frame.is_current ? " current" : ""}`;

        const header = document.createElement("div");
        header.className = "row-top";
        const left = document.createElement("span");
        left.textContent = frame.goal || frame.frame_id || "frame";
        const right = document.createElement("span");
        right.className = "status";
        right.textContent = frame.is_current ? "current" : `depth=${frame.depth ?? 0}`;
        header.append(left, right);
        block.appendChild(header);

        const addRow = (label, value) => {
          if (!value) return;
          const row = document.createElement("div");
          row.className = "row";
          const strong = document.createElement("strong");
          strong.textContent = `${label}: `;
          row.appendChild(strong);
          row.appendChild(document.createTextNode(value));
          block.appendChild(row);
        };

        addRow("上位目的", frame.parent_goal);
        addRow("現在フレームの目的", frame.goal);
        addRow("対象ファイル", frame.target_file);
        addRow("探索モード", frame.search_mode);
        addRow("この階層の問い", frame.question_to_answer);
        addRow("この階層でやり切ること", frame.commitment);
        addRow("現在フォーカス", frame.current_focus);
        addRow("読んだ参照", (frame.selected_context || []).join(", "));
        addRow("解決済み参照", (frame.resolved_context_keys || []).join(", "));
        addRow("継承元フレーム", (frame.inherited_frame_ids || []).join(" -> "));
        addRow("継承済み上位目標", (frame.inherited_goal_chain || []).join(" / "));
        addRow("継承した tool result 数", String(frame.inherited_tool_result_count || 0));
        addRow("継承済み知見", (frame.inherited_findings || []).join(" / "));
        addRow("この階層の tool result 数", String(frame.local_tool_result_count || 0));
        addRow("この階層で観測済みファイル", (frame.local_observed_files || []).join(", "));
        addRow("この階層で観測済み記号", (frame.local_observed_symbols || []).join(", "));
        addRow("この階層で得た知見", (frame.local_learned_findings || []).join(" / "));
        addRow("この階層の未解決問い", (frame.local_unresolved_questions || []).join(" / "));
        addRow("受け取った子返却数", String(frame.child_return_count || 0));
        addRow("子フレームから受け取った返却", (frame.child_return_summaries || []).join(" / "));
        addRow("このフレームが返す要約", frame.return_payload_summary);
        if (frame.latest_failure && (frame.latest_failure.error_type || frame.latest_failure.file || frame.latest_failure.detail)) {
          const parts = [];
          if (frame.latest_failure.error_type) parts.push(frame.latest_failure.error_type);
          if (frame.latest_failure.file) parts.push(`${frame.latest_failure.file}${frame.latest_failure.line ? `:${frame.latest_failure.line}` : ""}`);
          if (frame.latest_failure.detail) parts.push(frame.latest_failure.detail);
          addRow("局所失敗差分", parts.join(" / "));
        }
        addRow("次回制約", (frame.must_avoid_next || []).join(" / "));
        addRow("フレーム遷移要求", frame.decision_label || frame.decision);
        addRow("判断理由", frame.decision_reason);
        addRow("次に目指すこと", frame.next_goal);
        addRow("この階層の結果", frame.result_status);
        addRow("結果要約", frame.result_summary);
        return block;
      });

      replaceChildren(detailContainer, detailBlocks);
    }

    function renderRows(containerId, rows, formatter) {
      const container = document.getElementById(containerId);
      if (!rows.length) {
        container.textContent = containerId === "attempt-list" ? "まだ試行はありません。" : "まだ履歴はありません。";
        return;
      }
      const nodes = rows.map(formatter);
      replaceChildren(container, nodes);
    }

    function renderSessionEvents(snapshot) {
      const events = snapshot.latest_session_events || [];
      const container = document.getElementById("session-event-list");
      if (!events.length) {
        const runtime = snapshot.runtime_status || {};
        const row = document.createElement("div");
        row.className = "row";
        const top = document.createElement("div");
        top.className = "row-top";
        const left = document.createElement("span");
        left.textContent = "runtime / heartbeat";
        const right = document.createElement("span");
        right.className = "status";
        right.textContent = "live";
        top.append(left, right);
        row.appendChild(top);

        const detail = document.createElement("div");
        detail.textContent = `phase=${runtime.phase || runtime.current_phase || "待機"} / action=${runtime.current_action || runtime.current_step || "待機"} / event=${runtime.last_event || "情報なし"}`;
        row.appendChild(detail);

        const updatedAt = document.createElement("div");
        updatedAt.textContent = `updated_at=${runtime.updated_at || snapshot.generated_at || "情報なし"}`;
        row.appendChild(updatedAt);

        replaceChildren(container, [row]);
        return;
      }
      const nodes = events.map((event) => {
        const row = document.createElement("div");
        row.className = "row";
        const top = document.createElement("div");
        top.className = "row-top";
        const left = document.createElement("span");
        left.textContent = `step ${event.step ?? "?"} / ${event.action || "unknown"}`;
        const right = document.createElement("span");
        right.className = "status";
        right.textContent = event.result && event.result.ok === false ? "failed" : "ok";
        top.append(left, right);
        row.appendChild(top);

        const addRow = (label, value) => {
          if (!value) return;
          const line = document.createElement("div");
          const strong = document.createElement("strong");
          strong.textContent = `${label}: `;
          line.append(strong, document.createTextNode(value));
          row.appendChild(line);
        };

        addRow("thinking", event.thinking || "");
        addRow("入力", JSON.stringify(event.action_input || {}));
        addRow("結果", snapshot.latest_session_event_summary_map?.[String(event.step)] || "");
        return row;
      });
      replaceChildren(container, nodes);
    }

    function renderStream(snapshot) {
      window.__p2LatestSnapshot = snapshot;
      const runtime = snapshot.runtime_status || {};
      const latestAttempt = snapshot.latest_attempt || {};
      const latestCompleted = snapshot.latest_completed_attempt || {};
      const latestSessionEvents = snapshot.latest_session_events || [];
      const latestSessionEvent = latestSessionEvents.length ? latestSessionEvents[0] : null;
      const sections = streamSectionMap(snapshot);
      const isAutoMode = window.p2ActiveStreamTab === "auto";
      const preferredKey = isAutoMode ? phaseKey(runtime.phase) : window.p2ActiveStreamTab;
      const activeKey = isAutoMode ? (sections[preferredKey] ? preferredKey : "all") : preferredKey;
      const isRunning = runtime.status === "running";
      const activeLabel = {
        all: "全体",
        context_selecting: "追加文脈選択",
        reflecting: "自己診断",
        generating: "コード生成",
        acting: "アクション実行",
      }[activeKey] || activeKey;
      const stream = sections[activeKey] || (
        activeKey === "all"
          ? (isRunning ? "モデル出力を待機中です。" : "まだモデル出力はありません。")
          : `${activeLabel} はまだ出力がありません。`
      );
      const panel = document.getElementById("stream-output");
      panel.textContent = stream;
      panel.scrollTop = panel.scrollHeight;
      updateStreamTabButtons(activeKey);

      const inferPhaseFromEvent = (lastEvent) => {
        if (!lastEvent) return "";
        if (lastEvent.includes("context_selecting")) return "context_selecting";
        if (lastEvent.includes("reflecting")) return "reflecting";
        if (lastEvent.includes("generating")) return "generating";
        if (
          lastEvent.includes("acting") ||
          lastEvent.includes("session_event") ||
          lastEvent.includes("action_") ||
          lastEvent.includes("child_output")
        ) {
          return "acting";
        }
        return "";
      };
      const effectivePhase = runtime.phase || inferPhaseFromEvent(runtime.last_event) || "待機";
      document.getElementById("stream-phase").textContent = effectivePhase;

      const effectiveModel =
        runtime.model ||
        runtime.thinking_model ||
        latestAttempt.selected_coding_model ||
        latestCompleted.selected_coding_model ||
        "不明";
      document.getElementById("stream-current-model").textContent = effectiveModel;
      document.getElementById("stream-current-kernel").textContent =
        runtime.current_runtime_kernel || snapshot.latest_runtime_kernel || "不明";
      const fallbackAction = latestSessionEvent && latestSessionEvent.action
        ? `step ${latestSessionEvent.step || "?"} / ${latestSessionEvent.action}`
        : (isRunning ? "実行中" : "待機");
      document.getElementById("stream-current-action").textContent =
        runtime.current_action ? `step ${runtime.current_action_step || "?"} / ${runtime.current_action}` : fallbackAction;
      document.getElementById("stream-model-plan").textContent =
        `思考モデル=${runtime.thinking_model || "不明"} / コーディングモデル=${runtime.coding_model || "不明"} / 探索コーディングモデル=${runtime.exploratory_coding_model || "不明"} / 停滞打開モデル=${runtime.stagnation_coding_model || "不明"}`;
      document.getElementById("stream-selected-coding-model").textContent =
        latestAttempt.selected_coding_model || latestCompleted.selected_coding_model || "未選択";
      document.getElementById("stream-active-tab").textContent = activeLabel;
      const trend = snapshot.llm_timing_trend || {};
      const latestTiming = snapshot.latest_llm_timings || {};
      document.getElementById("stream-timing").textContent =
        `合計: ${formatDurationMs(latestTiming.total_duration_ms)} / 最近平均: ${formatDurationMs(trend.recent_average_duration_ms)}`;
    }

    function explainDecision(reason, status, validationSummary) {
      if (status === "promoted") {
        return "検証に通過し、昇格後の再検証も成功しました。";
      }
      if (status === "rolled_back") {
        return "候補自体は通ったが、昇格後の再検証で失敗したため元に戻しました。";
      }
      if (status === "failed") {
        const reasonText = (reason || "").trim();
        if (reasonText.includes("previous_run 中に異常終了") || reasonText.includes("attempt 完了前に終了")) {
          return `前回の run-loop が attempt 完了前に停止したため、この試行は未完了のまま次回起動時に failed として回収されました。${reasonText}`.trim();
        }
        return `試行が途中で停止し、完了状態まで記録できませんでした。${reasonText}`.trim();
      }
      if (reason === "validation failed") {
        return `検証に失敗しました。${validationSummary || ""}`.trim();
      }
      if (reason === "candidate did not change the target file") {
        return "候補に実質的な差分がなかったため却下しました。";
      }
      if ((reason || "").startsWith("candidate touched protected path")) {
        return "保護対象のパスに触れたため却下しました。";
      }
      if ((reason || "").includes("低価値な変更")) {
        return reason;
      }
      return reason || "情報なし";
    }

    function renderAttemptRow(attempt) {
      const row = document.createElement("div");
      row.className = "row";
      const top = document.createElement("div");
      top.className = "row-top";
      const left = document.createElement("span");
      left.textContent = attempt.candidate_id || "不明";
      const right = document.createElement("span");
      right.className = "status";
      right.textContent = labelForStatus(attempt.status);
      top.append(left, right);
      const target = document.createElement("div");
      target.textContent = `対象: ${attempt.target_file || "不明"}`;
      const reason = document.createElement("div");
      reason.textContent = `結果: ${explainDecision(attempt.decision_reason, attempt.status, attempt.validation_summary)}`;
      row.append(top, target, reason);
      return row;
    }

    function renderHistoryRow(entry) {
      const row = document.createElement("div");
      row.className = "row";
      const top = document.createElement("div");
      top.className = "row-top";
      const left = document.createElement("span");
      left.textContent = entry.timestamp || "不明";
      const right = document.createElement("span");
      right.className = "status";
      right.textContent = entry.outcome || "不明";
      top.append(left, right);
      const body = document.createElement("div");
      body.textContent = `${entry.step || "step"}: ${entry.message || ""}`;
      row.append(top, body);
      return row;
    }

    function renderSnapshot(snapshot) {
      bindInteractionHandlers();
      ensureGoalEditorCard();
      const goal = snapshot.goal || {};
      const runtime = snapshot.runtime_status || {};
      const version = snapshot.version || {};
      const latestAttempt = snapshot.latest_attempt || {};
      const latestValidation = snapshot.latest_validation || null;
      const latestCompleted = snapshot.latest_completed_attempt || {};
      const latestReasoning = snapshot.latest_reasoning_summary || latestCompleted.reasoning_summary || {};
      const latestPreReflection = snapshot.latest_pre_edit_reflection || latestCompleted.pre_edit_reflection || {};
      const latestPostReflection = snapshot.latest_post_edit_reflection || latestCompleted.post_edit_reflection || {};
      const latestSelectedContext = snapshot.latest_selected_context || latestCompleted.selected_context_payload || {};
      const latestDeltaContext = snapshot.latest_delta_context || latestCompleted.delta_context || {};
      const operatorInsights = snapshot.operator_insights || [];
      const systemSkills = snapshot.system_skills || [];
      const recentMemos = snapshot.recent_memos || [];
      const latestSelfMemo = snapshot.latest_self_memo || null;
      const recentAttempts = snapshot.recent_attempts || [];
      const recentHistory = snapshot.recent_history || [];
      const generationReport = snapshot.generation_report || [];
      const goalEditor = document.getElementById("goal-editor-text");
      const activeElement = document.activeElement;

      // Keep realtime stream visible even if another panel renderer fails later.
      renderStream(snapshot);

      document.getElementById("goal-text").textContent = goal.text || "情報なし";
      if (goalEditor instanceof HTMLTextAreaElement) {
        const shouldSync = !window.__p2GoalEditorDirty && activeElement !== goalEditor;
        if (shouldSync) {
          goalEditor.value = goal.text || "";
        }
      }
      document.getElementById("latest-attempt").textContent = latestAttempt.candidate_id || "なし";
      document.getElementById("latest-validation").textContent = latestValidation ? (latestValidation.passed ? "成功" : "失敗") : "情報なし";
      document.getElementById("latest-completed-attempt").textContent = latestCompleted.candidate_id || "なし";
      document.getElementById("latest-completed-status").textContent = labelForStatus(latestCompleted.status);
      document.getElementById("latest-completed-target").textContent = latestCompleted.target_file || "不明";
      document.getElementById("latest-completed-failure").textContent = explainDecision(
        latestCompleted.decision_reason,
        latestCompleted.status,
        latestCompleted.validation_summary,
      );
      document.getElementById("latest-completed-alternative").textContent = latestCompleted.chosen_response || "情報なし";
      document.getElementById("latest-completed-clone-reason").textContent = latestCompleted.clone_reason || "情報なし";

      replaceChildren(document.getElementById("meta-pills"), [
        pill(`世代: ${snapshot.active_generation || "不明"}`),
        pill(`ゴール: ${goal.status || "不明"}`),
        pill(`状態: ${runtime.status || "不明"}`),
        pill(`候補: ${runtime.current_candidate_id || "なし"}`),
      ]);

      renderSummaryList([
        `ゴール状態: ${goal.status || "不明"}`,
        `現在世代: ${version.active_generation || "不明"}`,
        `現在版: ${version.active_version_id || "不明"}`,
        `実行状態: ${runtime.status || "不明"}`,
        `現在候補: ${runtime.current_candidate_id || "不明"}`,
        `直近検証: ${latestValidation ? (latestValidation.passed ? "成功" : "失敗") : "情報なし"}`,
      ]);

      renderReasoningList(latestReasoning);
      renderReflectionList(latestPreReflection, latestPostReflection);
      renderContextList(latestSelectedContext, latestDeltaContext);
      renderContextAudit(snapshot.context_audit || {});
      renderHierarchicalContext(snapshot);
      renderPromptSnapshots(snapshot);
      renderInsightCards(operatorInsights);
      renderGenerationReport(generationReport);
      renderImplementationNotes(snapshot.implementation_notes || []);
      renderSkillCards(systemSkills);
      renderMemoCards(recentMemos, latestSelfMemo);
      renderRows("attempt-list", recentAttempts.slice(-8), renderAttemptRow);
      renderRows("history-list", recentHistory.slice(-10), renderHistoryRow);
      renderSessionEvents(snapshot);
      markScrollableSelectable(document);
    }

    document.addEventListener("mousedown", (event) => {
      const target = closestScrollableSelectable(event.target);
      if (!(target instanceof HTMLElement)) return;
      target.focus();
    }, true);

    window.addEventListener("keydown", (event) => {
      const isSelectAll = (event.metaKey || event.ctrlKey) && !event.altKey && event.key.toLowerCase() === "a";
      if (!isSelectAll) return;
      const selectTarget = resolveSelectAllTarget();
      if (!(selectTarget instanceof HTMLElement)) return;
      event.preventDefault();
      selectNodeContents(selectTarget);
      selectTarget.focus();
    }, true);

    markScrollableSelectable(document);

    const goalEditor = document.getElementById("goal-editor-text");
    if (goalEditor instanceof HTMLTextAreaElement) {
      goalEditor.addEventListener("input", () => {
        window.__p2GoalEditorDirty = true;
      });
    }

    fetch("/api/snapshot")
      .then((response) => response.json())
      .then((snapshot) => renderSnapshot(snapshot))
      .catch(() => null);

    eventSource.addEventListener("snapshot", (event) => {
      try {
        renderSnapshot(JSON.parse(event.data));
      } catch (error) {
        console.error("failed to render dashboard snapshot", error);
        try {
          renderStream(JSON.parse(event.data));
        } catch (_) {
          const panel = document.getElementById("stream-output");
          if (panel) {
            panel.textContent = "snapshot 描画エラーが発生しました。ページ再読み込みで復帰する場合があります。";
          }
        }
      }
    });
"""
