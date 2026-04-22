# P4 Core

P4 Core is a minimal local-LLM agent runtime for purpose execution.

## Current mainline

- Mainline version: `0.3.0-mainline`
- Mainline date: `2026-04-21`
- Canonical handoff: `handoff/p4-design-spec-2026-04-21.md`
- Code root for P4 inheritance: `p4-core/`
- Verification command: `python3 -m unittest discover -s tests`

This is the current P4 baseline. Older P4 notes and live workspaces are useful history, but P4 should inherit from this code root and the canonical handoff above.

It is intentionally smaller than P1/P2:

- session-based chat loop
- tool call -> tool result reinjection
- local Ollama model routing
- raw event persistence
- external dashboard for chat/control/visibility
- passive Japanese live commentary for step-by-step failure analysis
- blocked/success/failed operation status separation
- recursive frame hierarchy for local context isolation

## Supported models

- `gemma4:26b`
- `glm-4.7-flash`
- `qwen3-coder`
- `devstral`

## Quick start

```bash
cd /Users/satojunichi/Documents/openclaw/p4-core
python3 -m unittest discover -s tests
python3 -m p4_core.cli --root /tmp/p4-demo version
python3 -m p4_core.cli --root /tmp/p4-demo bootstrap --force
python3 -m p4_core.cli --root /tmp/p4-demo set-goal --text "READMEを読み、実行計画を返す"
python3 -m p4_core.cli --root /tmp/p4-demo chat --message "このworkspaceで最初に確認すべきファイルを読んで"
python3 -m p4_core.cli --root /tmp/p4-demo run-loop
python3 -m p4_core.cli --root /tmp/p4-demo dashboard --host 127.0.0.1 --port 8899
```

## Runtime observability

P4 records the agent loop as append-only JSONL events. Important event types:

- `user_message`
- `assistant_message`
- `tool_call`
- `tool_result`
- `finish`
- `system_note`
- `planning_note`
- `observer_note`
- `activity_update`
- `operation`
- `frame_opened`
- `frame_returned`
- `child_return`

Terminal agent turns use a text JSON action contract. If the LLM returns prose, truncated JSON, thinking-only output, or an invalid envelope instead of a valid `tool_name` / `tool_args` object, P4 records `system_note.code = llm_output_issue`. Parse failures are classified as `empty_output`, `thinking_only_output`, `missing_json_object`, `json_parse_error`, `length_truncated`, `invalid_tool_envelope`, or `json_contract_not_confirmed`. The latest class and stream metadata are also shown in runtime status and the dashboard. In Ollama chat responses, P4 parses assistant `content` only; `thinking` is retained as diagnostic evidence but is not treated as tool-call JSON.

Default Ollama output budgets are intentionally bounded but large enough for JSON tool calls: `terminal` and `coding` use `num_predict=2048`, while `reasoning` uses `1024` and `fast` stays at `384`. A `done_reason` of `length` means the model hit this budget before completing the required JSON envelope.

Action prompts are intentionally compact. They include the current user request, recent tool evidence, important system notes, current frame state, and a short reflection block. They do not replay old observer commentary, unrelated prior tasks, or large failed assistant outputs.

## Frame hierarchy

P4 exposes two frame actions to the LLM:

- `open_child_frame`: open a focused child frame with `goal` and `context_summary`
- `return_to_parent`: return from a child frame with `summary` and `findings`

These actions are shown with normal tools in the prompt, but the runtime treats them as kernel control actions. They do not go through `ToolExecutor`, and they are logged as `frame_opened` / `frame_returned` rather than `tool_call` / `tool_result`.

Each frame has isolated `session_events`. Child frame tool calls and tool results are not copied into the parent. The parent receives only a compact `child_return` event containing the child's return payload. This is the compatibility boundary: tasks that do not use frame actions continue through the P3-style session loop, while decomposed tasks keep local exploration out of the parent context.

Frame working memory starts with four fields: `observations`, `current_focus`, `unresolved_questions`, and `avoid_repeating`. Tool results update these fields automatically for common actions such as `read_file`, `search_code`, and `run_command`. Frame state is appended under `state/frames/frames.jsonl` and is also exposed in `status_snapshot()` and the dashboard snapshot.

Limits:

- maximum depth is 4
- each frame has a 15-step safety valve
- `finish` is blocked inside child frames; child frames must use `return_to_parent`
- a new queued user message abandons the previous active frame hierarchy and starts a new root frame

Coding/tool turns run inside a dedicated LLM workspace at `workspaces/runs/<turn_id>/`. P4 state, sessions, and dashboard logs stay in the root workspace, while `read_file`, `write_file`, `append_file`, `replace_text`, `search_code`, and `run_command` operate inside that turn workspace. The path is recorded as `llm_workspace` on events and is shown in the dashboard as the current or last work area.

For file edits, avoid putting long source code in one JSON argument. The chunk budget is configured once as `runtime.tool_content_chunk_bytes` and is used by both the runtime guard and the prompt. If file content exceeds that budget, the LLM should return only the next `write_file` / `append_file` chunk in the current step and continue with another `append_file` step later. Oversized `write_file` / `append_file` calls fail with guidance instead of silently accepting brittle payloads.

P4 does not use deterministic controller shortcuts for user work. Command execution and coding tasks go through the normal LLM action loop, with runtime guards validating requested commands, artifacts, and final grounding.

Finish is guarded by required-command checks, expected-artifact checks, and a grounding judge. The grounding judge asks for JSON verdicts and separates `ng` from judge failures such as `invalid_output`, `invalid_json`, `empty_output`, and `error`.

## Dashboard commentary

Set `runtime.observer_enabled` in the workspace config to enable the passive live commentator. The commentator is observational only; it does not control, approve, block, or finish tasks.

It runs after:

- an LLM response cannot be parsed as a tool call (`llm_output_issue`)
- a tool result is recorded
- a finish attempt is blocked
- a native chat response completes

The dashboard shows commentary inside the operation flow. Commentary includes what happened, why the LLM may have produced the failing output, and whether the context passed to the LLM appears noisy or contradictory.

The commentator uses the runtime `chat_timeout_seconds`; there is no separate shorter commentator timeout.

Controller/fast-path steps do not call the commentator LLM because there is no LLM action output to explain, and commentary must not block the main execution path.

Operation status values are:

- `running`
- `success`
- `failed`
- `blocked`

`blocked` means the task did not pass P4's finish/governance checks, even if the outer runner returned normally.

## Commands

- `bootstrap`
- `version`
- `status`
- `ollama-status`
- `set-goal`
- `chat`
- `run-loop`
- `worker`
- `dashboard`
