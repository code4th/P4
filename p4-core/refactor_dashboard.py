import os
import re

SNAPSHOT_PATH = "/Users/satojunichi/Documents/openclaw/p4-core/p4_core/dashboard/snapshot.py"
TEMPLATES_PATH = "/Users/satojunichi/Documents/openclaw/p4-core/p4_core/dashboard/templates.py"

with open(SNAPSHOT_PATH, "r") as f:
    snapshot_code = f.read()

with open(TEMPLATES_PATH, "r") as f:
    templates_code = f.read()

# 1. Update Snapshot.py
# We will modify _canonical_flow_steps_for_operation to aggregate events into "Cards"

new_canonical_flow_steps_code = """
def _canonical_flow_steps_for_operation(operation: dict[str, Any], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    operation_id = str(operation.get("operation_id") or "")
    grouped: dict[int, dict[str, Any]] = {}
    order: list[int] = []
    
    # Pre-process failure translations
    _FAILURE_TRANSLATIONS = {
        "missing_work_package_contract": {"title": "計画分解の契約不足", "desc": "LLMが decompose_tasks を提案したが、子タスク化に必要な項目が不足または不正だった"},
        "json_extraneous_text": {"title": "machine-control JSON形式違反", "desc": "JSONの外側にMarkdownや前置きがある"},
        "schema_validation_failed": {"title": "schema不一致", "desc": "JSON自体は読めるが、必要項目・enum・型がschemaと合わない"},
        "contract_incomplete": {"title": "完了条件未達", "desc": "TaskState が CompletionContract を満たしていない"},
        "judge_invalid_output": {"title": "judge出力形式エラー", "desc": "acceptance judge が期待schemaに合わないJSONを返した"},
        "tool_failed": {"title": "ツール実行失敗", "desc": "toolが returncode 非0または ok=false を返した"},
        "child_task_incomplete": {"title": "子タスク未完了", "desc": "first_action は成功したが success_evidence 未達"},
    }

    for row in events:
        if str(row.get("operation_id") or "") != operation_id:
            continue
        kind = str(row.get("kind") or "")
        if kind == "operation":
            continue
            
        step_index = int(row.get("step_index") or 0)
        if step_index not in grouped:
            order.append(step_index)
            grouped[step_index] = {
                "step_index": step_index,
                "title": "Input" if step_index == 0 else f"Step {step_index}",
                "items": [],
                "raw_events": [],
            }
            
        payload = dict(row.get("payload") or {})
        
        # Translate failure blocks
        if kind == "system_note" or kind == "decision":
            code = str(payload.get("code") or payload.get("decision_type") or "")
            reason_code = str(payload.get("reason_code") or "")
            
            # Map failed reasons
            if code in _FAILURE_TRANSLATIONS:
                payload["human_title"] = _FAILURE_TRANSLATIONS[code]["title"]
                payload["human_desc"] = _FAILURE_TRANSLATIONS[code]["desc"]
            elif reason_code in _FAILURE_TRANSLATIONS:
                payload["human_title"] = _FAILURE_TRANSLATIONS[reason_code]["title"]
                payload["human_desc"] = _FAILURE_TRANSLATIONS[reason_code]["desc"]
                
        grouped[step_index]["raw_events"].append(row)

    rows = []
    
    # Sub-task grouping variables
    child_tasks = []
    current_child_task = None
    
    def _close_child_task():
        nonlocal current_child_task
        if current_child_task:
            child_tasks.append(current_child_task)
            current_child_task = None

    def _open_child_task(title):
        nonlocal current_child_task
        _close_child_task()
        current_child_task = {
            "is_child_task": True,
            "title": title,
            "steps": [],
            "status": "running"
        }

    for index in order:
        step_group = grouped[index]
        raw_events = step_group.get("raw_events", [])
        
        # Aggregate raw events into consolidated cards
        cards_map = {}
        ordered_cards = []
        
        for row in raw_events:
            kind = str(row.get("kind") or "")
            status = str(row.get("status") or "")
            payload = dict(row.get("payload") or {})
            
            action_id = str(row.get("action_id") or row.get("tool_call_id") or payload.get("tool_call_id") or payload.get("action_id") or "")
            
            if kind == "llm":
                action_id = "llm_card"
            elif kind == "tool":
                action_id = action_id or "tool_card"
            elif kind == "decision" and str(payload.get("decision_type", "")) in ("finish_acceptance", "controller_finish"):
                action_id = "finish_card"
            elif kind == "system_note" and str(payload.get("code", "")) == "finish_blocked":
                action_id = "finish_card"
            else:
                action_id = action_id or f"item_{len(ordered_cards)}"
                
            if action_id not in cards_map:
                ordered_cards.append(action_id)
                cards_map[action_id] = {
                    "label": "consolidated_card",
                    "card_type": action_id, # 'llm_card', 'tool_card', 'finish_card', etc.
                    "status": "running",
                    "events": [],
                }
                
            cards_map[action_id]["events"].append(row)
            
            # Update status of card based on terminal events
            if status in {"finished", "failed", "invalid_output", "blocked"}:
                cards_map[action_id]["status"] = status
                
        # Synthesize each card
        consolidated_items = []
        for action_id in ordered_cards:
            card = cards_map[action_id]
            card_type = card["card_type"]
            events = card["events"]
            
            synth = {
                "label": "consolidated_card",
                "card_type": "generic",
                "status": card["status"],
                "content": "",
                "details": {},
                "raw_events": events,
                "frame_depth": int(events[0].get("payload", {}).get("frame_depth") or 0) if events else 0
            }
            
            if card_type == "llm_card":
                synth["card_type"] = "llm"
                for ev in events:
                    p = ev.get("payload", {})
                    if ev.get("status") == "stream":
                        synth["details"]["streaming_text"] = p.get("content_text") or p.get("thinking_text")
                    elif ev.get("status") == "finished":
                        synth["details"]["final_text"] = p.get("content_text")
                        synth["details"]["thinking_text"] = p.get("thinking_text")
                        synth["details"]["model"] = p.get("model")
            elif "tool" in card_type:
                synth["card_type"] = "tool"
                for ev in events:
                    p = ev.get("payload", {})
                    if ev.get("kind") == "tool":
                        synth["details"]["tool_name"] = p.get("tool_name")
                        if "tool_args" in p: synth["details"]["tool_args"] = p.get("tool_args")
                        if "tool_result" in p: synth["details"]["tool_result"] = p.get("tool_result")
                        if ev.get("status") == "finished":
                            synth["details"]["status"] = "finished"
            elif card_type == "finish_card":
                synth["card_type"] = "finish"
                for ev in events:
                    p = ev.get("payload", {})
                    if p.get("decision_type") == "finish_acceptance":
                        synth["details"]["acceptance"] = p
                    elif p.get("decision_type") == "controller_finish":
                        synth["details"]["controller_finish"] = p
                    elif ev.get("kind") == "system_note" and p.get("code") == "finish_blocked":
                        synth["details"]["blocked"] = p
            else:
                synth["card_type"] = "generic"
                # fallback to just canonical item
                synth = _canonical_flow_item(events[0])
                
            consolidated_items.append(synth)
            
        step_group["items"] = consolidated_items
        step_group["phase"] = _canonical_phase(step_group)
        
        # Sub-task grouping logic based on items
        has_decompose = any(i.get("details", {}).get("tool_name") == "decompose_tasks" for i in consolidated_items if i.get("card_type") == "tool")
        has_file_ops = any(i.get("details", {}).get("tool_name") in ("write_file", "append_file", "read_file") for i in consolidated_items if i.get("card_type") == "tool")
        has_run = any(i.get("details", {}).get("tool_name") == "run_command" for i in consolidated_items if i.get("card_type") == "tool")
        has_finish = any(i.get("card_type") == "finish" for i in consolidated_items)
        
        if not current_child_task:
            if has_decompose:
                _open_child_task("計画分解")
            elif has_file_ops:
                _open_child_task("ファイル作成・修正")
            elif has_run:
                _open_child_task("実行して表示")
            elif has_finish:
                _open_child_task("完了判定")
            else:
                _open_child_task("処理中")
                
        # Adjust grouping if phase shifts
        if current_child_task["title"] == "計画分解" and not has_decompose and (has_file_ops or has_run):
            if has_file_ops: _open_child_task("ファイル作成・修正")
            elif has_run: _open_child_task("実行して表示")
            
        if current_child_task["title"] == "ファイル作成・修正" and has_run and not has_file_ops:
            _open_child_task("実行して表示")
            
        if has_finish and current_child_task["title"] != "完了判定":
            _open_child_task("完了判定")
            
        current_child_task["steps"].append(step_group)
        
        # Set task status
        if any(i.get("status") == "blocked" for i in consolidated_items):
            current_child_task["status"] = "blocked"
        elif any(i.get("status") == "failed" for i in consolidated_items):
            current_child_task["status"] = "failed"
        elif current_child_task["status"] == "running":
            current_child_task["status"] = "finished" # optimistic finish for now

    _close_child_task()
    
    return child_tasks
"""

snapshot_code = re.sub(
    r"def _canonical_flow_steps_for_operation\(.*?\).*?return rows\n",
    new_canonical_flow_steps_code,
    snapshot_code,
    flags=re.DOTALL
)

with open(SNAPSHOT_PATH, "w") as f:
    f.write(snapshot_code)

print("Updated snapshot.py")
