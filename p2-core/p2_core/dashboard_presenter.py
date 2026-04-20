from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any

from p2_core.frame_language import FRAME_DECISION_LABELS
from p2_core.terminology import MEMORY_NAMES, MODEL_ROLE_NAMES, RUNTIME_NAMES


def human_readable_prompt_text(value: Any) -> str:
    if value in {None, ""}:
        return "情報なし"
    if not isinstance(value, str):
        try:
            return json.dumps(value, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            return str(value)

    text = value
    trimmed = text.strip()
    if trimmed.startswith("{") or trimmed.startswith("["):
        try:
            return json.dumps(json.loads(trimmed), ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            pass

    if "\\u" in text:
        try:
            decoded = text.encode("utf-8").decode("unicode_escape")
        except UnicodeDecodeError:
            return text
        decoded_trimmed = decoded.strip()
        if decoded_trimmed.startswith("{") or decoded_trimmed.startswith("["):
            try:
                return json.dumps(json.loads(decoded_trimmed), ensure_ascii=False, indent=2)
            except json.JSONDecodeError:
                return decoded
        return decoded
    return text


def validation_summary(validation: dict[str, Any] | None) -> str:
    if not validation:
        return ""
    stderr_path = validation.get("stderr_path")
    if stderr_path:
        try:
            text = Path(stderr_path).read_text(encoding="utf-8")
        except OSError:
            text = ""
        else:
            for line in text.splitlines():
                stripped = line.strip()
                if "SyntaxError:" in stripped:
                    return stripped
                if stripped.startswith("NameError:") or stripped.startswith("ImportError:"):
                    return stripped
    message = validation.get("message")
    if message:
        return str(message)
    return ""


def decision_explanation(reason: str | None, *, status: str | None, validation_summary: str | None = None) -> str:
    if status == "promoted":
        return "検証に通過し、昇格後の再検証も成功しました。"
    if status == "rolled_back":
        return "候補自体は通ったが、昇格後の再検証で失敗したため元に戻しました。"
    if status == "failed":
        reason_text = str(reason or "").strip()
        if "previous_run 中に異常終了" in reason_text or "attempt 完了前に終了" in reason_text:
            return (
                "前回の run-loop が attempt 完了前に停止したため、この試行は未完了のまま次回起動時に failed として回収されました。"
                f"{reason_text}"
            ).strip()
        return f"試行が途中で停止し、完了状態まで記録できませんでした。{reason_text}".strip()
    if reason == "validation failed":
        return f"検証に失敗しました。{validation_summary or ''}".strip()
    if reason == "candidate did not change the target file":
        return "候補に実質的な差分がなかったため却下しました。"
    if reason and reason.startswith("candidate touched protected path"):
        return "保護対象のパスに触れたため却下しました。"
    if reason:
        return str(reason)
    return "情報なし"


def clone_reason_from_attempt(attempt: dict[str, Any] | None) -> str:
    attempt = attempt or {}
    stored = str(attempt.get("clone_reason") or "").strip()
    if stored:
        return stored
    candidate_id = str(attempt.get("candidate_id") or "").strip()
    target_file = str(attempt.get("target_file") or "").strip()
    try:
        parent_generation = int(attempt.get("parent_generation"))
    except (TypeError, ValueError):
        parent_generation = 0
    if candidate_id and parent_generation and target_file:
        return (
            f"{RUNTIME_NAMES['active_version']['formal_name']} v{parent_generation:04d} を "
            f"{RUNTIME_NAMES['candidate_version']['formal_name']} {candidate_id} として分離し、"
            f"{target_file} の変更を現行コードへ直接当てずに検証するため。"
        )
    return "情報なし"


def model_plan_text(runtime: dict[str, Any], *, selected_coding_model: str | None = None) -> str:
    parts = [
        f"{MODEL_ROLE_NAMES['thinking_model']['formal_name']}={runtime.get('thinking_model') or '不明'}",
        f"{MODEL_ROLE_NAMES['coding_model']['formal_name']}={runtime.get('coding_model') or '不明'}",
        f"{MODEL_ROLE_NAMES['exploratory_coding_model']['formal_name']}={runtime.get('exploratory_coding_model') or '不明'}",
        f"{MODEL_ROLE_NAMES['stagnation_coding_model']['formal_name']}={runtime.get('stagnation_coding_model') or '不明'}",
    ]
    if selected_coding_model:
        parts.append(f"今回選択中の{MODEL_ROLE_NAMES['coding_model']['formal_name']}={selected_coding_model}")
    return " / ".join(parts)


def humanize_response_text(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    label = FRAME_DECISION_LABELS.get(text)
    if label:
        return f"{label} ({text})"
    return text


def render_dashboard_html(snapshot: dict[str, Any], *, dashboard_script: str) -> str:
    goal = snapshot.get("goal") or {}
    version = snapshot.get("version") or {}
    runtime = snapshot.get("runtime_status") or {}
    latest_attempt = snapshot.get("latest_attempt") or {}
    latest_completed_attempt = snapshot.get("latest_completed_attempt") or {}
    latest_validation = snapshot.get("latest_validation") or {}
    latest_reasoning = snapshot.get("latest_reasoning_summary") or {}
    latest_context_frame = snapshot.get("latest_context_frame") or {}
    current_focus = (
        ((latest_context_frame.get("context") or {}).get("local_working_memory") or {}).get("current_focus")
        or ((latest_context_frame.get("context") or {}).get("inherited_context") or {}).get("current_focus")
        or ""
    )
    current_phase = runtime.get("current_phase") or runtime.get("phase") or "待機"
    current_action = runtime.get("current_action") or runtime.get("current_step") or "待機"
    selected_model = runtime.get("model") or latest_attempt.get("selected_coding_model") or "情報なし"
    initial_stream_text = str(snapshot.get("current_stream_text") or "").strip() or "まだモデル出力はありません。"
    initial_selected_coding_model = (
        latest_attempt.get("selected_coding_model") or latest_completed_attempt.get("selected_coding_model") or "未選択"
    )
    latest_timing = snapshot.get("latest_llm_timings") or {}
    timing_total_ms = latest_timing.get("total_duration_ms")
    if timing_total_ms is None:
        initial_stream_timing = "情報なし"
    else:
        initial_stream_timing = f"合計: {timing_total_ms}ms"
    initial_stream_model_plan = model_plan_text(runtime, selected_coding_model=initial_selected_coding_model)
    recent_attempts = snapshot.get("recent_attempts") or []
    recent_history = snapshot.get("recent_history") or []
    operator_insights = snapshot.get("operator_insights") or []
    implementation_notes = snapshot.get("implementation_notes") or []
    system_skills = snapshot.get("system_skills") or []
    recent_memos = snapshot.get("recent_memos") or []
    generation_report = snapshot.get("generation_report") or []
    latest_self_memo = snapshot.get("latest_self_memo") or {}
    latest_prompt_snapshot = snapshot.get("latest_prompt_snapshot") or {}
    latest_prompt_request = latest_prompt_snapshot.get("request") or {}
    prompt_meta_parts = [
        f"phase={latest_prompt_snapshot.get('phase') or 'n/a'}",
        f"step={latest_prompt_snapshot.get('step') if latest_prompt_snapshot.get('step') is not None else 'n/a'}",
        f"frame={latest_prompt_snapshot.get('frame_id') or 'n/a'}",
        f"depth={latest_prompt_snapshot.get('frame_depth') if latest_prompt_snapshot.get('frame_depth') is not None else 'n/a'}",
        f"model={latest_prompt_snapshot.get('model') or 'n/a'}",
    ]
    if latest_prompt_request.get("transport"):
        prompt_meta_parts.append(f"transport={latest_prompt_request.get('transport')}")
    if latest_prompt_request.get("url"):
        prompt_meta_parts.append(f"url={latest_prompt_request.get('url')}")
    prompt_meta_text = " / ".join(prompt_meta_parts) if latest_prompt_snapshot else "まだ prompt snapshot はありません。"
    prompt_system_text = human_readable_prompt_text(latest_prompt_snapshot.get("system_prompt"))
    prompt_user_text = human_readable_prompt_text(latest_prompt_snapshot.get("user_prompt"))
    prompt_request_body = latest_prompt_request.get("request_body")
    if not prompt_request_body and latest_prompt_request.get("request_payload") is not None:
        prompt_request_body = json.dumps(latest_prompt_request.get("request_payload"), ensure_ascii=False, indent=2)
    prompt_request_text = human_readable_prompt_text(prompt_request_body)

    attempt_rows = []
    for attempt in recent_attempts[:8]:
        attempt_rows.append(
            "<div class='row'>"
            f"<div class='row-top'><span>{escape(str(attempt.get('candidate_id') or 'n/a'))}</span>"
            f"<span class='status'>{escape(str(attempt.get('status') or 'unknown'))}</span></div>"
            f"<div><strong>対象:</strong> {escape(str(attempt.get('target_file') or 'n/a'))}</div>"
            f"<div><strong>理由:</strong> {escape(decision_explanation(attempt.get('decision_reason'), status=attempt.get('status'), validation_summary=attempt.get('validation_summary')))}</div>"
            "</div>"
        )

    history_rows = []
    for row in recent_history[:10]:
        history_rows.append(
            "<div class='row'>"
            f"<div class='row-top'><span>{escape(str(row.get('timestamp') or 'n/a'))}</span>"
            f"<span class='status'>{escape(str(row.get('outcome') or 'n/a'))}</span></div>"
            f"<div><strong>{escape(str(row.get('step') or 'step'))}</strong>: {escape(str(row.get('message') or ''))}</div>"
            "</div>"
        )

    summary_points = [
        f"ゴール状態: {goal.get('status', '不明')}",
        f"現在世代: {version.get('active_generation', '不明')}",
        f"現在版: {version.get('active_version_id', '不明')}",
        f"実行状態: {runtime.get('status', '不明')}",
        f"現在候補: {runtime.get('current_candidate_id', '不明')}",
        f"直近検証: {'成功' if latest_validation.get('passed') else '失敗' if latest_validation else '情報なし'}",
    ]
    summary_html = "".join(f"<li>{escape(point)}</li>" for point in summary_points)

    reasoning_labels = {
        "problem_statement": "問題認識",
        "diagnosis": "診断",
        "edit_intent": "変更意図",
        "why_this_file": "対象ファイルの理由",
        "expected_effect": "期待効果",
        "validation_hypothesis": "検証仮説",
        "next_if_fail": "次の一手",
    }
    latest_reasoning_html = "".join(
        f"<li><strong>{escape(reasoning_labels.get(key, key))}:</strong> {escape(str(value or ''))}</li>"
        for key, value in latest_reasoning.items()
    ) or "<li>まだ思考要約はありません。</li>"
    insight_rows = []
    for insight in operator_insights:
        insight_rows.append(
            "<div class='row'>"
            f"<div class='row-top'><span>{escape(str(insight.get('title') or '重要点'))}</span>"
            f"<span class='status'>{escape(str(insight.get('level') or 'info'))}</span></div>"
            f"<div>{escape(str(insight.get('body') or ''))}</div>"
            "</div>"
        )
    implementation_rows = []
    for note in implementation_notes:
        implementation_rows.append(
            "<div class='row'>"
            f"<div class='row-top'><span>{escape(str(note.get('title') or '項目'))}</span></div>"
            f"<div>{escape(str(note.get('body') or ''))}</div>"
            "</div>"
        )
    skill_rows = []
    for skill in system_skills:
        skill_rows.append(
            "<div class='row'>"
            f"<div class='row-top'><span>{escape(str(skill.get('title') or skill.get('skill_id') or 'skill'))}</span>"
            f"<span class='status'>{escape(str(skill.get('skill_id') or ''))}</span></div>"
            f"<div><strong>概要:</strong> {escape(str(skill.get('summary') or ''))}</div>"
            f"<div><strong>期待効果:</strong> {escape(str(skill.get('expected_benefit') or ''))}</div>"
            f"<div><strong>使いどころ:</strong> {escape(' / '.join(str(item) for item in (skill.get('when_useful') or []) if item))}</div>"
            f"<div><strong>使い方:</strong> {escape(' / '.join(str(item) for item in (skill.get('how_to_use') or []) if item))}</div>"
            f"<div><strong>タグ:</strong> {escape(' / '.join(str(item) for item in (skill.get('keywords') or []) if item))}</div>"
            "</div>"
        )
    memo_rows = []
    if latest_self_memo:
        memo_rows.append(
            "<div class='row'>"
            f"<div class='row-top'><span>今回の{escape(MEMORY_NAMES['self_memo']['formal_name'])}</span><span class='status'>{escape(str(round(float(latest_self_memo.get('confidence', 0)) * 100)))}%</span></div>"
            f"<div><strong>題名:</strong> {escape(str(latest_self_memo.get('title') or ''))}</div>"
            f"<div><strong>戦術:</strong> {escape(str(latest_self_memo.get('tactic') or ''))}</div>"
            f"<div><strong>理由:</strong> {escape(str(latest_self_memo.get('why') or ''))}</div>"
            f"<div><strong>使う条件:</strong> {escape(str(latest_self_memo.get('when') or ''))}</div>"
            f"<div><strong>タグ:</strong> {escape(' / '.join(str(item) for item in (latest_self_memo.get('tags') or []) if item))}</div>"
            "</div>"
        )
    for memo in recent_memos:
        confidence = memo.get("confidence")
        confidence_text = ""
        if confidence is not None:
            confidence_text = f" / {round(float(confidence) * 100)}%"
        memo_rows.append(
            "<div class='row'>"
            f"<div class='row-top'><span>{escape(str(memo.get('title') or memo.get('memo_id') or 'memo'))}</span>"
            f"<span class='status'>{escape(str(memo.get('memo_id') or ''))}{escape(confidence_text)}</span></div>"
            f"<div><strong>戦術:</strong> {escape(str(memo.get('tactic') or ''))}</div>"
            f"<div><strong>理由:</strong> {escape(str(memo.get('why') or ''))}</div>"
            f"<div><strong>使う条件:</strong> {escape(str(memo.get('when') or ''))}</div>"
            f"<div><strong>由来:</strong> {escape(str(memo.get('source_candidate_id') or ''))}</div>"
            f"<div><strong>証拠:</strong> {escape(' / '.join(str(item) for item in [((memo.get('evidence') or {}).get('error_type')), ((memo.get('evidence') or {}).get('failure_detail'))] if item))}</div>"
            f"<div><strong>タグ:</strong> {escape(' / '.join(str(item) for item in (memo.get('tags') or []) if item))}</div>"
            "</div>"
        )
    generation_rows = []
    for entry in generation_report[:24]:
        generation_rows.append(
            "<div class='row'>"
            f"<div class='row-top'><span>gen {escape(str(entry.get('generation') or 'n/a'))} / {escape(str(entry.get('version_id') or 'n/a'))}</span>"
            f"<span class='status'>{escape(str(entry.get('candidate_id') or 'n/a'))}</span></div>"
            f"<div><strong>対象:</strong> {escape(str(entry.get('target_file') or 'n/a'))}</div>"
            f"<div><strong>変更箇所:</strong> {escape(', '.join(str(item) for item in (entry.get('changed_functions') or []) if item) or '関数検出なし')}</div>"
            f"<div><strong>差分量:</strong> +{escape(str(entry.get('added_lines') if entry.get('added_lines') is not None else 'n/a'))}"
            f" / -{escape(str(entry.get('removed_lines') if entry.get('removed_lines') is not None else 'n/a'))}</div>"
            f"<div><strong>実績:</strong> {escape(str(entry.get('outcome') or '情報なし'))}</div>"
            f"<pre class='mono-box' style='height:140px'>{escape(chr(10).join(str(item) for item in (entry.get('diff_excerpt') or []) if item) or '差分抜粋なし')}</pre>"
            "</div>"
        )

    task_hierarchy = snapshot.get("task_hierarchy") or []
    thought_history = snapshot.get("thought_history") or []
    latest_session_events = snapshot.get("latest_session_events") or []
    latest_session_event_summary_map = snapshot.get("latest_session_event_summary_map") or {}

    hierarchy_current = next((frame for frame in task_hierarchy if frame.get("is_current")), None)
    hierarchy_path_text = "まだ現在の思考パスはありません。"
    if hierarchy_current:
        by_id = {str(frame.get("frame_id")): frame for frame in task_hierarchy if frame.get("frame_id")}
        chain: list[dict[str, Any]] = []
        cursor = hierarchy_current
        while cursor:
            chain.append(cursor)
            parent_id = str(cursor.get("parent_frame_id") or "")
            cursor = by_id.get(parent_id) if parent_id else None
        hierarchy_path_text = " -> ".join(
            str(frame.get("goal") or frame.get("frame_id") or "frame")
            for frame in reversed(chain)
        )

    hierarchy_tree_rows = []
    for frame in task_hierarchy:
        depth = int(frame.get("depth") or 0)
        label = str(frame.get("goal") or frame.get("frame_id") or "frame")
        status = "current" if frame.get("is_current") else str(frame.get("result_status") or "active")
        meta = f"depth={depth} / {frame.get('decision_label') or frame.get('decision') or 'このフレームで続行'}"
        proposed = list(frame.get("proposed_child_goals") or [])
        if proposed:
            meta += f" / proposed_child_goals={len(proposed)}"
        hierarchy_tree_rows.append(
            "<div class='row' style='margin-left:"
            f"{depth * 16}px'>"
            f"<div class='row-top'><span>{escape(label)}</span><span class='status'>{escape(status)}</span></div>"
            f"<div>{escape(meta)}</div>"
            "</div>"
        )
    hierarchy_tree_html = "".join(hierarchy_tree_rows) or "まだ階層構造はありません。"

    hierarchy_detail_rows = []
    for frame in task_hierarchy:
        title = str(frame.get("goal") or frame.get("frame_id") or "frame")
        status_label = "current" if frame.get("is_current") else f"depth={frame.get('depth') or 0}"
        detail_parts = [
            f"対象={frame.get('target_file') or 'n/a'}",
            f"探索={frame.get('search_mode') or 'n/a'}",
            f"フォーカス={frame.get('current_focus') or 'n/a'}",
        ]
        proposed = list(frame.get("proposed_child_goals") or [])
        if proposed:
            detail_parts.append("提案child=" + " | ".join(str(item) for item in proposed[:4]))
        hierarchy_detail_rows.append(
            "<div class='row'>"
            f"<div class='row-top'><span>{escape(title)}</span><span class='status'>{escape(status_label)}</span></div>"
            f"<div>{escape(' / '.join(detail_parts))}</div>"
            "</div>"
        )
    hierarchy_detail_html = "".join(hierarchy_detail_rows) or "まだ階層コンテキストはありません。"

    thought_history_rows = []
    for event in thought_history:
        depth = int(event.get("depth") or 0)
        label = str(event.get("label") or event.get("frame_id") or "frame")
        message = str(event.get("message") or event.get("summary") or "")
        thought_history_rows.append(
            "<div class='row' style='margin-left:"
            f"{depth * 16}px'>"
            f"<div class='row-top'><span>{escape(label)}</span><span class='status'>{escape(str(event.get('type') or 'event'))}</span></div>"
            f"<div>{escape(message)}</div>"
            "</div>"
        )
    thought_history_html = "".join(thought_history_rows) or "まだ思考履歴はありません。"

    thought_action_chain = snapshot.get("thought_action_chain") or []
    thought_action_chain_source = str(snapshot.get("thought_action_chain_source") or "current_snapshot")
    current_candidate_event_count = int(snapshot.get("current_candidate_event_count") or 0)
    current_candidate_has_events = bool(snapshot.get("current_candidate_has_events"))
    thought_action_chain_rows = []
    for event in thought_action_chain:
        depth = int(event.get("depth") or 0)
        frame_title = str(event.get("frame_goal") or event.get("frame_id") or "frame")
        step = event.get("step")
        action = str(event.get("action") or "unknown")
        status = "failed" if event.get("result_ok") is False else "ok"
        detail_rows = []
        if event.get("thinking"):
            detail_rows.append(f"<div class='hierarchy-node-meta'><strong>thinking:</strong> {escape(str(event.get('thinking') or ''))}</div>")
        if event.get("action_input_text"):
            detail_rows.append(f"<div class='hierarchy-node-meta'><strong>action_input:</strong> {escape(str(event.get('action_input_text') or ''))}</div>")
        if event.get("result_text"):
            detail_rows.append(f"<div class='hierarchy-node-meta'><strong>result:</strong> {escape(str(event.get('result_text') or ''))}</div>")
        if event.get("transition_label") or event.get("next_action") or event.get("next_thinking"):
            next_bits = []
            if event.get("transition_label"):
                next_bits.append(str(event.get("transition_label") or ""))
            if event.get("next_action"):
                next_bits.append(f"次action={event.get('next_action')}")
            if event.get("next_thinking"):
                next_bits.append(f"次thinking={str(event.get('next_thinking') or '')}")
            detail_rows.append(f"<div class='hierarchy-node-meta'><strong>next:</strong> {escape(' / '.join(bit for bit in next_bits if bit))}</div>")
        thought_action_chain_rows.append(
            "<div class='row' style='margin-left:"
            f"{depth * 16}px'>"
            f"<div class='row-top'><span>step {escape(str(step if step is not None else '?'))} / {escape(action)} / {escape(frame_title)}</span><span class='status'>{escape(status)}</span></div>"
            + "".join(detail_rows)
            + "</div>"
        )
    thought_action_chain_html = "".join(thought_action_chain_rows) or thought_history_html

    session_event_rows = []
    for event in latest_session_events:
        step = event.get("step")
        action = str(event.get("action") or "unknown")
        result_summary = str(latest_session_event_summary_map.get(str(step)) or "")
        session_event_rows.append(
            "<div class='row'>"
            f"<div class='row-top'><span>step {escape(str(step if step is not None else '?'))} / {escape(action)}</span><span class='status'>{escape('failed' if (event.get('result') or {}).get('ok') is False else 'ok')}</span></div>"
            f"<div>{escape(result_summary)}</div>"
            "</div>"
        )
    if not session_event_rows:
        runtime_phase = str(runtime.get("phase") or runtime.get("current_phase") or "待機")
        runtime_event = str(runtime.get("last_event") or "情報なし")
        runtime_action = str(runtime.get("current_action") or runtime.get("current_step") or "待機")
        runtime_updated_at = str(runtime.get("updated_at") or snapshot.get("generated_at") or "情報なし")
        session_event_rows.append(
            "<div class='row'>"
            "<div class='row-top'><span>runtime / heartbeat</span><span class='status'>live</span></div>"
            f"<div>phase={escape(runtime_phase)} / action={escape(runtime_action)} / event={escape(runtime_event)}</div>"
            f"<div>updated_at={escape(runtime_updated_at)}</div>"
            "</div>"
        )
    session_event_html = "".join(session_event_rows)

    return f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8" />
  <title>P2 ダッシュボード</title>
  <style>
    :root {{
      --bg: #08131b;
      --panel: #0f2230;
      --panel-2: #153448;
      --text: #edf6f9;
      --muted: #9fc3d1;
      --accent: #f4a261;
      --accent-2: #2a9d8f;
      --border: rgba(255,255,255,0.09);
    }}
    body {{ margin: 0; font-family: "Helvetica Neue", sans-serif; background: radial-gradient(circle at top, #12344d 0%, var(--bg) 55%); color: var(--text); font-size: 12px; line-height: 1.42; }}
    header {{ padding: 12px 16px 8px; border-bottom: 1px solid var(--border); position: sticky; top: 0; background: rgba(8,19,27,0.94); backdrop-filter: blur(10px); z-index: 20; }}
    h1 {{ margin: 0 0 4px; font-size: 18px; }}
    .subhead {{ color: var(--muted); font-size: 11px; }}
    .meta {{ display: flex; flex-wrap: wrap; gap: 6px; }}
    .pill {{ padding: 4px 8px; border: 1px solid var(--border); border-radius: 999px; background: rgba(255,255,255,0.05); font-size: 11px; }}
    .hero {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; padding: 10px 16px 4px; }}
    .hero-card {{ background: linear-gradient(180deg, rgba(255,255,255,0.06), rgba(255,255,255,0.02)); border: 1px solid var(--border); border-radius: 12px; padding: 10px 12px; min-height: 68px; box-shadow: 0 8px 24px rgba(0,0,0,0.18); }}
    .label {{ color: var(--muted); font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 4px; }}
    .value {{ font-size: 14px; font-weight: 700; }}
    .value.compact {{ font-size: 13px; font-weight: 600; }}
    .dashboard-grid {{ display: grid; grid-template-columns: 300px minmax(0, 1fr) 420px; gap: 12px; align-items: start; padding: 10px 16px 18px; }}
    .rail, .maincol, .sidecol {{ display: flex; flex-direction: column; gap: 12px; min-width: 0; }}
    .card {{ background: var(--panel); border: 1px solid var(--border); border-radius: 14px; padding: 12px; min-width: 0; box-shadow: 0 10px 24px rgba(0,0,0,0.16); }}
    .card h2 {{ margin: 0 0 8px; font-size: 14px; }}
    .card h3 {{ margin: 0 0 6px; font-size: 12px; color: var(--muted); }}
    .row {{ padding: 6px 0; border-top: 1px solid var(--border); }}
    .row:first-child {{ border-top: 0; padding-top: 0; }}
    .row-top {{ display: flex; justify-content: space-between; gap: 8px; font-size: 11px; color: var(--muted); margin-bottom: 3px; }}
    .status {{ color: var(--accent-2); font-weight: 700; }}
    ul {{ margin: 0; padding-left: 16px; }}
    li {{ margin: 2px 0; }}
    code {{ font-family: "SFMono-Regular", monospace; font-size: 11px; }}
    .stack {{ display: flex; flex-direction: column; gap: 8px; }}
    .mono-box {{ white-space: pre-wrap; height: 260px; overflow: auto; margin: 0; font-family: SFMono-Regular, monospace; font-size: 11px; line-height: 1.4; border-radius: 10px; background: rgba(0,0,0,0.18); padding: 10px; }}
    .stream-box {{ height: 420px; }}
    .event-box {{ max-height: 320px; overflow: auto; border-radius: 10px; background: rgba(255,255,255,0.02); padding: 8px; }}
    .scroll-selectable {{ user-select: text; }}
    .scroll-selectable:focus {{ outline: 1px solid rgba(244,162,97,0.7); outline-offset: 1px; }}
    .prompt-box {{ display: grid; grid-template-columns: 1fr; gap: 8px; }}
    .stream-meta {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 6px 10px; }}
    .stream-tabs {{ display: flex; flex-wrap: wrap; gap: 6px; }}
    .stream-tab {{ padding: 4px 8px; border: 1px solid var(--border); border-radius: 999px; background: rgba(255,255,255,0.04); color: var(--text); font-size: 11px; cursor: pointer; }}
    .stream-tab.active {{ background: rgba(42,157,143,0.16); border-color: rgba(42,157,143,0.5); color: #dff7f3; }}
    .goal-editor {{ display: grid; gap: 8px; }}
    .goal-editor textarea {{ width: 100%; min-height: 96px; resize: vertical; border-radius: 10px; border: 1px solid var(--border); background: rgba(0,0,0,0.2); color: var(--text); padding: 8px; font-family: SFMono-Regular, monospace; font-size: 11px; line-height: 1.4; }}
    .goal-editor-controls {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }}
    .goal-editor select {{ border-radius: 8px; border: 1px solid var(--border); background: rgba(255,255,255,0.06); color: var(--text); padding: 5px 8px; }}
    .goal-editor button {{ border-radius: 8px; border: 1px solid var(--border); background: rgba(244,162,97,0.18); color: var(--text); padding: 6px 12px; cursor: pointer; }}
    .operator-strip {{ display: grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap: 8px; }}
    .brief {{ border: 1px solid var(--border); border-radius: 10px; background: rgba(255,255,255,0.03); padding: 8px; }}
    .brief strong {{ display: block; font-size: 11px; color: var(--muted); margin-bottom: 4px; }}
    .hierarchy-layout {{ display: grid; grid-template-columns: minmax(220px, 0.9fr) minmax(280px, 1.3fr); gap: 10px; }}
    .hierarchy-panel {{ border: 1px solid var(--border); border-radius: 10px; background: rgba(255,255,255,0.02); padding: 8px; min-width: 0; }}
    .hierarchy-panel h3 {{ margin: 0 0 8px; font-size: 12px; color: var(--muted); }}
    .hierarchy-tree {{ display: flex; flex-direction: column; gap: 8px; max-height: 380px; overflow: auto; }}
    .hierarchy-tree-children {{ margin-left: 18px; padding-left: 14px; border-left: 1px solid rgba(255,255,255,0.12); display: flex; flex-direction: column; gap: 8px; margin-top: 8px; }}
    .hierarchy-detail {{ display: flex; flex-direction: column; gap: 8px; max-height: 380px; overflow: auto; }}
    .hierarchy-node {{ position: relative; border: 1px solid var(--border); border-radius: 10px; padding: 8px; background: rgba(255,255,255,0.02); }}
    .hierarchy-node.current {{ border-color: rgba(244,162,97,0.55); background: rgba(244,162,97,0.08); }}
    .hierarchy-node-top {{ display: flex; justify-content: space-between; gap: 8px; font-size: 12px; }}
    .hierarchy-node-meta {{ margin-top: 4px; color: var(--muted); font-size: 11px; }}
    .hierarchy-detail-block {{ border: 1px solid var(--border); border-radius: 10px; padding: 8px; background: rgba(255,255,255,0.02); }}
    .hierarchy-detail-block.current {{ border-color: rgba(244,162,97,0.55); background: rgba(244,162,97,0.08); }}
    @media (max-width: 1320px) {{
      .dashboard-grid {{ grid-template-columns: 280px minmax(0, 1fr); }}
      .sidecol {{ grid-column: span 2; }}
    }}
    @media (max-width: 980px) {{
      .hero {{ grid-template-columns: 1fr 1fr; }}
      .dashboard-grid {{ grid-template-columns: 1fr; }}
      .hierarchy-layout {{ grid-template-columns: 1fr; }}
      .operator-strip {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>P2 自己改善ダッシュボード</h1>
    <div class="subhead">検証用 UI。いま何を試し、何が起きていて、次にどこを見るべきかを 1 画面で判断するための表示。</div>
    <div class="meta" id="meta-pills">
      <span class="pill">世代: {escape(str(snapshot.get("active_generation") or "不明"))}</span>
      <span class="pill">ゴール: {escape(str(goal.get("status") or "不明"))}</span>
      <span class="pill">状態: {escape(str(runtime.get("status") or "不明"))}</span>
      <span class="pill">候補: {escape(str(runtime.get("current_candidate_id") or "なし"))}</span>
      <span class="pill">kernel: {escape(str((latest_attempt.get("runtime_kernel") or runtime.get("current_runtime_kernel") or "legacy_phase_loop_v1")))}</span>
    </div>
  </header>

  <section class="hero">
    <div class="hero-card">
      <div class="label">現在のゴール</div>
      <div class="value compact" id="goal-text">{escape(str(goal.get("text") or "情報なし"))}</div>
    </div>
    <div class="hero-card">
      <div class="label">現在の実行</div>
      <div class="value" id="latest-attempt">{escape(str(latest_attempt.get("candidate_id") or "なし"))}</div>
      <div class="subhead">phase: {escape(str(current_phase))} / action: {escape(str(current_action))}</div>
    </div>
    <div class="hero-card">
      <div class="label">最新の検証</div>
      <div class="value" id="latest-validation">{escape('成功' if latest_validation.get('passed') else '失敗' if latest_validation else '情報なし')}</div>
    </div>
    <div class="hero-card">
      <div class="label">現在の焦点</div>
      <div class="value compact">{escape(str(current_focus or '未設定'))}</div>
      <div class="subhead">model: {escape(str(selected_model))}</div>
    </div>
  </section>

  <main class="dashboard-grid">
    <div class="rail">
    <section class="card">
      <h2>要約</h2>
      <ul id="summary-list">{summary_html}</ul>
    </section>
    <section class="card">
      <h2>直近の思考要約</h2>
      <ul id="reasoning-list">{latest_reasoning_html}</ul>
    </section>
    <section class="card">
      <h2>P2 の自己診断</h2>
      <div id="reflection-list"></div>
    </section>
    <section class="card">
      <h2>追加文脈と局所失敗差分</h2>
      <div id="context-list">まだ追加文脈はありません。</div>
    </section>
    <section class="card">
      <h2>コンテキスト管理監査</h2>
      <div id="context-audit-list">まだ監査結果はありません。</div>
    </section>
    </div>

    <div class="maincol">
    <section class="card">
      <h2>今わかっている重要点</h2>
      <div class="operator-strip">
        <div class="brief"><strong>いま確認したいこと</strong><span>停滞か改善か、いまの候補がどの能力を上げようとしているか。</span></div>
        <div class="brief"><strong>まず見る場所</strong><span>リアルタイム出力 → 階層コンテキスト → prompt の順で確認。</span></div>
      </div>
      <div id="insight-list">{''.join(insight_rows) or 'まだ重要点はありません。'}</div>
    </section>
    <section class="card">
      <h2>目的を編集してやり直し</h2>
      <div class="goal-editor">
        <div class="row"><strong>使い方:</strong> 目的を書き換えて適用すると、すぐ次の実行で反映されます。必要なら初期状態から再実行します。</div>
        <textarea id="goal-editor-text" spellcheck="false">{escape(str(goal.get("text") or ""))}</textarea>
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
    </section>
    <section class="card">
      <h2>モデルのリアルタイム出力</h2>
      <div class="stack">
        <div class="stream-meta">
          <div class="row"><strong>現在phase:</strong> <span id="stream-phase">{escape(str(current_phase))}</span></div>
          <div class="row"><strong>現在kernel:</strong> <span id="stream-current-kernel">{escape(str(runtime.get("current_runtime_kernel") or snapshot.get("latest_runtime_kernel") or "情報なし"))}</span></div>
          <div class="row"><strong>現在action:</strong> <span id="stream-current-action">{escape(str(current_action))}</span></div>
          <div class="row"><strong>現在使用モデル:</strong> <span id="stream-current-model">{escape(str(selected_model))}</span></div>
          <div class="row"><strong>今回表示中の役割:</strong> <span id="stream-active-tab">全体</span></div>
          <div class="row"><strong>今回選ばれたコーディングモデル:</strong> <span id="stream-selected-coding-model">{escape(str(initial_selected_coding_model))}</span></div>
          <div class="row"><strong>思考時間:</strong> <span id="stream-timing">{escape(str(initial_stream_timing))}</span></div>
          <div class="row"><strong>モデル構成:</strong> <span id="stream-model-plan">{escape(str(initial_stream_model_plan))}</span></div>
        </div>
        <div class="stream-tabs">
          <button type="button" class="stream-tab" data-stream-tab="auto">自動</button>
          <button type="button" class="stream-tab" data-stream-tab="context_selecting">追加文脈選択</button>
          <button type="button" class="stream-tab" data-stream-tab="reflecting">自己診断</button>
          <button type="button" class="stream-tab" data-stream-tab="generating">コード生成</button>
          <button type="button" class="stream-tab" data-stream-tab="acting">アクション実行</button>
          <button type="button" class="stream-tab" data-stream-tab="all">全体</button>
        </div>
        <div class="row"><pre id="stream-output" class="mono-box stream-box">{escape(initial_stream_text)}</pre></div>
      </div>
    </section>
    <section class="card">
      <h2>現在の階層コンテキスト</h2>
      <div class="hierarchy-layout" id="hierarchy-context">
        <div class="hierarchy-panel">
          <h3>全体構造</h3>
          <div id="hierarchy-tree" class="hierarchy-tree">{hierarchy_tree_html}</div>
        </div>
        <div class="hierarchy-panel">
          <h3>現在の思考コンテキスト</h3>
          <div class="label">各フレームの詳細</div>
          <div class="row"><strong>現在の思考パス:</strong> <span id="hierarchy-current-path">{escape(hierarchy_path_text)}</span></div>
          <div id="hierarchy-detail" class="hierarchy-detail">{hierarchy_detail_html}</div>
        </div>
      </div>
    </section>
    <section class="card">
      <h2>思考履歴の階層表示</h2>
      <div class="row"><strong>表示方針:</strong> thinking → action → result → next を階層つきで表示</div>
      <div class="row"><strong>表示元:</strong> <span id="thought-history-source">{escape(thought_action_chain_source)}</span></div>
      <div class="row"><strong>現在候補イベント件数:</strong> <span id="thought-history-current-event-count">{escape(str(current_candidate_event_count))}</span> / <strong>現在候補に履歴あり:</strong> <span id="thought-history-current-event-presence">{escape('yes' if current_candidate_has_events else 'no')}</span></div>
      <div id="thought-history-tree" class="hierarchy-tree">{thought_action_chain_html}</div>
    </section>
    <section class="card">
      <h2>session action/result 履歴</h2>
      <div id="session-event-list" class="event-box">{session_event_html}</div>
    </section>
    <section class="card">
      <h2>直近の完了試行の説明</h2>
      <div class="row"><div class="row-top"><span>候補ID</span><span class="status" id="latest-completed-status">{escape(str(latest_completed_attempt.get("status") or "不明"))}</span></div><div id="latest-completed-attempt">{escape(str(latest_completed_attempt.get("candidate_id") or "なし"))}</div></div>
      <div class="row"><strong>対象ファイル:</strong> <span id="latest-completed-target">{escape(str(latest_completed_attempt.get("target_file") or "不明"))}</span></div>
      <div class="row"><strong>どう失敗したか:</strong> <span id="latest-completed-failure">{escape(str(latest_completed_attempt.get("decision_explanation") or "情報なし"))}</span></div>
      <div class="row"><strong>どんな代替を提案したか:</strong> <span id="latest-completed-alternative">{escape(str(latest_completed_attempt.get("chosen_response") or "情報なし"))}</span></div>
      <div class="row"><strong>追加で読んだ文脈:</strong> <span>{escape(", ".join(latest_completed_attempt.get("selected_context") or []) or "情報なし")}</span></div>
      <div class="row"><strong>何を知るために読んだか:</strong> <span>{escape(str(latest_completed_attempt.get("question_to_answer") or "情報なし"))}</span></div>
      <div class="row"><strong>なぜクローンしたか:</strong> <span id="latest-completed-clone-reason">{escape(str(latest_completed_attempt.get("clone_reason") or "情報なし"))}</span></div>
    </section>
    </div>

    <div class="sidecol">
    <section class="card">
      <h2>LLM に渡した prompt</h2>
      <div class="stack">
        <div class="row" id="prompt-meta">{escape(prompt_meta_text)}</div>
        <div class="prompt-box">
          <div>
            <div class="label">system_prompt</div>
            <pre id="prompt-system" class="mono-box">{escape(prompt_system_text)}</pre>
          </div>
          <div>
            <div class="label">user_prompt</div>
            <pre id="prompt-user" class="mono-box">{escape(prompt_user_text)}</pre>
          </div>
          <div>
            <div class="label">request_body</div>
            <pre id="prompt-request-body" class="mono-box">{escape(prompt_request_text)}</pre>
          </div>
        </div>
      </div>
    </section>
    <section class="card">
      <h2>永続メモ</h2>
      <div id="memo-list">{''.join(memo_rows) or 'まだメモはありません。'}</div>
    </section>
    <section class="card">
      <h2>最近の試行</h2>
      <div id="attempt-list">{''.join(attempt_rows) or 'まだ試行はありません。'}</div>
    </section>
    <section class="card">
      <h2>最近の履歴</h2>
      <div id="history-list">{''.join(history_rows) or 'まだ履歴はありません。'}</div>
    </section>
    <section class="card">
      <h2>P2 世代更新レポート</h2>
      <div id="generation-report-list">{''.join(generation_rows) or 'まだ世代更新レポートはありません。'}</div>
    </section>
    <section class="card wide">
      <h2>今回実装した内容</h2>
      <div id="implementation-list">{''.join(implementation_rows) or 'まだ情報はありません。'}</div>
    </section>
    <section class="card wide">
      <h2>システムスキル</h2>
      <div id="skill-list">{''.join(skill_rows) or 'まだスキルはありません。'}</div>
    </section>
    </div>
  </main>

  <script>{dashboard_script}</script>
</body>
</html>"""
