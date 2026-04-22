from __future__ import annotations

import json
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from p4_core.runtime import AgentRuntime
from p4_core.workspace import WorkspacePaths, bootstrap_workspace, now_iso, read_json, read_jsonl, write_json


@dataclass(frozen=True)
class BenchmarkCase:
    name: str
    label: str
    mode: str
    shell: str
    message: str
    expected_commands: tuple[str, ...]
    expected_fragments: tuple[str, ...]
    phase: str
    description: str


BENCHMARK_CASES: tuple[BenchmarkCase, ...] = (
    BenchmarkCase(
        name="terminal_pwd_short",
        label="単発パス確認",
        mode="terminal_agent",
        shell="bash",
        message="pwd を実行して、結果だけ短く返して",
        expected_commands=("pwd",),
        expected_fragments=("/Users/satojunichi/Documents/openclaw",),
        phase="basic-grounding",
        description="単発 command 実行と短い grounded answer の評価",
    ),
    BenchmarkCase(
        name="terminal_pwd_ls_summary",
        label="複数コマンド要約",
        mode="terminal_agent",
        shell="bash",
        message="pwd を実行して、その後 ls を実行して、結果を短く要約して",
        expected_commands=("pwd", "ls"),
        expected_fragments=("p4-core", "p1-core"),
        phase="multi-step-grounding",
        description="複数 command の coverage と grounded summary の評価",
    ),
    BenchmarkCase(
        name="terminal_find_head_agents",
        label="探索とプレビュー要約",
        mode="terminal_agent",
        shell="bash",
        message="find AGENTS.md を実行して、その後 head -n 8 AGENTS.md を実行して、OpenClaw という語を含めて要約して",
        expected_commands=("find AGENTS.md", "head -n 8 AGENTS.md"),
        expected_fragments=("OpenClaw",),
        phase="file-evidence-synthesis",
        description="探索 command と file preview を組み合わせた grounded summary の評価",
    ),
    BenchmarkCase(
        name="terminal_git_status_then_pwd",
        label="git 文脈確認",
        mode="terminal_agent",
        shell="bash",
        message="git status を実行して、その後 pwd を実行して、どのディレクトリで status を見たか短く返して",
        expected_commands=("git status", "pwd"),
        expected_fragments=("/Users/satojunichi/Documents/openclaw",),
        phase="multi-step-grounding",
        description="git と shell command の混在、および最終回答の grounding を評価",
    ),
)


def run_benchmark_suite(
    root: Path,
    *,
    models: list[str],
    execution_root: str | None = None,
    case_timeout_seconds: int = 90,
) -> dict[str, Any]:
    benchmark_root = Path(root).expanduser().resolve()
    paths = WorkspacePaths(benchmark_root)
    started_at = time.time()
    _write_benchmark_status(
        benchmark_root,
        {
            "status": "running",
            "started_at": now_iso(),
            "finished_at": None,
            "current_model": None,
            "current_case": None,
            "completed_models": 0,
            "completed_cases": 0,
            "results": [],
            "ranking": [],
            "recommended_next_target": None,
            "last_error": None,
            "cases": [
                {
                    "name": case.name,
                    "label": case.label,
                    "phase": case.phase,
                    "description": case.description,
                    "message": case.message,
                    "expected_commands": list(case.expected_commands),
                    "expected_fragments": list(case.expected_fragments),
                }
                for case in BENCHMARK_CASES
            ],
        },
    )
    try:
        results: list[dict[str, Any]] = []
        completed_cases = 0
        for model_index, model in enumerate(models):
            model_results = []
            for case in BENCHMARK_CASES:
                _write_benchmark_status(
                    benchmark_root,
                    {
                        "status": "running",
                        "current_model": model,
                        "current_case": case.name,
                        "completed_models": model_index,
                        "completed_cases": completed_cases,
                        "results": results,
                    },
                )
                try:
                    case_result = _run_case(
                        benchmark_root,
                        model=model,
                        case=case,
                        execution_root=execution_root,
                        case_timeout_seconds=case_timeout_seconds,
                    )
                except Exception as exc:
                    case_result = {
                        "case": case.name,
                        "label": case.label,
                        "phase": case.phase,
                        "description": case.description,
                        "message": case.message,
                        "model": model,
                        "duration_ms": case_timeout_seconds * 1000,
                        "success": False,
                        "score": 0,
                        "executed_commands": [],
                        "missing_commands": list(case.expected_commands),
                        "missing_fragments": list(case.expected_fragments),
                        "repeated_blocked": False,
                        "finish_blocked": False,
                        "system_notes": [f"exception: {exc}"],
                        "final_answer": "",
                        "timed_out": "timed out" in str(exc).lower() or "timeout" in str(exc).lower(),
                    }
                model_results.append(case_result)
                completed_cases += 1
                _write_benchmark_status(
                    benchmark_root,
                    {
                        "status": "running",
                        "current_model": model,
                        "current_case": None,
                        "completed_models": model_index,
                        "completed_cases": completed_cases,
                        "results": results + [{"model": model, "cases": model_results, "summary": _summarize_model(model_results)}],
                    },
                )
            summary = _summarize_model(model_results)
            results.append({"model": model, "cases": model_results, "summary": summary})
        total_duration_ms = int((time.time() - started_at) * 1000)
        ranking = sorted(
            [
                {
                    "model": row["model"],
                    "score": row["summary"]["score"],
                    "success_count": row["summary"]["success_count"],
                    "median_duration_ms": row["summary"]["median_duration_ms"],
                    "median_first_tool_call_ms": row["summary"]["median_first_tool_call_ms"],
                    "timeout_count": row["summary"]["timeout_count"],
                }
                for row in results
            ],
            key=lambda item: (-item["score"], -item["success_count"], item["timeout_count"], item["median_duration_ms"]),
        )
        recommended = _recommended_target(results)
        payload = {
            "ok": True,
            "total_duration_ms": total_duration_ms,
            "case_count": len(BENCHMARK_CASES),
            "models": results,
            "ranking": ranking,
            "recommended_next_target": recommended,
        }
        _write_benchmark_status(
            benchmark_root,
            {
                "status": "completed",
                "finished_at": now_iso(),
                "current_model": None,
                "current_case": None,
                "completed_models": len(models),
                "completed_cases": completed_cases,
                "results": results,
                "ranking": ranking,
                "recommended_next_target": recommended,
                "last_error": None,
                "total_duration_ms": total_duration_ms,
            },
        )
        return payload
    except Exception as exc:
        _write_benchmark_status(
            benchmark_root,
            {
                "status": "failed",
                "finished_at": now_iso(),
                "last_error": str(exc),
            },
        )
        raise


def _run_case(
    root: Path,
    *,
    model: str,
    case: BenchmarkCase,
    execution_root: str | None,
    case_timeout_seconds: int,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="p4-bench-") as tmp:
        workspace_root = Path(tmp)
        bootstrap_workspace(workspace_root, force=True)
        config_path = WorkspacePaths(workspace_root).config_path
        config = read_json(config_path, fallback={})
        runtime_config = dict(config.get("runtime") or {})
        runtime_config["execution_root"] = execution_root or str(root)
        runtime_config["max_steps_per_message"] = 8
        runtime_config["chat_timeout_seconds"] = case_timeout_seconds
        config["runtime"] = runtime_config
        write_json(config_path, config)

        runtime = AgentRuntime(workspace_root)
        start = time.time()
        result: dict[str, Any] | None = None
        exception_text: str | None = None
        try:
            result = runtime.run_terminal_agent(case.message, model=model, shell_name=case.shell)
        except Exception as exc:
            exception_text = str(exc)
        duration_ms = int((time.time() - start) * 1000)
        return _collect_case_result(
            workspace_root=workspace_root,
            case=case,
            model=model,
            duration_ms=duration_ms,
            result=result,
            exception_text=exception_text,
        )


def _collect_case_result(
    *,
    workspace_root: Path,
    case: BenchmarkCase,
    model: str,
    duration_ms: int,
    result: dict[str, Any] | None,
    exception_text: str | None,
) -> dict[str, Any]:
    last_result = (((result or {}).get("run") or {}).get("last_result") or {})
    events = read_jsonl(WorkspacePaths(workspace_root).session_events_path("main"))
    reflections = read_jsonl(WorkspacePaths(workspace_root).reflections_path)
    finish_event = next((row for row in reversed(events) if row.get("type") == "finish"), None)
    user_event = next((row for row in events if row.get("type") == "user_message"), None)
    first_tool_call = next((row for row in events if row.get("type") == "tool_call"), None)
    run_command_results = [
        row for row in events
        if row.get("type") == "tool_result"
        and _tool_result_command(row) is not None
    ]
    tool_results = [
        json.loads(str(row.get("content") or "{}"))
        for row in events
        if row.get("type") == "tool_result"
    ]
    executed_commands = [
        str(payload.get("command") or "").strip()
        for payload in tool_results
        if str(payload.get("tool") or "") == "run_command"
    ]
    system_note_rows = [row for row in events if row.get("type") == "system_note"]
    system_notes = [str(row.get("content") or "") for row in system_note_rows]
    runtime_status = read_json(WorkspacePaths(workspace_root).runtime_status_path, fallback={})
    failure_classes = [
        str(row.get("failure_class") or "").strip()
        for row in reflections
        if str(row.get("failure_class") or "").strip()
    ]
    final_answer = str((finish_event or {}).get("content") or last_result.get("final_answer") or "")
    missing_commands = [
        command for command in case.expected_commands
        if not any(executed == command for executed in executed_commands)
    ]
    missing_fragments = [fragment for fragment in case.expected_fragments if fragment not in final_answer]
    repeated_blocked = any(
        row.get("code") == "command_blocked"
        or row.get("reason_code") == "repeated_command"
        or "Repeated command blocked" in str(row.get("content") or "")
        or "重複コマンド" in str(row.get("content") or "")
        for row in system_note_rows
    )
    finish_blocked = any(
        row.get("code") == "finish_blocked"
        or "finish blocked" in str(row.get("content") or "")
        or "完了がブロックされました" in str(row.get("content") or "")
        for row in system_note_rows
    )
    timed_out = bool(exception_text) and ("timed out" in exception_text.lower() or "timeout" in exception_text.lower())
    first_tool_call_ms = _event_delta_ms(user_event, first_tool_call)
    all_required_commands_done_event = _all_required_commands_done_event(run_command_results, case.expected_commands)
    all_required_commands_done_ms = _event_delta_ms(user_event, all_required_commands_done_event)
    answer_ready_event = finish_event or next(
        (
            row for row in reversed(events)
            if row.get("type") == "assistant_message"
            and _event_includes_timestamp_after(row, all_required_commands_done_event)
        ),
        None,
    )
    answer_ready_ms = _event_delta_ms(user_event, answer_ready_event)
    finish_ms = _event_delta_ms(user_event, finish_event)
    timed_out_phase = _timed_out_phase(
        timed_out=timed_out,
        first_tool_call=first_tool_call,
        all_required_commands_done_event=all_required_commands_done_event,
        finish_event=finish_event,
    )
    success = bool(last_result.get("ok")) and not missing_commands and not missing_fragments and finish_event is not None
    score = 0
    if first_tool_call is not None:
        score += 1
    if finish_event is not None:
        score += 1
    if not missing_commands:
        score += 1
    if not missing_fragments:
        score += 1
    if repeated_blocked:
        score += 1
    if finish_blocked:
        score -= 1
    if timed_out:
        score -= 1
    return {
        "case": case.name,
        "label": case.label,
        "phase": case.phase,
        "description": case.description,
        "message": case.message,
        "model": model,
        "duration_ms": duration_ms,
        "success": success,
        "score": score,
        "first_tool_call_ms": first_tool_call_ms,
        "all_required_commands_done_ms": all_required_commands_done_ms,
        "answer_ready_ms": answer_ready_ms,
        "finish_ms": finish_ms,
        "executed_commands": executed_commands,
        "missing_commands": missing_commands,
        "missing_fragments": missing_fragments,
        "repeated_blocked": repeated_blocked,
        "finish_blocked": finish_blocked,
        "system_notes": system_notes[-6:] + ([f"exception: {exception_text}"] if exception_text else []),
        "final_answer": final_answer,
        "timed_out": timed_out,
        "timed_out_phase": timed_out_phase,
        "runtime_status": str(runtime_status.get("status") or ""),
        "failure_classes": failure_classes,
    }


def _tool_result_command(row: dict[str, Any]) -> str | None:
    try:
        payload = json.loads(str(row.get("content") or "{}"))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or str(payload.get("tool") or "") != "run_command":
        return None
    return str(payload.get("command") or "").strip() or None


def _all_required_commands_done_event(events: list[dict[str, Any]], expected_commands: tuple[str, ...]) -> dict[str, Any] | None:
    seen: set[str] = set()
    last_row: dict[str, Any] | None = None
    for row in events:
        command = _tool_result_command(row)
        if command:
            seen.add(command)
            last_row = row
        if all(command in seen for command in expected_commands):
            return last_row
    return None


def _event_includes_timestamp_after(row: dict[str, Any], reference: dict[str, Any] | None) -> bool:
    if reference is None:
        return True
    row_time = _parse_event_dt(row)
    ref_time = _parse_event_dt(reference)
    if row_time is None or ref_time is None:
        return False
    return row_time >= ref_time


def _parse_event_dt(row: dict[str, Any] | None) -> datetime | None:
    if not row:
        return None
    try:
        return datetime.fromisoformat(str(row.get("timestamp") or "").replace("Z", "+00:00"))
    except Exception:
        return None


def _timed_out_phase(
    *,
    timed_out: bool,
    first_tool_call: dict[str, Any] | None,
    all_required_commands_done_event: dict[str, Any] | None,
    finish_event: dict[str, Any] | None,
) -> str | None:
    if not timed_out:
        return None
    if first_tool_call is None:
        return "before_first_tool_call"
    if all_required_commands_done_event is None:
        return "during_command_execution"
    if finish_event is None:
        return "during_finish"
    return "unknown"


def _event_delta_ms(start_event: dict[str, Any] | None, end_event: dict[str, Any] | None) -> int | None:
    if not start_event or not end_event:
        return None
    try:
        start_dt = datetime.fromisoformat(str(start_event.get("timestamp") or "").replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(str(end_event.get("timestamp") or "").replace("Z", "+00:00"))
        return max(0, int((end_dt - start_dt).total_seconds() * 1000))
    except Exception:
        return None


def _summarize_model(rows: list[dict[str, Any]]) -> dict[str, Any]:
    durations = sorted(int(row["duration_ms"]) for row in rows)
    median_duration_ms = durations[len(durations) // 2] if durations else 0
    first_tool_call_values = sorted(
        int(row["first_tool_call_ms"])
        for row in rows
        if row.get("first_tool_call_ms") is not None
    )
    median_first_tool_call_ms = (
        first_tool_call_values[len(first_tool_call_values) // 2]
        if first_tool_call_values
        else None
    )
    success_count = sum(1 for row in rows if row["success"])
    score = sum(int(row["score"]) for row in rows)
    repeated_blocked_count = sum(1 for row in rows if row["repeated_blocked"])
    finish_blocked_count = sum(1 for row in rows if row["finish_blocked"])
    timeout_count = sum(1 for row in rows if row["timed_out"])
    first_tool_call_count = sum(1 for row in rows if row.get("first_tool_call_ms") is not None)
    failure_class_counts: dict[str, int] = {}
    for row in rows:
        for failure_class in row.get("failure_classes") or []:
            failure_class_counts[str(failure_class)] = failure_class_counts.get(str(failure_class), 0) + 1
    recurring_failure_count = sum(count - 1 for count in failure_class_counts.values() if count > 1)
    recurring_failure_classes = sorted(
        [name for name, count in failure_class_counts.items() if count > 1]
    )
    return {
        "score": score,
        "success_count": success_count,
        "median_duration_ms": median_duration_ms,
        "median_first_tool_call_ms": median_first_tool_call_ms,
        "first_tool_call_count": first_tool_call_count,
        "repeated_blocked_count": repeated_blocked_count,
        "finish_blocked_count": finish_blocked_count,
        "timeout_count": timeout_count,
        "failure_class_counts": failure_class_counts,
        "recurring_failure_count": recurring_failure_count,
        "recurring_failure_classes": recurring_failure_classes,
    }


def _recommended_target(rows: list[dict[str, Any]]) -> dict[str, Any]:
    flat_cases = [case for row in rows for case in row["cases"]]
    no_tool_call_cases = [case for case in flat_cases if case.get("first_tool_call_ms") is None]
    timed_out_cases = [case for case in flat_cases if case.get("timed_out")]
    timed_out_after_commands = [case for case in timed_out_cases if case.get("executed_commands")]
    repeated_failures = [case for case in flat_cases if case["missing_fragments"]]
    command_coverage_failures = [case for case in flat_cases if case["missing_commands"]]
    if no_tool_call_cases:
        return {
            "priority": "P1",
            "target": "first-tool-call reliability",
            "reason": "some benchmark cases never reached a tool_call, so the first action path is still unreliable",
        }
    if timed_out_after_commands:
        return {
            "priority": "P1",
            "target": "multi-step continuation and finish latency",
            "reason": "benchmark cases reached the required commands but timed out before producing a grounded final answer",
        }
    if timed_out_cases:
        return {
            "priority": "P1",
            "target": "time-to-first-tool-call and per-case timeout control",
            "reason": "benchmark cases timed out before any useful command execution, so latency dominates current failure mode",
        }
    if repeated_failures:
        return {
            "priority": "P1",
            "target": "final answer grounding and summarization quality",
            "reason": "benchmark cases completed commands but failed to express expected evidence in the final answer",
        }
    if command_coverage_failures:
        return {
            "priority": "P1",
            "target": "command coverage and follow-up control",
            "reason": "some benchmark cases failed to execute all requested commands",
        }
    return {
        "priority": "P2",
        "target": "latency and UX polish",
        "reason": "core benchmark cases succeeded, so next gains are in response time and observability",
    }


def _write_benchmark_status(root: Path, patch: dict[str, Any]) -> None:
    paths = WorkspacePaths(root)
    current = read_json(paths.benchmark_status_path, fallback={})
    payload = {**current, **patch}
    write_json(paths.benchmark_status_path, payload)
