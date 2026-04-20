from __future__ import annotations

from typing import Any

from p2_core.dashboard_presenter import humanize_response_text, model_plan_text
from p2_core.frame_language import FRAME_DECISION_LABELS
from p2_core.terminology import FRAME_SYSTEM_NAMES, MEMORY_NAMES, OBSERVABILITY_NAMES


def stringify_lines(values: list[str] | None, *, limit: int = 2) -> str:
    if not values:
        return ""
    trimmed = []
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if not text or text.lower() == "none":
            continue
        trimmed.append(text)
    if not trimmed:
        return ""
    return " / ".join(trimmed[:limit])


def format_duration_ms(value: Any) -> str:
    if value in {None, "", "n/a"}:
        return "n/a"
    try:
        seconds = float(value) / 1000.0
    except (TypeError, ValueError):
        return "n/a"
    if seconds >= 10:
        return f"{seconds:.1f} 秒"
    return f"{seconds:.2f} 秒"


def build_operator_insights(snapshot: dict[str, Any]) -> list[dict[str, str]]:
    insight_attempt = snapshot.get("latest_completed_attempt") or snapshot.get("latest_attempt") or {}
    latest_pre = insight_attempt.get("pre_edit_reflection") or {}
    latest_meta = insight_attempt.get("meta_diagnosis") or {}
    latest_situation = insight_attempt.get("situation_report") or {}
    latest_attempted_change = {
        "candidate_id": insight_attempt.get("candidate_id"),
        "target_file": insight_attempt.get("target_file"),
        "status": insight_attempt.get("status"),
        "decision_reason": insight_attempt.get("decision_reason"),
        "change_summary": insight_attempt.get("change_summary"),
    }

    insights: list[dict[str, str]] = []

    reflection_summary = stringify_lines(
        [
            latest_pre.get("what_kept_happening"),
            latest_pre.get("what_this_suggests_about_my_search"),
        ]
    )
    if reflection_summary:
        insights.append(
            {
                "title": "1. P2 が自分で気づいていること",
                "level": "self",
                "body": reflection_summary,
            }
        )

    if insight_attempt and insight_attempt.get("status") == "rejected":
        reason = str(insight_attempt.get("decision_reason") or "不明")
        validation_state = "検証失敗" if insight_attempt.get("validation_summary") else "候補却下"
        chosen_response = humanize_response_text(latest_situation.get("chosen_response"))
        body = f"{validation_state}が続いており、直近候補 {insight_attempt.get('candidate_id') or 'n/a'} は {reason}。"
        if chosen_response:
            body += f" P2 の対処方針: {chosen_response}"
        insights.append(
            {
                "title": "2. 気づきはあるが、まだ前進に変わっていない",
                "level": "risk",
                "body": body,
            }
        )

    observation_bundle = latest_meta.get("observation_bundle") or {}
    validation_summaries = observation_bundle.get("recent_validation_summaries") or []
    target_histogram = observation_bundle.get("target_histogram") or {}
    dominant_target = ""
    if target_histogram:
        dominant_target = max(target_histogram.items(), key=lambda item: item[1])[0]
    repeated_failures = stringify_lines(validation_summaries)
    if dominant_target or repeated_failures:
        parts = []
        if dominant_target:
            parts.append(f"変更対象は {dominant_target} に集中している")
        if repeated_failures:
            parts.append(f"直近の失敗型は {repeated_failures}")
        if latest_attempted_change.get("change_summary"):
            summary = latest_attempted_change["change_summary"]
            parts.append(
                f"直近差分は added={summary.get('added_lines', 'n/a')} removed={summary.get('removed_lines', 'n/a')}"
            )
        insights.append(
            {
                "title": "3. 今の停滞パターン" if latest_meta.get("status") == "stagnating" else "3. 最近の探索パターン",
                "level": str(latest_meta.get("search_mode") or "info"),
                "body": "。".join(parts) + "。",
            }
        )

    return insights


def build_implementation_notes(
    raw_snapshot: dict[str, Any],
    public_snapshot: dict[str, Any],
) -> list[dict[str, str]]:
    runtime = public_snapshot.get("runtime_status") or {}
    latest_attempt = public_snapshot.get("latest_attempt") or {}
    latest_frame = public_snapshot.get("latest_context_frame") or {}
    raw_skills = raw_snapshot.get("system_skills") or []
    recent_memos = raw_snapshot.get("recent_memos") or []
    latest_self_memo = raw_snapshot.get("latest_self_memo") or {}

    frame_context = latest_frame.get("context") or {}
    local_context = frame_context.get("local_context") or {}
    capabilities = local_context.get("system_capabilities") or latest_frame.get("system_capabilities") or {}
    frame_affordances = local_context.get("frame_affordances") or latest_frame.get("frame_affordances") or {}

    reference_capability = capabilities.get("reference_lookup") or {}
    skill_capability = capabilities.get("skill_lookup") or {}
    memo_capability = capabilities.get("memo_lookup") or {}

    available_reference_ids = list(reference_capability.get("available_reference_ids") or [])
    available_skill_ids = list(skill_capability.get("available_skill_ids") or [])
    available_memo_ids = list(memo_capability.get("available_memo_ids") or [])
    skill_titles = [str(skill.get("title") or skill.get("skill_id") or "").strip() for skill in raw_skills]
    skill_titles = [title for title in skill_titles if title]
    if not available_skill_ids:
        available_skill_ids = [str(skill.get("skill_id")) for skill in raw_skills if skill.get("skill_id")]
    if not available_memo_ids:
        available_memo_ids = [str(memo.get("memo_id")) for memo in recent_memos if memo.get("memo_id")]
    if not available_reference_ids:
        available_reference_ids = [
            "active_target_file",
            "tests_context",
            "current_task_stack",
            *available_skill_ids,
            *available_memo_ids,
        ]

    current_depth = frame_affordances.get("current_depth")
    max_depth = frame_affordances.get("max_depth")
    allowed_decisions = [str(item) for item in (frame_affordances.get("allowed_decisions") or []) if item]

    notes: list[dict[str, str]] = []
    notes.append(
        {
            "title": "参照選択",
            "body": (
                f"LLM が必要な情報だけを {OBSERVABILITY_NAMES['selected_context']['formal_name']} で要求し、"
                f" システムが {OBSERVABILITY_NAMES['resolved_context']['short_name']} として返す。"
                f" 現在公開中: 参照 {len(available_reference_ids)} 件 / スキル {len(available_skill_ids)} 件 / メモ {len(available_memo_ids)} 件。"
            ),
        }
    )

    memo_text = f"まだ{MEMORY_NAMES['persistent_self_memo']['short_name']}はありません。"
    if recent_memos:
        latest_memo = recent_memos[-1]
        memo_text = (
            f"保存済み{MEMORY_NAMES['persistent_self_memo']['short_name']} {len(recent_memos)} 件。"
            f" 最新: {latest_memo.get('title') or latest_memo.get('memo_id') or '無題'}。"
        )
    elif latest_self_memo:
        memo_text = (
            f"今回の試行で {MEMORY_NAMES['self_memo']['formal_name']} を扱える。"
            f" 直近内容: {latest_self_memo.get('title') or '無題'}。"
        )
    notes.append(
        {
            "title": "システムスキルとメモ",
            "body": (
                f"{MEMORY_NAMES['system_skill']['formal_name']}: {', '.join(skill_titles) if skill_titles else 'なし'}。"
                f" {memo_text}"
            ),
        }
    )

    if current_depth is None and max_depth is None and not allowed_decisions:
        hierarchy_body = (
            f"親文脈を保持したまま {FRAME_SYSTEM_NAMES['child_frame']['formal_name']} へ降りる"
            f" {FRAME_SYSTEM_NAMES['frame_system']['formal_name']} を使える。現在は待機中で詳細は未生成。"
        )
    else:
        hierarchy_body = (
            f"親文脈を保持したまま {FRAME_SYSTEM_NAMES['child_frame']['formal_name']} へ降りる"
            f" {FRAME_SYSTEM_NAMES['frame_system']['formal_name']} を使える。"
            f" 現在 depth={current_depth if current_depth is not None else 'n/a'}"
            f"/{max_depth if max_depth is not None else 'n/a'}。"
        )
        if allowed_decisions:
            labels = [FRAME_DECISION_LABELS.get(code, code) for code in allowed_decisions]
            hierarchy_body += f" 利用可能なフレーム遷移要求: {', '.join(labels)}。"
    notes.append(
        {
            "title": FRAME_SYSTEM_NAMES["frame_system"]["formal_name"],
            "body": hierarchy_body,
        }
    )

    selected_coding_model = latest_attempt.get("selected_coding_model") or "未選択"
    notes.append(
        {
            "title": "役割別モデル構成",
            "body": model_plan_text(runtime, selected_coding_model=selected_coding_model),
        }
    )
    runtime_kernel = latest_attempt.get("runtime_kernel") or runtime.get("current_runtime_kernel") or "legacy_phase_loop_v1"
    latest_events = raw_snapshot.get("latest_session_events") or []
    notes.append(
        {
            "title": "実行kernel",
            "body": (
                f"現在の実行kernelは {runtime_kernel}。"
                f" 最新 session event 数は {len(latest_events)} 件。"
                " action -> result -> 次の action の列を監査できる。"
            ),
        }
    )

    latest_timing = public_snapshot.get("latest_llm_timings") or {}
    trend = public_snapshot.get("llm_timing_trend") or {}
    notes.append(
        {
            "title": "ストリーミングと計測",
            "body": (
                "追加文脈選択 / 自己診断 / コード生成を分けてストリーミング表示する。"
                f" 直近合計 {format_duration_ms(latest_timing.get('total_duration_ms') if latest_timing else None)},"
                f" 最近平均 {format_duration_ms(trend.get('recent_average_duration_ms') if trend else None)}。"
            ),
        }
    )
    return notes


def derive_reasoning_summary(
    *,
    snapshot: dict[str, Any],
    latest_attempt: dict[str, Any],
    latest_completed_attempt: dict[str, Any] | None,
) -> dict[str, str]:
    explicit = snapshot.get("latest_reasoning_summary")
    if isinstance(explicit, dict) and any(str(value or "").strip() for value in explicit.values()):
        return explicit
    if latest_completed_attempt:
        completed_reasoning = latest_completed_attempt.get("reasoning_summary")
        if isinstance(completed_reasoning, dict) and any(str(value or "").strip() for value in completed_reasoning.values()):
            return completed_reasoning

    latest_task_frame = snapshot.get("latest_task_frame") or {}
    selected_context = snapshot.get("latest_selected_context") or {}
    delta_context = snapshot.get("latest_delta_context") or {}
    latest_failure = delta_context.get("latest_failure") or {}
    must_avoid = list(delta_context.get("must_avoid_next") or [])
    target_file = latest_task_frame.get("target_file") or latest_attempt.get("target_file") or ""
    question = (
        latest_task_frame.get("question_to_answer")
        or selected_context.get("question_to_answer")
        or (snapshot.get("goal") or {}).get("text")
        or ""
    )
    commitment = latest_task_frame.get("commitment") or selected_context.get("commitment") or "次の小さな action を決める"
    diagnosis_parts = []
    if latest_failure.get("summary"):
        diagnosis_parts.append(str(latest_failure.get("summary")))
    if latest_failure.get("detail"):
        diagnosis_parts.append(str(latest_failure.get("detail")))
    diagnosis = " / ".join(diagnosis_parts) or "まだ明示的な思考要約は生成されていない"
    next_if_fail = " / ".join(must_avoid) if must_avoid else "直近の失敗差分と検証結果を読み直す"
    return {
        "problem_statement": str(question),
        "diagnosis": diagnosis,
        "edit_intent": str(commitment),
        "why_this_file": str(target_file or "対象ファイルは未確定"),
        "expected_effect": str(latest_task_frame.get("current_focus") or "次に進むための局所判断材料を得る"),
        "validation_hypothesis": "検証結果と差分を突き合わせて次の action を決める",
        "next_if_fail": next_if_fail,
    }
